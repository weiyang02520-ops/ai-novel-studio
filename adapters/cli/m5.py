"""M5 CLI adapters for Writer, context planning, and draft inspection."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from agents.definitions import writer_agent_def
from agents.task_card import WritingTaskCard
from core import project as project_core
from core.chapter import draft_path, list_chapters, parse_frontmatter
from core.config import ConfigError, Settings
from core.context import collect_project_context
from core.context_budget import ContextBudgetError, plan_context, render_writer_context
from core.generation import GenerationWorkspace, list_partials
from core.mutation import file_revision
from core.relevance import RelevanceError, resolve_relevant_entities
from core.storage import DataIntegrityError, ProjectStore, StorageError
from core.write_workflow import WriteRequest, WriteWorkflow, WriteWorkflowError
from llm.factory import create_provider
from llm.provider import BaseProvider, ProviderError
from llm.secret_store import default_secret_store
from llm.usage import UsageService

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _open(args):
    store = ProjectStore(Path(getattr(args, "data_dir", None) or PROJECT_ROOT / "data" / "novels"))
    return project_core.open_project(store, args.project_id)


def _print_error(exc: Exception) -> int:
    message = getattr(exc, "message", str(exc))
    print(f"错误: {message}")
    return 1


def _prompt(name: str) -> str:
    return (PROJECT_ROOT / "agents" / "prompts" / name).read_text(encoding="utf-8")


def _plan_dict(plan) -> dict:
    def item(row):
        return {
            "source": row.source,
            "type": row.type,
            "status": row.status,
            "original_chars": row.original_chars,
            "included_chars": row.included_chars,
            "estimated_tokens": row.estimated_tokens,
            "revision": row.revision,
        }
    return {
        "model_max_tokens": plan.model_max_tokens,
        "reserve_output_tokens": plan.reserve_output_tokens,
        "safety_margin_tokens": plan.safety_margin_tokens,
        "fixed_prompt_tokens": plan.fixed_prompt_tokens,
        "input_budget_tokens": plan.input_budget_tokens,
        "estimated_input_tokens": plan.estimated_input_tokens,
        "estimated_total_tokens": plan.estimated_total_tokens,
        "context_hash": plan.context_hash,
        "items": [item(x) for x in plan.selected_items + plan.dropped_items],
    }


def _public_task_card(card) -> dict:
    """CLI-safe planning view; never echo opaque Chief brief/raw model text."""
    data = card.to_dict()
    fields = (
        "chapter", "title", "goal", "target_chars", "opening", "conflict",
        "turning_point", "ending_hook", "characters", "world_elements",
        "continuity_requirements", "style_requirements", "forbidden_changes",
        "user_instruction", "source",
    )
    return {name: data[name] for name in fields}


def _show_context_plan(plan, model: str, *, as_json: bool = False, show_text: bool = False) -> None:
    if as_json:
        payload = _plan_dict(plan) | {"model": model}
        if show_text:
            payload["rendered_context"] = render_writer_context(plan)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"MODEL: {model}")
    print(f"max_context: {plan.model_max_tokens}")
    print(f"reserve_output: {plan.reserve_output_tokens}")
    print(f"input_budget: {plan.input_budget_tokens}")
    print(f"estimated_input: {plan.estimated_input_tokens}")
    for row in plan.selected_items + plan.dropped_items:
        tokens = str(row.estimated_tokens) if row.status != "DROP" else "-"
        print(f"{row.status:<8} {row.type:<20} {tokens:>7} tok  {row.source}")
    if show_text:
        print(render_writer_context(plan))


def _record_usages(args, cfg, usages, *, stream: bool, duration_ms: float) -> None:
    service = UsageService(Path(getattr(args, "usage_path", None) or PROJECT_ROOT / "data/logs/usage.jsonl"))
    for usage in usages:
        ok = service.record_success(
            provider=cfg.provider,
            model=cfg.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            estimated=usage.estimated,
            duration_ms=duration_ms,
            stream=stream,
        )
        if not ok:
            print("(warning: usage 记录写入失败，不影响草稿结果)", file=sys.stderr)


def cmd_write(args) -> int:
    try:
        project = _open(args)
        settings = Settings.load(args.config_path)
    except (StorageError, DataIntegrityError, ConfigError) as exc:
        return _print_error(exc)
    chief_cfg, writer_cfg = settings.model_for("chief"), settings.model_for("writer")
    secret_store = default_secret_store()
    chief = writer = None
    result = None
    started = time.monotonic()
    try:
        chief = create_provider(chief_cfg, secret_store)
        writer = create_provider(writer_cfg, secret_store)
        workflow = WriteWorkflow(
            chief_provider=chief,
            writer_provider=writer,
            chief_prompt=_prompt("chief_writer_plan.md"),
            writer_prompt=writer_agent_def().system_prompt,
            settings=settings,
        )
        stages = {"Planning": "[1/4] Planning...", "Context": "[2/4] Context...",
                  "Writing": "[3/4] Writing...", "Saving": "[4/4] Saving..."}
        mode = "rewrite" if args.rewrite else "continue" if args.continue_mode else "resume" if args.resume else "new"
        request = WriteRequest(
            project=project,
            chapter=args.chapter,
            instruction=args.instruction or "",
            title=args.title or "",
            target_chars=args.target_chars,
            characters=args.character or [],
            world=args.world or [],
            mode=mode,
            plan_only=args.plan_only,
        )
        try:
            result = workflow.run(
                request,
                on_stage=lambda name: print(stages[name]),
                on_text_delta=None if args.no_stream else lambda text: print(text, end="", flush=True),
            )
        except KeyboardInterrupt:
            workspace = GenerationWorkspace(project, request.chapter or project.current_chapter + 1)
            if workspace.text():
                print(f"\n生成已中断。已保存部分正文：{workspace.partial.relative_to(project.dir).as_posix()}", file=sys.stderr)
                print(f"可运行：write {project.id} {workspace.chapter} --resume", file=sys.stderr)
            raise
    except (StorageError, DataIntegrityError, ProviderError, WriteWorkflowError,
            RelevanceError, ContextBudgetError, ValueError, OSError) as exc:
        return _print_error(exc)
    finally:
        if chief is not None:
            chief.close()
        if writer is not None:
            writer.close()

    duration_ms = (time.monotonic() - started) * 1000
    _record_usages(args, chief_cfg, result.chief_usages, stream=False, duration_ms=duration_ms)
    _record_usages(args, writer_cfg, result.writer_usages, stream=True, duration_ms=duration_ms)
    if not args.no_stream and result.writer_result:
        print()
    if args.show_plan or args.plan_only:
        print(json.dumps(_public_task_card(result.card), ensure_ascii=False, indent=2))
    if args.show_context or args.plan_only:
        _show_context_plan(result.context_plan, writer_cfg.model)
    if result.status == "planned":
        print("PLAN ONLY: no project mutation")
        return 0
    if result.status == "interrupted":
        print("生成已中断。")
        print(f"已保存部分正文：{result.partial_path}")
        print(f"可运行：write {project.id} {result.chapter} --resume")
        return 1
    draft = result.draft_result
    print("DRAFT SAVED")
    print(f"chapter: {draft.chapter}")
    print(f"path: {draft.path}")
    print(f"words: {draft.words}")
    print(f"revision: {draft.revision}")
    print(f"state: {draft.state}")
    if draft.state == "truncated":
        print(f"可运行：write {project.id} {draft.chapter} --continue")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def cmd_context(args) -> int:
    try:
        project = _open(args)
        settings = Settings.load(args.config_path)
        cfg = settings.model_for("writer")
        chapter = args.chapter or project.current_chapter + 1
        card = WritingTaskCard(
            chapter=chapter,
            goal=(args.instruction or "Dry-run context planning"),
            target_chars=4000,
            characters=args.character or [],
            world_elements=args.world or [],
            user_instruction=args.instruction or "",
            source="synthetic",
        )
        entities = resolve_relevant_entities(
            project, card, args.instruction or "", characters=args.character, world=args.world
        )
        items = collect_project_context(
            project,
            current_volume=int(project.metadata.get("current_volume", 1)),
            target_chapter=chapter,
            character_names=[Path(x).stem for x in entities.characters],
            world_names=[Path(x).stem for x in entities.world],
            include_memory=True,
            recent_chapters=int(settings.context.get("max_recent_chapters", 5)),
            max_recent_chars=int(settings.context.get("max_recent_text_chars", 3000)),
        )
        fixed = BaseProvider.estimate_tokens(writer_agent_def().system_prompt) + BaseProvider.estimate_tokens(
            json.dumps(card.to_dict(), ensure_ascii=False)
        ) + 128
        plan = plan_context(
            items,
            model_max_tokens=cfg.max_context_tokens,
            reserve_output_tokens=int(settings.context.get("reserve_output_tokens", 4096)),
            fixed_prompt_tokens=fixed,
        )
        _show_context_plan(plan, cfg.model, as_json=args.json, show_text=args.show_text)
        return 0
    except (StorageError, DataIntegrityError, ConfigError, RelevanceError,
            ContextBudgetError, ValueError, OSError) as exc:
        return _print_error(exc)


def cmd_draft(args) -> int:
    try:
        project = _open(args)
        if args.draft_command == "list":
            rows = [row for row in list_chapters(project) if row["location"] == "draft"]
            print("CH   TITLE                 ORIGIN STATUS         GEN_STATE  WORDS REVISION")
            for row in rows:
                path = draft_path(project, row["chapter"])
                print(f"{row['chapter']:<4} {row['title'][:20]:<20} {row.get('origin',''):<6} "
                      f"{row['status']:<14} {row.get('generation_state',''):<10} {row['words']:<5} {file_revision(path)}")
            return 0
        if args.draft_command in {"show", "info"}:
            path = draft_path(project, args.chapter)
            text = path.read_text(encoding="utf-8")
            if args.draft_command == "show":
                print(text, end="" if text.endswith("\n") else "\n")
            else:
                metadata, _ = parse_frontmatter(text)
                metadata["revision"] = file_revision(path)
                print(json.dumps(metadata, ensure_ascii=False, indent=2))
            return 0
        if args.draft_command == "partial":
            if args.partial_command == "list":
                print("CH   CHARS MODE       CREATED_AT")
                for row in list_partials(project):
                    print(f"{row['chapter']:<4} {row['chars']:<5} {row.get('mode',''):<10} {row.get('created_at','')}")
                return 0
            workspace = GenerationWorkspace(project, args.chapter)
            if args.partial_command == "show":
                text = workspace.text()
                if not workspace.partial.exists():
                    raise StorageError("partial 不存在")
                print(text, end="" if text.endswith("\n") else "\n")
                return 0
            if args.partial_command == "discard":
                if not workspace.partial.exists() and not workspace.sidecar.exists():
                    raise StorageError("partial 不存在")
                warnings = workspace.cleanup()
                if warnings:
                    raise StorageError("; ".join(warnings))
                print(f"已丢弃 partial ch{args.chapter:04d}（未产生 history）")
                return 0
        raise ValueError("未知 draft 子命令")
    except (StorageError, DataIntegrityError, ValueError, OSError) as exc:
        return _print_error(exc)
