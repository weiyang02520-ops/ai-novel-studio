"""M6 CLI adapter for bounded, revision-safe draft review."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from agents.definitions import reviewer_agent_def
from agents.review_report import ReviewReportError
from core import project as project_core
from core.config import ConfigError, Settings
from core.context_budget import ContextBudgetError
from core.mutation import file_revision
from core.relevance import RelevanceError
from core.review import ReviewError, ReviewService
from core.review_workflow import ReviewWorkflow, ReviewWorkflowError, ReviewWorkflowRequest
from core.storage import DataIntegrityError, ProjectStore, StorageError
from llm.factory import create_provider
from llm.provider import BaseProvider, ProviderError
from llm.secret_store import default_secret_store
from llm.usage import UsageService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ACTIONS = {"show", "info", "issues", "recover", "reopen"}


def _open(args, project_id: str):
    store = ProjectStore(Path(getattr(args, "data_dir", None) or PROJECT_ROOT / "data" / "novels"))
    return project_core.open_project(store, project_id)


def _error(exc: Exception) -> int:
    print(f"错误: {getattr(exc, 'message', str(exc))}")
    return 1


def _parse_shape(values: list[str]) -> tuple[str, str, int | None]:
    if not values:
        raise ValueError("用法: review <project_id> [chapter]")
    action = values[0] if values[0] in _ACTIONS else "run"
    rest = values[1:] if action != "run" else values
    required = 1 if action == "run" else 2
    maximum = 2
    if len(rest) < required or len(rest) > maximum:
        raise ValueError(
            "用法: review <project_id> [chapter] 或 review "
            "{show|info|issues|recover|reopen} <project_id> <chapter>"
        )
    chapter = None
    if len(rest) == 2:
        try:
            chapter = int(rest[1])
        except ValueError as exc:
            raise ValueError("chapter 必须是正整数") from exc
        if chapter < 1:
            raise ValueError("chapter 必须是正整数")
    return action, rest[0], chapter


def _plan_payload(plan) -> dict:
    def row(item):
        return {
            "source": item.source, "type": item.type, "status": item.status,
            "original_chars": item.original_chars, "included_chars": item.included_chars,
            "estimated_tokens": item.estimated_tokens, "revision": item.revision,
        }
    shared = plan.shared
    return {
        "model_max_tokens": shared.model_max_tokens,
        "reserve_output_tokens": shared.reserve_output_tokens,
        "safety_margin_tokens": shared.safety_margin_tokens,
        "fixed_prompt_tokens": shared.fixed_prompt_tokens,
        "estimated_input_tokens": shared.estimated_input_tokens,
        "estimated_total_tokens": shared.estimated_total_tokens,
        "context_hash": shared.context_hash,
        "draft_revision": plan.draft_revision,
        "draft_truncated": plan.draft_truncated,
        "items": [row(x) for x in shared.selected_items + shared.dropped_items],
    }


def _print_manifest(plan) -> None:
    payload = _plan_payload(plan)
    print(f"MODEL MAX: {payload['model_max_tokens']}")
    print(f"ESTIMATED TOTAL: {payload['estimated_total_tokens']}")
    print(f"CONTEXT HASH: {payload['context_hash']}")
    for row in payload["items"]:
        print(f"{row['status']:<8} {row['type']:<22} {row['source']}")


def _print_report(report: dict, *, stale: bool = False) -> None:
    if stale:
        print("STALE REVIEW")
    issues = report.get("issues", [])
    counts = {severity: sum(1 for issue in issues if issue.get("severity") == severity)
              for severity in ("BLOCKER", "MAJOR", "MINOR", "INFO")}
    print(f"VERDICT: {report.get('verdict', '')}")
    for severity, count in counts.items():
        print(f"{severity} {count}")
    if report.get("summary"):
        print(report["summary"])
    for issue in issues:
        location = issue.get("location") or {}
        start, end, anchor = location.get("line_start"), location.get("line_end"), location.get("anchor")
        where = f"第 {start}-{end or start} 行" if start else (anchor or "未知位置")
        print(f"[{issue.get('severity')}][{issue.get('category')}] {issue.get('title')}")
        print(f"位置: {where}")
        print(f"依据: {issue.get('evidence', '')}")
        print(f"建议: {issue.get('suggestion', '')}")
    if report.get("verdict") == "PASS" and not stale:
        print("READY FOR USER CONFIRMATION")


def _inspect(args, action: str, project_id: str, chapter: int) -> int:
    project = _open(args, project_id)
    service = ReviewService(project)
    if action == "recover":
        service.recover(chapter=chapter)
        return 0
    if action == "reopen":
        from core.chapter import draft_path
        result = service.reopen(chapter=chapter, expected_revision=file_revision(draft_path(project, chapter)))
        print("REVIEW REOPENED")
        print(f"chapter: {result.chapter}")
        print(f"draft_revision: {result.draft_revision}")
        return 0
    inspection = service.inspect(chapter=chapter)
    artifact = inspection.artifact
    if action == "info":
        info = {key: artifact[key] for key in (
            "chapter", "draft_revision", "reviewed_at", "reviewer_model",
            "context_hash", "report_hash", "verdict"
        )}
        info["current_draft_revision"] = inspection.draft_revision
        info["report_revision"] = inspection.report_revision
        info["current"] = inspection.current
        print(json.dumps(info, ensure_ascii=False, indent=2))
        if not inspection.current:
            print("STALE REVIEW")
        return 0
    if action == "issues":
        issues = artifact["issues"]
        if args.severity:
            issues = [x for x in issues if x["severity"] == args.severity]
        if args.category:
            issues = [x for x in issues if x["category"] == args.category]
        filtered = dict(artifact); filtered["issues"] = issues
        _print_report(filtered, stale=not inspection.current)
        return 0
    _print_report(artifact, stale=not inspection.current)
    return 0


def _record_usage(args, cfg, usage, duration_ms: float) -> None:
    if not usage:
        return
    service = UsageService(Path(getattr(args, "usage_path", None) or PROJECT_ROOT / "data/logs/usage.jsonl"))
    ok = service.record_success(
        provider=cfg.provider, model=cfg.model, prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens, total_tokens=usage.total_tokens,
        estimated=usage.estimated, duration_ms=duration_ms, stream=False,
    )
    if not ok:
        print("(warning: usage 记录写入失败，不影响审稿结果)", file=sys.stderr)


def cmd_review(args) -> int:
    provider = None
    try:
        action, project_id, chapter = _parse_shape(args.review_args)
        if action != "run":
            if args.plan_only or args.show_json or args.show_context or args.instruction or args.character or args.world:
                raise ValueError("inspection/recover/reopen 不接受 review run 选项")
            return _inspect(args, action, project_id, chapter)
        project = _open(args, project_id)
        settings = Settings.load(args.config_path)
        cfg = settings.model_for("reviewer")
        # Offline plan-only must not initialize an HTTP transport or read a key.
        provider = BaseProvider(cfg) if args.plan_only else create_provider(cfg, default_secret_store())
        workflow = ReviewWorkflow(
            reviewer_provider=provider,
            reviewer_prompt=reviewer_agent_def().system_prompt,
            settings=settings,
        )
        started = time.monotonic()
        result = workflow.run(ReviewWorkflowRequest(
            project=project, chapter=chapter, instruction=args.instruction or "",
            characters=args.character or [], world=args.world or [], plan_only=args.plan_only,
        ))
        duration_ms = (time.monotonic() - started) * 1000
        if args.show_context or args.plan_only:
            _print_manifest(result.context_plan)
        if args.plan_only:
            print("PLAN ONLY: no Provider call or project mutation")
            return 0
        _record_usage(args, cfg, result.reviewer_result.usage if result.reviewer_result else None, duration_ms)
        if args.show_json:
            print(json.dumps(result.report.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_report(result.report.to_dict())
        return 0
    except (ValueError, OSError, StorageError, DataIntegrityError, ConfigError,
            ProviderError, ReviewWorkflowError, ReviewError, ReviewReportError,
            RelevanceError, ContextBudgetError) as exc:
        return _error(exc)
    finally:
        if provider is not None:
            provider.close()
