import argparse
from types import SimpleNamespace as NS

import pytest

from adapters.cli.main import build_parser
from adapters.cli.m7 import validate_max_rounds
from core.compose_state import ComposeRunState, ComposeRunStore
from core.project import create_project
from core.storage import ProjectStore


def test_compose_parser_accepts_complete_m7_flag_set():
    args = build_parser().parse_args([
        "compose", "my-novel", "37",
        "--instruction", "推进北门冲突",
        "--title", "北门",
        "--target-chars", "5000",
        "--character", "沈砚",
        "--character", "林小满",
        "--world", "契纹",
        "--review-instruction", "重点检查连续性",
        "--max-rounds", "3",
        "--resume",
        "--no-stream",
        "--show-rounds",
    ])

    assert args.command == "compose"
    assert args.project_id == "my-novel"
    assert args.chapter == 37
    assert args.instruction == "推进北门冲突"
    assert args.title == "北门"
    assert args.target_chars == 5000
    assert args.character == ["沈砚", "林小满"]
    assert args.world == ["契纹"]
    assert args.review_instruction == "重点检查连续性"
    assert args.max_rounds == 3
    assert args.resume is True
    assert args.status is False
    assert args.reset_run is False
    assert args.no_stream is True
    assert args.show_rounds is True


def test_compose_parser_allows_default_chapter_and_offline_modes():
    parser = build_parser()

    normal = parser.parse_args(["compose", "my-novel"])
    assert normal.chapter is None
    assert normal.max_rounds is None
    assert normal.character == []
    assert normal.world == []

    assert parser.parse_args(["compose", "my-novel", "2", "--status"]).status
    assert parser.parse_args(["compose", "my-novel", "2", "--reset-run"]).reset_run


@pytest.mark.parametrize("value", ["1", "10"])
def test_compose_max_rounds_accepts_closed_range(value):
    assert validate_max_rounds(value) == int(value)


@pytest.mark.parametrize("value", ["0", "11", "not-a-number"])
def test_compose_max_rounds_rejects_out_of_range_or_non_integer(value):
    with pytest.raises(argparse.ArgumentTypeError):
        validate_max_rounds(value)


@pytest.mark.parametrize(
    "flags",
    [
        ["--resume", "--status"],
        ["--resume", "--reset-run"],
        ["--status", "--reset-run"],
    ],
)
def test_compose_resume_status_and_reset_are_mutually_exclusive(flags):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["compose", "my-novel", *flags])


def test_compose_has_no_auto_confirm_option():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["compose", "my-novel", "--auto-confirm"])


def _project(tmp_path):
    return create_project(ProjectStore(tmp_path / "novels"), "M7", project_id="my-novel")


def _args(tmp_path, *extra):
    parser = build_parser()
    args = parser.parse_args(["compose", "my-novel", *extra])
    args.data_dir = tmp_path / "novels"
    args.config_path = tmp_path / "settings.json"
    args.usage_path = tmp_path / "usage.jsonl"
    return args


def test_status_and_reset_are_strictly_offline(monkeypatch, tmp_path, capsys):
    p = _project(tmp_path)
    state = ComposeRunState(
        1, "a" * 32, "ESCALATED", 3, 2, "ABSENT", "", "NEEDS_WORK", (),
        "2026-08-13T00:00:00Z", "2026-08-13T00:00:00Z", "rewrite",
        {"chief": "c", "writer": "w", "reviewer": "r"}, "b" * 64)
    ComposeRunStore(p, 1).save(state)
    import adapters.cli.m7 as m7
    monkeypatch.setattr(m7, "create_provider", lambda *_: pytest.fail("provider initialized"))
    monkeypatch.setattr(m7, "default_secret_store", lambda: pytest.fail("secret store read"))
    monkeypatch.setattr(m7.Settings, "load", lambda *_: pytest.fail("config loaded"))

    assert m7.cmd_compose(_args(tmp_path, "1", "--status")) == 0
    assert "compose phase: ESCALATED" in capsys.readouterr().out
    assert m7.cmd_compose(_args(tmp_path, "1", "--reset-run")) == 0
    assert not ComposeRunStore(p, 1).exists()


