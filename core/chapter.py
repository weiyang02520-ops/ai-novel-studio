"""章节数据/格式/状态(Core, 无 UI 依赖)。

- 自研有限 frontmatter 解析/写出(只处理我们生成的格式, 不引入 YAML 依赖)
- 章节文件: drafts/chNNNN.draft.md(草稿) / chapters/chNNNN.md(已确认)
- 状态机(手动路径): DRAFT → USER_CONFIRMED → CONFIRMED
- current_chapter 只在 CONFIRMED 后推进(max 语义, 不倒退)
- 已确认正文受保护(普通 write/update 不得覆盖)
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any, Optional

from .project import Project
from .storage import (
    DataIntegrityError,
    StorageError,
    atomic_write_text,
    format_chapter_filename,
    parse_chapter_number_from_filename,
)

VALID_STATUSES = ("draft", "reviewing", "ready", "user_confirmed", "confirmed")
VALID_ORIGINS = ("manual", "ai")

# 自研 frontmatter: 只解析我们生成的有限格式
# 格式: ---\nkey: value\n...\n---\n正文
FM_BLOCK_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
FM_LINE_RE = re.compile(r"^([a-z_]+):\s*(.*)$")
FM_INT_FIELDS = {"chapter", "volume", "words"}
FM_LIST_FIELDS = {"characters"}
FM_BOOL_FIELDS = set()

REQUIRED_FIELDS = {"chapter", "volume", "title", "status", "origin", "created_at", "updated_at"}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 frontmatter, 返回 (meta, body)。

    损坏(缺 frontmatter 块/缺必需字段/类型错误)→ DataIntegrityError。
    """
    m = FM_BLOCK_RE.match(text)
    if not m:
        raise DataIntegrityError("frontmatter 块缺失(文件应以 --- 开头)")
    header = m.group(1)
    meta: dict[str, Any] = {}
    for line in header.splitlines():
        lm = FM_LINE_RE.match(line)
        if not lm:
            raise DataIntegrityError(f"frontmatter 行无法解析: {line!r}")
        key, val = lm.group(1), lm.group(2).strip()
        if key in FM_INT_FIELDS:
            try:
                meta[key] = int(val)
            except ValueError:
                raise DataIntegrityError(f"frontmatter 字段 {key} 需要整数, 实际: {val!r}")
        elif key in FM_LIST_FIELDS:
            # 格式: [a, b] 或 [] 或空
            if val in ("", "[]"):
                meta[key] = []
            else:
                inner = val.strip("[]").strip()
                meta[key] = [x.strip() for x in inner.split(",")] if inner else []
        elif key in FM_BOOL_FIELDS:
            meta[key] = val.lower() == "true"
        else:
            meta[key] = val

    missing = REQUIRED_FIELDS - set(meta)
    if missing:
        raise DataIntegrityError(f"frontmatter 缺少必需字段: {sorted(missing)}")
    if meta["status"] not in VALID_STATUSES:
        raise DataIntegrityError(f"非法 status: {meta['status']!r}(允许 {VALID_STATUSES})")
    if meta["origin"] not in VALID_ORIGINS:
        raise DataIntegrityError(f"非法 origin: {meta['origin']!r}(允许 {VALID_ORIGINS})")

    body = text[m.end():]
    return meta, body


def build_frontmatter(meta: dict[str, Any]) -> str:
    """按固定字段顺序生成 frontmatter 块(round-trip 稳定)。"""
    order = ["chapter", "volume", "title", "status", "origin", "words", "created_at", "updated_at", "summary", "characters"]
    lines = ["---"]
    for key in order:
        if key not in meta:
            continue
        val = meta[key]
        if key in FM_LIST_FIELDS:
            lines.append(f"{key}: {val!r}" if isinstance(val, list) else f"{key}: []")
        elif key in FM_INT_FIELDS:
            lines.append(f"{key}: {int(val)}")
        else:
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def count_words(text: str) -> int:
    """words = 正文中非空白字符数量(确定性算法, 无外部分词)。"""
    return sum(1 for ch in text if not ch.isspace())


class Chapter:
    """章节(磁盘文件的内存视图)。"""

    def __init__(self, meta: dict[str, Any], body: str, path: Path, is_draft: bool):
        self.meta = meta
        self.body = body
        self.path = path
        self.is_draft = is_draft

    @property
    def number(self) -> int:
        return int(self.meta["chapter"])

    @property
    def title(self) -> str:
        return str(self.meta.get("title", ""))

    @property
    def status(self) -> str:
        return str(self.meta["status"])

    @property
    def origin(self) -> str:
        return str(self.meta.get("origin", "manual"))

    @property
    def words(self) -> int:
        return int(self.meta.get("words", 0))

    @property
    def updated_at(self) -> str:
        return str(self.meta.get("updated_at", ""))

    def render(self) -> str:
        """frontmatter + 正文(原子写用)。"""
        return build_frontmatter(self.meta) + self.body


# ── 文件读写 ─────────────────────────────────────────────

def draft_path(project: Project, number: int) -> Path:
    return project.store.safe_path(project.id, f"drafts/{format_chapter_filename(number)}.draft.md")


