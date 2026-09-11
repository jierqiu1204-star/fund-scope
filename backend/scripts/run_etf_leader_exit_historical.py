"""Bounded, read-only historical leader-exit dataset freeze and comparison."""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import os
import re
import time
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass, replace
from datetime import UTC, date, datetime
from datetime import time as clock_time
from pathlib import Path
from queue import Empty
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.services.strategy_lab.etf_leader_exit_historical import (
    run_historical_v2_exit_comparison,
)
from app.services.strategy_lab.etf_leader_exit_historical_loader import (
    DATASET_NAMESPACE,
    FORMAL_END,
    FORMAL_START,
    dataset_from_json,
    dataset_to_json,
    load_historical_v2_dataset,
)
from app.services.strategy_lab.etf_strategy_route_comparison import LEADER_EXIT_POLICY_IDS

START_BOUND = FORMAL_START
END_BOUND = FORMAL_END
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--compare", action="store_true")
    mode.add_argument("--prepare", action="store_true")
    parser.add_argument("--start-date", type=date.fromisoformat, default=START_BOUND)
    parser.add_argument("--end-date", type=date.fromisoformat, default=END_BOUND)
    parser.add_argument("--frozen-at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=55.0)
    parser.add_argument("--cursor")
    parser.add_argument("--page-size", type=int, default=16)
    return parser.parse_args(argv)


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    return value


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    message = re.sub(r"(?i)(://)[^/@\s]+:[^/@\s]+@", r"\1***:***@", message)
    message = re.sub(r"(?i)(password|passwd|secret|token)=([^\s,;]+)", r"\1=***", message)
    return message[:500] or type(exc).__name__


def _manifest(dataset: Any, key: str) -> dict[str, object]:
    for item_key, value in dataset.source_manifest:
        if item_key == key and isinstance(value, dict):
            return value
    return {}


def _same_instant(left: datetime, right: datetime) -> bool:
    return left.astimezone(UTC) == right.astimezone(UTC)


def _validate_config(dataset: Any, arguments: argparse.Namespace) -> None:
    config = _manifest(dataset, "__config__")
    if dataset.start_date != arguments.start_date or dataset.end_date != arguments.end_date:
        raise ValueError("artifact dataset date range does not match requested parameters")
    expected = {
        "namespace": DATASET_NAMESPACE,
        "start_date": arguments.start_date.isoformat(),
        "end_date": arguments.end_date.isoformat(),
        "page_size": arguments.page_size,
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("artifact configuration does not match requested parameters")
    manifest_frozen = config.get("frozen_at")
    if not isinstance(manifest_frozen, str):
        raise ValueError("artifact manifest frozen-at is missing")
    try:
        manifest_frozen_at = datetime.fromisoformat(manifest_frozen)
    except ValueError as exc:
        raise ValueError("artifact manifest frozen-at is invalid") from exc
    if manifest_frozen_at.tzinfo is None or manifest_frozen_at.utcoffset() is None:
        raise ValueError("artifact manifest frozen-at must include a timezone")
    if not _same_instant(dataset.frozen_at, manifest_frozen_at):
        raise ValueError("artifact manifest frozen-at does not match dataset")
    if not _same_instant(dataset.frozen_at, arguments.frozen_at):
        raise ValueError("artifact frozen-at does not match requested parameters")


def _is_complete(dataset: Any) -> bool:
    pagination = _manifest(dataset, "__pagination__")
    universe = _manifest(dataset, "__universe__")
    if not pagination.get("load_complete") or pagination.get("has_more"):
        return False
    codes = tuple(universe.get("codes", ()))
    if len(codes) != len(set(codes)) or not codes:
        return False
    if universe.get("tradable_count") != len(codes):
        return False
    if (
        pagination.get("last_code") != codes[-1]
        or pagination.get("next_cursor") is not None
    ):
        return False
    code_set = set(codes)
    asset_codes = tuple(item.asset_code for item in dataset.assets)
    if len(asset_codes) != len(set(asset_codes)) or not set(asset_codes) <= code_set:
        return False
    exclusion_codes = tuple(code for code, _reason in dataset.exclusions if code in code_set)
    if len(exclusion_codes) != len(set(exclusion_codes)):
        return False
    if any(code not in code_set for code, _reason in dataset.exclusions):
        return False
    if set(asset_codes) & set(exclusion_codes):
        return False
    return set(asset_codes) | set(exclusion_codes) == code_set


def _status(dataset: Any, *, mode: str) -> str:
    if not dataset.assets:
        return "blocked"
    if mode == "compare" and not _is_complete(dataset):
        return "partial"
    if not _is_complete(dataset) or dataset.exclusions:
        return "partial"
    return "ready"


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def _merge_page(previous: Any, page: Any, *, cursor: str) -> Any:
    _validate_config(page, argparse.Namespace(
        start_date=previous.start_date,
        end_date=previous.end_date,
        page_size=_manifest(previous, "__config__").get("page_size", 16),
        frozen_at=previous.frozen_at,
    ))
    previous_pagination = _manifest(previous, "__pagination__")
    page_pagination = _manifest(page, "__pagination__")
    if not previous_pagination.get("has_more") or previous_pagination.get("next_cursor") != cursor:
        raise ValueError("cursor is not the next frozen continuation")
    if page_pagination.get("cursor") != cursor:
        raise ValueError("continuation page cursor does not match the request")
    if tuple(page.trading_sessions) != tuple(previous.trading_sessions):
        raise ValueError("continuation calendar does not match frozen dataset")
    previous_universe = _manifest(previous, "__universe__")
    page_universe = _manifest(page, "__universe__")
    if tuple(page_universe.get("codes", ())) != tuple(previous_universe.get("codes", ())):
        raise ValueError("continuation universe does not match frozen dataset")
    if page_universe != previous_universe:
        raise ValueError("continuation universe denominator does not match frozen dataset")
    previous_processed = {item.asset_code for item in previous.assets} | {
        code for code, _reason in previous.exclusions
    }
    page_processed = {item.asset_code for item in page.assets} | {
        code for code, _reason in page.exclusions
    }
    if previous_processed & page_processed:
        raise ValueError("continuation page repeats a frozen code")
    assets = {item.asset_code: item for item in previous.assets}
    for item in page.assets:
        assets[item.asset_code] = item
    exclusions = tuple(sorted(set((*previous.exclusions, *page.exclusions))))
    manifest = {key: value for key, value in previous.source_manifest}
    manifest.update({key: value for key, value in page.source_manifest})
    return replace(
        previous,
        assets=tuple(sorted(assets.values(), key=lambda item: item.asset_code)),
        exclusions=exclusions,
        source_manifest=tuple(sorted(manifest.items(), key=lambda item: item[0])),
        source_hash="",
    )


def _comparison_worker(artifact: str, result_queue: Any) -> None:
    try:
        dataset = dataset_from_json(Path(artifact).read_text(encoding="utf-8"))
        comparison = run_historical_v2_exit_comparison(dataset, policies=LEADER_EXIT_POLICY_IDS)
        result_queue.put({"comparison": _json_safe(comparison)})
    except Exception as exc:  # noqa: BLE001
        result_queue.put({"error": _safe_error(exc)})


def _compare_with_timeout(path: Path, timeout: float) -> dict[str, object]:
    context = multiprocessing.get_context("fork")
    result_queue = context.Queue()
    worker = context.Process(target=_comparison_worker, args=(str(path), result_queue))
    worker.start()
    deadline = time.monotonic() + timeout
    result: dict[str, object] | None = None
    while worker.is_alive() and result is None:
        try:
            result = result_queue.get(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
        except Empty:
            if time.monotonic() >= deadline:
                worker.terminate()
                worker.join(2)
                if worker.is_alive():
                    worker.kill()
                    worker.join(1)
                if worker.is_alive():
                    raise RuntimeError("comparison worker could not be terminated") from None
                raise TimeoutError("comparison exceeded max-seconds") from None
    worker.join(1)
    if worker.is_alive():
        worker.terminate()
        worker.join(1)
        if worker.is_alive():
            worker.kill()
            worker.join(1)
        if worker.is_alive():
            raise RuntimeError("comparison worker could not be terminated") from None
    if result is None:
        try:
            result = result_queue.get(timeout=1)
        except Empty as exc:
            raise RuntimeError("comparison worker exited without a result") from exc
    if result.get("error"):
        raise ValueError(str(result["error"]))
    return result


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    started = time.monotonic()
    if not START_BOUND <= arguments.start_date <= arguments.end_date <= END_BOUND:
        raise ValueError("historical window must remain within 2026-07-15..2026-09-10")
    if arguments.frozen_at.tzinfo is None or arguments.frozen_at.utcoffset() is None:
        raise ValueError("frozen-at must include a timezone")
    frozen_utc = arguments.frozen_at.astimezone(UTC)
    if frozen_utc > datetime.now(UTC):
        raise ValueError("frozen-at must not be in the future")
    formal_close = datetime.combine(arguments.end_date, clock_time(15, 0), tzinfo=SHANGHAI)
    if frozen_utc < formal_close.astimezone(UTC):
        raise ValueError("formal end date has not closed at frozen-at")
    if arguments.end_date >= datetime.now(SHANGHAI).date():
        raise ValueError("formal end date must be a closed trading date")
    if not 0 < arguments.max_seconds <= 55:
        raise ValueError("max-seconds must be within (0, 55]")
    if not 1 <= arguments.page_size <= 16:
        raise ValueError("page-size must be between 1 and 16")
    mode = "preflight" if arguments.preflight else "compare" if arguments.compare else "prepare"
    if arguments.cursor and mode != "prepare":
        raise ValueError("cursor is only valid for prepare continuation")
    if mode == "prepare" and arguments.cursor and not arguments.artifact.exists():
        raise ValueError("cursor continuation requires an existing frozen artifact")
    if mode == "compare":
        if arguments.cursor or not arguments.artifact.exists():
            raise ValueError("compare requires an existing frozen artifact without cursor")
        dataset = dataset_from_json(arguments.artifact.read_text(encoding="utf-8"))
        _validate_config(dataset, arguments)
        if not _is_complete(dataset):
            raise ValueError("cannot compare an incomplete frozen dataset")
    elif mode == "preflight" and arguments.artifact.exists() and not arguments.cursor:
        dataset = dataset_from_json(arguments.artifact.read_text(encoding="utf-8"))
        _validate_config(dataset, arguments)
    else:
        if mode == "prepare" and arguments.artifact.exists() and not arguments.cursor:
            raise ValueError("artifact exists; pass the frozen continuation cursor to continue")
        database = DatabaseManager(get_settings().database_url)
        try:
            remaining = arguments.max_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("max-seconds exhausted before database load")
            async with database.session() as session:
                dataset = await asyncio.wait_for(
                    load_historical_v2_dataset(
                        session,
                        frozen_at=arguments.frozen_at,
                        start_date=arguments.start_date,
                        end_date=arguments.end_date,
                        cursor=arguments.cursor,
                        page_size=arguments.page_size,
                    ),
                    timeout=remaining,
                )
        finally:
            await database.engine.dispose()
        if arguments.cursor:
            previous = dataset_from_json(arguments.artifact.read_text(encoding="utf-8"))
            _validate_config(previous, arguments)
            dataset = _merge_page(previous, dataset, cursor=arguments.cursor)
        if mode == "prepare":
            _atomic_write(arguments.artifact, dataset_to_json(dataset))
    result: dict[str, object] = {
        "status": _status(dataset, mode=mode),
        "mode": mode,
        "namespace": DATASET_NAMESPACE,
        "dataset_hash": dataset.source_hash,
        "asset_count": len(dataset.assets),
        "exclusion_count": len(dataset.exclusions),
        "exclusions": dataset.exclusions,
        "artifact": str(arguments.artifact),
        "pagination": _manifest(dataset, "__pagination__"),
        "universe": _manifest(dataset, "__universe__"),
        "research_only": True,
    }
    if mode == "compare":
        remaining = arguments.max_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("max-seconds exhausted before comparison")
        result.update(_compare_with_timeout(arguments.artifact, remaining))
    return result


def main(argv: list[str] | None = None) -> None:
    arguments = _arguments(argv)
    try:
        result = asyncio.run(_run(arguments))
    except Exception as exc:  # noqa: BLE001
        result = {"status": "blocked", "research_only": True, "reason": _safe_error(exc)}
    print(json.dumps(_json_safe(result), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
