"""M1 稳定化测试: confirm 事务 / 故障注入 / 崩溃残留 / metadata 严格 / history 损坏 / frontmatter roundtrip。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from core import chapter as ch  # noqa: E402
from core.chapter import (  # noqa: E402
    build_frontmatter,
    confirm_draft,
    list_chapters,
    parse_frontmatter,
    read_confirmed,
    read_draft,
    update_draft,
    write_draft,
)
from core.history import list_history, undo_last  # noqa: E402
from core.project import create_project, open_project, validate_project  # noqa: E402
from core.storage import (  # noqa: E402
    DataIntegrityError,
    ProjectStore,
    StorageError,
    atomic_write_text,
)


@pytest.fixture
def proj(tmp_path):
    store = ProjectStore(tmp_path / "novels")
    return create_project(store, "S Novel", project_id="s-novel")


# ── 故障注入: confirm 中途失败必须 rollback(§27) ──────────

def _inject_fail(monkeypatch, target: str):
    """注入 os.replace 失败(原子写失败), target 选择受影响的文件。

    兼容 Windows 反斜杠与 POSIX 正斜杠路径。
    """
    import os
    real_replace = os.replace
    target_alt = target.replace("/", "\\")

    def failing_replace(src, dst):
        d = str(dst)
        if target in d or target_alt in d:
            raise OSError(f"simulated failure on {target}")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)


def test_confirm_failure_writing_confirmed_rolls_back(proj, monkeypatch):
    """A: 写 confirmed 失败 → draft 原样 + confirmed 不存在 + current_chapter 不变 + validate PASS。"""
    write_draft(proj, 1, "t", "CONTENT")
    _inject_fail(monkeypatch, "chapters/ch0001.md")

    with pytest.raises(StorageError):
        confirm_draft(proj, 1)

    monkeypatch.undo()
    # draft 原样
    assert read_draft(proj, 1).body == "CONTENT"
    # confirmed 不存在
    assert not (proj.store.safe_path(proj.id, "chapters/ch0001.md")).exists()
    # current_chapter 不变
    assert proj.current_chapter == 0
    assert validate_project(proj.store, proj.id) == []


def test_confirm_failure_updating_project_json_rolls_back(proj, monkeypatch):
    """B: 更新 project.json 失败 → 全部恢复。"""
    write_draft(proj, 1, "t", "CONTENT")
    _inject_fail(monkeypatch, "project.json")

    with pytest.raises(StorageError):
        confirm_draft(proj, 1)

    monkeypatch.undo()
    assert read_draft(proj, 1).body == "CONTENT"
    assert not (proj.store.safe_path(proj.id, "chapters/ch0001.md")).exists()
    assert proj.current_chapter == 0
    assert validate_project(proj.store, proj.id) == []


def test_confirm_failure_deleting_draft_rolls_back(proj, monkeypatch):
    """C: 删除 draft 失败 → 同样 rollback(不留下双份)。"""
    import os
    write_draft(proj, 1, "t", "CONTENT")
    real_unlink = os.unlink

    def failing_unlink(path):
        if str(path).endswith("ch0001.draft.md"):
            raise OSError("simulated unlink failure")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", failing_unlink)
    with pytest.raises(StorageError):
        confirm_draft(proj, 1)
    monkeypatch.undo()

    # rollback: draft 恢复, confirmed 删除, current_chapter 不变
    assert read_draft(proj, 1).body == "CONTENT"
    assert not (proj.store.safe_path(proj.id, "chapters/ch0001.md")).exists()
    assert proj.current_chapter == 0
    assert validate_project(proj.store, proj.id) == []


def test_confirm_failure_history_write_rolls_back(proj, monkeypatch):
    """D: history 快照写失败(复制失败)→ 业务文件不得出现半确认。"""
    import shutil
    write_draft(proj, 1, "t", "CONTENT")
    real_copy2 = shutil.copy2

    def failing_copy2(src, dst):
        raise OSError("simulated snapshot copy failure")

    monkeypatch.setattr(shutil, "copy2", failing_copy2)
    with pytest.raises(StorageError):
        confirm_draft(proj, 1)
    monkeypatch.undo()
    assert read_draft(proj, 1).body == "CONTENT"
    assert not (proj.store.safe_path(proj.id, "chapters/ch0001.md")).exists()
    assert proj.current_chapter == 0


# ── 崩溃残留检测(§28) ────────────────────────────────────

def test_duplicate_draft_confirmed_detected(proj):
    """人为制造 draft + confirmed 同编号(合法 frontmatter 残留)→ validate FAIL + list 冲突。"""
    write_draft(proj, 1, "t", "DRAFT")
    confirm_draft(proj, 1)
    # 人为恢复带合法 frontmatter 的草稿(模拟崩溃残留; write_draft 会被 confirmed 保护拦截, 直接写文件)
    draft_text = (
        "---\nchapter: 1\nvolume: 1\ntitle: 残留\nstatus: draft\norigin: manual\n"
        "words: 4\ncreated_at: t\nupdated_at: t\nsummary: \"\"\ncharacters: []\n---\n残留草稿\n"
    )
    atomic_write_text(proj.store.safe_path(proj.id, "drafts/ch0001.draft.md"), draft_text)

    issues = validate_project(proj.store, proj.id)
    assert any("duplicate" in i or "同时存在" in i for i in issues), f"issues={issues}"

    items = list_chapters(proj)
    assert len(items) == 2, "list 不得静默隐藏冲突"
    assert all(i.get("conflict") for i in items), "冲突条目必须标记"
    assert all(i["status"] == "CONFLICT" for i in items)


def test_current_chapter_mismatch_high_detected(proj):
    """current_chapter=1 但 confirmed/ 空 → validate FAIL。"""
    write_draft(proj, 1, "t", "c")
    confirm_draft(proj, 1)
    # 人为删除 confirmed(模拟崩溃残留)
    (proj.store.safe_path(proj.id, "chapters/ch0001.md")).unlink()
    issues = validate_project(proj.store, proj.id)
    assert any("current_chapter" in i for i in issues)


def test_current_chapter_mismatch_low_detected(proj):
    """current_chapter=0 但 chapters/ch0001.md 存在 → validate FAIL。"""
    write_draft(proj, 1, "t", "c")
    confirm_draft(proj, 1)
    # 人为回退 current_chapter
    proj.metadata["current_chapter"] = 0
    proj.save_metadata()
    issues = validate_project(proj.store, proj.id)
    assert any("current_chapter" in i for i in issues)


def test_validate_checks_filename_frontmatter_consistency(proj):
    """ch0002.draft.md 但 frontmatter chapter=7 → validate 检测。"""
    write_draft(proj, 2, "t", "c")
    path = proj.store.safe_path(proj.id, "drafts/ch0002.draft.md")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("chapter: 2", "chapter: 7"), encoding="utf-8")
    issues = validate_project(proj.store, proj.id)
    assert any("ch0002" in i and "不一致" in i for i in issues)


def test_validate_current_chapter_matches_max_confirmed(proj):
    """[1,5] confirmed → expected current_chapter=5。"""
    write_draft(proj, 1, "t1", "c1")
    write_draft(proj, 5, "t5", "c5")
    confirm_draft(proj, 1)
    confirm_draft(proj, 5)
    assert proj.current_chapter == 5
    assert validate_project(proj.store, proj.id) == []


# ── metadata 严格验证(§29) ───────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("current_chapter", "1"),       # str
    ("current_chapter", True),      # bool
    ("current_chapter", 1.5),       # float
    ("current_chapter", -1),        # 负数
    ("current_volume", 0),          # <1
    ("current_volume", True),       # bool
    ("auto_accept", "false"),       # str
    ("name", None),                 # null
    ("defaults", []),               # list 非 object
])
def test_metadata_corruption_rejected(proj, field, value):
    path = proj.store.safe_path(proj.id, "project.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        open_project(proj.store, proj.id)


def test_metadata_ok_types(proj):
    """正常类型必须通过(回归)。"""
    assert open_project(proj.store, proj.id) is not None


# ── history 损坏(§30) ─────────────────────────────────────

def _write_history_line(proj, line: str) -> None:
    idx = proj.store.safe_path(proj.id, ".history/index.jsonl")
    idx.write_text(line + "\n", encoding="utf-8")


def test_history_bad_json_line_raises(proj):
    write_draft(proj, 1, "t", "OLD")
    update_draft(proj, 1, content="NEW")
    idx = proj.store.safe_path(proj.id, ".history/index.jsonl")
    idx.write_text(idx.read_text(encoding="utf-8") + "{ broken json\n", encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        list_history(proj)
    with pytest.raises(DataIntegrityError):
        undo_last(proj)


def test_history_bad_seq_raises(proj):
    write_draft(proj, 1, "t", "OLD")
    update_draft(proj, 1, content="NEW")
    idx = proj.store.safe_path(proj.id, ".history/index.jsonl")
    idx.write_text('{"seq": "x", "operation": "chapter.update", "timestamp": "t", "changes": [{"target": "a", "previous": "absent", "backup": null}]}\n',
                   encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        list_history(proj)


def test_history_snapshot_rejects_corrupt_index(proj):
    """index 损坏时 snapshot 也必须拒绝(不重复 seq)。"""
    write_draft(proj, 1, "t", "OLD")
    idx = proj.store.safe_path(proj.id, ".history/index.jsonl")
    idx.write_text("{ broken\n", encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        update_draft(proj, 1, content="NEW")


def test_history_missing_backup_undo_fails_without_modification(proj):
    """backup 缺失 → undo 明确失败, 不修改小说文件。"""
    write_draft(proj, 1, "t", "OLD CONTENT")
    update_draft(proj, 1, content="NEW CONTENT")
    rec = list_history(proj)[0]
    backup = proj.store.safe_path(proj.id, rec["changes"][0]["backup"])
    backup.unlink()
    with pytest.raises(StorageError):
        undo_last(proj)
    # 文件未被修改
    assert read_draft(proj, 1).body == "NEW CONTENT"


# ── frontmatter roundtrip(§21-22) ────────────────────────

@pytest.mark.parametrize("chars", [
    [],
    ["a"],
    ["lin-xiaoman", "lao-chen"],
    ["沈砚", "陈旧"],
])
def test_frontmatter_list_roundtrip(chars):
    meta = {
        "chapter": 1, "volume": 1, "title": "t", "status": "draft",
        "origin": "manual", "words": 5, "created_at": "t", "updated_at": "t",
        "summary": "", "characters": chars,
    }
    text = build_frontmatter(meta)
    parsed, _ = parse_frontmatter(text)
    assert parsed["characters"] == chars, f"list roundtrip 失败: {chars!r}"


def test_frontmatter_title_with_newline_rejected():
    meta = {
        "chapter": 1, "volume": 1, "title": "第一行\n第二行", "status": "draft",
        "origin": "manual", "words": 1, "created_at": "t", "updated_at": "t",
        "summary": "", "characters": [],
    }
    with pytest.raises(DataIntegrityError):
        build_frontmatter(meta)


def test_write_draft_rejects_newline_title(proj):
    with pytest.raises(DataIntegrityError):
        write_draft(proj, 1, "标题\n带换行", "c")


# ── update guards(§19-20) ────────────────────────────────

def test_update_rejects_reviewing_status(proj):
    write_draft(proj, 1, "t", "c")
    path = proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("status: draft", "status: reviewing"), encoding="utf-8")
    with pytest.raises(StorageError):
        update_draft(proj, 1, content="x")


def test_update_rejects_ai_origin(proj):
    write_draft(proj, 1, "t", "c")
    path = proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("origin: manual", "origin: ai"), encoding="utf-8")
    with pytest.raises(StorageError):
        update_draft(proj, 1, content="x")


def test_update_user_confirmed_allowed_returns_draft(proj):
    write_draft(proj, 1, "t", "c")
    path = proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("status: draft", "status: user_confirmed"), encoding="utf-8")
    c = update_draft(proj, 1, content="updated")
    assert c.status == "draft", "user_confirmed 更新后应回 draft"
