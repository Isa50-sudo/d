"""Permission-System: Zugriffsstufen, Overrides und nicht überschreibbare Regeln."""
from __future__ import annotations

from jarvis.config.user_settings import PermissionSettings
from jarvis.permissions.manager import Policy, resolve_policy
from jarvis.tools.base import NoArgs, Risk, Tool


async def _noop(args, ctx):  # pragma: no cover
    return None


def make(risk: Risk, **kw) -> Tool:
    return Tool(name=f"t_{risk.value}", description="", args_model=NoArgs, risk=risk, category="test", func=_noop, **kw)


def policy(tool: Tool, level: str, overrides: dict | None = None) -> Policy:
    return resolve_policy(tool, PermissionSettings(access_level=level, tool_overrides=overrides or {})).policy


def test_read_only_denies_everything_but_reads():
    assert policy(make(Risk.READ), "READ_ONLY") == Policy.ALLOW
    assert policy(make(Risk.WRITE), "READ_ONLY") == Policy.DENY
    assert policy(make(Risk.CRITICAL), "READ_ONLY") == Policy.DENY


def test_read_only_cannot_be_lifted_by_override():
    tool = make(Risk.WRITE)
    assert policy(tool, "READ_ONLY", {tool.name: "allow"}) == Policy.DENY


def test_limited_defaults():
    assert policy(make(Risk.WRITE), "LIMITED") == Policy.ALLOW
    assert policy(make(Risk.WRITE, confirm_in_limited=True), "LIMITED") == Policy.CONFIRM
    assert policy(make(Risk.CRITICAL), "LIMITED") == Policy.CONFIRM


def test_critical_always_needs_confirmation_even_with_full_access_and_override():
    tool = make(Risk.CRITICAL)
    assert policy(tool, "FULL_ACCESS") == Policy.CONFIRM
    assert policy(tool, "FULL_ACCESS", {tool.name: "allow"}) == Policy.CONFIRM


def test_always_confirm_flag_is_locked():
    tool = make(Risk.WRITE, always_confirm=True)
    decision = resolve_policy(tool, PermissionSettings(access_level="FULL_ACCESS", tool_overrides={tool.name: "allow"}))
    assert decision.policy == Policy.CONFIRM and decision.locked


def test_override_can_deny_or_require_confirmation():
    tool = make(Risk.READ)
    assert policy(tool, "FULL_ACCESS", {tool.name: "deny"}) == Policy.DENY
    assert policy(tool, "LIMITED", {tool.name: "confirm"}) == Policy.CONFIRM


def test_builtin_dangerous_tools_are_protected():
    from jarvis.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.load_builtin()
    for name in ("delete_file", "email_send", "install_application", "close_application"):
        tool = registry.get(name)
        assert tool is not None
        assert policy(tool, "FULL_ACCESS", {name: "allow"}) == Policy.CONFIRM, name


def test_no_arbitrary_code_or_shell_tool_exists():
    from jarvis.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.load_builtin()
    forbidden = ("exec", "shell", "command", "python", "eval", "run_code", "powershell", "bash")
    for tool in registry.all():
        assert not any(word in tool.name for word in forbidden), tool.name
