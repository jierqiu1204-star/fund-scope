"""Bounded subprocess for one factual Eastmoney concept constituent set."""

from __future__ import annotations

import json
import signal
import sys
from dataclasses import dataclass

MAX_SUBPROCESS_SECONDS = 15
MAX_OUTPUT_BYTES = 512_000
MAX_MEMBERSHIPS = 2_000


@dataclass(frozen=True, slots=True)
class RegisteredFineThemeSource:
    """One provider label for a registered normalized fine-theme family."""

    provider_label: str
    normalized_theme_key: str
    canonical_label: str
    provider_symbol: str | None = None


# Ordered and append-only: resumable callers may persist provider_label.
REGISTERED_FINE_THEME_SOURCES = (
    RegisteredFineThemeSource("创新药", "innovation_drug", "创新药", "BK1106"),
    RegisteredFineThemeSource("稀土", "rare_earth", "稀土/稀土永磁"),
    RegisteredFineThemeSource("稀土永磁", "rare_earth", "稀土/稀土永磁"),
    RegisteredFineThemeSource("被动元件概念", "passive_components", "被动元件/MLCC"),
    RegisteredFineThemeSource("MLCC", "passive_components", "被动元件/MLCC"),
    RegisteredFineThemeSource("液冷服务器", "liquid_cooling", "液冷", "BK1138"),
)
REGISTERED_FINE_THEME_KEYS = tuple(
    dict.fromkeys(source.normalized_theme_key for source in REGISTERED_FINE_THEME_SOURCES)
)
REGISTERED_FINE_THEME_LABELS = tuple(
    source.provider_label for source in REGISTERED_FINE_THEME_SOURCES
)


def registered_fine_theme_sources() -> tuple[RegisteredFineThemeSource, ...]:
    return REGISTERED_FINE_THEME_SOURCES


def registered_fine_theme_keys() -> tuple[str, ...]:
    return REGISTERED_FINE_THEME_KEYS


def resolve_registered_fine_theme_source(label: str) -> RegisteredFineThemeSource:
    compact = "".join(str(label).split())
    for source in REGISTERED_FINE_THEME_SOURCES:
        if source.provider_label == compact:
            return source
    raise ValueError("fine_theme_source_not_registered")


def _start_timeout_alarm() -> bool:
    if not hasattr(signal, "SIGALRM"):
        return False

    def _timeout_handler(_signum: int, _frame: object) -> None:
        raise TimeoutError("fine_theme_provider_timeout")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(MAX_SUBPROCESS_SECONDS)
    return True


def _stop_timeout_alarm(installed: bool) -> None:
    if installed:
        signal.alarm(0)


def _emit_membership(payload: dict[str, str], emitted_bytes: int) -> int:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    size = len(encoded.encode("utf-8")) + 1
    if emitted_bytes + size > MAX_OUTPUT_BYTES:
        raise RuntimeError("fine_theme_provider_response_too_large")
    print(encoded, flush=True)
    return emitted_bytes + size


def main() -> int:
    themes = tuple(item.strip() for item in sys.argv[1:] if item.strip())
    if len(themes) != 1:
        print("fine_theme_source_requires_exactly_one", file=sys.stderr)
        return 2
    try:
        source = resolve_registered_fine_theme_source(themes[0])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    import akshare as ak  # isolated so a stuck provider can be killed by the parent

    emitted = 0
    emitted_bytes = 0
    seen_codes: set[str] = set()
    alarm_installed = _start_timeout_alarm()
    try:
        try:
            frame = ak.stock_board_concept_cons_em(
                symbol=source.provider_symbol or source.provider_label
            )
        except Exception as exc:
            print(
                f"fine_theme_provider_failed:{source.provider_label}:"
                f"{type(exc).__name__}:{str(exc)[:120]}",
                file=sys.stderr,
            )
            return 1
        code_column = next(
            (name for name in ("代码", "证券代码") if name in frame.columns),
            None,
        )
        if code_column is None:
            print(
                f"fine_theme_provider_failed:{source.provider_label}:concept_code_column_missing",
                file=sys.stderr,
            )
            return 1
        for raw_code in frame[code_column].tolist():
            code = str(raw_code).strip().split(".")[0].zfill(6)
            if len(code) != 6 or not code.isdigit() or code in seen_codes:
                continue
            seen_codes.add(code)
            emitted += 1
            if emitted > MAX_MEMBERSHIPS:
                raise RuntimeError("fine_theme_provider_response_too_large")
            emitted_bytes = _emit_membership(
                {"theme": source.provider_label, "asset_code": code},
                emitted_bytes,
            )
        if emitted == 0:
            print(
                f"fine_theme_provider_failed:{source.provider_label}:empty_provider_response",
                file=sys.stderr,
            )
            return 1
        return 0
    except TimeoutError as exc:
        print(
            f"fine_theme_provider_timeout:{source.provider_label}:{str(exc)[:120]}",
            file=sys.stderr,
        )
        return 124
    except Exception as exc:
        print(
            f"fine_theme_provider_failed:{source.provider_label}:"
            f"{type(exc).__name__}:{str(exc)[:120]}",
            file=sys.stderr,
        )
        return 1
    finally:
        _stop_timeout_alarm(alarm_installed)


if __name__ == "__main__":
    raise SystemExit(main())
