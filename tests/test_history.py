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
    assert rec["target"] == "drafts/ch0001.draft.md"
    assert rec["previous"] == "present"
    assert rec["backup"]  # 快照文件存在


def test_snapshot_contains_previous_content(proj):
    write_draft(proj, 1, "t", "OLD CONTENT")
    update_draft(proj, 1, content="NEW CONTENT")
    rec = list_history(proj)[0]
    backup = proj.store.safe_path(proj.id, rec["backup"])
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
    write_draft(proj, 1, "t", "c")
    confirm_draft(proj, 1)
    # confirm 对 project.json 的修改应留下快照
    records = list_history(proj)
    assert any(r["operation"] == "chapter.confirm" and r["target"] == "project.json" for r in records)


def test_undo_confirm_project_json(proj):
    """undo confirm 的 project.json 快照 → current_chapter 回退。"""
    write_draft(proj, 1, "t", "c")
    confirm_draft(proj, 1)
    assert proj.current_chapter == 1
    undo_last(proj)  # 回滚 project.json
    # 重新打开验证 current_chapter 恢复
    from core.project import open_project
    p2 = open_project(proj.store, proj.id)
    assert p2.current_chapter == 0, "undo confirm 后 current_chapter 应回退"
