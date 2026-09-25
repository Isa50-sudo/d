"""PermissionManager – entscheidet pro Tool: erlauben, bestätigen oder verbieten.

Auflösungsreihenfolge:
  1. Zugriffsstufe (READ_ONLY / LIMITED / FULL_ACCESS) liefert Grundrichtlinie
     abhängig vom Risiko des Tools.
  2. Tool-spezifischer Override aus den Settings ersetzt die Grundrichtlinie.
  3. Harte Regeln, die NICHT überschreibbar sind:
       * READ_ONLY verbietet alles außer lesenden Tools.
       * Kritische Tools und Tools mit always_confirm werden nie ohne
         Bestätigung ausgeführt ("allow" wird zu "confirm").
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from jarvis.config.user_settings import PermissionSettings
from jarvis.tools.base import Risk, Tool


class AccessLevel(str, enum.Enum):
    READ_ONLY = "READ_ONLY"
    LIMITED = "LIMITED"
    FULL_ACCESS = "FULL_ACCESS"


class Policy(str, enum.Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


_BASE: dict[AccessLevel, dict[Risk, Policy]] = {
    AccessLevel.READ_ONLY: {Risk.READ: Policy.ALLOW, Risk.WRITE: Policy.DENY, Risk.CRITICAL: Policy.DENY},
    AccessLevel.LIMITED: {Risk.READ: Policy.ALLOW, Risk.WRITE: Policy.ALLOW, Risk.CRITICAL: Policy.CONFIRM},
    AccessLevel.FULL_ACCESS: {Risk.READ: Policy.ALLOW, Risk.WRITE: Policy.ALLOW, Risk.CRITICAL: Policy.CONFIRM},
}


@dataclass(frozen=True)
class Decision:
    policy: Policy
    reason: str
    locked: bool  # True = durch harte Regel festgelegt


def resolve_policy(tool: Tool, settings: PermissionSettings) -> Decision:
    level = AccessLevel(settings.access_level)
    policy = _BASE[level][tool.risk]
    reason = f"Zugriffsstufe {level.value}"

    if level == AccessLevel.LIMITED and tool.risk == Risk.WRITE and tool.confirm_in_limited:
        policy = Policy.CONFIRM
        reason = "In Stufe LIMITED bestätigungspflichtig"

    override = settings.tool_overrides.get(tool.name)
    if override:
        policy = Policy(override)
        reason = "Benutzerdefinierte Tool-Regel"

    if level == AccessLevel.READ_ONLY and tool.risk != Risk.READ:
        return Decision(Policy.DENY, "Zugriffsstufe READ_ONLY erlaubt nur lesende Aktionen", True)

    if policy == Policy.ALLOW and (tool.risk == Risk.CRITICAL or tool.always_confirm):
        return Decision(Policy.CONFIRM, "Kritische Aktion – Bestätigung immer erforderlich", True)

    return Decision(policy, reason, False)


class PermissionManager:
    def __init__(self, get_settings) -> None:  # type: ignore[no-untyped-def]
        self._get_settings = get_settings

    def decide(self, tool: Tool) -> Decision:
        return resolve_policy(tool, self._get_settings().permissions)
