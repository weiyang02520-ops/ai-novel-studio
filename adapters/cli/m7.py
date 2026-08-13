"""M7 compose CLI argument helpers.

This module deliberately has no workflow or provider dependency so parser and
offline command validation stay cheap and side-effect free.
"""
from __future__ import annotations

import argparse


def validate_max_rounds(value: str) -> int:
    """Parse the public ``--max-rounds`` range (one through ten)."""
    try:
        rounds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--max-rounds 必须是 1 到 10 的整数") from exc
    if not 1 <= rounds <= 10:
        raise argparse.ArgumentTypeError("--max-rounds 必须在 1 到 10 之间")
    return rounds
