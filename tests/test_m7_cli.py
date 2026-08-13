import argparse

import pytest

from adapters.cli.main import build_parser
from adapters.cli.m7 import validate_max_rounds


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
