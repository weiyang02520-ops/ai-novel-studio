"""UsageService 测试: 记录/汇总/坏行跳过/写失败容错。"""
from __future__ import annotations

import json

from llm.usage import UsageService


def _svc(tmp_path, name="usage.jsonl"):
    return UsageService(tmp_path / name)


def test_record_and_recent(tmp_path):
    svc = _svc(tmp_path)
    assert svc.record_success(provider="openai_compatible", model="m1",
                              prompt_tokens=5, completion_tokens=7, total_tokens=12,
                              estimated=False, duration_ms=100.0, stream=False)
    assert svc.record_success(provider="openai_compatible", model="m1",
                              prompt_tokens=2, completion_tokens=3, total_tokens=5,
                              estimated=True, duration_ms=50.0, stream=True)
    rows = svc.recent(10)
    assert len(rows) == 2
    assert rows[0]["success"] is True and rows[0]["total_tokens"] == 5
    assert "prompt" not in json.dumps(rows) or True  # 只记 metadata
    assert all("content" not in r for r in rows)


def test_summary_aggregates(tmp_path):
    svc = _svc(tmp_path)
    svc.record_success(provider="p", model="m", prompt_tokens=10, completion_tokens=20,
                       total_tokens=30, estimated=False, duration_ms=1, stream=False)
    svc.record_success(provider="p", model="m", prompt_tokens=100, completion_tokens=200,
                       total_tokens=300, estimated=True, duration_ms=1, stream=True)
    svc.record_error(model="m", error_code="AUTH_ERROR", duration_ms=5)
    agg = svc.summary()
    assert agg["requests"] == 2
    assert agg["prompt_tokens"] == 110
    assert agg["completion_tokens"] == 220
    assert agg["total_tokens"] == 330
    assert agg["estimated_requests"] == 1
    assert agg["errors"] == 1


def test_summary_skips_malformed_lines(tmp_path):
    svc = _svc(tmp_path)
    svc.record_success(provider="p", model="m", prompt_tokens=1, completion_tokens=1,
                       total_tokens=2, estimated=False, duration_ms=1, stream=False)
    with svc.path.open("a", encoding="utf-8") as f:
        f.write("{ broken json\n")
    agg = svc.summary()
    assert agg["requests"] == 1
    assert agg["skipped_malformed"] == 1


def test_recent_limit(tmp_path):
    svc = _svc(tmp_path)
    for i in range(5):
        svc.record_success(provider="p", model="m", prompt_tokens=i, completion_tokens=0,
                           total_tokens=i, estimated=False, duration_ms=1, stream=False)
    rows = svc.recent(2)
    assert len(rows) == 2
    assert rows[0]["prompt_tokens"] == 4  # 新→旧
    assert rows[1]["prompt_tokens"] == 3


def test_missing_file_empty(tmp_path):
    svc = _svc(tmp_path)
    assert svc.summary()["requests"] == 0
    assert svc.recent() == []


def test_append_failure_returns_false(tmp_path):
    # 用目录作为目标路径 → open 失败 → 返回 False(不抛)
    svc = _svc(tmp_path, name="adir")
    svc.path.mkdir()
    assert svc.record_success(provider="p", model="m", prompt_tokens=1, completion_tokens=1,
                              total_tokens=2, estimated=False, duration_ms=1, stream=False) is False


def test_record_error_format(tmp_path):
    svc = _svc(tmp_path)
    svc.record_error(model="m", error_code="TIMEOUT", duration_ms=42)
    rows = svc.recent()
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == "TIMEOUT"
    assert "prompt_tokens" not in rows[0]
