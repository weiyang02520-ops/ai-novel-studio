"""共享测试辅助(conftest: 自动加载)。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from llm.secret_store import SecretStoreError  # noqa: E402


def fake_key(prefix: str = "sk-test") -> str:
    """动态拼接 fake secret(测试源码不得出现字面量 key 模式, 避免 checkpoint scanner 误报)。"""
    return prefix + "-" + "X" * 24 + str(len(prefix))


class FakeSecretStore:
    """内存 SecretStore(测试用)。"""

    def __init__(self, keys: dict[str, str] | None = None):
        self.keys = dict(keys or {})

    def get(self, reference: str) -> str:
        if reference not in self.keys:
            raise SecretStoreError("KEY_NOT_FOUND", f"not found: {reference}")
        return self.keys[reference]

    def exists(self, reference: str) -> bool:
        return reference in self.keys

    def set(self, reference: str, value: str) -> None:
        self.keys[reference] = value

    def delete(self, reference: str) -> None:
        self.keys.pop(reference, None)


import pytest  # noqa: E402


@pytest.fixture
def server():
    """localhost mock HTTP server(每个测试独立实例)。"""
    from mock_server import MockServer
    s = MockServer().start()
    yield s
    s.stop()
