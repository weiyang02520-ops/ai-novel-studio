"""小说项目管理(Core, 无 UI 依赖)。

- create / open / list / show / validate
- 目录骨架生成(PROJECT_FORMAT §2)
- project_id 安全 + 中文名自动生成稳定 ID
"""
from __future__ import annotations

import datetime
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from .storage import (
    DataIntegrityError,
    ProjectStore,
    StorageError,
    atomic_write_json,
    validate_project_id,
)

FORMAT_VERSION = 1

# 目录骨架(clean-room PROJECT_FORMAT §2): 目录 → 是否含初始文件
SKELETON: dict[str, list[str]] = {
    "outline": ["summary.md"],
    "outline/volumes": [],
    "outline/chapters": [],
    "characters": [],
    "world": [],
    "rules": ["writing_rules.md"],
    "chapters": [],
    "drafts": [],
    "memory": [],
    "memory/summaries": [],
    "memory/foreshadowing": ["index.md"],
    "review": [],
}

INITIAL_FILES: dict[str, str] = {
    "outline/summary.md": "# 全书梗概\n\n(待填写)\n",
    "rules/writing_rules.md": "# 写作规则\n\n(待填写)\n",
    "memory/index.md": "# 记忆索引\n\n## 章节摘要\n\n(空)\n",
    "memory/foreshadowing/index.md": "# 伏笔索引\n\n| ID | 伏笔 | 埋设章 | 状态 |\n|---|---|---|---|\n",
    "memory/characters.md": "# 人物当前状态(派生)\n\n(空)\n",
    "memory/world.md": "# 世界当前状态(派生)\n\n(空)\n",
    "memory/timeline.md": "# 时间线索引\n\n| 章 | 时间 | 事件 |\n|---|---|---|\n",
    "memory/long_term.md": "# 长期记忆摘要\n\n(空)\n",
}

DEFAULT_METADATA: dict[str, Any] = {
    "format_version": FORMAT_VERSION,
    "status": "active",
    "current_volume": 1,
    "current_chapter": 0,
    "writing_style": "",
    "editorial_notes": "",
    "defaults": {},
    "auto_accept": False,
}

# 允许的中文/ASCII 名(显示名) — 不做严格限制, 但不能为空/纯空白
NAME_RE = re.compile(r"^\s*$")


