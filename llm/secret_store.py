"""SecretStore — 密钥安全存储抽象(Core 层, 无 UI 依赖)。

真实 API Key 只存这里, 绝不进入:
  - settings.json / Git / checkpoint
  - 日志 / 报错信息
  - 小说数据目录

后端:
  1. 系统凭据管理器(Python keyring: Windows Credential Manager / macOS Keychain / Linux Secret Service)
  2. 环境变量(开发/CI 模式): NOVEL_API_KEY, NOVEL_<ROLE>_API_KEY
读取优先级: 环境变量 > SecretStore。

错误语义(不再把异常吞成 None):
  KEY_NOT_FOUND       — 未找到该 reference 的 Key(正常未配置)
  BACKEND_UNAVAILABLE — 后端不可用(keyring 未安装/无系统凭据服务)
  BACKEND_ERROR       — 后端操作失败(读/写/删异常)
"""
from __future__ import annotations

import os
import re
from typing import Optional


class SecretStoreError(Exception):
    """SecretStore 错误(携带错误码, 消息不含真实 Key)。"""

    def __init__(self, code: str, message: str):
        self.code = code  # KEY_NOT_FOUND | BACKEND_UNAVAILABLE | BACKEND_ERROR
        super().__init__(f"[SecretStore:{code}] {message}")


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
        try:
            return self.get(reference) is not None
        except SecretStoreError as e:
            if e.code == "KEY_NOT_FOUND":
                return False
            raise


class EnvSecretStore(SecretStore):
    """环境变量后端(开发模式, 只读)。"""

    def _env_name(self, reference: str) -> str:
        suffix = re.sub(r"[^A-Za-z0-9]", "_", reference.upper())
        return f"NOVEL_API_KEY_{suffix}" if suffix else "NOVEL_API_KEY"

    def get(self, reference: str) -> Optional[str]:
        v = os.environ.get(self._env_name(reference)) or os.environ.get("NOVEL_API_KEY")
        if v:
            return v
        raise SecretStoreError("KEY_NOT_FOUND", f"环境变量中未找到 Key: {self._env_name(reference)} 或 NOVEL_API_KEY")

    def set(self, reference: str, value: str) -> None:
        raise SecretStoreError("BACKEND_UNAVAILABLE", "环境变量后端不支持写入; 请直接设置环境变量")

    def delete(self, reference: str) -> None:
        raise SecretStoreError("BACKEND_UNAVAILABLE", "环境变量后端不支持删除")


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
        # 测试钩子: NOVEL_DISABLE_KEYRING=1 模拟无凭据服务(供集成测试)
        if os.environ.get("NOVEL_DISABLE_KEYRING") == "1":
            return False
        return self._keyring is not None

    def _check(self) -> None:
        if not self._keyring:
            raise SecretStoreError(
                "BACKEND_UNAVAILABLE",
                "keyring 未安装或无系统凭据服务。请安装 keyring 依赖, 或使用环境变量模式(NOVEL_API_KEY_<REF>)。",
            )

    def get(self, reference: str) -> Optional[str]:
        self._check()
        try:
            v = self._keyring.get_password(self.SERVICE_NAME, reference)
        except Exception:
            raise SecretStoreError("BACKEND_ERROR", "读取系统凭据管理器失败")
        if v:
            return v
        raise SecretStoreError("KEY_NOT_FOUND", f"未找到 Key: {reference}")

    def set(self, reference: str, value: str) -> None:
        self._check()
        try:
            self._keyring.set_password(self.SERVICE_NAME, reference, value)
        except Exception:
            raise SecretStoreError("BACKEND_ERROR", "写入系统凭据管理器失败(不会以明文文件存储 Key)")

    def delete(self, reference: str) -> None:
        self._check()
        try:
            self._keyring.delete_password(self.SERVICE_NAME, reference)
        except Exception:
            raise SecretStoreError("BACKEND_ERROR", "删除系统凭据失败")

    def exists(self, reference: str) -> bool:
        if not self._keyring:
            raise SecretStoreError(
                "BACKEND_UNAVAILABLE",
                "keyring 未安装或无系统凭据服务",
            )
        try:
            return self.get(reference) is not None
        except SecretStoreError as e:
            if e.code == "KEY_NOT_FOUND":
                return False
            raise


class CompositeSecretStore(SecretStore):
    """组合: 环境变量优先, 其次系统凭据管理器。"""

    def __init__(self, env: SecretStore | None = None, keyring: KeyringSecretStore | None = None):
        self._env = env or EnvSecretStore()
        self._keyring = keyring or KeyringSecretStore()

    def get(self, reference: str) -> Optional[str]:
        try:
            v = self._env.get(reference)
            if v:
                return v
        except SecretStoreError as e:
            if e.code == "KEY_NOT_FOUND":
                pass  # 环境变量没有 → 继续查 keyring
            else:
                raise
        return self._keyring.get(reference)

    def set(self, reference: str, value: str) -> None:
        if self._keyring.available:
            self._keyring.set(reference, value)
            return
        raise SecretStoreError(
            "BACKEND_UNAVAILABLE",
            "系统凭据管理器不可用。请安装 keyring 支持包, 或使用环境变量模式(NOVEL_API_KEY_<REF>)。不会以明文文件存储 Key。",
        )

    def delete(self, reference: str) -> None:
        if self._keyring.available:
            self._keyring.delete(reference)
            return
        raise SecretStoreError("BACKEND_UNAVAILABLE", "系统凭据管理器不可用")

    def exists(self, reference: str) -> bool:
        try:
            return self.get(reference) is not None
        except SecretStoreError as e:
            if e.code == "KEY_NOT_FOUND":
                return False
            raise


def default_secret_store() -> CompositeSecretStore:
    """构建默认 SecretStore。"""
    return CompositeSecretStore()
