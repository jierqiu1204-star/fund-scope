from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeVar

from app.services.tracked_positions.lifecycle import stable_contract_hash, stable_contract_json

from .checkpoint import (
    ReplayCheckpoint,
    ReplayRunContract,
    checkpoint_from_json,
    checkpoint_to_json,
)
from .features import AdjustedDailyInput, BoundedWorkLimitError, FeatureRow
from .replay import (
    CandidateReplayEvent,
    CrossSectionCompletionManifest,
    DailyRankingEvent,
    EquityCurvePoint,
    ReplayPolicyOutput,
    _manifest_payload,
    canonical_policy_input_hash,
)

_T = TypeVar("_T")


class ArtifactConflictError(ValueError):
    pass


class StaleCheckpointGenerationError(ValueError):
    pass


def _require_sha256(label: str, value: str) -> None:
    try:
        valid = len(value) == 64 and int(value, 16) >= 0
    except ValueError:
        valid = False
    if not valid:
        raise ArtifactConflictError(f"{label} must be a SHA-256 hash")


@dataclass(frozen=True)
class StoredReplayPage:
    manifests: tuple[CrossSectionCompletionManifest, ...]
    feature_rows: tuple[FeatureRow, ...]
    has_more: bool


@dataclass(frozen=True)
class StoredResearchArtifact:
    item_key: str
    artifact_hash: str
    payload: dict[str, Any]


def feature_artifact_identity(
    feature_rows: Sequence[FeatureRow],
    manifests: Sequence[CrossSectionCompletionManifest],
) -> tuple[str, tuple[str, ...]]:
    feature_keys = tuple(sorted(row.feature_key for row in feature_rows))
    manifest_hashes = tuple(sorted(row.manifest_hash for row in manifests))
    return (
        stable_contract_hash(
            {
                "features": tuple(
                    sorted(
                        (row.feature_key, stable_contract_hash(row))
                        for row in feature_rows
                    )
                ),
                "sealed_manifests": tuple(
                    sorted(
                        (row.manifest_hash, stable_contract_hash(row))
                        for row in manifests
                    )
                ),
            }
        ),
        tuple(sorted((*feature_keys, *manifest_hashes))),
    )


def replay_artifact_identity(
    rankings: Sequence[DailyRankingEvent],
    events: Sequence[CandidateReplayEvent],
    equity_curve: Sequence[EquityCurvePoint],
) -> tuple[str, tuple[str, ...]]:
    ordered_rankings = tuple(sorted(rankings, key=lambda row: row.session_date))
    if not ordered_rankings:
        raise ArtifactConflictError("replay commit requires at least one ranking")
    ranking_keys = tuple(
        f"ranking:{row.session_date.isoformat()}:{row.cross_section_hash}"
        for row in ordered_rankings
    )
    output_keys = tuple(
        sorted(
            (
                *ranking_keys,
                *(row.event_key for row in events),
                *(row.stable_key for row in equity_curve),
            )
        )
    )
    return (
        stable_contract_hash(
            {
                "rankings": tuple(stable_contract_hash(row) for row in ordered_rankings),
                "events": tuple(
                    sorted(
                        (row.event_key, stable_contract_hash(row)) for row in events
                    )
                ),
                "equity": tuple(
                    sorted(
                        (row.stable_key, stable_contract_hash(row))
                        for row in equity_curve
                    )
                ),
            }
        ),
        output_keys,
    )