def test_run_constructs_three_roles_records_all_usage_and_never_confirms(monkeypatch, tmp_path, capsys):
    _project(tmp_path)
    import adapters.cli.m7 as m7
    created = []
    class Provider:
        def __init__(self, cfg): self.config, self.closed = cfg, False
        def close(self): self.closed = True
    def provider(cfg, _secrets):
        value = Provider(cfg); created.append(value); return value
    monkeypatch.setattr(m7, "create_provider", provider)
    monkeypatch.setattr(m7, "default_secret_store", lambda: object())
    usages = NS(chief_usages=[NS(prompt_tokens=1, completion_tokens=2, total_tokens=3, estimated=False)],
                writer_usages=[NS(prompt_tokens=2, completion_tokens=3, total_tokens=5, estimated=False)],
                reviewer_usages=[NS(prompt_tokens=3, completion_tokens=4, total_tokens=7, estimated=False),
                                 NS(prompt_tokens=1, completion_tokens=1, total_tokens=2, estimated=False)])
    result = NS(chapter=1, final_state="READY", status="READY", reason="", rounds_completed=1,
                draft_revision="d" * 64, latest_verdict="PASS", latest_report_hash="e" * 64,
                rounds=[NS(round_number=1, review_verdict="PASS", review_issue_counts={"MAJOR": 0},
                           writer_mode="new", writer_model="w", reviewer_model="r")], warnings=[], **vars(usages))
    seen = {}
    class FakeCreation:
        def __init__(self, **kwargs): seen.update(kwargs)
        def run(self, request, **_): seen["request"] = request; return result
    monkeypatch.setattr(m7, "CreationWorkflow", FakeCreation)
    recorded = []
    monkeypatch.setattr(m7, "_record_usages", lambda _a, cfg, rows, **kw: recorded.extend((cfg.model, x.total_tokens) for x in rows))

    code = m7.cmd_compose(_args(tmp_path, "1", "--show-rounds", "--no-stream"))

    assert code == 0 and len(created) == 3 and all(x.closed for x in created)
    assert sorted(total for _, total in recorded) == [2, 3, 5, 7]
    assert seen["request"].stream is False
    output = capsys.readouterr().out
    assert "COMPOSE COMPLETE" in output and "User confirmation required" in output
    assert "confirm_draft" not in output


@pytest.mark.parametrize(("state", "code"), [("ESCALATED", 2), ("BLOCKED", 2), ("INTERRUPTED", 130)])
def test_run_outputs_nonready_terminal_states(monkeypatch, tmp_path, capsys, state, code):
    _project(tmp_path)
    import adapters.cli.m7 as m7
    class Provider:
        config = NS(model="m")
        def close(self): pass
    monkeypatch.setattr(m7, "create_provider", lambda *_: Provider())
    monkeypatch.setattr(m7, "default_secret_store", lambda: object())
    result = NS(chapter=1, final_state=state, status=state, reason="WHY", rounds_completed=0,
                draft_revision="ABSENT", latest_verdict="", latest_report_hash="", rounds=[],
                warnings=[], chief_usages=[], writer_usages=[], reviewer_usages=[])
    monkeypatch.setattr(m7, "CreationWorkflow", lambda **_: NS(run=lambda *_a, **_k: result))
    monkeypatch.setattr(m7, "_record_usages", lambda *_a, **_k: None)
    assert m7.cmd_compose(_args(tmp_path, "1")) == code
    assert f"COMPOSE {state}" in capsys.readouterr().out


def test_main_routes_compose_to_m7_adapter(monkeypatch):
    import adapters.cli.m7 as m7
    seen = []
    monkeypatch.setattr(m7, "cmd_compose", lambda args: seen.append(args.project_id) or 7)
    from adapters.cli.main import _main

    assert _main(["compose", "my-novel", "--status"]) == 7
    assert seen == ["my-novel"]
