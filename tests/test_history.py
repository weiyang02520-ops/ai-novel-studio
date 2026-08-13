"""M1 history 测试: 快照 / undo / 项目隔离 / 无记录错误。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from core.chapter import confirm_draft, read_draft, update_draft, write_draft  # noqa: E402
from core.history import list_history, snapshot, undo_last  # noqa: E402
from core.project import create_project  # noqa: E402
from core.storage import ProjectStore, StorageError  # noqa: E402


@pytest.fixture
def proj(tmp_path):
    store = ProjectStore(tmp_path / "novels")
    return create_project(store, "H Novel", project_id="h-novel")


def test_update_creates_snapshot(proj):
    write_draft(proj, 1, "t", "v1 content")
    update_draft(proj, 1, content="v2 content")
    records = list_history(proj)
    assert len(records) == 1
    rec = records[0]
    assert rec["operation"] == "chapter.update"
    assert len(rec["changes"]) == 1
    ch = rec["changes"][0]
    assert ch["target"] == "drafts/ch0001.draft.md"
    assert ch["previous"] == "present"
    assert ch["backup"]


def test_immediate_snapshot_commit_failure_releases_history_lock(proj, monkeypatch):
    import core.history as history
    write_draft(proj, 1, "t", "v1")
    real = history.atomic_write_text
    failed = False

    def fail_index_once(path, text):
        nonlocal failed
        if path.name == "index.jsonl" and not failed:
            failed = True
            raise StorageError("index fault")
        return real(path, text)

    monkeypatch.setattr(history, "atomic_write_text", fail_index_once)
    with pytest.raises(StorageError, match="index fault"):
        snapshot(proj, "test.failed", "drafts/ch0001.draft.md")
    # Must not wait for Snapshot GC or hit the 30-second lock timeout.
    record = snapshot(proj, "test.after", "drafts/ch0001.draft.md")
    assert record["operation"] == "test.after" and record["seq"] == 1


def test_snapshot_contains_previous_content(proj):
    write_draft(proj, 1, "t", "OLD CONTENT")
    update_draft(proj, 1, content="NEW CONTENT")
    rec = list_history(proj)[0]
    backup = proj.store.safe_path(proj.id, rec["changes"][0]["backup"])
    assert "OLD CONTENT" in backup.read_text(encoding="utf-8")


def test_undo_restores(proj):
    write_draft(proj, 1, "t", "OLD")
    update_draft(proj, 1, content="NEW")
    assert read_draft(proj, 1).body == "NEW"
    undo_last(proj)
    assert read_draft(proj, 1).body == "OLD", "undo 必须恢复旧内容"


def test_undo_removes_record(proj):
    write_draft(proj, 1, "t", "OLD")
    update_draft(proj, 1, content="NEW")
    assert len(list_history(proj)) == 1
    undo_last(proj)
    assert list_history(proj) == [], "undo 后记录应从 index 移除"


def test_nothing_to_undo_clear_error(proj):
    with pytest.raises(StorageError) as e:
        undo_last(proj)
    assert "没有可回滚" in str(e.value)


def test_history_isolated_between_projects(tmp_path):
    store = ProjectStore(tmp_path / "novels")
    a = create_project(store, "A", project_id="novel-a")
    b = create_project(store, "B", project_id="novel-b")

    write_draft(a, 1, "t", "A v1")
    update_draft(a, 1, content="A v2")
    assert len(list_history(a)) == 1
    assert list_history(b) == [], "B 不得看到 A 的历史"


def test_confirm_project_json_snapshot(proj):
    """confirm 快照: changes 列表含 project.json + draft + confirmed(absent)。"""
    write_draft(proj, 1, "t", "c")
    confirm_draft(proj, 1)
    records = list_history(proj)
    confirm_rec = next(r for r in records if r["operation"] == "chapter.confirm")
    targets = [ch["target"] for ch in confirm_rec["changes"]]
    assert "project.json" in targets
    assert "drafts/ch0001.draft.md" in targets
    assert "chapters/ch0001.md" in targets


# ── 完整 confirm undo(§10) ───────────────────────────────

def test_confirm_undo_restores_full_state(proj):
    """undo confirm → current_chapter 恢复 + draft 恢复(内容一致) + confirmed 删除 + validate PASS。"""
    from core.project import validate_project
    write_draft(proj, 1, "第一章", "V2 FINAL CONTENT")
    confirm_draft(proj, 1)
    assert proj.current_chapter == 1
    assert not (proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")).exists()

    undo_last(proj)
    # current_chapter 恢复
    assert proj.current_chapter == 0
    # draft 恢复, 内容一致
    c = read_draft(proj, 1)
    assert c.body == "V2 FINAL CONTENT"
    assert c.status == "draft"
    # confirmed 删除
    assert not (proj.store.safe_path(proj.id, "chapters/ch0001.md")).exists()
    # 完整验证
    assert validate_project(proj.store, proj.id) == []


def test_confirm_undo_disk_state_after_reopen(proj):
    """undo confirm 后重开项目, 磁盘状态完全回到 confirm 前。"""
    from core.project import open_project, validate_project
    write_draft(proj, 1, "t", "CONTENT X")
    confirm_draft(proj, 1)
    undo_last(proj)
    p2 = open_project(proj.store, proj.id)
    assert p2.current_chapter == 0
    assert read_draft(p2, 1).body == "CONTENT X"
    assert validate_project(proj.store, proj.id) == []


def test_undo_confirm_syncs_metadata_object(proj):
    """undo 恢复 project.json 后, Project 内存 metadata 同步(不要求重开进程)。"""
    write_draft(proj, 1, "t", "c")
    confirm_draft(proj, 1)
    assert proj.current_chapter == 1
    undo_last(proj)
    assert proj.current_chapter == 0, "内存 metadata 必须同步恢复"