class ReplayArtifactStore:
    """Run-local SQLite artifacts; no global application schema dependency."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._statement_count = 0
        self._rows_bound = 0
        self._initialize()
        self.reset_query_count()

    @property
    def query_count(self) -> int:
        return self._statement_count

    @property
    def statement_count(self) -> int:
        return self._statement_count

    @property
    def rows_bound(self) -> int:
        return self._rows_bound

    def reset_query_count(self) -> None:
        self._statement_count = 0
        self._rows_bound = 0

    @staticmethod
    def _deadline(max_seconds: float, label: str) -> float:
        if not 0 < max_seconds <= 55.0:
            raise BoundedWorkLimitError(f"{label} max_seconds must be within (0, 55]")
        return time.monotonic() + max_seconds

    @staticmethod
    def _check_deadline(deadline: float, label: str) -> None:
        if time.monotonic() >= deadline:
            raise BoundedWorkLimitError(f"{label} exceeds max_seconds")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _execute(
        self,
        connection: sqlite3.Connection,
        sql: str,
        parameters: Sequence[object] = (),
    ) -> sqlite3.Cursor:
        self._statement_count += 1
        return connection.execute(sql, parameters)

    def _executemany(
        self,
        connection: sqlite3.Connection,
        sql: str,
        rows: Sequence[Sequence[object]],
    ) -> sqlite3.Cursor:
        self._statement_count += 1
        self._rows_bound += len(rows)
        return connection.executemany(sql, rows)

    def _initialize(self) -> None:
        with self._connect() as connection:
            self._statement_count += 1
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS source_rows (
                    run_id TEXT NOT NULL,
                    asset_code TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, asset_code, session_date)
                );
                CREATE INDEX IF NOT EXISTS ix_source_rows_page
                    ON source_rows (run_id, asset_code, session_date);

                CREATE TABLE IF NOT EXISTS feature_rows (
                    run_id TEXT NOT NULL,
                    feature_key TEXT NOT NULL,
                    asset_code TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    feature_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, feature_key)
                );
                CREATE INDEX IF NOT EXISTS ix_feature_rows_day
                    ON feature_rows (run_id, session_date, asset_code);

                CREATE TABLE IF NOT EXISTS completion_manifests (
                    run_id TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, session_date)
                );

                CREATE TABLE IF NOT EXISTS replay_rankings (
                    run_id TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, session_date)
                );
                CREATE TABLE IF NOT EXISTS replay_events (
                    run_id TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, event_key)
                );
                CREATE TABLE IF NOT EXISTS replay_equity (
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, candidate_id, session_date)
                );
                CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    checkpoint_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, stage)
                );

                CREATE TABLE IF NOT EXISTS policy_output_registry (
                    run_id TEXT NOT NULL PRIMARY KEY,
                    policy_input_hash TEXT NOT NULL,
                    candidate_config_hash TEXT NOT NULL,
                    frozen_parameter_hash TEXT NOT NULL,
                    input_snapshot_hash TEXT NOT NULL,
                    row_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policy_outputs (
                    run_id TEXT NOT NULL,
                    policy_key TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, policy_key)
                );
                CREATE INDEX IF NOT EXISTS ix_policy_outputs_page
                    ON policy_outputs (run_id, session_date, candidate_id);

                CREATE TABLE IF NOT EXISTS validation_evidence_seals (
                    run_id TEXT NOT NULL PRIMARY KEY,
                    replay_checkpoint_hash TEXT NOT NULL,
                    input_snapshot_hash TEXT NOT NULL,
                    evidence_bundle_hash TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_artifacts (
                    run_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    artifact_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, phase, item_key)
                );
                CREATE INDEX IF NOT EXISTS ix_research_artifacts_page
                    ON research_artifacts (run_id, phase, item_key);
                """
            )

    @staticmethod
    def _immutable_upsert_sql(
        table: str,
        key_columns: tuple[str, ...],
        value_columns: tuple[str, ...],
        hash_column: str,
    ) -> str:
        columns = (*key_columns, *value_columns)
        placeholders = ",".join("?" for _ in columns)
        keys = ",".join(key_columns)
        assignments = ",".join(
            f"{column}=CASE WHEN {table}.{hash_column}=excluded.{hash_column} "
            f"THEN {table}.{column} ELSE NULL END"
            for column in value_columns
        )
        return (
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({keys}) DO UPDATE SET {assignments}"
        )

    def _assert_generation(
        self,
        connection: sqlite3.Connection,
        *,
        checkpoint: ReplayCheckpoint,
        expected_generation: int,
    ) -> None:
        if expected_generation < 0 or checkpoint.generation != expected_generation + 1:
            raise StaleCheckpointGenerationError(
                "checkpoint generation must equal expected_generation + 1"
            )
        row = self._execute(
            connection,
            """
            SELECT generation, payload_json FROM pipeline_checkpoints
            WHERE run_id=? AND stage=?
            """,
            (checkpoint.run_id, checkpoint.stage),
        ).fetchone()
        actual = int(row[0]) if row is not None else 0
        if actual != expected_generation:
            raise StaleCheckpointGenerationError(
                f"stale checkpoint generation: expected {expected_generation}, found {actual}"
            )
        if row is not None:
            existing = checkpoint_from_json(str(row[1]))
            existing_identity = (
                existing.run_id,
                existing.contract_hash,
                existing.input_snapshot_hash,
                existing.code_hash,
                existing.schema_hash,
                existing.candidate_config_hash,
                existing.frozen_parameter_hash,
                existing.policy_input_hash,
                existing.data_cutoff,
                existing.warmup_boundary,
                existing.candidate_ids,
            )
            new_identity = (
                checkpoint.run_id,
                checkpoint.contract_hash,
                checkpoint.input_snapshot_hash,
                checkpoint.code_hash,
                checkpoint.schema_hash,
                checkpoint.candidate_config_hash,
                checkpoint.frozen_parameter_hash,
                checkpoint.policy_input_hash,
                checkpoint.data_cutoff,
                checkpoint.warmup_boundary,
                checkpoint.candidate_ids,
            )
            if existing_identity != new_identity:
                raise ArtifactConflictError(
                    "checkpoint run contract is immutable across generations"
                )

    def _write_checkpoint(
        self,
        connection: sqlite3.Connection,
        checkpoint: ReplayCheckpoint,
    ) -> None:
        raw_json = checkpoint_to_json(checkpoint)
        checkpoint_from_json(raw_json)
        self._execute(
            connection,
            """
            INSERT INTO pipeline_checkpoints
                (run_id, stage, generation, checkpoint_hash, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (run_id, stage) DO UPDATE SET
                generation=excluded.generation,
                checkpoint_hash=excluded.checkpoint_hash,
                payload_json=excluded.payload_json
            """,
            (
                checkpoint.run_id,
                checkpoint.stage,
                checkpoint.generation,
                checkpoint.checkpoint_hash,
                raw_json,
            ),
        )

    @staticmethod
    def _bounded_tuple(
        rows: Iterable[_T],
        *,
        max_rows: int,
        label: str,
        deadline: float | None = None,
    ) -> tuple[_T, ...]:
        if max_rows < 1:
            raise BoundedWorkLimitError(f"{label} max_rows must be positive")
        output: list[_T] = []
        for row in rows:
            if deadline is not None and time.monotonic() >= deadline:
                raise BoundedWorkLimitError(f"{label} exceeds max_seconds")
            if len(output) >= max_rows:
                raise BoundedWorkLimitError(f"{label} exceeds max_rows")
            output.append(row)
        return tuple(output)

    @staticmethod
    def _source_from_json(raw_json: str) -> AdjustedDailyInput:
        payload = json.loads(raw_json)
        return AdjustedDailyInput(
            asset_code=str(payload["asset_code"]),
            session_date=date.fromisoformat(str(payload["session_date"])),
            raw_open=float(payload["raw_open"]),
            raw_high=float(payload["raw_high"]),
            raw_low=float(payload["raw_low"]),
            raw_close=float(payload["raw_close"]),
            volume=float(payload["volume"]),
            adjustment_factor=float(payload["adjustment_factor"]),
            adjusted_data_source=str(payload["adjusted_data_source"]),
            adjustment_kind=str(payload["adjustment_kind"]),
            decision_eligible=bool(payload["decision_eligible"]),
            provider_healthy=bool(payload["provider_healthy"]),
            fresh_at_cutoff=bool(payload["fresh_at_cutoff"]),
            known_at=datetime.fromisoformat(str(payload["known_at"])),
            source_cutoff=datetime.fromisoformat(str(payload["source_cutoff"])),
            suspended=bool(payload.get("suspended", False)),
            limit_locked=bool(payload.get("limit_locked", False)),
            demonstrably_tradable=bool(payload.get("demonstrably_tradable", False)),
            delisted=bool(payload.get("delisted", False)),
        )

    @staticmethod
    def _feature_from_json(raw_json: str) -> FeatureRow:
        payload = json.loads(raw_json)
        return FeatureRow(
            run_id=str(payload["run_id"]),
            feature_contract_hash=str(payload["feature_contract_hash"]),
            input_snapshot_hash=str(payload["input_snapshot_hash"]),
            asset_code=str(payload["asset_code"]),
            session_date=date.fromisoformat(str(payload["session_date"])),
            raw_open=float(payload["raw_open"]),
            raw_high=float(payload["raw_high"]),
            raw_low=float(payload["raw_low"]),
            raw_close=float(payload["raw_close"]),
            volume=float(payload["volume"]),
            adjusted_open=float(payload["adjusted_open"]),
            adjusted_high=float(payload["adjusted_high"]),
            adjusted_low=float(payload["adjusted_low"]),
            adjusted_close=float(payload["adjusted_close"]),
            momentum_return=float(payload["momentum_return"]),
            score=float(payload["score"]),
            warmup_sessions=int(payload["warmup_sessions"]),
            warmup_boundary=date.fromisoformat(str(payload["warmup_boundary"])),
            warmup_row_hashes=tuple(str(item) for item in payload["warmup_row_hashes"]),
            warmup_provenance_hash=str(payload["warmup_provenance_hash"]),
            adjustment_factor=float(payload["adjustment_factor"]),
            adjusted_data_source=str(payload["adjusted_data_source"]),
            adjustment_kind=str(payload["adjustment_kind"]),
            decision_eligible=bool(payload["decision_eligible"]),
            provider_healthy=bool(payload["provider_healthy"]),
            fresh_at_cutoff=bool(payload["fresh_at_cutoff"]),
            known_at=datetime.fromisoformat(str(payload["known_at"])),
            source_cutoff=datetime.fromisoformat(str(payload["source_cutoff"])),
            input_row_hash=str(payload["input_row_hash"]),
            suspended=bool(payload.get("suspended", False)),
            limit_locked=bool(payload.get("limit_locked", False)),
            demonstrably_tradable=bool(payload.get("demonstrably_tradable", False)),
            delisted=bool(payload.get("delisted", False)),
            feature_schema_version=str(payload["feature_schema_version"]),
        )

    @staticmethod
    def _manifest_from_json(raw_json: str) -> CrossSectionCompletionManifest:
        payload = json.loads(raw_json)
        return CrossSectionCompletionManifest(
            run_id=str(payload["run_id"]),
            feature_contract_hash=str(payload["feature_contract_hash"]),
            input_snapshot_hash=str(payload["input_snapshot_hash"]),
            session_date=date.fromisoformat(str(payload["session_date"])),
            eligible_asset_codes=tuple(
                str(item) for item in payload["eligible_asset_codes"]
            ),
            expected_universe_count=int(payload["expected_universe_count"]),
            canonical_membership_hash=str(payload["canonical_membership_hash"]),
            universe_snapshot_hash=str(payload["universe_snapshot_hash"]),
            feature_hashes=tuple(
                (str(item[0]), str(item[1])) for item in payload["feature_hashes"]
            ),
            manifest_hash=str(payload["manifest_hash"]),
        )

    @staticmethod
    def _policy_from_json(raw_json: str) -> ReplayPolicyOutput:
        payload = json.loads(raw_json)
        decision_eligible = payload["decision_eligible"]
        if not isinstance(decision_eligible, bool):
            raise ArtifactConflictError(
                "stored policy decision_eligible must be a JSON boolean"
            )
        return ReplayPolicyOutput(
            candidate_id=str(payload["candidate_id"]),
            candidate_config_hash=str(payload["candidate_config_hash"]),
            frozen_parameter_hash=str(payload["frozen_parameter_hash"]),
            input_snapshot_hash=str(payload["input_snapshot_hash"]),
            session_date=date.fromisoformat(str(payload["session_date"])),
            asset_code=str(payload["asset_code"]),
            rule_id=str(payload["rule_id"]),
            alert_episode_id=str(payload["alert_episode_id"]),
            action_cycle_id=str(payload["action_cycle_id"]),
            action_decision_id=str(payload["action_decision_id"]),
            target_remaining_fraction=float(payload["target_remaining_fraction"]),
            decision_eligible=decision_eligible,
        )

    @staticmethod
    def _assert_payload_hash(value: _T, expected_hash: object, label: str) -> _T:
        if stable_contract_hash(value) != str(expected_hash):
            raise ArtifactConflictError(f"{label} payload hash mismatch")
        return value

    @classmethod
    def _checked_source(cls, payload_hash: object, raw_json: object) -> AdjustedDailyInput:
        return cls._assert_payload_hash(
            cls._source_from_json(str(raw_json)),
            payload_hash,
            "source row",
        )

    @classmethod
    def _checked_feature(cls, feature_hash: object, raw_json: object) -> FeatureRow:
        return cls._assert_payload_hash(
            cls._feature_from_json(str(raw_json)),
            feature_hash,
            "feature row",
        )

    @classmethod
    def _checked_policy(cls, payload_hash: object, raw_json: object) -> ReplayPolicyOutput:
        return cls._assert_payload_hash(
            cls._policy_from_json(str(raw_json)),
            payload_hash,
            "policy row",
        )

    @classmethod
    def _checked_manifest(
        cls,
        manifest_hash: object,
        raw_json: object,
    ) -> CrossSectionCompletionManifest:
        manifest = cls._manifest_from_json(str(raw_json))
        if (
            manifest.manifest_hash != str(manifest_hash)
            or stable_contract_hash(_manifest_payload(manifest)) != manifest.manifest_hash
        ):
            raise ArtifactConflictError("manifest payload hash mismatch")
        return manifest

    def write_source_rows(
        self,
        *,
        run_id: str,
        rows: Iterable[AdjustedDailyInput],
        max_rows: int,
        max_seconds: float = 55.0,
    ) -> int:
        if not run_id.strip():
            raise ValueError("run_id is required")
        deadline = self._deadline(max_seconds, "source input")
        bounded = self._bounded_tuple(
            rows,
            max_rows=max_rows,
            label="source input",
            deadline=deadline,
        )
        values: list[tuple[object, ...]] = []
        for row in bounded:
            self._check_deadline(deadline, "source input serialization")
            values.append(
                (
                    run_id,
                    row.asset_code,
                    row.session_date.isoformat(),
                    stable_contract_hash(row),
                    stable_contract_json(row),
                )
            )
        connection = self._connect()
        try:
            self._check_deadline(deadline, "source input commit")
            self._execute(connection, "BEGIN IMMEDIATE")
            if values:
                self._executemany(
                    connection,
                    self._immutable_upsert_sql(
                        "source_rows",
                        ("run_id", "asset_code", "session_date"),
                        ("payload_hash", "payload_json"),
                        "payload_hash",
                    ),
                    values,
                )
            self._check_deadline(deadline, "source input commit")
            self._execute(connection, "COMMIT")
        except BaseException:
            try:
                self._execute(connection, "ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()
        return len(values)

    def read_source_page(
        self,
        *,
        run_id: str,
        asset_codes: tuple[str, ...],
        start_date: date,
        end_date: date,
        max_rows: int,
        max_seconds: float = 55.0,
    ) -> tuple[AdjustedDailyInput, ...]:
        if (
            max_rows < 1
            or not asset_codes
            or len(asset_codes) != len(set(asset_codes))
        ):
            raise BoundedWorkLimitError("source SQL page request is invalid")
        deadline = self._deadline(max_seconds, "source SQL page")
        placeholders = ",".join("?" for _ in asset_codes)
        parameters: tuple[object, ...] = (
            run_id,
            *asset_codes,
            start_date.isoformat(),
            end_date.isoformat(),
            max_rows + 1,
        )
        with self._connect() as connection:
            rows = self._execute(
                connection,
                f"""
                SELECT payload_hash, payload_json FROM source_rows
                WHERE run_id=? AND asset_code IN ({placeholders})
                  AND session_date>=? AND session_date<=?
                ORDER BY asset_code, session_date
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        self._check_deadline(deadline, "source SQL page")
        if len(rows) > max_rows:
            raise BoundedWorkLimitError("source SQL page exceeds max_rows")
        output: list[AdjustedDailyInput] = []
        for row in rows:
            self._check_deadline(deadline, "source SQL page decode")
            output.append(self._checked_source(row[0], row[1]))
        return tuple(output)

    def write_policy_outputs(
        self,
        *,
        run_id: str,
        outputs: Iterable[ReplayPolicyOutput],
        expected_policy_input_hash: str,
        candidate_config_hash: str,
        frozen_parameter_hash: str,
        input_snapshot_hash: str,
        max_rows: int,
        max_seconds: float = 55.0,
    ) -> int:
        identity = (
            run_id,
            expected_policy_input_hash,
            candidate_config_hash,
            frozen_parameter_hash,
            input_snapshot_hash,
        )
        if any(not value.strip() for value in identity):
            raise ArtifactConflictError("policy registry identity is required")
        deadline = self._deadline(max_seconds, "policy import")
        rows = self._bounded_tuple(
            outputs,
            max_rows=max_rows,
            label="policy import",
            deadline=deadline,
        )
        if any(
            row.candidate_config_hash != candidate_config_hash
            or row.frozen_parameter_hash != frozen_parameter_hash
            or row.input_snapshot_hash != input_snapshot_hash
            for row in rows
        ):
            raise ArtifactConflictError("policy output identity does not match registry")
        if canonical_policy_input_hash(rows) != expected_policy_input_hash:
            raise ArtifactConflictError("policy input hash does not match imported rows")
        self._check_deadline(deadline, "policy import")

        connection = self._connect()
        try:
            self._execute(connection, "BEGIN IMMEDIATE")
            existing = self._execute(
                connection,
                """
                SELECT policy_input_hash, candidate_config_hash,
                       frozen_parameter_hash, input_snapshot_hash, row_count
                FROM policy_output_registry WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            expected_registry = (
                expected_policy_input_hash,
                candidate_config_hash,
                frozen_parameter_hash,
                input_snapshot_hash,
                len(rows),
            )
            if existing is not None and tuple(existing) != expected_registry:
                raise ArtifactConflictError(
                    "policy registry is immutable for an existing run"
                )
            values = tuple(
                (
                    run_id,
                    row.policy_key,
                    row.candidate_id,
                    row.session_date.isoformat(),
                    stable_contract_hash(row),
                    stable_contract_json(row),
                )
                for row in rows
            )
            if values:
                self._executemany(
                    connection,
                    self._immutable_upsert_sql(
                        "policy_outputs",
                        ("run_id", "policy_key"),
                        (
                            "candidate_id",
                            "session_date",
                            "payload_hash",
                            "payload_json",
                        ),
                        "payload_hash",
                    ),
                    values,
                )
            if existing is None:
                self._execute(
                    connection,
                    """
                    INSERT INTO policy_output_registry
                        (run_id, policy_input_hash, candidate_config_hash,
                         frozen_parameter_hash, input_snapshot_hash, row_count)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, *expected_registry),
                )
            actual_count = int(
                self._execute(
                    connection,
                    "SELECT COUNT(*) FROM policy_outputs WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
            )
            if actual_count != len(rows):
                raise ArtifactConflictError("policy artifact set is incomplete or mixed")
            self._check_deadline(deadline, "policy import commit")
            self._execute(connection, "COMMIT")
        except BaseException:
            try:
                self._execute(connection, "ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()
        return len(rows)

    def read_policy_page(
        self,
        *,
        run_id: str,
        session_dates: tuple[date, ...],
        expected_policy_input_hash: str,
        candidate_config_hash: str,
        frozen_parameter_hash: str,
        input_snapshot_hash: str,
        max_rows: int,
        max_seconds: float = 55.0,
    ) -> tuple[ReplayPolicyOutput, ...]:
        if (
            max_rows < 1
            or not session_dates
            or tuple(sorted(set(session_dates))) != session_dates
        ):
            raise BoundedWorkLimitError("policy SQL page request is invalid")
        deadline = self._deadline(max_seconds, "policy SQL page")
        with self._connect() as connection:
            registry = self._execute(
                connection,
                """
                SELECT policy_input_hash, candidate_config_hash,
                       frozen_parameter_hash, input_snapshot_hash
                FROM policy_output_registry WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            expected = (
                expected_policy_input_hash,
                candidate_config_hash,
                frozen_parameter_hash,
                input_snapshot_hash,
            )
            if registry is None or tuple(registry) != expected:
                raise ArtifactConflictError(
                    "policy registry does not match immutable run contract"
                )
            placeholders = ",".join("?" for _ in session_dates)
            rows = self._execute(
                connection,
                f"""
                SELECT payload_hash, payload_json FROM policy_outputs
                WHERE run_id=? AND session_date IN ({placeholders})
                ORDER BY session_date, candidate_id, policy_key
                LIMIT ?
                """,
                (
                    run_id,
                    *(item.isoformat() for item in session_dates),
                    max_rows + 1,
                ),
            ).fetchall()
        self._check_deadline(deadline, "policy SQL page")
        if len(rows) > max_rows:
            raise BoundedWorkLimitError("policy SQL page exceeds max_rows")
        output: list[ReplayPolicyOutput] = []
        for row in rows:
            self._check_deadline(deadline, "policy SQL page decode")
            output.append(self._checked_policy(row[0], row[1]))
        return tuple(output)

    def commit_feature_batch(
        self,
        *,
        feature_rows: Iterable[FeatureRow],
        manifests: Iterable[CrossSectionCompletionManifest],
        checkpoint: ReplayCheckpoint,
        expected_generation: int,
        max_feature_rows: int,
        max_manifests: int,
        max_seconds: float = 55.0,
    ) -> None:
        if checkpoint.stage != "feature":
            raise ValueError("feature artifact commit requires a feature checkpoint")
        if not 0 < max_seconds <= 55.0:
            raise BoundedWorkLimitError("feature commit max_seconds must be within (0, 55]")
        deadline = time.monotonic() + max_seconds
        features = self._bounded_tuple(
            feature_rows,
            max_rows=max_feature_rows,
            label="feature commit",
            deadline=deadline,
        )
        manifest_rows = self._bounded_tuple(
            manifests,
            max_rows=max_manifests,
            label="manifest commit",
            deadline=deadline,
        )
        if any(row.run_id != checkpoint.run_id for row in features):
            raise ArtifactConflictError("feature run id does not match checkpoint")
        if any(row.run_id != checkpoint.run_id for row in manifest_rows):
            raise ArtifactConflictError("manifest run id does not match checkpoint")
        if any(
            row.input_snapshot_hash != checkpoint.input_snapshot_hash
            for row in features
        ) or any(
            row.input_snapshot_hash != checkpoint.input_snapshot_hash
            for row in manifest_rows
        ):
            raise ArtifactConflictError(
                "feature input snapshot does not match checkpoint"
            )
        if any(
            row.warmup_boundary < checkpoint.warmup_boundary for row in features
        ):
            raise ArtifactConflictError(
                "feature warmup boundary precedes checkpoint contract"
            )
        manifest_hash, output_keys = feature_artifact_identity(features, manifest_rows)
        self._check_deadline(deadline, "feature commit identity")
        if (
            checkpoint.manifest_hash != manifest_hash
            or checkpoint.output_keys != output_keys
        ):
            raise ArtifactConflictError(
                "feature checkpoint does not describe committed artifacts"
            )
        connection = self._connect()
        try:
            self._execute(connection, "BEGIN IMMEDIATE")
            self._assert_generation(
                connection,
                checkpoint=checkpoint,
                expected_generation=expected_generation,
            )
            feature_values = tuple(
                (
                    row.run_id,
                    row.feature_key,
                    row.asset_code,
                    row.session_date.isoformat(),
                    row.feature_hash,
                    stable_contract_json(row),
                )
                for row in features
            )
            self._check_deadline(deadline, "feature commit serialization")
            if feature_values:
                self._executemany(
                    connection,
                    self._immutable_upsert_sql(
                        "feature_rows",
                        ("run_id", "feature_key"),
                        (
                            "asset_code",
                            "session_date",
                            "feature_hash",
                            "payload_json",
                        ),
                        "feature_hash",
                    ),
                    feature_values,
                )
                self._check_deadline(deadline, "feature commit write")
            manifest_values = tuple(
                (
                    row.run_id,
                    row.session_date.isoformat(),
                    row.manifest_hash,
                    stable_contract_json(row),
                )
                for row in manifest_rows
            )
            self._check_deadline(deadline, "manifest commit serialization")
            if manifest_values:
                self._executemany(
                    connection,
                    self._immutable_upsert_sql(
                        "completion_manifests",
                        ("run_id", "session_date"),
                        ("manifest_hash", "payload_json"),
                        "manifest_hash",
                    ),
                    manifest_values,
                )
                self._check_deadline(deadline, "manifest commit write")
            self._write_checkpoint(connection, checkpoint)
            self._check_deadline(deadline, "feature checkpoint commit")
            self._execute(connection, "COMMIT")
        except BaseException:
            try:
                self._execute(connection, "ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def load_replay_page(
        self,
        *,
        run_id: str,
        start_date: date,
        end_date: date,
        after_date: date | None,
        max_dates: int,
        max_feature_rows: int,
        max_seconds: float = 55.0,
    ) -> StoredReplayPage:
        if max_dates < 1 or max_feature_rows < 1:
            raise BoundedWorkLimitError("replay SQL page bounds must be positive")
        deadline = self._deadline(max_seconds, "replay SQL page")
        clauses = ["run_id=?", "session_date>=?", "session_date<=?"]
        parameters: list[object] = [
            run_id,
            start_date.isoformat(),
            end_date.isoformat(),
        ]
        if after_date is not None:
            clauses.append("session_date>?")
            parameters.append(after_date.isoformat())
        where = " AND ".join(clauses)
        with self._connect() as connection:
            manifest_rows = self._execute(
                connection,
                f"""
                SELECT manifest_hash, payload_json FROM completion_manifests
                WHERE {where}
                ORDER BY session_date
                LIMIT ?
                """,
                (*parameters, max_dates),
            ).fetchall()
            self._check_deadline(deadline, "replay manifest SQL page")
            has_more = (
                self._execute(
                    connection,
                    f"""
                    SELECT 1 FROM completion_manifests
                    WHERE {where}
                    ORDER BY session_date
                    LIMIT 1 OFFSET ?
                    """,
                    (*parameters, max_dates),
                ).fetchone()
                is not None
            )
            manifests = tuple(
                self._checked_manifest(row[0], row[1]) for row in manifest_rows
            )
            if not manifests:
                return StoredReplayPage((), (), has_more)
            dates = tuple(item.session_date.isoformat() for item in manifests)
            placeholders = ",".join("?" for _ in dates)
            rows = self._execute(
                connection,
                f"""
                SELECT feature_hash, payload_json FROM feature_rows
                WHERE run_id=? AND session_date IN ({placeholders})
                ORDER BY session_date, asset_code
                LIMIT ?
                """,
                (run_id, *dates, max_feature_rows + 1),
            ).fetchall()
        self._check_deadline(deadline, "replay feature SQL page")
        if len(rows) > max_feature_rows:
            raise BoundedWorkLimitError("feature SQL page exceeds max_feature_rows")
        features_list: list[FeatureRow] = []
        for row in rows:
            self._check_deadline(deadline, "replay feature SQL page decode")
            features_list.append(self._checked_feature(row[0], row[1]))
        features = tuple(features_list)
        return StoredReplayPage(manifests, features, has_more)

    def read_feature_day(
        self,
        *,
        run_id: str,
        session_date: date,
        max_rows: int,
        max_seconds: float = 55.0,
    ) -> tuple[FeatureRow, ...]:
        if max_rows < 1:
            raise BoundedWorkLimitError("feature day max_rows must be positive")
        deadline = self._deadline(max_seconds, "feature day")
        with self._connect() as connection:
            rows = self._execute(
                connection,
                """
                SELECT feature_hash, payload_json FROM feature_rows
                WHERE run_id=? AND session_date=?
                ORDER BY asset_code
                LIMIT ?
                """,
                (run_id, session_date.isoformat(), max_rows + 1),
            ).fetchall()
        self._check_deadline(deadline, "feature day")
        if len(rows) > max_rows:
            raise BoundedWorkLimitError("feature day exceeds max_rows")
        output: list[FeatureRow] = []
        for row in rows:
            self._check_deadline(deadline, "feature day decode")
            output.append(self._checked_feature(row[0], row[1]))
        return tuple(output)

    def commit_replay_batch(
        self,
        *,
        rankings: Iterable[DailyRankingEvent],
        events: Iterable[CandidateReplayEvent],
        equity_curve: Iterable[EquityCurvePoint],
        checkpoint: ReplayCheckpoint,
        expected_generation: int,
        max_rankings: int,
        max_events: int,
        max_equity_rows: int,
        max_seconds: float = 55.0,
    ) -> None:
        if checkpoint.stage != "replay":
            raise ValueError("replay artifact commit requires a replay checkpoint")
        if not 0 < max_seconds <= 55.0:
            raise BoundedWorkLimitError("replay commit max_seconds must be within (0, 55]")
        deadline = time.monotonic() + max_seconds
        ranking_rows = self._bounded_tuple(
            rankings,
            max_rows=max_rankings,
            label="ranking commit",
            deadline=deadline,
        )
        event_rows = self._bounded_tuple(
            events,
            max_rows=max_events,
            label="event commit",
            deadline=deadline,
        )
        equity_rows = self._bounded_tuple(
            equity_curve,
            max_rows=max_equity_rows,
            label="equity commit",
            deadline=deadline,
        )
        if any(row.run_id != checkpoint.run_id for row in ranking_rows):
            raise ArtifactConflictError("ranking run id does not match checkpoint")
        if any(row.run_id != checkpoint.run_id for row in event_rows):
            raise ArtifactConflictError("event run id does not match checkpoint")
        if any(row.run_id != checkpoint.run_id for row in equity_rows):
            raise ArtifactConflictError("equity run id does not match checkpoint")
        candidate_rows = (*event_rows, *equity_rows)
        if any(
            row.candidate_id not in checkpoint.candidate_ids
            or row.candidate_config_hash != checkpoint.candidate_config_hash
            for row in candidate_rows
        ):
            raise ArtifactConflictError(
                "replay candidate config does not match checkpoint"
            )
        manifest_hash, output_keys = replay_artifact_identity(
            ranking_rows,
            event_rows,
            equity_rows,
        )
        self._check_deadline(deadline, "replay commit identity")
        if (
            checkpoint.manifest_hash != manifest_hash
            or checkpoint.output_keys != output_keys
        ):
            raise ArtifactConflictError(
                "replay checkpoint does not describe committed artifacts"
            )

        connection = self._connect()
        try:
            self._execute(connection, "BEGIN IMMEDIATE")
            self._assert_generation(
                connection,
                checkpoint=checkpoint,
                expected_generation=expected_generation,
            )
            ranking_values = tuple(
                (
                    row.run_id,
                    row.session_date.isoformat(),
                    stable_contract_hash(row),
                    stable_contract_json(row),
                )
                for row in ranking_rows
            )
            self._check_deadline(deadline, "ranking commit serialization")
            if ranking_values:
                self._executemany(
                    connection,
                    self._immutable_upsert_sql(
                        "replay_rankings",
                        ("run_id", "session_date"),
                        ("payload_hash", "payload_json"),
                        "payload_hash",
                    ),
                    ranking_values,
                )
                self._check_deadline(deadline, "ranking commit write")
            event_values = tuple(
                (
                    row.run_id,
                    row.event_key,
                    stable_contract_hash(row),
                    stable_contract_json(row),
                )
                for row in event_rows
            )
            self._check_deadline(deadline, "event commit serialization")
            if event_values:
                self._executemany(
                    connection,
                    self._immutable_upsert_sql(
                        "replay_events",
                        ("run_id", "event_key"),
                        ("payload_hash", "payload_json"),
                        "payload_hash",
                    ),
                    event_values,
                )
                self._check_deadline(deadline, "event commit write")
            equity_values = tuple(
                (
                    row.run_id,
                    row.candidate_id,
                    row.session_date.isoformat(),
                    stable_contract_hash(row),
                    stable_contract_json(row),
                )
                for row in equity_rows
            )
            self._check_deadline(deadline, "equity commit serialization")
            if equity_values:
                self._executemany(
                    connection,
                    self._immutable_upsert_sql(
                        "replay_equity",
                        ("run_id", "candidate_id", "session_date"),
                        ("payload_hash", "payload_json"),
                        "payload_hash",
                    ),
                    equity_values,
                )
                self._check_deadline(deadline, "equity commit write")
            self._write_checkpoint(connection, checkpoint)
            self._check_deadline(deadline, "replay checkpoint commit")
            self._execute(connection, "COMMIT")
        except BaseException:
            try:
                self._execute(connection, "ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def load_checkpoint(
        self,
        *,
        run_id: str,
        stage: str,
        expected_contract: ReplayRunContract,
        max_seconds: float = 55.0,
    ) -> ReplayCheckpoint | None:
        deadline = self._deadline(max_seconds, "checkpoint load")
        with self._connect() as connection:
            row = self._execute(
                connection,
                "SELECT payload_json FROM pipeline_checkpoints WHERE run_id=? AND stage=?",
                (run_id, stage),
            ).fetchone()
        self._check_deadline(deadline, "checkpoint load")
        if row is None:
            return None
        return checkpoint_from_json(str(row[0]), expected_contract=expected_contract)

    def seal_validation_evidence_bundle(
        self,
        *,
        run_id: str,
        expected_contract: ReplayRunContract,
        evidence_bundle_hash: str,
        max_seconds: float = 55.0,
    ) -> ReplayCheckpoint:
        """Persist one immutable Section 9 -> Section 10 content-hash handoff."""
        _require_sha256("validation evidence bundle hash", evidence_bundle_hash)
        deadline = self._deadline(max_seconds, "validation evidence seal")
        checkpoint = self.load_checkpoint(
            run_id=run_id,
            stage="replay",
            expected_contract=expected_contract,
            max_seconds=max_seconds,
        )
        self._check_deadline(deadline, "validation evidence seal")
        if checkpoint is None or not checkpoint.atomic_complete:
            raise ArtifactConflictError(
                "validation evidence requires an atomic replay checkpoint"
            )
        identity = (
            checkpoint.checkpoint_hash,
            checkpoint.input_snapshot_hash,
            evidence_bundle_hash,
        )
        connection = self._connect()
        try:
            self._execute(connection, "BEGIN IMMEDIATE")
            existing = self._execute(
                connection,
                """
                SELECT replay_checkpoint_hash, input_snapshot_hash,
                       evidence_bundle_hash
                FROM validation_evidence_seals WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            if existing is not None and tuple(existing) != identity:
                raise ArtifactConflictError(
                    "validation evidence seal is immutable for an existing replay run"
                )
            if existing is None:
                self._execute(
                    connection,
                    """
                    INSERT INTO validation_evidence_seals
                        (run_id, replay_checkpoint_hash, input_snapshot_hash,
                         evidence_bundle_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (run_id, *identity),
                )
            self._check_deadline(deadline, "validation evidence seal")
            self._execute(connection, "COMMIT")
        except BaseException:
            try:
                self._execute(connection, "ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()
        return checkpoint

    def require_validation_evidence_bundle(
        self,
        *,
        run_id: str,
        expected_contract: ReplayRunContract,
        evidence_bundle_hash: str,
        max_seconds: float = 55.0,
    ) -> ReplayCheckpoint:
        """Load and verify the immutable handoff without allowing an implicit seal."""
        _require_sha256("validation evidence bundle hash", evidence_bundle_hash)
        deadline = self._deadline(max_seconds, "validation evidence load")
        checkpoint = self.load_checkpoint(
            run_id=run_id,
            stage="replay",
            expected_contract=expected_contract,
            max_seconds=max_seconds,
        )
        self._check_deadline(deadline, "validation evidence load")
        if checkpoint is None or not checkpoint.atomic_complete:
            raise ArtifactConflictError(
                "validation evidence requires an atomic replay checkpoint"
            )
        with self._connect() as connection:
            row = self._execute(
                connection,
                """
                SELECT replay_checkpoint_hash, input_snapshot_hash,
                       evidence_bundle_hash
                FROM validation_evidence_seals WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
        self._check_deadline(deadline, "validation evidence load")
        expected = (
            checkpoint.checkpoint_hash,
            checkpoint.input_snapshot_hash,
            evidence_bundle_hash,
        )
        if row is None or tuple(row) != expected:
            raise ArtifactConflictError(
                "validation evidence bundle was not sealed by the replay artifact store"
            )
        return checkpoint

    def write_research_artifacts(
        self,
        *,
        run_id: str,
        phase: str,
        artifacts: Sequence[tuple[str, Mapping[str, Any]]],
        max_seconds: float = 5.0,
    ) -> tuple[StoredResearchArtifact, ...]:
        """Persist one immutable research page without coupling its payload shape."""

        if not run_id.strip() or not phase.strip():
            raise ArtifactConflictError("research artifact identity is incomplete")
        if not 1 <= len(artifacts) <= 20:
            raise BoundedWorkLimitError(
                "research artifact page must contain between 1 and 20 rows"
            )
        deadline = self._deadline(max_seconds, "research artifact write")
        normalized: list[StoredResearchArtifact] = []
        seen: set[str] = set()
        for item_key, payload in artifacts:
            self._check_deadline(deadline, "research artifact write")
            key = item_key.strip()
            if not key or key in seen:
                raise ArtifactConflictError(
                    "research artifact keys must be non-empty and unique per page"
                )
            seen.add(key)
            normalized_payload = dict(payload)
            artifact_hash = stable_contract_hash(
                {
                    "schema_version": "generic_research_artifact_v1",
                    "run_id": run_id,
                    "phase": phase,
                    "item_key": key,
                    "payload": normalized_payload,
                }
            )
            normalized.append(
                StoredResearchArtifact(
                    item_key=key,
                    artifact_hash=artifact_hash,
                    payload=normalized_payload,
                )
            )

        with self._connect() as connection:
            self._execute(connection, "BEGIN IMMEDIATE")
            try:
                for item in normalized:
                    self._check_deadline(deadline, "research artifact write")
                    existing = self._execute(
                        connection,
                        """
                        SELECT artifact_hash FROM research_artifacts
                        WHERE run_id=? AND phase=? AND item_key=?
                        """,
                        (run_id, phase, item.item_key),
                    ).fetchone()
                    if existing is not None:
                        if str(existing[0]) != item.artifact_hash:
                            raise ArtifactConflictError(
                                "research artifact identity already has a different payload"
                            )
                        continue
                    self._execute(
                        connection,
                        """
                        INSERT INTO research_artifacts
                            (run_id, phase, item_key, artifact_hash, payload_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            phase,
                            item.item_key,
                            item.artifact_hash,
                            stable_contract_json(item.payload),
                        ),
                    )
                self._execute(connection, "COMMIT")
            except Exception:
                self._execute(connection, "ROLLBACK")
                raise
        return tuple(normalized)

    def read_research_artifact_page(
        self,
        *,
        run_id: str,
        phase: str,
        after_item_key: str | None = None,
        max_rows: int = 20,
        max_seconds: float = 5.0,
    ) -> tuple[StoredResearchArtifact, ...]:
        if not 1 <= max_rows <= 20:
            raise BoundedWorkLimitError("research artifact page must be within 1..20")
        deadline = self._deadline(max_seconds, "research artifact read")
        with self._connect() as connection:
            rows = self._execute(
                connection,
                """
                SELECT item_key, artifact_hash, payload_json
                FROM research_artifacts
                WHERE run_id=? AND phase=? AND item_key>?
                ORDER BY item_key ASC
                LIMIT ?
                """,
                (run_id, phase, after_item_key or "", max_rows),
            ).fetchall()
        self._check_deadline(deadline, "research artifact read")
        return tuple(
            StoredResearchArtifact(
                item_key=str(row[0]),
                artifact_hash=str(row[1]),
                payload=dict(json.loads(str(row[2]))),
            )
            for row in rows
        )

    def research_phase_digest(
        self,
        *,
        run_id: str,
        phase: str,
        max_seconds: float = 5.0,
    ) -> tuple[str, int]:
        """Return a page-size-independent digest without loading payloads in memory."""

        deadline = self._deadline(max_seconds, "research artifact digest")
        digest = hashlib.sha256()
        digest.update(
            stable_contract_json(
                {
                    "schema_version": "research_phase_digest_v1",
                    "run_id": run_id,
                    "phase": phase,
                }
            ).encode("utf-8")
        )
        count = 0
        with self._connect() as connection:
            cursor = self._execute(
                connection,
                """
                SELECT item_key, artifact_hash FROM research_artifacts
                WHERE run_id=? AND phase=? ORDER BY item_key ASC
                """,
                (run_id, phase),
            )
            while True:
                self._check_deadline(deadline, "research artifact digest")
                rows = cursor.fetchmany(256)
                if not rows:
                    break
                for item_key, artifact_hash in rows:
                    digest.update(b"\n")
                    digest.update(
                        stable_contract_json(
                            {
                                "item_key": str(item_key),
                                "artifact_hash": str(artifact_hash),
                            }
                        ).encode("utf-8")
                    )
                    count += 1
        return digest.hexdigest(), count

    def artifact_counts(self, run_id: str) -> dict[str, int]:
        tables = {
            "policies": "policy_outputs",
            "features": "feature_rows",
            "manifests": "completion_manifests",
            "rankings": "replay_rankings",
            "events": "replay_events",
            "equity": "replay_equity",
            "checkpoints": "pipeline_checkpoints",
            "research": "research_artifacts",
        }
        counts: dict[str, int] = {}
        with self._connect() as connection:
            for label, table in tables.items():
                row = self._execute(
                    connection,
                    f"SELECT COUNT(*) FROM {table} WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                counts[label] = int(row[0])
        return counts
