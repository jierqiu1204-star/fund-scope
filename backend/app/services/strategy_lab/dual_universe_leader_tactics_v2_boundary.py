"""Fail-closed boundary checks for V2 research-only execution."""

from __future__ import annotations

from collections.abc import Mapping

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import V2ContractError

V2_RESEARCH_TABLE_PREFIXES = (
    "leader_tactics_v2_",
    "ashare_research_",
    "ashare_fine_theme_",
    "ashare_theme_",
    "ashare_adjusted_",
)
FORBIDDEN_PRODUCTION_TABLE_MARKERS = (
    "ranking",
    "allocation",
    "position",
    "alert",
    "notification",
    "smtp",
    "execution",
)


def assert_v2_research_table(table_name: str) -> None:
    normalized = table_name.strip().lower()
    if not normalized.startswith(V2_RESEARCH_TABLE_PREFIXES):
        raise V2ContractError("V2 research path cannot write outside its research namespace")
    if any(marker in normalized for marker in FORBIDDEN_PRODUCTION_TABLE_MARKERS):
        raise V2ContractError("V2 research path cannot write production policy state")


def assert_v2_research_payload(payload: Mapping[str, object]) -> None:
    if payload.get("research_only") is not True:
        raise V2ContractError("V2 payload must remain research-only")
    if payload.get("production_mutation_allowed") is not False:
        raise V2ContractError("V2 payload cannot authorize production mutation")
    if payload.get("notification_provenance") not in {None, "none", "simulated"}:
        raise V2ContractError("V2 payload cannot claim live notification provenance")
    if payload.get("execution_provenance") not in {None, "none", "simulated"}:
        raise V2ContractError("V2 payload cannot claim live execution provenance")


__all__ = ["assert_v2_research_payload", "assert_v2_research_table"]