def _now_iso() -> str:
    """ISO-8601 UTC 时间。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify_ascii(name: str) -> Optional[str]:
    """从 ASCII 名生成合法 slug(小写字母数字, 空格/非法字符→-)。

    纯中文/无法生成 → None。
    """
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if not s or not re.match(r"^[a-z][a-z0-9_-]{1,63}$", s):
        return None
    return s


def generate_project_id(name: str) -> str:
    """生成 project_id:
    1. ASCII 名 → slug;
    2. 无法 slug(如纯中文)→ novel-<稳定随机 hex>(显示名仍保留原名)。
    """
    slug = slugify_ascii(name)
    if slug:
        return slug
    return f"novel-{uuid.uuid4().hex[:8]}"


class Project:
    """已打开的小说项目(磁盘状态的内存视图)。"""

    def __init__(self, store: ProjectStore, project_id: str, dir_path: Path, metadata: dict[str, Any]):
        self.store = store
        self.id = project_id
        self.dir = dir_path
        self.metadata = metadata

    # 便捷字段访问
    @property
    def name(self) -> str:
        return str(self.metadata.get("name", self.id))

    @property
    def genre(self) -> str:
        return str(self.metadata.get("genre", ""))

    @property
    def status(self) -> str:
        return str(self.metadata.get("status", "active"))

    @property
    def current_chapter(self) -> int:
        v = self.metadata.get("current_chapter", 0)
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    @property
    def auto_accept(self) -> bool:
        return bool(self.metadata.get("auto_accept", False))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.metadata)

    def save_metadata(self) -> None:
        """原子写 project.json。"""
        atomic_write_json(self.dir / "project.json", self.metadata)


def create_project(
    store: ProjectStore,
    name: str,
    project_id: Optional[str] = None,
    genre: str = "",
) -> Project:
    """创建小说项目。

    - name: 显示名(可中文)
    - project_id: 显式指定(必须合法); 缺省自动生成(slug 或 novel-<hex>)
    - genre: 题材(可选)
    """
    name = (name or "").strip()
    if not name or NAME_RE.match(name):
        raise StorageError("项目名不能为空")

    if project_id:
        project_id = project_id.strip()
        if not validate_project_id(project_id):
            raise StorageError(f"非法的 project_id: {project_id!r}(需匹配 [a-z][a-z0-9_-]{{1,63}})")
    else:
        project_id = generate_project_id(name)

    pdir = store.project_dir(project_id)  # 校验 + 解析
    if pdir.exists():
        raise StorageError(f"项目已存在: {project_id}")

    now = _now_iso()
    metadata: dict[str, Any] = {
        **DEFAULT_METADATA,
        "id": project_id,
        "name": name,
        "genre": genre,
        "created_at": now,
        "updated_at": now,
    }

    # 先建目录骨架 + 初始文件
    try:
        for rel in SKELETON:
            (pdir / rel).mkdir(parents=True, exist_ok=True)
        for rel, content in INITIAL_FILES.items():
            target = pdir / rel
            if not target.exists():
                _write_text_safe(target, content)
        (pdir / ".history").mkdir(exist_ok=True)
        atomic_write_json(pdir / "project.json", metadata)
        # settings.json(项目级, 空配置结构)
        atomic_write_json(pdir / "settings.json", {"defaults": {}, "auto_accept": False})
    except Exception as e:
        # 创建失败: 清理不完整目录(仅限刚创建的)
        import shutil
        shutil.rmtree(pdir, ignore_errors=True)
        if isinstance(e, StorageError):
            raise
        raise StorageError(f"创建项目失败: {e}")

    return Project(store, project_id, pdir, metadata)


def _write_text_safe(path: Path, content: str) -> None:
    from .storage import atomic_write_text
    atomic_write_text(path, content)


def open_project(store: ProjectStore, project_id: str) -> Project:
    """从磁盘打开并验证项目。

    损坏(非法 JSON / 根非 object / format_version 错误 / id 与目录不一致)→ DataIntegrityError。
    """
    pdir = store.project_dir(project_id)
    if not pdir.exists():
        raise StorageError(f"项目不存在: {project_id}")

    pj_path = pdir / "project.json"
    if not pj_path.exists():
        raise DataIntegrityError(f"{project_id}: 缺少 project.json")

    try:
        raw = pj_path.read_text(encoding="utf-8")
    except OSError as e:
        raise DataIntegrityError(f"{project_id}: 无法读取 project.json: {e}")
    try:
        import json
        metadata = json.loads(raw)
    except json.JSONDecodeError as e:
        raise DataIntegrityError(
            f"{project_id}: project.json JSON 语法错误(第 {e.lineno} 行第 {e.colno} 列): {e.msg}")

    if not isinstance(metadata, dict):
        raise DataIntegrityError(f"{project_id}: project.json 根节点不是对象")

    if metadata.get("format_version") != FORMAT_VERSION:
        raise DataIntegrityError(
            f"{project_id}: format_version 不支持(期望 {FORMAT_VERSION}, 实际 {metadata.get('format_version')!r})")

    if metadata.get("id") != project_id:
        raise DataIntegrityError(
            f"{project_id}: project.json id 与目录不一致({metadata.get('id')!r} != {project_id!r})")

    return Project(store, project_id, pdir, metadata)


def list_projects(store: ProjectStore) -> list[dict[str, Any]]:
    """列出全部项目。

    损坏项目不阻塞其它项目: 标记 INVALID + 错误信息。
    返回: [{id, name, genre, status, current_chapter, valid, error?}]
    """
    result = []
    for pdir in store.list_project_dirs():
        pid = pdir.name
        try:
            proj = open_project(store, pid)
            result.append({
                "id": pid,
                "name": proj.name,
                "genre": proj.genre,
                "status": proj.status,
                "current_chapter": proj.current_chapter,
                "valid": True,
            })
        except (StorageError, DataIntegrityError) as e:
            result.append({
                "id": pid,
                "name": pid,
                "genre": "",
                "status": "INVALID",
                "current_chapter": 0,
                "valid": False,
                "error": str(e),
            })
    return result


def validate_project(store: ProjectStore, project_id: str) -> list[str]:
    """项目一致性验证, 返回问题列表(空 = 通过)。

    检查: project.json 完整 / id 一致 / chapters 状态合法 / drafts 状态合法。
    """
    issues: list[str] = []
    proj = open_project(store, project_id)  # 抛 DataIntegrityError

    # chapters/: 必须 status=confirmed
    chapters_dir = proj.dir / "chapters"
    if chapters_dir.exists():
        for f in sorted(chapters_dir.iterdir()):
            if not f.is_file() or not f.name.endswith(".md"):
                continue
            from .chapter import read_confirmed_chapter_file
            try:
                meta = read_confirmed_chapter_file(f)
                if meta.get("status") != "confirmed":
                    issues.append(f"chapters/{f.name}: status={meta.get('status')!r}(应为 confirmed)")
            except DataIntegrityError as e:
                issues.append(f"chapters/{f.name}: {e}")

    # drafts/: 必须 status=draft 或 user_confirmed(未收编)
    drafts_dir = proj.dir / "drafts"
    if drafts_dir.exists():
        for f in sorted(drafts_dir.iterdir()):
            if not f.is_file() or not f.name.endswith(".draft.md"):
                continue
            from .chapter import read_draft_file
            try:
                meta = read_draft_file(f)
                if meta.get("status") not in ("draft", "user_confirmed"):
                    issues.append(f"drafts/{f.name}: status={meta.get('status')!r}(应为 draft/user_confirmed)")
            except DataIntegrityError as e:
                issues.append(f"drafts/{f.name}: {e}")

    return issues
