"""M7 compose CLI: offline inspection and production orchestration wiring."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from agents.definitions import reviewer_agent_def, writer_agent_def
from core import project as project_core
from core.compose_state import ComposeRunStore, ComposeStateError, compose_status
from core.config import ConfigError, Settings
from core.creation_workflow import CreationRequest, CreationWorkflow, CreationWorkflowError
from core.review_workflow import ReviewWorkflow
from core.storage import DataIntegrityError, ProjectStore, StorageError
from core.write_workflow import WriteWorkflow
from llm.factory import create_provider
from llm.provider import ProviderError
from llm.secret_store import default_secret_store
from llm.usage import UsageService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def validate_max_rounds(value: str) -> int:
    """Parse the public ``--max-rounds`` range (one through ten)."""
    try:
        rounds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--max-rounds 必须是 1 到 10 的整数") from exc
    if not 1 <= rounds <= 10:
        raise argparse.ArgumentTypeError("--max-rounds 必须在 1 到 10 之间")
    return rounds


def _open(args):
    root = Path(getattr(args, "data_dir", None) or PROJECT_ROOT / "data/novels")
    return project_core.open_project(ProjectStore(root), args.project_id)


def _prompt(name: str) -> str:
    return (PROJECT_ROOT / "agents/prompts" / name).read_text(encoding="utf-8")


def _record_usages(args, cfg, usages, *, stream: bool, duration_ms: float) -> None:
    service = UsageService(Path(getattr(args, "usage_path", None)
                                or PROJECT_ROOT / "data/logs/usage.jsonl"))
    for usage in usages:
        ok = service.record_success(
            provider=cfg.provider, model=cfg.model,
            prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens, estimated=usage.estimated,
            duration_ms=duration_ms, stream=stream,
        )
        if not ok:
            print("(warning: usage 记录写入失败，不影响 compose 结果)", file=sys.stderr)


def _print_status(status) -> None:
    print(f"chapter: {status.chapter}")
    print(f"chapter state: {status.chapter_state}")
    print(f"draft revision: {status.draft_revision}")
    print(f"review current: {'YES' if status.review_current else 'NO'}")
    print(f"latest verdict: {status.latest_verdict or '-'}")
    print(f"review rounds: {status.review_rounds}")
    print(f"compose phase: {status.compose_phase or '-'}")
    print(f"partial exists: {'YES' if status.partial_exists else 'NO'}")
    print(f"can resume: {'YES' if status.can_resume else 'NO'}")
    print(f"can confirm: {'YES' if status.can_confirm else 'NO'}")


def _print_result(result, *, show_rounds: bool, project_id: str = "<project>") -> int:
    state = result.final_state
    heading = "COMPOSE COMPLETE" if state == "READY" else f"COMPOSE {state}"
    print(heading)
    print(f"chapter: {result.chapter}")
    print(f"state: {state}")
    if result.reason:
        print(f"reason: {result.reason}")
    print(f"review rounds: {result.rounds_completed}")
    print(f"draft revision: {result.draft_revision}")
    print(f"latest review: {result.latest_verdict or '-'}")
    print(f"report hash: {result.latest_report_hash or '-'}")
    if show_rounds:
        for row in result.rounds:
            counts = " ".join(f"{key}={value}" for key, value in row.review_issue_counts.items())
            print(f"round {row.round_number}: {row.review_verdict} {counts}".rstrip())
            print(f"  writer: {row.writer_mode or '-'} {row.writer_model or '-'}")
            print(f"  reviewer: {row.reviewer_model or '-'}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if state == "READY":
        print("READY FOR USER CONFIRMATION")
        print(f"Run: chapter confirm {project_id} {result.chapter}")
        print("User confirmation required. No automatic confirmation occurred.")
        return 0
    if state == "INTERRUPTED":
        print("Run compose again with --resume.")
        return 130
    if state == "ESCALATED":
        print("USER ATTENTION REQUIRED")
        print("No automatic confirmation occurred.")
        return 2
    print("No automatic confirmation occurred.")
    return 2


def cmd_compose(args) -> int:
    providers = []
    try:
        project = _open(args)
        chapter = args.chapter or project.current_chapter + 1
        # These branches are deliberately before Settings/SecretStore/Provider.
        if args.status:
            _print_status(compose_status(project, chapter))
            return 0
        if args.reset_run:
            removed = ComposeRunStore(project, chapter).reset()
            print("COMPOSE RUN RESET" if removed else "COMPOSE RUN NOT FOUND")
            return 0

        settings = Settings.load(args.config_path)
        chief_cfg = settings.model_for("chief")
        writer_cfg = settings.model_for("writer")
        reviewer_cfg = settings.model_for("reviewer")
        secrets = default_secret_store()
        chief = create_provider(chief_cfg, secrets); providers.append(chief)
        writer = create_provider(writer_cfg, secrets); providers.append(writer)
        reviewer = create_provider(reviewer_cfg, secrets); providers.append(reviewer)
        write_workflow = WriteWorkflow(
            chief_provider=chief, writer_provider=writer,
            chief_prompt=_prompt("chief_writer_plan.md"),
            writer_prompt=writer_agent_def().system_prompt, settings=settings)
        review_workflow = ReviewWorkflow(
            reviewer_provider=reviewer, reviewer_prompt=reviewer_agent_def().system_prompt,
            settings=settings)
        workflow = CreationWorkflow(
            write_workflow_factory=lambda _project: write_workflow,
            review_workflow_factory=lambda _project: review_workflow,
            settings=settings,
        )
        request = CreationRequest(
            project=project, chapter=args.chapter, instruction=args.instruction or "",
            title=args.title or "", target_chars=args.target_chars,
            characters=args.character or [], world=args.world or [],
            max_review_rounds=args.max_rounds,
            review_instruction=args.review_instruction or "", resume=args.resume,
            stream=not args.no_stream,
        )
        started = time.monotonic()
        result = workflow.run(request)
        duration_ms = (time.monotonic() - started) * 1000
        _record_usages(args, chief_cfg, result.chief_usages, stream=False,
                       duration_ms=duration_ms)
        _record_usages(args, writer_cfg, result.writer_usages, stream=not args.no_stream,
                       duration_ms=duration_ms)
        _record_usages(args, reviewer_cfg, result.reviewer_usages, stream=False,
                       duration_ms=duration_ms)
        return _print_result(result, show_rounds=args.show_rounds, project_id=project.id)
    except (StorageError, DataIntegrityError, ConfigError, ComposeStateError,
            CreationWorkflowError, ProviderError, OSError, ValueError) as exc:
        print(f"错误: {getattr(exc, 'message', str(exc))}", file=sys.stderr)
        return 1
    finally:
        for provider in reversed(providers):
            provider.close()
