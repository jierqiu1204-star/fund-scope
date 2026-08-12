from __future__ import annotations

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import V2ContractError
from app.services.strategy_lab.dual_universe_leader_tactics_v2_boundary import (
    assert_v2_research_payload,
    assert_v2_research_table,
)


def test_v2_boundary_allows_only_research_tables() -> None:
    assert_v2_research_table("leader_tactics_v2_candidate_observations")
    assert_v2_research_table("ashare_adjusted_price_facts")
    assert_v2_research_table("ashare_fine_theme_membership_facts")
    with pytest.raises(V2ContractError):
        assert_v2_research_table("etf_ranking_snapshots")
    with pytest.raises(V2ContractError):
        assert_v2_research_table("leader_tactics_v2_notification_log")


def test_v2_boundary_rejects_live_provenance() -> None:
    assert_v2_research_payload(
        {
            "research_only": True,
            "production_mutation_allowed": False,
            "notification_provenance": "none",
            "execution_provenance": "none",
        }
    )
    with pytest.raises(V2ContractError):
        assert_v2_research_payload(
            {
                "research_only": True,
                "production_mutation_allowed": True,
                "notification_provenance": "none",
                "execution_provenance": "none",
            }
        )
