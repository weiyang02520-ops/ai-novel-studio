from adapters.cli.main import build_parser
from adapters.cli.m6 import _parse_shape
import pytest


def test_review_cli_accepts_run_and_inspection_shapes():
    parser = build_parser()
    run = parser.parse_args([
        "review", "novel", "2", "--instruction", "重点检查对白",
        "--character", "沈砚", "--world", "灵河", "--show-context",
        "--show-json",
    ])
    assert run.command == "review"
    assert run.review_args == ["novel", "2"]
    assert run.instruction == "重点检查对白"
    assert run.character == ["沈砚"]
    assert run.world == ["灵河"]
    assert run.show_context and run.show_json

    show = parser.parse_args(["review", "show", "novel", "2"])
    assert show.review_args == ["show", "novel", "2"]


def test_review_cli_accepts_plan_filters_recover_and_reopen():
    parser = build_parser()
    plan = parser.parse_args(["review", "novel", "--plan-only"])
    assert plan.review_args == ["novel"]
    assert plan.plan_only

    issues = parser.parse_args([
        "review", "issues", "novel", "2", "--severity", "MAJOR",
        "--category", "CHARACTER",
    ])
    assert issues.severity == "MAJOR"
    assert issues.category == "CHARACTER"
    assert parser.parse_args(["review", "recover", "novel", "2"]).review_args[0] == "recover"
    assert parser.parse_args(["review", "reopen", "novel", "2"]).review_args[0] == "reopen"


def test_review_shape_parser_is_strict_and_supports_default_chapter():
    assert _parse_shape(["novel"]) == ("run", "novel", None)
    assert _parse_shape(["novel", "2"]) == ("run", "novel", 2)
    assert _parse_shape(["show", "novel", "2"]) == ("show", "novel", 2)
    with pytest.raises(ValueError):
        _parse_shape([])
    with pytest.raises(ValueError):
        _parse_shape(["show", "novel"])
    with pytest.raises(ValueError):
        _parse_shape(["novel", "zero"])
