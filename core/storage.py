"""存储层(Core, 无 UI 依赖)。

- ProjectStore: storage root 注入(data/novels/ 生产, tmp_path 测试)
- 原子写入: same-directory temp + flush + os.replace
- 路径安全: 所有路径 resolve 后必须位于 project root 内(防穿越)
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

# 项目 ID 规则(clean-room PROJECT_FORMAT §3)
PROJECT_ID_RE = r"^[a-z][a-z0-9_-]{1,63}$"
PROJECT_ID_PATTERN = re.compile(PROJECT_ID_RE)


class StorageError(Exception):
    """存储层错误(路径越界/写入失败等)。"""


class DataIntegrityError(StorageError):
    """数据完整性错误(损坏 project.json / chapter frontmatter / 状态不一致)。

    CLI 层捕获后输出人类可读错误 + exit != 0, 禁止裸 traceback。
    """


def validate_project_id(project_id: str) -> bool:
    """校验 project_id 是否符合 [a-z][a-z0-9_-]{1,63}(防路径穿越)。"""
    return bool(PROJECT_ID_PATTERN.match(project_id))


def format_chapter_filename(number: int) -> str:
    """章节编号 → 文件名: 1→ch0001, 25→ch0025, 9999→ch9999, 10000→ch10000。

    4 位零填充; 超过 4 位直接使用原数字(字典序 = 章节序)。
    """
    if not isinstance(number, int) or isinstance(number, bool):
        raise ValueError(f"章节号必须是整数, 实际: {number!r}")
    if number <= 0:
        raise ValueError(f"章节号必须为正整数, 实际: {number}")
    if number < 10000:
        return f"ch{number:04d}"
    return f"ch{number}"


def parse_chapter_number_from_filename(name: str) -> Optional[int]:
    """从文件名解析章节号: ch0001 → 1。非法返回 None。"""
    if name.startswith("ch") and name.endswith(".md"):
        digits = name[2:-3]
        if digits and digits.isdigit():
            return int(digits)
    return None


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子写文本: 同目录临时文件 + flush + os.replace。"""
    _atomic_write(path, content, encoding, is_json=False)


def atomic_write_json(path: Path, data: Any, encoding: str = "utf-8") -> None:
    """原子写 JSON(ensure_ascii=False, indent=2)。"""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    _atomic_write(path, text, encoding, is_json=True)


def _atomic_write(path: Path, content: str, encoding: str, is_json: bool) -> None:
    path = Path(path)
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise StorageError(f"无法创建目录 {parent}: {e}")
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except OSError as e:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise StorageError(f"写入失败 {path}: {e}")


class ProjectStore:
    """项目存储根(data/novels/ 生产, tmp_path 测试)。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    # ── 路径安全 ─────────────────────────────────────────

    def project_dir(self, project_id: str) -> Path:
        """项目目录; 校验 ID 合法且解析后仍在 root 内。"""
        if not validate_project_id(project_id):
            raise StorageError(f"非法的 project_id: {project_id!r}(需匹配 {PROJECT_ID_RE})")
        p = (self.root / project_id).resolve()
        root_resolved = self.root.resolve()
        if not p.is_relative_to(root_resolved):
            raise StorageError(f"路径越界: {project_id}")
        return p

    def safe_path(self, project_id: str, rel_path: str | Path) -> Path:
        """项目内安全路径: 解析后必须位于项目目录内(防 .. 穿越)。"""
        pdir = self.project_dir(project_id)
        target = (pdir / rel_path).resolve()
        if not target.is_relative_to(pdir.resolve()):
            raise StorageError(f"路径越界: {rel_path!r}")
        return target

    # ── 枚举 ──────────────────────────────────────────────

    def list_project_dirs(self) -> list[Path]:
        """列出 root 下的项目目录(按名称排序)。"""
        if not self.root.exists():
            return []
        return sorted(
            (p for p in self.root.iterdir() if p.is_dir() and validate_project_id(p.name)),
            key=lambda p: p.name,
        )
