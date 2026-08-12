"""本地 usage 记录(不上传, 只记 metadata)。

- JSONL append: 一行一对象; 写失败 → 返回 False(调用方 warning, 不阻断聊天成功)
- 绝不记录: prompt / response / API Key / Authorization / 完整 request body
- 坏行策略与 M1 history 不同: usage 是可观测派生数据, 跳过坏行 + 计数 warning
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class UsageService:
    """本地 usage 记录服务。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    # ── 写入 ─────────────────────────────────────────────

    def record_success(self, *, provider: str, model: str, prompt_tokens: int,
                       completion_tokens: int, total_tokens: int, estimated: bool,
                       duration_ms: float, stream: bool) -> bool:
        rec = {
            "timestamp": _now_iso(),
            "provider": provider,
            "model": model,
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "total_tokens": int(total_tokens),
            "estimated": bool(estimated),
            "duration_ms": round(float(duration_ms), 1),
            "stream": bool(stream),
            "success": True,
        }
        return self._append(rec)

    def record_error(self, *, model: str, error_code: str, duration_ms: float) -> bool:
        rec = {
            "timestamp": _now_iso(),
            "model": model,
            "error_code": error_code,
            "duration_ms": round(float(duration_ms), 1),
            "success": False,
        }
        return self._append(rec)

    def _append(self, rec: dict[str, Any]) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
            return True
        except OSError:
            return False

    # ── 读取 ─────────────────────────────────────────────

    def _read_rows(self) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        skipped = 0
        if not self.path.exists():
            return rows, skipped
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return rows, skipped
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    raise ValueError("not dict")
                rows.append(rec)
            except (json.JSONDecodeError, ValueError):
                skipped += 1
        return rows, skipped

    def summary(self) -> dict[str, Any]:
        """聚合统计(跳过坏行, 返回 skipped 计数)。"""
        rows, skipped = self._read_rows()
        agg = {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_requests": 0,
            "errors": 0,
            "skipped_malformed": skipped,
        }
        for r in rows:
            if r.get("success") is True:
                agg["requests"] += 1
                agg["prompt_tokens"] += int(r.get("prompt_tokens") or 0)
                agg["completion_tokens"] += int(r.get("completion_tokens") or 0)
                agg["total_tokens"] += int(r.get("total_tokens") or 0)
                if r.get("estimated"):
                    agg["estimated_requests"] += 1
            elif r.get("success") is False:
                agg["errors"] += 1
        return agg

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """最近 N 条(新→旧)。"""
        rows, _ = self._read_rows()
        return list(reversed(rows))[:limit]
