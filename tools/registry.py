"""ToolRegistry — 工具注册与安全执行(Core, 无 UI 依赖)。

- 显式白名单注册(不注册 = 不可用; 绝不 getattr/eval/exec/shell)
- 执行双重防御: ToolDef 必须存在 + agent_def.tools 白名单必须包含(§36-37)
- 参数严格 JSON(validate_arguments)
- 工具异常 → 安全 ToolExecutionError(不含绝对路径)
- 输出截断(Unicode-safe, 默认 4K + [TRUNCATED] 标记)
"""
from __future__ import annotations

import time
from typing import Any, Optional

from agents.types import AgentContext, AgentToolCallRecord
from tools.types import ToolDef, ToolExecutionError, validate_arguments

MAX_TOOL_OUTPUT_CHARS = 4000  # §44


def truncate_output(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Unicode-safe 截断(Python str 按字符切片, 中文/emoji 不乱码)。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[TRUNCATED total_chars={len(text)}]"


class ToolRegistry:
    """工具注册表: register / get / schemas_for / execute。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def schemas_for(self, agent_tools: list[str]) -> list[dict[str, Any]]:
        """Agent 白名单内的工具 schema(保持白名单顺序)。"""
        out = []
        for name in agent_tools:
            tool = self._tools.get(name)
            if tool is not None:
                out.append(tool.to_schema())
        return out

    def execute(self, agent: Any, name: str, arguments_json: str,
                ctx: AgentContext) -> tuple[str, AgentToolCallRecord]:
        """执行工具, 返回 (output, trace)。任何失败都不会崩溃, 转为安全消息。"""
        t0 = time.monotonic()
        record = AgentToolCallRecord(name=name, success=False)

        # 第一道: 工具必须已注册(§36: 不存在 → TOOL_NOT_FOUND, 绝不 eval/exec)
        tool = self._tools.get(name)
        if tool is None:
            record.error = "TOOL_NOT_FOUND"
            record.duration_ms = _ms(t0)
            return "TOOL_ERROR: TOOL_NOT_FOUND(工具不存在, 不可执行)", record
        record.mutates_project = bool(tool.mutates_project)

        # 第二道: Agent 白名单权限(§37: 即使注册了写工具, 白名单外也拒绝)
        if name not in agent.tools:
            record.error = "TOOL_PERMISSION_DENIED"
            record.duration_ms = _ms(t0)
            return f"TOOL_ERROR: TOOL_PERMISSION_DENIED(当前 Agent 无权调用 {name})", record

        # 参数解析 + schema 校验(§39-42)
        args, arg_err = validate_arguments(arguments_json, tool.parameters)
        if arg_err is not None:
            record.error = "INVALID_TOOL_ARGUMENTS"
            record.duration_ms = _ms(t0)
            return f"TOOL_ERROR: {arg_err}", record

        # 执行(异常 → 安全错误回填给模型, 允许其修正)
        try:
            output = tool.handler(ctx, args)
            record.success = True
            record.output_length = len(output)
            if tool.mutates_project:
                import re
                match = re.search(r"diff: \+(\d+)/-(\d+)", output)
                if match:
                    record.added_lines, record.removed_lines = map(int, match.groups())
                if "DIFF_PREVIEW_BEGIN\n" in output:
                    record.diff_preview = output.split("DIFF_PREVIEW_BEGIN\n", 1)[1].split("\nDIFF_PREVIEW_END", 1)[0]
        except ToolExecutionError as e:
            output = f"TOOL_ERROR: {e}"
            record.error = str(e)
        except (FileNotFoundError, OSError, UnicodeError, ValueError) as e:
            output = "TOOL_ERROR: 读取项目数据失败(内部错误)"
            record.error = "INTERNAL_READ_ERROR"
        except Exception as e:  # 兜底: 任何异常不得泄漏 traceback/路径
            output = "TOOL_ERROR: 工具执行内部错误"
            record.error = "INTERNAL_ERROR"

        record.duration_ms = _ms(t0)
        return truncate_output(output), record

    def preflight_batch(self, agent: Any, calls: list[Any]) -> Optional[str]:
        """Validate the complete model batch before any handler executes."""
        mutation_count = 0
        for call in calls:
            tool = self._tools.get(call.name)
            if tool is None:
                return "TOOL_NOT_FOUND"
            if call.name not in agent.tools:
                return "TOOL_PERMISSION_DENIED"
            _, error = validate_arguments(call.arguments_json, tool.parameters)
            if error:
                return error
            mutation_count += int(tool.mutates_project)
        if mutation_count and len(calls) != 1:
            return "MUTATION_BATCH_REJECTED: mutation 必须独占一个 LLM response batch"
        return None


def _ms(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000, 1)
