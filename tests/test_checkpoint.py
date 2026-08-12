"""checkpoint 安全扫描生产路径测试。

用临时 git 仓库真实模拟生产行为:
  - 不修改真实仓库
  - 不接触真实 API Key
  - 假 Key 一律动态拼接(避免把完整 Key 字面量写进测试源码,
    否则 checkpoint 扫描测试文件本身会误报)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import ai_checkpoint as ac  # noqa: E402


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def make_repo(tmp_path, monkeypatch):
    """创建临时 git 仓库, 并把 ac.ROOT 指向它(隔离, 不碰真实仓库)。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "test@test"], repo)
    _run(["git", "config", "user.name", "test"], repo)
    monkeypatch.setattr(ac, "ROOT", str(repo))
    return repo


def fake_key(prefix="sk"):
    """动态拼接假 Key(不把完整字面量写进源码)。"""
    return prefix + "-" + "x" * 28 + str(len(prefix))


# ── Case 1: 新增普通目录 ──────────────────────────────────

def test_new_directory_safe_content(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    d = repo / "new_feature"
    d.mkdir()
    (d / "hello.txt").write_text("safe content", encoding="utf-8")

    files = ac.collect_scan_files()
    assert any("hello.txt" in f for f in files), "扫描器必须覆盖新目录内文件"

    blocked, findings = ac.security_scan()
    assert not blocked, f"安全内容不应被拦截: {findings}"


# ── Case 2: 新增目录中的敏感路径 ──────────────────────────

def test_new_directory_sensitive_filename(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    d = repo / "new_feature"
    d.mkdir()
    (d / "credentials.json").write_text("{}", encoding="utf-8")

    blocked, findings = ac.security_scan()
    assert blocked, "credentials.json 必须被路径级拦截"
    assert any("credentials" in f["pattern"] or "路径" in f["pattern"] for f in findings)


# ── Case 3: 新增目录中的 secret 内容 ───────────────────────

def test_new_directory_secret_content(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    d = repo / "new_feature"
    d.mkdir()
    (d / "notes.txt").write_text(
        f"这里有个 key: {fake_key()}\n", encoding="utf-8")

    blocked, findings = ac.security_scan()
    assert blocked, "文本内容中的假 Key 必须被拦截"
    assert any("Key" in f["pattern"] for f in findings)


# ── Case 4: rename 到敏感路径 ─────────────────────────────

def test_rename_to_sensitive_path(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    (repo / "safe.txt").write_text("safe", encoding="utf-8")
    _run(["git", "add", "safe.txt"], repo)
    _run(["git", "commit", "-q", "-m", "init"], repo)
    _run(["git", "mv", "safe.txt", "credentials.json"], repo)

    files = ac.collect_scan_files()
    assert any("credentials.json" in f for f in files), "rename 目标必须进入扫描集合"

    blocked, findings = ac.security_scan()
    assert blocked, "rename 到 credentials.json 必须被拦截"


# ── Case 5: 空格文件名 ────────────────────────────────────

def test_space_filename(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    (repo / "my safe file.txt").write_text("safe", encoding="utf-8")

    files = ac.collect_scan_files()
    assert any("my safe file.txt" in f for f in files), "空格文件名不得被截断"

    blocked, findings = ac.security_scan()
    assert not blocked, f"安全空格文件不应误阻: {findings}"


# ── Case 6: Unicode 文件名 ────────────────────────────────

def test_unicode_filename(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    (repo / "测试文件.txt").write_text("safe", encoding="utf-8")

    files = ac.collect_scan_files()
    assert any("测试文件" in f for f in files), "Unicode 文件名必须正确进入集合"

    blocked, findings = ac.security_scan()
    assert not blocked, f"安全 Unicode 文件不应误阻: {findings}"


# ── Case 7: 高风险 binary ─────────────────────────────────

def test_high_risk_binary(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    (repo / "something.exe").write_bytes(b"MZ\x90\x00binary")

    blocked, findings = ac.security_scan()
    assert blocked, ".exe 高风险二进制必须默认阻止"
    assert any("二进制" in f["pattern"] for f in findings)


# ── Case 8: 大文本后半段 secret ───────────────────────────

def test_large_text_secret_in_tail(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    # >512KB 文本, secret 放在后半段 → 证明不是只扫开头
    head = "a" * (600 * 1024)  # 600KB 安全内容
    tail = f"\nsecret key here: {fake_key()}\n"
    (repo / "big.txt").write_text(head + tail, encoding="utf-8")

    blocked, findings = ac.security_scan()
    assert blocked, "大文本后半段的假 Key 必须被拦截(分块扫描)"

    # 允许的安全图片等
    (repo / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)


# ── Case 9: 正常图片不误阻 ────────────────────────────────

def test_safe_image_not_blocked(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    (repo / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    (repo / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    (repo / "pic.webp").write_bytes(b"RIFF" + b"\x00" * 100)

    blocked, findings = ac.security_scan()
    assert not blocked, f"白名单图片不应误阻: {findings}"


# ── Case 10: 三种 Git 状态都进入扫描集合 ──────────────────

def test_all_git_states_in_scan_set(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)

    # tracked + staged(修改)
    (repo / "tracked.txt").write_text("v1", encoding="utf-8")
    _run(["git", "add", "tracked.txt"], repo)
    _run(["git", "commit", "-q", "-m", "init"], repo)

    # unstaged modified
    (repo / "tracked.txt").write_text("v2 changed", encoding="utf-8")

    # staged modified
    (repo / "staged.txt").write_text("s1", encoding="utf-8")
    _run(["git", "add", "staged.txt"], repo)
    (repo / "staged.txt").write_text("s2 staged", encoding="utf-8")
    _run(["git", "add", "staged.txt"], repo)

    # untracked
    (repo / "untracked.txt").write_text("u", encoding="utf-8")

    files = ac.collect_scan_files()
    assert any("tracked.txt" in f for f in files), "unstaged modified 必须进入集合"
    assert any("staged.txt" in f for f in files), "staged 必须进入集合"
    assert any("untracked.txt" in f for f in files), "untracked 必须进入集合"

    blocked, findings = ac.security_scan()
    assert not blocked, f"三种状态的安全文件不应误阻: {findings}"


# ── 安全: 删除的文件不报错(不存在于磁盘) ──────────────────

def test_deleted_file_skipped(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    (repo / "old.txt").write_text("safe", encoding="utf-8")
    _run(["git", "add", "old.txt"], repo)
    _run(["git", "commit", "-q", "-m", "init"], repo)
    _run(["git", "rm", "-q", "old.txt"], repo)  # 已删除, 磁盘不存在

    blocked, findings = ac.security_scan()  # 不应崩溃
    assert not blocked, f"删除文件不应触发扫描: {findings}"


# ── M0 Final: 枚举失败必须 fail-closed ────────────────────

def test_enumeration_failure_blocks(monkeypatch):
    """任一 Git 枚举命令失败 → BLOCK(SCAN_ERROR), 不得继续。"""
    import ai_checkpoint as ac_mod

    def fake_run_nul(cmd, cwd=None, timeout=60):
        if "ls-files" in cmd:
            return 128, []  # 模拟 git ls-files 失败
        return 0, ["safe.txt"]

    monkeypatch.setattr(ac_mod, "run_nul", fake_run_nul)
    blocked, findings = ac.security_scan()
    assert blocked, "枚举失败必须 fail-closed"
    assert any("SCAN_ERROR" in f["pattern"] or "fail-closed" in f["pattern"] or "枚举失败" in f["pattern"] for f in findings)


def test_run_nul_preserves_leading_space(monkeypatch):
    """run_nul 不得 strip 前导/尾随空格文件名。"""
    import ai_checkpoint as ac_mod
    import subprocess as sp
    real_run = sp.run

    def fake_run(cmd, cwd=None, capture_output=True, timeout=None):
        class R:
            returncode = 0
            stdout = b" leading file.txt\x00trailing.txt \x00"
            stderr = b""
        return R()

    monkeypatch.setattr(sp, "run", fake_run)
    code, paths = ac_mod.run_nul(["git", "diff", "--name-only", "-z"])
    assert paths == [" leading file.txt", "trailing.txt "], (
        f"NUL 路径不得 strip, 实际: {paths!r}")
    monkeypatch.setattr(sp, "run", real_run)


# ── M0 Final: chunk-boundary secret ────────────────────────

def test_chunk_boundary_secret_blocked(tmp_path, monkeypatch):
    """假 secret 故意跨越 64KB chunk 边界 → 必须 BLOCK(carry buffer 覆盖)。"""
    repo = make_repo(tmp_path, monkeypatch)
    # 构造: chunk1 末尾几个字节 = "sk-", chunk2 开头 = 剩余假 key
    # 64KB = 65536 字符; 让 "sk-" 恰好落在 65533-65535 位置
    chunk_size = 64 * 1024
    # 前缀填充到 chunk 边界前 3 个字符
    prefix_len = chunk_size - 3
    key_tail = "x" * 26  # 假 key 剩余部分(与 "sk-" 拼接后 28+ 字符)
    content = "a" * prefix_len + "sk-" + key_tail + "\n"
    assert len(content) > chunk_size, "内容必须跨块"
    (repo / "boundary.txt").write_text(content, encoding="utf-8")

    blocked, findings = ac.security_scan()
    assert blocked, "跨 64KB chunk 边界的假 Key 必须被拦截(carry buffer)"
    assert any("Key" in f["pattern"] for f in findings)
