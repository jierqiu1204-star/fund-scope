"""Independent integration acceptance for historical V2 continuation."""

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, time
from types import SimpleNamespace

import pytest
from test_etf_route_comparison_acceptance import CODES, START, _calendar, _input

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab import etf_strategy_route_comparison_inputs as inputs
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BREAKOUT_V2,
    V2AdjustedBar,
    V2AssetInput,
    V2PITMembership,
    screen_dual_universe,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_etf_inputs import V2EtfInputBundle
from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
from app.services.strategy_lab.etf_strategy_route_comparison import (
    ROUTE_V2_BREAKOUT,
    run_comparison,
)


def _bundle(day):
    calendar = _calendar()
    days = tuple(value for value in calendar if value <= day)[-127:]
    items = []
    for index, code in enumerate(CODES):
        group = f"group-{index // 5}"
        membership = V2PITMembership(
            group_id=group,
            effective_from=START,
            effective_to=None,
            observed_at=datetime.combine(START, time(10)),
            mapping_kind="historical_pit",
            taxonomy_version="acceptance-v1",
            theme=group,
            sector=group,
            tracked_index=f"index-{code}",
            clone_group=f"index:{code}",
            issuer="fixture",
            fact_hash="",
        )
        membership = replace(
            membership, fact_hash=stable_contract_hash(membership.canonical_payload())
        )
        bars = []
        for session_day in days:
            ordinal = calendar.index(session_day)
            close = 100.0 + ordinal * 0.01
            if session_day >= START:
                close = 130.0 if index == 0 else 110.0 if index < 5 else 101.0
                if session_day > START and index == 0:
                    close = 132.0
                if session_day >= date(2026, 8, 28) and index == 0:
                    close = 100.0
            bars.append(
                V2AdjustedBar(
                    trade_date=session_day,
                    adjusted_open=close - 0.1,
                    adjusted_high=close + 0.2,
                    adjusted_low=close - 0.2,
                    adjusted_close=close,
                    volume=50000.0 if index == 0 else 1000.0,
                    amount=close * (50000.0 if index == 0 else 1000.0),
                    turnover=1.0,
                    observed_at=datetime.combine(session_day, time(10)),
                    provider="eastmoney",
                    adjustment_version="total-return-v1",
                    revision_id=f"fixture-{code}-{session_day}",
                )
            )
        items.append(
            V2AssetInput(
                universe="etf",
                asset_code=code,
                asset_name=f"ETF {code}",
                signal_date=day,
                source_cutoff=datetime.combine(day, time(11)),
                bars=tuple(bars),
                membership=membership,
            )
        )
    return V2EtfInputBundle(
        signal_date=day,
        source_cutoff=datetime.combine(day, time(11)),
        identity_cutoff=datetime.combine(day, time(11)),
        inputs=tuple(items),
        universe_count=10,
        adjusted_120_count=10,
        adjusted_180_count=0,
        pit_group_count=10,
        provider_health=(),
        exclusions=(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_after_first_day", [False, True])
async def test_rebuild_continues_original_signal_and_reuses_identical_artifacts(
    tmp_path, monkeypatch, resume_after_first_day
):
    core_input = _input(end=date(2026, 8, 31))
    snapshots = tuple(
        SimpleNamespace(
            signal_date=snapshot.signal_date,
            decision_cutoff=snapshot.decision_cutoff,
            eligible_asset_codes=snapshot.eligible_asset_codes,
            core_snapshot=snapshot,
            source_hash=snapshot.source_hash,
            history_eligible_asset_codes=CODES,
            ready_for_signal_filters=True,
        )
        for snapshot in core_input.decision_snapshots
    )
    first_screen = screen_dual_universe(_bundle(START).inputs)
    assert any(
        row.asset_code == CODES[0] and row.formula_id == BREAKOUT_V2 and row.qualifies
        for row in first_screen.observations
    ), [
        (row.formula_id, row.exclusion_reasons)
        for row in first_screen.observations
        if row.asset_code == CODES[0]
    ]

    async def no_manifest(*args, **kwargs):
        return None

    async def load_bundle(session, *, snapshot, **kwargs):
        return _bundle(snapshot.signal_date)

    async def lease(*args, **kwargs):
        return datetime.now(UTC)

    async def release(*args, **kwargs):
        return None

    monkeypatch.setattr(inputs, "_read_v2_manifest", no_manifest)
    monkeypatch.setattr(inputs, "load_v2_comparison_inputs", load_bundle)
    monkeypatch.setattr(inputs, "acquire_v2_global_run_lease", lease)
    monkeypatch.setattr(inputs, "release_v2_global_run_lease", release)
    path = tmp_path / "research.sqlite3"
    store = ReplayArtifactStore(path)
    kwargs = dict(
        snapshots=snapshots,
        research_store=path,
        run_id="etf-strategy-route-comparison-acceptance",
        lease_owner="independent-acceptance",
    )
    skipped = await inputs.prepare_v2_day_checks(
        object(), **{**kwargs, "snapshots": (snapshots[0], snapshots[2])}
    )
    assert skipped.status == "blocked", "Missing middle sessions must be rejected before sealing."
    if resume_after_first_day:
        partial = await inputs.prepare_v2_day_checks(
            object(), **{**kwargs, "snapshots": snapshots[:1]}
        )
        assert partial.status == "complete", partial.reasons
    actual_computation_start = datetime.now(UTC)
    prepared = await inputs.prepare_v2_day_checks(object(), **kwargs)
    assert prepared.status == "complete", prepared.reasons
    evidence = await inputs.read_v2_day_evidence(
        object(),
        snapshot=snapshots[1].core_snapshot,
        research_store=path,
        run_id=kwargs["run_id"],
    )
    assert evidence.available, evidence.reason
    assert evidence.computed_at >= actual_computation_start
    assert any(
        event.asset_code == CODES[0]
        and event.event_type == "confirmation"
        and event.original_signal_date == START
        for event in evidence.core_events
    )
    day_evidence = tuple(
        [
            await inputs.read_v2_day_evidence(
                object(),
                snapshot=snapshot.core_snapshot,
                research_store=path,
                run_id=kwargs["run_id"],
            )
            for snapshot in snapshots
        ]
    )
    assert all(item.available for item in day_evidence), [item.reason for item in day_evidence]
    assert all(
        event.signal_date == item.signal_date for item in day_evidence for event in item.core_events
    ), "Continuation must not re-emit a prior date's confirmation."
    comparison = run_comparison(
        replace(
            core_input,
            v2_events=tuple(event for item in day_evidence for event in item.core_events),
            v2_state_checks=tuple(item.core_state_check for item in day_evidence),
        )
    )
    route = next(item for item in comparison.routes if item.route_id == ROUTE_V2_BREAKOUT)
    assert route.status == "completed", route.reason
    ledger = route.base_ledger
    assert ledger is not None
    assert not ledger.points[0].net_holdings and not ledger.points[1].net_holdings
    assert CODES[0] in dict(ledger.points[2].net_holdings)
    assert ledger.points[2].order_count > 0
    assert ledger.points[2].transaction_cost > 0
    assert CODES[0] not in dict(ledger.points[3].net_holdings)
    assert ledger.points[3].transaction_cost > 0
    digest_before = store.research_phase_digest(
        run_id=kwargs["run_id"], phase=inputs.V2_DAY_CHECK_PHASE
    )
    repeated = await inputs.prepare_v2_day_checks(object(), **kwargs)
    assert repeated.status == "complete", repeated.reasons
    assert (
        store.research_phase_digest(run_id=kwargs["run_id"], phase=inputs.V2_DAY_CHECK_PHASE)
        == digest_before
    )
    # Rehashing malformed contents must not turn an incomplete prior screen
    # into a valid lifecycle proof for a later holding.
    first_key = f"v2-day-check:{snapshots[0].signal_date.isoformat()}"
    with sqlite3.connect(path) as connection:
        original_payload, original_hash = connection.execute(
            "SELECT payload_json, artifact_hash FROM research_artifacts "
            "WHERE run_id = ? AND phase = ? AND item_key = ?",
            (kwargs["run_id"], inputs.V2_DAY_CHECK_PHASE, first_key),
        ).fetchone()
        forged = json.loads(original_payload)
        forged["required_observation_keys"] = []
        forged_hash = inputs._artifact_hash(
            run_id=kwargs["run_id"],
            phase=inputs.V2_DAY_CHECK_PHASE,
            item_key=first_key,
            payload=forged,
        )
        connection.execute(
            "UPDATE research_artifacts SET payload_json = ?, artifact_hash = ? "
            "WHERE run_id = ? AND phase = ? AND item_key = ?",
            (
                json.dumps(forged),
                forged_hash,
                kwargs["run_id"],
                inputs.V2_DAY_CHECK_PHASE,
                first_key,
            ),
        )
    invalid_proof = await inputs.read_v2_day_evidence(
        object(),
        snapshot=snapshots[0].core_snapshot,
        research_store=path,
        run_id=kwargs["run_id"],
    )
    assert not invalid_proof.available
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE research_artifacts SET payload_json = ?, artifact_hash = ? "
            "WHERE run_id = ? AND phase = ? AND item_key = ?",
            (
                original_payload,
                original_hash,
                kwargs["run_id"],
                inputs.V2_DAY_CHECK_PHASE,
                first_key,
            ),
        )
    # A valid outer artifact hash cannot substitute for the missing prior
    # lifecycle record on which the held position's continuation depends.
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM research_artifacts WHERE run_id = ? AND phase = ? AND item_key = ?",
            (
                kwargs["run_id"],
                inputs.V2_DAY_CHECK_PHASE,
                f"v2-day-check:{snapshots[1].signal_date.isoformat()}",
            ),
        )
    broken_chain = await inputs.read_v2_day_evidence(
        object(),
        snapshot=snapshots[2].core_snapshot,
        research_store=path,
        run_id=kwargs["run_id"],
    )
    assert not broken_chain.available, "A missing intermediate seal must block continuation."
