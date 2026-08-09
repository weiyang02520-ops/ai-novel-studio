"""SecretStore — 密钥安全存储抽象(Core 层, 无 UI 依赖)。

真实 API Key 只存这里, 绝不进入:
  - settings.json / Git / checkpoint
  - 日志 / 报错信息
  - 小说数据目录

后端:
  1. 系统凭据管理器(Python keyring: Windows Credential Manager / macOS Keychain / Linux Secret Service)
  2. 环境变量(开发/CI 模式): NOVEL_API_KEY, NOVEL_<ROLE>_API_KEY
读取优先级: 环境变量 > SecretStore。
"""
from __future__ import annotations

import os
from typing import Optional


class SecretStore:
    """平台无关密钥存储抽象。"""

    SERVICE_NAME = "ai-novel-studio"

    def get(self, reference: str) -> Optional[str]:
        raise NotImplementedError

    def set(self, reference: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, reference: str) -> None:
        raise NotImplementedError

    def exists(self, reference: str) -> bool:
        return self.get(reference) is not None


class EnvSecretStore(SecretStore):
    """环境变量后端(开发模式)。"""

    def _env_name(self, reference: str) -> str:
        # reference: "deepseek-main" → NOVEL_API_KEY_MAIN? 规范: NOVEL_API_KEY_<REF 大写非字母→_>
        import re
        suffix = re.sub(r"[^A-Za-z0-9]", "_", reference.upper())
        return f"NOVEL_API_KEY_{suffix}" if suffix else "NOVEL_API_KEY"

    def get(self, reference: str) -> Optional[str]:
        v = os.environ.get(self._env_name(reference)) or os.environ.get("NOVEL_API_KEY")
        return v if v else None

    def set(self, reference: str, value: str) -> None:
        raise NotImplementedError("环境变量后端不支持写入; 请直接设置环境变量")

    def delete(self, reference: str) -> None:
        raise NotImplementedError("环境变量后端不支持删除")


class KeyringSecretStore(SecretStore):
    """系统凭据管理器后端(推荐)。"""

    def __init__(self) -> None:
        self._keyring = None
        try:
            import keyring
            self._keyring = keyring
        except ImportError:
            self._keyring = None

    @property
    def available(self) -> bool:
        return self._keyring is not None

    def get(self, reference: str) -> Optional[str]:
        if not self._keyring:
            return None
        try:
            return self._keyring.get_password(self.SERVICE_NAME, reference)
        except Exception:
            return None

    def set(self, reference: str, value: str) -> None:
        if not self._keyring:
            raise RuntimeError("keyring 不可用(未安装 keyring 包或无系统凭据服务)")
        self._keyring.set_password(self.SERVICE_NAME, reference, value)

    def delete(self, reference: str) -> None:
        if self._keyring:
            try:
                self._keyring.delete_password(self.SERVICE_NAME, reference)
            except Exception:
                pass

    def exists(self, reference: str) -> bool:
        if not self._keyring:
            return False
        return self.get(reference) is not None


class CompositeSecretStore(SecretStore):
    """组合: 环境变量优先, 其次系统凭据管理器。

    读取: env → keyring → None
    写入: 仅 keyring(环境变量为开发只读)。
    """

    def __init__(self, env: SecretStore | None = None, keyring: KeyringSecretStore | None = None):
        self._env = env or EnvSecretStore()
        self._keyring = keyring or KeyringSecretStore()

    def get(self, reference: str) -> Optional[str]:
        v = self._env.get(reference)
        if v:
            return v
        return self._keyring.get(reference)

    def set(self, reference: str, value: str) -> None:
        if self._keyring.available:
            self._keyring.set(reference, value)
            return
        # 无系统凭据服务 → 不降级为明文文件; 提示用环境变量
        raise RuntimeError(
            "SecretStore: 系统凭据管理器不可用。请安装 keyring 支持包, "
            "或使用环境变量模式(NOVEL_API_KEY_<REF>)。不会以明文文件存储 Key。"
        )

    def delete(self, reference: str) -> None:
        self._keyring.delete(reference)

    def exists(self, reference: str) -> bool:
        return self.get(reference) is not None


def default_secret_store() -> CompositeSecretStore:
    """构建默认 SecretStore。"""
    return CompositeSecretStore()