def confirmed_path(project: Project, number: int) -> Path:
    return project.store.safe_path(project.id, f"chapters/{format_chapter_filename(number)}.md")


def read_draft_file(path: Path) -> dict[str, Any]:
    """读草稿文件(路径已由调用方保证安全), 返回 meta(含损坏检测)。"""
    if not path.exists():
        raise StorageError(f"草稿不存在: {path.name}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise DataIntegrityError(f"无法读取草稿 {path.name}: {e}")
    meta, _ = parse_frontmatter(text)
    return meta


def read_confirmed_chapter_file(path: Path) -> dict[str, Any]:
    """读已确认章节文件, 返回 meta(含损坏检测)。"""
    if not path.exists():
        raise StorageError(f"章节不存在: {path.name}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise DataIntegrityError(f"无法读取章节 {path.name}: {e}")
    meta, _ = parse_frontmatter(text)
    return meta


def _parse_number_from_any_filename(name: str) -> Optional[int]:
    """从文件名解析章节号, 兼容 ch0001.md 与 ch0001.draft.md。"""
    if name.endswith(".draft.md"):
        name = name[: -len(".draft.md")] + ".md"
    return parse_chapter_number_from_filename(name)


def _check_chapter_number_consistency(meta: dict[str, Any], path: Path) -> None:
    """章节号与文件名一致性检查。"""
    parsed = _parse_number_from_any_filename(path.name)
    if parsed is not None and parsed != int(meta["chapter"]):
        raise DataIntegrityError(
            f"{path.name}: frontmatter chapter={meta['chapter']} 与文件名不一致({parsed})")


# ── 业务操作 ─────────────────────────────────────────────

def write_draft(project: Project, number: int, title: str, content: str) -> Chapter:
    """创建草稿(不存在时)。已存在 → StorageError(明确提示用 update)。"""
    _validate_number(number)
    path = draft_path(project, number)

    if path.exists():
        raise StorageError(
            f"草稿已存在: drafts/{path.name}(如需修改请使用 chapter update)")

    confirmed = confirmed_path(project, number)
    if confirmed.exists():
        raise StorageError(
            f"章节 {number} 已确认(chapters/{confirmed.name}), 正式正文受保护, 不得覆盖")

    now = _now_iso()
    meta = {
        "chapter": number,
        "volume": project.metadata.get("current_volume", 1),
        "title": title,
        "status": "draft",
        "origin": "manual",  # M1 只允许 manual
        "words": count_words(content),
        "created_at": now,
        "updated_at": now,
        "summary": "",
        "characters": [],
    }
    chapter = Chapter(meta, content, path, is_draft=True)
    atomic_write_text(path, chapter.render())
    return chapter


def read_draft(project: Project, number: int) -> Chapter:
    """读草稿。"""
    _validate_number(number)
    path = draft_path(project, number)
    text = _read_any(path, f"草稿不存在: drafts/ch{format_chapter_filename(number)}.draft.md")
    meta, body = parse_frontmatter(text)
    _check_chapter_number_consistency(meta, path)
    if meta["status"] == "confirmed":
        raise DataIntegrityError(
            f"drafts/{path.name}: status=confirmed 但位于草稿目录(状态与目录不一致)")
    return Chapter(meta, body, path, is_draft=True)


def update_draft(project: Project, number: int, title: Optional[str] = None,
                 content: Optional[str] = None) -> Chapter:
    """更新草稿(仅 draft/user_confirmed 状态; 已确认正文受保护)。

    修改前自动 snapshot(history)。
    """
    _validate_number(number)
    confirmed = confirmed_path(project, number)
    if confirmed.exists():
        raise StorageError(f"章节 {number} 已确认, 正式正文受保护, 不得覆盖")

    path = draft_path(project, number)
    if not path.exists():
        raise StorageError(f"草稿不存在: drafts/{path.name}(请先使用 chapter write)")

    # 修改前快照(已有内容)
    from .history import snapshot
    snapshot(project, "chapter.update", f"drafts/{path.name}")

    text = _read_any(path, "无法读取草稿")
    meta, body = parse_frontmatter(text)
    _check_chapter_number_consistency(meta, path)

    new_meta = dict(meta)
    if title is not None:
        new_meta["title"] = title
    if content is not None:
        body = content
    new_meta["words"] = count_words(body)
    new_meta["updated_at"] = _now_iso()
    chapter = Chapter(new_meta, body, path, is_draft=True)
    atomic_write_text(path, chapter.render())
    return chapter


def confirm_draft(project: Project, number: int) -> Chapter:
    """手动确认草稿: DRAFT → USER_CONFIRMED → CONFIRMED。

    规则:
      - 草稿必须存在;
      - status 必须为 draft(或 user_confirmed, 允许重复确认幂等);
      - origin=ai 且 status != ready → 拒绝(M1 尚未创建 ai, 但状态边界不留口);
      - confirm 后才更新 current_chapter(max 语义);
      - 多文件操作: 先验证 → 原子写 project.json → 原子写 confirmed → 删 draft。
    """
    _validate_number(number)
    path = draft_path(project, number)
    if not path.exists():
        raise StorageError(f"草稿不存在: drafts/{path.name}, 无法确认")

    text = _read_any(path, "无法读取草稿")
    meta, body = parse_frontmatter(text)
    _check_chapter_number_consistency(meta, path)

    status = meta["status"]
    if status == "confirmed":
        raise DataIntegrityError(f"{path.name}: 已确认章节不应位于草稿目录")
    if status not in ("draft", "user_confirmed"):
        raise DataIntegrityError(
            f"{path.name}: 当前 status={status!r}, 仅 draft/user_confirmed 可确认")
    if meta["origin"] == "ai" and status != "ready":
        raise DataIntegrityError(
            f"{path.name}: AI 章节必须先经 ready 才能确认(M1 仅 manual)")

    confirmed = confirmed_path(project, number)
    if confirmed.exists():
        raise DataIntegrityError(f"已存在确认章节 chapters/{confirmed.name}, 拒绝覆盖")

    # 新 frontmatter(收编后)
    now = _now_iso()
    new_meta = dict(meta)
    new_meta["status"] = "confirmed"
    new_meta["origin"] = "manual"
    new_meta["updated_at"] = now
    confirmed_chapter = Chapter(new_meta, body, confirmed, is_draft=False)

    # 顺序: project.json 先更新(原子, 修改前快照), 再写 confirmed, 最后删 draft
    # (崩溃残留: open/validate 能检测 chapters/ 中 status != confirmed 或 draft 残留)
    old_current = project.current_chapter
    new_current = max(old_current, number)
    if new_current != old_current:
        from .history import snapshot
        snapshot(project, "chapter.confirm", "project.json")
        project.metadata["current_chapter"] = new_current
        project.metadata["updated_at"] = now
        project.save_metadata()

    atomic_write_text(confirmed, confirmed_chapter.render())
    try:
        path.unlink()
    except OSError:
        pass  # draft 删除失败不致命(validate 会报告残留)

    return confirmed_chapter


def read_confirmed(project: Project, number: int) -> Chapter:
    """读已确认章节。"""
    _validate_number(number)
    path = confirmed_path(project, number)
    text = _read_any(path, f"章节不存在: chapters/{format_chapter_filename(number)}.md")
    meta, body = parse_frontmatter(text)
    _check_chapter_number_consistency(meta, path)
    if meta["status"] != "confirmed":
        raise DataIntegrityError(
            f"chapters/{path.name}: status={meta['status']!r}(应为 confirmed)")
    return Chapter(meta, body, path, is_draft=False)


def list_chapters(project: Project) -> list[dict[str, Any]]:
    """列出全部章节(草稿 + 已确认), 按章节号排序。

    返回: [{chapter, title, status, words, updated_at, location: draft|confirmed}]
    """
    entries: dict[int, dict[str, Any]] = {}

    drafts_dir = project.store.safe_path(project.id, "drafts")
    if drafts_dir.exists():
        for f in sorted(drafts_dir.iterdir()):
            if not f.is_file() or not f.name.endswith(".draft.md"):
                continue
            # "ch0001.draft.md" → 去掉 .draft.md → "ch0001" → 解析
            base = f.name[: -len(".draft.md")]
            n = parse_chapter_number_from_filename(base + ".md")
            if n is None:
                continue
            try:
                meta = read_draft_file(f)
                entries[n] = {
                    "chapter": n, "title": meta.get("title", ""),
                    "status": meta.get("status", "?"), "words": int(meta.get("words", 0)),
                    "updated_at": meta.get("updated_at", ""), "location": "draft",
                }
            except DataIntegrityError:
                entries[n] = {
                    "chapter": n, "title": "(损坏)", "status": "INVALID",
                    "words": 0, "updated_at": "", "location": "draft",
                }

    chapters_dir = project.store.safe_path(project.id, "chapters")
    if chapters_dir.exists():
        for f in sorted(chapters_dir.iterdir()):
            if not f.is_file() or not f.name.endswith(".md"):
                continue
            n = parse_chapter_number_from_filename(f.name)
            if n is None:
                continue
            try:
                meta = read_confirmed_chapter_file(f)
                entries[n] = {
                    "chapter": n, "title": meta.get("title", ""),
                    "status": meta.get("status", "?"), "words": int(meta.get("words", 0)),
                    "updated_at": meta.get("updated_at", ""), "location": "confirmed",
                }
            except DataIntegrityError:
                entries[n] = {
                    "chapter": n, "title": "(损坏)", "status": "INVALID",
                    "words": 0, "updated_at": "", "location": "confirmed",
                }

    return [entries[k] for k in sorted(entries)]


def _validate_number(number: int) -> None:
    if not isinstance(number, int) or isinstance(number, bool):
        raise StorageError(f"章节号必须是整数, 实际: {number!r}")
    if number <= 0:
        raise StorageError(f"章节号必须为正整数, 实际: {number}")


def _read_any(path: Path, missing_msg: str) -> str:
    if not path.exists():
        raise StorageError(missing_msg)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise DataIntegrityError(f"无法读取 {path.name}: {e}")
