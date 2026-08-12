"""M1 存储层测试: 原子写 / 临时文件清理 / 写入失败不破坏原文件。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from core.project import create_project  # noqa: E402
from core.storage import (  # noqa: E402
    ProjectStore,
    StorageError,
    atomic_write_json,
    atomic_write_text,
    validate_project_id,
)


def test_atomic_text_write(tmp_path):
    target = tmp_path / "sub" / "file.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_json_write(tmp_path):
    target = tmp_path / "data.json"
    atomic_write_json(target, {"a": 1, "中文": "值"})
    import json
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"a": 1, "中文": "值"}


def test_atomic_write_no_temp_leftover(tmp_path):
    target = tmp_path / "ok.txt"
    atomic_write_text(target, "x")
    leftovers = [f for f in tmp_path.iterdir() if f.name.startswith(".tmp_")]
    assert leftovers == [], f"原子写后不应残留临时文件: {leftovers}"


def test_write_failure_does_not_destroy_existing(tmp_path, monkeypatch):
    """os.replace 失败 → 原文件内容保持不变, 临时文件被清理。"""
    target = tmp_path / "important.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    real_replace = os.replace
    def failing_replace(src, dst):
        raise OSError("simulated replace failure")
    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(StorageError):
        atomic_write_text(target, "NEW CONTENT")

    monkeypatch.setattr(os, "replace", real_replace)
    assert target.read_text(encoding="utf-8") == "ORIGINAL", "写入失败不得破坏原文件"
    leftovers = [f for f in tmp_path.iterdir() if f.name.startswith(".tmp_")]
    assert leftovers == [], f"失败后应清理临时文件: {leftovers}"


def test_atomic_json_failure_keeps_original(tmp_path, monkeypatch):
    target = tmp_path / "cfg.json"
    atomic_write_json(target, {"v": 1})

    real_replace = os.replace
    def failing_replace(src, dst):
        raise OSError("boom")
    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(StorageError):
        atomic_write_json(target, {"v": 2})
    monkeypatch.setattr(os, "replace", real_replace)

    import json
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 1}


def test_project_store_path_safety(tmp_path):
    store = ProjectStore(tmp_path / "novels")
    # 合法
    p = store.safe_path("novel-a", "drafts/ch0001.draft.md")
    assert ".." not in str(p)
    # 穿越拒绝
    with pytest.raises(StorageError):
        store.safe_path("novel-a", "../outside.txt")
    with pytest.raises(StorageError):
        store.safe_path("novel-a", "drafts/../../outside.txt")
    # 绝对路径拒绝
    with pytest.raises(StorageError):
        store.safe_path("novel-a", str(tmp_path / "abs.txt"))


def test_project_store_rejects_bad_ids(tmp_path):
    store = ProjectStore(tmp_path / "novels")
    for bad in ("../evil", "a/b", "a\\b", "", "X"):
        with pytest.raises(StorageError):
            store.project_dir(bad)
