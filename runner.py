import os
from typing import AsyncIterator, Awaitable, Callable, Optional, Tuple

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

# Read-only tools are pre-approved with no Discord round-trip.
READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "LS"}

# Auto / bypass-permissions mode (default on; CC_SKIP_PERMISSIONS=0 to require approval).
# When on, SDK-mode turns run every tool without a Discord approval round-trip — matching
# the terminal sessions launched with --dangerously-skip-permissions.
SKIP_PERMISSIONS = os.environ.get("CC_SKIP_PERMISSIONS", "1") != "0"

# Async approver: given (tool_name, tool_input), returns True iff the user approved.
Approver = Callable[[str, dict], Awaitable[bool]]


def _make_can_use_tool(on_approval: Optional[Approver]):
    async def can_use_tool(tool_name, input_data, context):
        if tool_name in READ_ONLY_TOOLS:
            return PermissionResultAllow(updated_input=input_data)
        if on_approval is None:
            return PermissionResultDeny(
                message=f"{tool_name} is a mutating tool; no approver configured."
            )
        approved = await on_approval(tool_name, input_data)
        if approved:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(
            message=f"User denied {tool_name} via Discord."
        )

    return can_use_tool


async def _single_prompt_stream(text: str):
    """can_use_tool requires streaming mode, so the prompt must be an async iterable."""
    yield {"type": "user", "message": {"role": "user", "content": text}}


async def run_turn(
    prompt: str,
    cwd: str,
    resume_id: Optional[str] = None,
    on_approval: Optional[Approver] = None,
) -> AsyncIterator[Tuple]:
    """Drive one Claude turn.

    Yields:
        ("text", str)
        ("tool", str, dict)            — tool was attempted (allow/deny is handled in callback)
        ("done", session_id, cost_usd)
    """
    # Load the user's CLI settings so SDK turns inherit the claude.ai MCP connectors
    # (Gmail, Google Calendar, etc.). Without this, SDK 0.2.x starts with NO settings and
    # those tools are invisible — even though `claude mcp list` shows them connected.
    setting_sources = ["user", "project", "local"]

    if SKIP_PERMISSIONS:
        # Bypass mode: no can_use_tool callback, so tools run with no Discord approval.
        options = ClaudeAgentOptions(
            cwd=cwd,
            resume=resume_id,
            permission_mode="bypassPermissions",
            setting_sources=setting_sources,
        )
    else:
        options = ClaudeAgentOptions(
            cwd=cwd,
            resume=resume_id,
            can_use_tool=_make_can_use_tool(on_approval),
            setting_sources=setting_sources,
        )

    session_id: Optional[str] = None
    cost: Optional[float] = None

    async for message in query(prompt=_single_prompt_stream(prompt), options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    yield ("text", block.text)
                elif isinstance(block, ToolUseBlock):
                    yield ("tool", block.name, block.input)
        elif isinstance(message, ResultMessage):
            session_id = getattr(message, "session_id", None)
            cost = getattr(message, "total_cost_usd", None)

    yield ("done", session_id, cost)
