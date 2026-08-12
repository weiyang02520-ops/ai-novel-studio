"""M1 TRANSACTION CLOSEOUT 测试。

- undo all-or-nothing: preflight 失败 0 修改 / 应用中途失败自动回滚 / 成功完整断言
- confirm 事务: 失败不留幽灵 history / 无 orphan backup / snapshot 半成品清理
- history commit 失败: 业务文件必须一起回滚(无 history 的业务 commit 不发生)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from core.chapter import confirm_draft, list_chapters, read_draft, update_draft, write_draft  # noqa: E402
from core.history import list_history, prepare_snapshot, undo_last  # noqa: E402
from core.project import create_project, open_project, validate_project  # noqa: E402
from core.storage import DataIntegrityError, ProjectStore, StorageError  # noqa: E402


@pytest.fixture
def proj(tmp_path):
    store = ProjectStore(tmp_path / "novels")
    return create_project(store, "T Novel", project_id="t-novel")


# ── helpers ───────────────────────────────────────────────

def _confirm_backup_path(proj, target_rel: str) -> Path:
    """最近 confirm record 中某 target 的 backup 绝对路径。"""
    rec = list_history(proj)[0]
    ch = next(c for c in rec["changes"] if c["target"] == target_rel)
    assert ch["previous"] == "present", f"{target_rel} 应为 present"
    return proj.store.safe_path(proj.id, ch["backup"])


def _history_backup_files(proj) -> list[Path]:
    hdir = proj.store.safe_path(proj.id, ".history")
    if not hdir.exists():
        return []
    return sorted(hdir.glob("*.bak"))


def _state_bytes(proj) -> dict[str, bytes | None]:
    """三文件当前字节(用于 0 修改 / 回滚完整性断言)。"""

    def snap(rel: str) -> bytes | None:
        p = proj.store.safe_path(proj.id, rel)
        return p.read_bytes() if p.exists() else None

    return {
        "draft": snap("drafts/ch0001.draft.md"),
        "confirmed": snap("chapters/ch0001.md"),
        "project.json": snap("project.json"),
    }


def _inject_replace_fail_once(monkeypatch, target: str):
    """os.replace 对 target 只失败一次(允许 rollback 复用同函数成功)。"""
    real_replace = os.replace
    target_alt = target.replace("/", "\\")
    state = {"fired": False}

    def failing_replace(src, dst):
        d = str(dst)
        if (target in d or target_alt in d) and not state["fired"]:
            state["fired"] = True
            raise OSError(f"simulated failure on {target}")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)
    return state


def _inject_copy2_fail_second(monkeypatch):
    """shutil.copy2 第 2 次调用失败(第 1 次成功, 允许断言 cleanup)。"""
    real_copy2 = shutil.copy2
    state = {"fired": 0}

    def failing_copy2(src, dst):
        if state["fired"] == 1:
            raise OSError("simulated copy failure")
        state["fired"] += 1
        return real_copy2(src, dst)

    monkeypatch.setattr(shutil, "copy2", failing_copy2)
    return state


# ── §6 Case A: preflight 检测 backup 缺失, 0 修改 ──────────

def test_case_a_missing_second_backup_preflight_fails_no_modification(proj):
    """第 2 个 backup(project.json)缺失 → undo 失败, 0 partial modification。"""
    write_draft(proj, 1, "t", "v1")
    update_draft(proj, 1, content="v2")
    confirm_draft(proj, 1)
    before = _state_bytes(proj)
    assert before["confirmed"] is not None and before["draft"] is None
    assert proj.current_chapter == 1
    n_records = len(list_history(proj))

    _confirm_backup_path(proj, "project.json").unlink()

    with pytest.raises(StorageError):
        undo_last(proj)

    after = _state_bytes(proj)
    assert after == before, "preflight 失败必须 0 修改"
    # confirmed 仍存在 / draft 仍不存在 / current_chapter 仍 1
    assert after["confirmed"] is not None and after["draft"] is None
    assert proj.current_chapter == 1  # 内存 metadata 未变
    assert proj.metadata["current_chapter"] == 1
    assert len(list_history(proj)) == n_records  # history record 仍存在


def test_case_a_corrupt_project_json_backup_preflight_fails(proj):
    """project.json backup 是坏 JSON → preflight 失败, 0 修改。"""
    write_draft(proj, 1, "t", "v1")
    update_draft(proj, 1, content="v2")
    confirm_draft(proj, 1)
    before = _state_bytes(proj)
    n_records = len(list_history(proj))

    _confirm_backup_path(proj, "project.json").write_text("{ broken json", encoding="utf-8")

    with pytest.raises(DataIntegrityError):
        undo_last(proj)

    assert _state_bytes(proj) == before
    assert proj.current_chapter == 1
    assert len(list_history(proj)) == n_records


# ── §6 Case B: 应用中途 os.replace 失败 → 自动回滚 ────────

def test_case_b_mid_apply_replace_failure_rolls_back(proj, monkeypatch):
    """恢复第 2 个 target(project.json)时写失败 → undo 整体失败 + 字节级回滚。"""
    write_draft(proj, 1, "t", "v1")
    update_draft(proj, 1, content="v2")
    confirm_draft(proj, 1)
    before = _state_bytes(proj)
    assert proj.current_chapter == 1
    n_records = len(list_history(proj))

    state = _inject_replace_fail_once(monkeypatch, "project.json")
    with pytest.raises(StorageError):
        undo_last(proj)
    monkeypatch.undo()
    assert state["fired"], "故障注入必须生效"

    after = _state_bytes(proj)
    assert after == before, "undo 失败必须自动回滚到 undo 前字节状态"
    assert proj.current_chapter == 1  # 失败不同步 metadata
    assert len(list_history(proj)) == n_records  # record 未被消耗


# ── §6 Case C: 删除 previous=absent target 失败 → 回滚 ────

def test_case_c_unlink_failure_on_absent_target_rolls_back(proj, monkeypatch):
    """恢复 confirm 时删除 chapters/ch0001.md 的 unlink 失败 → 整体 FAIL + 回滚。"""
    write_draft(proj, 1, "t", "v1")
    update_draft(proj, 1, content="v2")
    confirm_draft(proj, 1)
    before = _state_bytes(proj)
    n_records = len(list_history(proj))

    real_unlink = os.unlink

    def failing_unlink(path):
        s = str(path)
        if "chapters" in s and s.endswith(".md"):
            raise OSError("simulated unlink failure on confirmed chapter")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", failing_unlink)
    with pytest.raises(StorageError):
        undo_last(proj)
    monkeypatch.undo()

    after = _state_bytes(proj)
    assert after == before, "unlink 失败必须整体回滚"
    assert proj.current_chapter == 1
    assert len(list_history(proj)) == n_records


# ── §7: 成功 undo 完整断言 ────────────────────────────────

def test_undo_success_full_assertions(proj):
    """write v1 → update v2 → confirm → undo-last 的磁盘/内存/重开进程完整断言。"""
    write_draft(proj, 1, "t", "v1")
    update_draft(proj, 1, content="v2")
    confirm_draft(proj, 1)

    undo_last(proj)

    # 磁盘
    draft = proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")
    confirmed = proj.store.safe_path(proj.id, "chapters/ch0001.md")
    assert draft.exists() and read_draft(proj, 1).body == "v2"
    assert not confirmed.exists()
    pj = json.loads(proj.store.safe_path(proj.id, "project.json").read_text(encoding="utf-8"))
    assert pj["current_chapter"] == 0
    # 内存 metadata
    assert proj.current_chapter == 0
    # validate PASS
    assert validate_project(proj.store, proj.id) == []
    # list: 只有 draft, 无 conflict
    items = list_chapters(proj)
    assert len(items) == 1 and items[0]["location"] == "draft" and not items[0].get("conflict")
    # confirm record 被移除(update 的 record 仍在)
    records = list_history(proj)
    assert [r["operation"] for r in records] == ["chapter.update"]
    # 重启新进程后一致
    p2 = open_project(proj.store, proj.id)
    assert p2.current_chapter == 0
    assert read_draft(p2, 1).body == "v2"
    assert validate_project(proj.store, proj.id) == []


# ── §8: 失败的 confirm 不留 history / 无 orphan backup ────

def _assert_no_history_growth(proj, n_before: int, n_bak_before: int) -> None:
    assert len(list_history(proj)) == n_before, "失败操作不得新增 history record"
    assert len(_history_backup_files(proj)) == n_bak_before, "失败操作不得遗留 orphan backup"


@pytest.mark.parametrize("fail_target", ["chapters/ch0001.md", "project.json"])
def test_failed_confirm_no_history_write_failure(proj, monkeypatch, fail_target):
    """写 confirmed / project.json 失败 → history count 不变 + 无 orphan backup。"""
    write_draft(proj, 1, "t", "CONTENT")
    n, n_bak = len(list_history(proj)), len(_history_backup_files(proj))

    state = _inject_replace_fail_once(monkeypatch, fail_target)
    with pytest.raises(StorageError):
        confirm_draft(proj, 1)
    monkeypatch.undo()
    assert state["fired"], "故障注入必须生效"

    _assert_no_history_growth(proj, n, n_bak)
    assert read_draft(proj, 1).body == "CONTENT"
    assert not (proj.store.safe_path(proj.id, "chapters/ch0001.md")).exists()
    assert proj.current_chapter == 0
    assert validate_project(proj.store, proj.id) == []


def test_failed_confirm_no_history_draft_unlink_failure(proj, monkeypatch):
    """删 draft 失败 → 无 record + 无 orphan backup + 双份不留下。"""
    write_draft(proj, 1, "t", "CONTENT")
    n, n_bak = len(list_history(proj)), len(_history_backup_files(proj))

    real_unlink = os.unlink

    def failing_unlink(path):
        if str(path).endswith("ch0001.draft.md"):
            raise OSError("simulated unlink failure")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", failing_unlink)
    with pytest.raises(StorageError):
        confirm_draft(proj, 1)
    monkeypatch.undo()

    _assert_no_history_growth(proj, n, n_bak)
    # 完整 rollback: draft 在, confirmed 不在, current_chapter 不变
    assert read_draft(proj, 1).body == "CONTENT"
    assert not (proj.store.safe_path(proj.id, "chapters/ch0001.md")).exists()
    assert proj.current_chapter == 0
    assert validate_project(proj.store, proj.id) == []


def test_failed_confirm_no_history_snapshot_prep_failure(proj, monkeypatch):
    """snapshot 准备失败 → 无 record + 无 orphan backup + 业务文件不变。"""
    write_draft(proj, 1, "t", "CONTENT")
    n = len(list_history(proj))

    state = _inject_copy2_fail_second(monkeypatch)
    with pytest.raises(StorageError):
        confirm_draft(proj, 1)
    monkeypatch.undo()
    assert state["fired"] == 1, "故障注入必须生效(第 2 次 copy 失败)"

    _assert_no_history_growth(proj, n, 0)
    assert read_draft(proj, 1).body == "CONTENT"


# ── §9: snapshot 半成品清理 + seq 不混乱 ──────────────────

def test_snapshot_partial_failure_cleanup_and_seq(proj, monkeypatch):
    """prepare 第 2 个 target 失败 → 第 1 个 backup 清理 + index 无记录 + 原文件不变。"""
    write_draft(proj, 1, "t", "CONTENT")
    draft_bytes = proj.store.safe_path(proj.id, "drafts/ch0001.draft.md").read_bytes()
    pj_bytes = proj.store.safe_path(proj.id, "project.json").read_bytes()

    state = _inject_copy2_fail_second(monkeypatch)
    with pytest.raises(StorageError):
        prepare_snapshot(proj, "test.op",
                         ["drafts/ch0001.draft.md", "project.json", "chapters/ch0001.md"])
    monkeypatch.undo()
    assert state["fired"] == 1, "故障注入必须生效"

    assert _history_backup_files(proj) == [], "部分成功的 backup 必须清理"
    assert list_history(proj) == [], "index 不得有新记录"
    assert proj.store.safe_path(proj.id, "drafts/ch0001.draft.md").read_bytes() == draft_bytes
    assert proj.store.safe_path(proj.id, "project.json").read_bytes() == pj_bytes

    # 后续正常 snapshot seq 不混乱
    update_draft(proj, 1, content="V2")
    records = list_history(proj)
    assert len(records) == 1 and records[0]["seq"] == 1


# ── §10: history commit 失败 → 业务一起回滚 ────────────────

def test_update_history_commit_failure_rolls_back_business(proj, monkeypatch):
    """update: 业务写成功但 index commit 失败 → 业务文件恢复 + 无 record。"""
    write_draft(proj, 1, "t", "OLD")
    n_bak = len(_history_backup_files(proj))

    state = _inject_replace_fail_once(monkeypatch, "index.jsonl")
    with pytest.raises(StorageError):
        update_draft(proj, 1, content="NEW")
    monkeypatch.undo()
    assert state["fired"], "故障注入必须生效"

    assert read_draft(proj, 1).body == "OLD", "commit 失败必须恢复业务文件"
    assert list_history(proj) == [], "不得留下无业务对应的 record"
    assert len(_history_backup_files(proj)) == n_bak, "不得遗留 orphan backup"


def test_confirm_history_commit_failure_rolls_back_business(proj, monkeypatch):
    """confirm: 业务全成功但 index commit 失败 → 整体 rollback, 无幽灵 confirm。"""
    write_draft(proj, 1, "t", "CONTENT")

    state = _inject_replace_fail_once(monkeypatch, "index.jsonl")
    with pytest.raises(StorageError):
        confirm_draft(proj, 1)
    monkeypatch.undo()
    assert state["fired"], "故障注入必须生效"

    assert read_draft(proj, 1).body == "CONTENT"
    assert not (proj.store.safe_path(proj.id, "chapters/ch0001.md")).exists()
    assert proj.current_chapter == 0
    assert list_history(proj) == []
    assert _history_backup_files(proj) == []
    assert validate_project(proj.store, proj.id) == []


# ── 诚实报错(§5) ──────────────────────────────────────────

def test_confirm_failure_message_honest_rollback_success(proj, monkeypatch):
    """rollback 成功 → 消息明确宣称已恢复到确认前状态。"""
    write_draft(proj, 1, "t", "CONTENT")
    _inject_replace_fail_once(monkeypatch, "chapters/ch0001.md")

    with pytest.raises(StorageError) as e:
        confirm_draft(proj, 1)
    assert "已恢复到确认前状态" in str(e.value)
    assert "请运行 novel validate" not in str(e.value)


def test_confirm_failure_message_honest_rollback_failed(proj, monkeypatch):
    """rollback 本身也失败 → 高严重度报错, 不得声称已回滚。"""
    write_draft(proj, 1, "t", "CONTENT")
    # 写 confirmed 失败且后续所有 replace 都失败(rollback 无法恢复)
    real_replace = os.replace

    def failing_replace(src, dst):
        raise OSError("simulated total replace failure")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(DataIntegrityError) as e:
        confirm_draft(proj, 1)
    assert "自动恢复未完整完成" in str(e.value)
    assert "请运行 novel validate" in str(e.value)
    assert "已恢复到确认前状态" not in str(e.value)
