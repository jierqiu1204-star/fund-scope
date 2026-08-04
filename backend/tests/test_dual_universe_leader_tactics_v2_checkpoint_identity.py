from __future__ import annotations

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
)
from app.services.workflows.dual_universe_leader_tactics_v2 import (
    bind_v2_checkpoint_manifest,
)


def test_checkpoint_manifest_identity_is_bound_once() -> None:
    checkpoint = V2CollectorCheckpoint(
        cursor=None,
        batch_size=5,
        completed_codes=(),
        status="paused",
    )
    bound = bind_v2_checkpoint_manifest(checkpoint, manifest_hash="m" * 64)
    assert bound.manifest_hash == "m" * 64
    assert bind_v2_checkpoint_manifest(bound, manifest_hash="m" * 64) == bound
    with pytest.raises(ValueError, match="manifest identity"):
        bind_v2_checkpoint_manifest(bound, manifest_hash="x" * 64)
