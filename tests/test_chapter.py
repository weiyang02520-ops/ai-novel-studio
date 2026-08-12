"""M1 章节测试: 草稿 CRUD / confirm / 状态规则 / 损坏检测 / 编号。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from core.chapter import (  # noqa: E402
    confirm_draft,
    count_words,
    format_chapter_filename,
    list_chapters,
    read_confirmed,
    read_draft,
    update_draft,
    write_draft,
)
from core.project import create_project  # noqa: E402
from core.storage import (  # noqa: E402
    DataIntegrityError,
    ProjectStore,
    StorageError,
)


@pytest.fixture
def proj(tmp_path):
    store = ProjectStore(tmp_path / "novels")
    return create_project(store, "Test Novel", project_id="test-novel")


# ── 编号 ─────────────────────────────────────────────────

def test_chapter_filename_formatting():
    assert format_chapter_filename(1) == "ch0001"
    assert format_chapter_filename(9) == "ch0009"
    assert format_chapter_filename(10) == "ch0010"
    assert format_chapter_filename(999) == "ch0999"
    assert format_chapter_filename(9999) == "ch9999"
    assert format_chapter_filename(10000) == "ch10000"
    with pytest.raises(ValueError):
        format_chapter_filename(0)
    with pytest.raises(ValueError):
        format_chapter_filename(-1)
    with pytest.raises(ValueError):
        format_chapter_filename(1.5)


# ── 创建/读取 ────────────────────────────────────────────

def test_write_draft(proj):
    c = write_draft(proj, 1, "第一章", "正文内容")
    assert c.number == 1
    assert c.status == "draft"
    assert c.origin == "manual"
    assert c.words == count_words("正文内容")
    path = proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")
    assert path.exists()


def test_write_duplicate_rejected(proj):
    write_draft(proj, 1, "t", "c")
    with pytest.raises(StorageError):
        write_draft(proj, 1, "t2", "c2")


def test_read_draft(proj):
    write_draft(proj, 1, "第一章", "这是正文。")
    c = read_draft(proj, 1)
    assert c.body == "这是正文。"
    assert c.title == "第一章"


def test_read_missing_draft(proj):
    with pytest.raises(StorageError):
        read_draft(proj, 1)


def test_unicode_content_roundtrip(proj):
    content = "第一章\n这里有中文。\nThere is English.\n符号：，。！？“”"
    write_draft(proj, 1, "测试", content)
    c = read_draft(proj, 1)
    assert c.body == content, "Unicode 正文 round-trip 必须完全一致"


def test_words_is_non_whitespace_count():
    assert count_words("abc") == 3
    assert count_words("a b c") == 3
    assert count_words("中文测试") == 4
    assert count_words("   \n\t ") == 0


# ── 更新 ─────────────────────────────────────────────────

def test_update_draft(proj):
    write_draft(proj, 1, "t", "v1")
    c = update_draft(proj, 1, content="v2 content")
    assert c.body == "v2 content"
    assert read_draft(proj, 1).body == "v2 content"


def test_update_title(proj):
    write_draft(proj, 1, "old", "c")
    c = update_draft(proj, 1, title="new")
    assert c.title == "new"
    assert c.body == "c"  # 正文不变


def test_update_missing_draft_rejected(proj):
    with pytest.raises(StorageError):
        update_draft(proj, 1, content="x")


# ── list ─────────────────────────────────────────────────

def test_list_drafts_and_confirmed(proj):
    write_draft(proj, 1, "草稿一", "c1")
    write_draft(proj, 2, "草稿二", "c2")
    confirm_draft(proj, 1)
    items = list_chapters(proj)
    assert len(items) == 2
    ch1 = next(i for i in items if i["chapter"] == 1)
    ch2 = next(i for i in items if i["chapter"] == 2)
    assert ch1["status"] == "confirmed" and ch1["location"] == "confirmed"
    assert ch2["status"] == "draft" and ch2["location"] == "draft"


# ── confirm ──────────────────────────────────────────────

def test_confirm_moves_draft_to_chapters(proj):
    write_draft(proj, 1, "第一章", "最终正文")
    c = confirm_draft(proj, 1)
    assert c.status == "confirmed"
    assert c.origin == "manual"
    # draft 删除, confirmed 存在
    assert not (proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")).exists()
    confirmed = proj.store.safe_path(proj.id, "chapters/ch0001.md")
    assert confirmed.exists()
    # 内容一致
    assert read_confirmed(proj, 1).body == "最终正文"


def test_confirm_updates_current_chapter(proj):
    write_draft(proj, 1, "t", "c")
    assert proj.current_chapter == 0
    confirm_draft(proj, 1)
    assert proj.current_chapter == 1


def test_draft_only_does_not_advance_current_chapter(proj):
    write_draft(proj, 1, "t", "c")
    write_draft(proj, 2, "t2", "c2")
    assert proj.current_chapter == 0  # 仅草稿不变


def test_update_draft_does_not_advance(proj):
    write_draft(proj, 1, "t", "v1")
    update_draft(proj, 1, content="v2")
    assert proj.current_chapter == 0


def test_old_chapter_confirm_does_not_decrease(proj):
    write_draft(proj, 1, "t1", "c1")
    write_draft(proj, 5, "t5", "c5")
    confirm_draft(proj, 5)
    assert proj.current_chapter == 5
    confirm_draft(proj, 1)  # 旧章节确认
    assert proj.current_chapter == 5  # 不倒退(max 语义)


def test_confirm_missing_draft_rejected(proj):
    with pytest.raises(StorageError):
        confirm_draft(proj, 1)
    assert proj.current_chapter == 0


def test_confirm_ai_origin_not_ready_rejected(proj):
    """origin=ai 且未 ready → 拒绝(M1 状态边界不留口)。"""
    write_draft(proj, 1, "t", "c")
    path = proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("origin: manual", "origin: ai"), encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        confirm_draft(proj, 1)


def test_confirmed_protected_from_overwrite(proj):
    """confirmed 后: write/update 都不得覆盖正式正文。"""
    write_draft(proj, 1, "t", "final")
    confirm_draft(proj, 1)
    with pytest.raises(StorageError):
        write_draft(proj, 1, "new", "x")
    with pytest.raises(StorageError):
        update_draft(proj, 1, content="overwrite")


# ── 损坏检测 ─────────────────────────────────────────────

def test_damaged_frontmatter_missing_block(proj):
    write_draft(proj, 1, "t", "c")
    path = proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")
    path.write_text("没有 frontmatter 的正文", encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        read_draft(proj, 1)


def test_damaged_frontmatter_bad_status(proj):
    write_draft(proj, 1, "t", "c")
    path = proj.store.safe_path(proj.id, "drafts/ch0001.draft.md")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("status: draft", "status: nonsense"), encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        read_draft(proj, 1)


def test_chapter_number_mismatch_detected(proj):
    write_draft(proj, 2, "t", "c")
    path = proj.store.safe_path(proj.id, "drafts/ch0002.draft.md")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("chapter: 2", "chapter: 7"), encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        read_draft(proj, 2)


def test_confirmed_dir_with_draft_status_detected(proj):
    write_draft(proj, 1, "t", "c")
    confirm_draft(proj, 1)
    # 破坏: chapters/ch0001.md 的 status 改为 draft
    path = proj.store.safe_path(proj.id, "chapters/ch0001.md")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("status: confirmed", "status: draft"), encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        read_confirmed(proj, 1)
