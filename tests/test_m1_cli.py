"""M1 CLI 集成测试(通过 subprocess 真实调用 CLI, 使用临时 data 目录)。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

CLI = PROJECT_ROOT / "adapters" / "cli" / "main.py"


def _run(tmp_path, *args, **kw):
    data_dir = tmp_path / "novels"
    data_dir.mkdir(exist_ok=True)
    cmd = [sys.executable, str(CLI), "--config", str(tmp_path / "settings.json"),
           "--data-dir", str(data_dir), *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT, **kw)


def test_novel_create_and_list(tmp_path):
    r = _run(tmp_path, "novel", "create", "CLI Test", "--id", "cli-test")
    assert r.returncode == 0, r.stderr
    assert "已创建" in r.stdout

    r2 = _run(tmp_path, "novel", "list")
    assert r2.returncode == 0
    assert "cli-test" in r2.stdout


def test_novel_show_open(tmp_path):
    _run(tmp_path, "novel", "create", "S", "--id", "cli-show")
    r = _run(tmp_path, "novel", "show", "cli-show")
    assert r.returncode == 0
    assert "current_chapter" in r.stdout
    r2 = _run(tmp_path, "novel", "open", "cli-show")
    assert r2.returncode == 0
    assert "已打开" in r2.stdout


def test_full_chapter_flow(tmp_path):
    _run(tmp_path, "novel", "create", "F", "--id", "cli-flow")

    # write
    r = _run(tmp_path, "chapter", "write", "cli-flow", "1", "--title", "第一章", "--content", "第一版")
    assert r.returncode == 0, r.stderr

    # list
    r = _run(tmp_path, "chapter", "list", "cli-flow")
    assert "ch0001" in r.stdout or "1" in r.stdout

    # read draft
    r = _run(tmp_path, "chapter", "read", "cli-flow", "1", "--draft")
    assert r.returncode == 0 and "第一版" in r.stdout

    # update
    r = _run(tmp_path, "chapter", "update", "cli-flow", "1", "--content", "第二版")
    assert r.returncode == 0

    # confirm
    r = _run(tmp_path, "chapter", "confirm", "cli-flow", "1")
    assert r.returncode == 0, r.stderr
    assert "current_chapter = 1" in r.stdout

    # read confirmed
    r = _run(tmp_path, "chapter", "read", "cli-flow", "1")
    assert r.returncode == 0 and "第二版" in r.stdout

    # history undo-last 不应失败(有记录)
    r = _run(tmp_path, "history", "undo-last", "cli-flow")
    assert r.returncode == 0


def test_errors_no_traceback(tmp_path):
    _run(tmp_path, "novel", "create", "E", "--id", "cli-err")

    # 不存在项目
    r = _run(tmp_path, "chapter", "read", "no-such", "1")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr

    # 重复创建
    _run(tmp_path, "novel", "create", "E2", "--id", "cli-dup")
    r = _run(tmp_path, "novel", "create", "E3", "--id", "cli-dup")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr

    # 非法 project_id
    r = _run(tmp_path, "novel", "create", "Bad", "--id", "../evil")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "project_id" in r.stdout or "非法的" in r.stdout

    # 确认不存在的草稿
    r = _run(tmp_path, "chapter", "confirm", "cli-err", "9")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr


def test_from_file(tmp_path):
    _run(tmp_path, "novel", "create", "FF", "--id", "cli-ff")
    src = tmp_path / "chapter.txt"
    src.write_text("从文件读取的中文正文。", encoding="utf-8")
    r = _run(tmp_path, "chapter", "write", "cli-ff", "1", "--title", "t", "--from-file", str(src))
    assert r.returncode == 0, r.stderr
    r2 = _run(tmp_path, "chapter", "read", "cli-ff", "1", "--draft")
    assert "从文件读取的中文正文。" in r2.stdout
