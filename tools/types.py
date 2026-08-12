"""工具系统内部类型 + 轻量参数校验(Core, 无 UI 依赖)。

- ToolDef: 工具定义(name/description/parameters/handler/read_only)
- ToolExecutionError: 面向模型的安全错误(不含绝对路径/敏感信息)
- validate_arguments: 严格 JSON object + 类型校验 + 未知字段拒绝(不引入 jsonschema)
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional


class ToolExecutionError(Exception):
    """工具执行安全错误。消息面向模型(可回填让其修正), 不含绝对路径/敏感信息。"""


# 支持的参数类型(轻量 schema)
_SUPPORTED_TYPES = ("string", "integer", "boolean")


def _type_check(value: Any, typ: str) -> bool:
    if typ == "string":
        return isinstance(value, str)
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "boolean":
        return isinstance(value, bool)
    return False


def validate_arguments(arguments_json: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
    """解析 + 校验 tool arguments(§39-42)。

    返回 (args, error); error 非 None 时 args 为空。
    - 非法 JSON / 非 object / 未知字段 / 类型错误 / 缺少 required → 拒绝
    - 错误消息安全(不含原始输入全文)
    """
    raw = (arguments_json or "").strip()
    if not raw:
        return {}, "INVALID_TOOL_ARGUMENTS: 参数为空(需要 JSON object)"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        return {}, "INVALID_TOOL_ARGUMENTS: 参数不是合法 JSON"
    if not isinstance(args, dict):
        return {}, "INVALID_TOOL_ARGUMENTS: 参数必须是 JSON object"

    props = parameters.get("properties") or {}
    if not isinstance(props, dict):
        return {}, "INVALID_TOOL_ARGUMENTS: 工具 schema 损坏"
    required = parameters.get("required") or []
    if not isinstance(required, list):
        required = []

    for field in required:
        if field not in args:
            return {}, f"INVALID_TOOL_ARGUMENTS: 缺少必需参数 '{field}'"

    for field, value in args.items():
        prop = props.get(field)
        if prop is None:
            # 未知字段: 拒绝, 不 silent ignore(§42)
            return {}, f"INVALID_TOOL_ARGUMENTS: 未知参数 '{field}'"
        typ = prop.get("type")
        if typ not in _SUPPORTED_TYPES:
            return {}, f"INVALID_TOOL_ARGUMENTS: 参数 '{field}' 类型不受支持"
        if not _type_check(value, typ):
            return {}, f"INVALID_TOOL_ARGUMENTS: 参数 '{field}' 需要 {typ}, 实际: {type(value).__name__}"
    return dict(args), None


class ToolDef:
    """工具定义(只读工具 read_only=True; M3 全部只读)。"""

    def __init__(self, name: str, description: str, parameters: dict[str, Any],
                 handler: Callable[..., str], *, read_only: bool = True,
                 mutates_project: bool = False):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.read_only = read_only
        self.mutates_project = mutates_project

    def to_schema(self) -> dict[str, Any]:
        """Provider 层工具 schema(内部格式; 不暴露实现细节, 只描述用户语义)。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
