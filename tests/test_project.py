"""M1 项目测试: 创建/ID 安全/目录骨架/元数据/损坏检测。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from core.project import (  # noqa: E402
    create_project,
    list_projects,
    open_project,
    validate_project,
)
from core.storage import (  # noqa: E402
    DataIntegrityError,
    ProjectStore,
    StorageError,
    validate_project_id,
)


@pytest.fixture
def store(tmp_path) -> ProjectStore:
    return ProjectStore(tmp_path / "novels")


# ── ID 安全 ──────────────────────────────────────────────

def test_project_id_validation():
    assert validate_project_id("wanxiang-journey")
    assert validate_project_id("novel_001")
    assert not validate_project_id("")                    # 空
    assert not validate_project_id("A")                   # 大写开头
    assert not validate_project_id("1abc")                # 数字开头
    assert not validate_project_id("../evil")             # 路径穿越
    assert not validate_project_id("..")                  # ..
    assert not validate_project_id("a" * 65)              # 超长
    assert not validate_project_id("has space")           # 空格
    assert not validate_project_id("a/b")                 # 斜杠
    assert not validate_project_id("a\\b")                # 反斜杠


def test_path_traversal_id_rejected(store):
    with pytest.raises(StorageError):
        create_project(store, "x", project_id="../evil")
    with pytest.raises(StorageError):
        create_project(store, "x", project_id="..")
    with pytest.raises(StorageError):
        open_project(store, "../../etc")


# ── 创建 ─────────────────────────────────────────────────

def test_create_with_generated_id(store):
    p = create_project(store, "Wanxiang Journey")
    assert p.id == "wanxiang-journey"
    assert (store.root / p.id).exists()


def test_create_chinese_name_generated_id(store):
    """纯中文名 → novel-<hex> 稳定 ID; 显示名保留中文。"""
    p = create_project(store, "山河不记")
    assert p.id.startswith("novel-")
    assert len(p.id) == len("novel-") + 8
    assert p.name == "山河不记"
    # 重新打开显示名不变
    p2 = open_project(store, p.id)
    assert p2.name == "山河不记"


def test_create_explicit_valid_id(store):
    p = create_project(store, "万象之旅", project_id="wanxiang-journey", genre="奇幻")
    assert p.id == "wanxiang-journey"
    assert p.genre == "奇幻"


def test_create_duplicate_rejected(store):
    create_project(store, "A", project_id="novel-a")
    with pytest.raises(StorageError):
        create_project(store, "B", project_id="novel-a")


def test_create_empty_name_rejected(store):
    with pytest.raises(StorageError):
        create_project(store, "   ")


# ── 目录骨架 ─────────────────────────────────────────────

def test_directory_skeleton(store):
    p = create_project(store, "Skeleton", project_id="skeleton-test")
    expected = [
        "project.json", "settings.json", ".history",
        "outline/summary.md", "outline/volumes", "outline/chapters",
        "characters", "world", "rules/writing_rules.md",
        "chapters", "drafts",
        "memory/index.md", "memory/summaries", "memory/characters.md",
        "memory/world.md", "memory/timeline.md",
        "memory/foreshadowing/index.md", "memory/long_term.md",
        "review",
    ]
    for rel in expected:
        assert (p.dir / rel).exists(), f"缺少 {rel}"


def test_project_json_metadata(store):
    p = create_project(store, "Meta", project_id="meta-test", genre="科幻")
    md = p.metadata
    assert md["format_version"] == 1
    assert md["id"] == "meta-test"
    assert md["name"] == "Meta"
    assert md["genre"] == "科幻"
    assert md["status"] == "active"
    assert md["current_volume"] == 1
    assert md["current_chapter"] == 0
    assert md["auto_accept"] is False
    # ISO-8601 UTC
    assert "T" in md["created_at"] and md["created_at"].endswith("Z")


# ── open / list ──────────────────────────────────────────

def test_open_and_reopen(store):
    p = create_project(store, "Reopen", project_id="reopen-test")
    p2 = open_project(store, "reopen-test")
    assert p2.id == p.id
    assert p2.name == "Reopen"


def test_open_nonexistent(store):
    with pytest.raises(StorageError):
        open_project(store, "no-such-project")


def test_list_multiple(store):
    create_project(store, "A", project_id="novel-a")
    create_project(store, "B", project_id="novel-b")
    items = list_projects(store)
    ids = [i["id"] for i in items]
    assert ids == ["novel-a", "novel-b"]  # 排序


def test_list_with_corrupted_project_marks_invalid(store):
    create_project(store, "Good", project_id="good-novel")
    bad = store.root / "bad-novel"
    bad.mkdir()
    (bad / "project.json").write_text("{ broken", encoding="utf-8")
    items = list_projects(store)
    assert any(i["id"] == "good-novel" and i["valid"] for i in items)
    bad_item = next(i for i in items if i["id"] == "bad-novel")
    assert bad_item["valid"] is False
    assert "INVALID" in bad_item["status"]


# ── 损坏检测 ─────────────────────────────────────────────

def test_corrupted_project_invalid_json(store):
    create_project(store, "X", project_id="corrupt-json")
    (store.root / "corrupt-json" / "project.json").write_text("{ broken", encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        open_project(store, "corrupt-json")


def test_corrupted_project_root_not_object(store):
    create_project(store, "X", project_id="corrupt-list")
    (store.root / "corrupt-list" / "project.json").write_text("[1,2]", encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        open_project(store, "corrupt-list")


def test_corrupted_project_bad_format_version(store):
    create_project(store, "X", project_id="corrupt-fmt")
    path = store.root / "corrupt-fmt" / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format_version"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataIntegrityError) as e:
        open_project(store, "corrupt-fmt")
    assert "format_version" in str(e.value)


def test_corrupted_project_id_mismatch(store):
    create_project(store, "X", project_id="corrupt-id")
    path = store.root / "corrupt-id" / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id"] = "other-id"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataIntegrityError) as e:
        open_project(store, "corrupt-id")
    assert "id" in str(e.value)


# ── validate ─────────────────────────────────────────────

def test_validate_ok(store):
    create_project(store, "V", project_id="validate-ok")
    assert validate_project(store, "validate-ok") == []


def test_validate_detects_status_mismatch(store):
    """drafts/ 里 status=confirmed → 验证发现问题。"""
    from core.chapter import write_draft, read_draft
    p = create_project(store, "V", project_id="validate-bad")
    write_draft(p, 1, "t", "c")
    # 手动破坏状态
    draft = store.root / "validate-bad" / "drafts" / "ch0001.draft.md"
    text = draft.read_text(encoding="utf-8")
    draft.write_text(text.replace("status: draft", "status: confirmed"), encoding="utf-8")
    issues = validate_project(store, "validate-bad")
    assert any("status" in i for i in issues)
