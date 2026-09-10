"""Explicit ETF tracked-underlying metadata from public fund archives.

The archive page is used only for the labelled ``跟踪标的`` field.  ETF names,
keywords, and the page title are never used to infer an index identity.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.models.entities import utcnow
from app.services.short_research.etf_identity_facts import (
    IdentityFactProviderPage,
    TrackedUnderlyingFactInput,
)

EASTMONEY_TRACKED_UNDERLYING_SOURCE = "eastmoney.fundf10"
EASTMONEY_TRACKED_UNDERLYING_PROVIDER_VERSION = "fundf10-jbgk-v1"
EASTMONEY_TRACKED_UNDERLYING_RULE_VERSION = "fundf10-tracked-underlying-v1"
EASTMONEY_TRACKED_UNDERLYING_URL = "https://fundf10.eastmoney.com/jbgk_{code}.html"
EASTMONEY_TRACKED_UNDERLYING_TIMEOUT_SECONDS = 8.0
MAX_TRACKED_UNDERLYING_PAGE_CODES = 20

_CODE_PATTERN = re.compile(r"(?<!\d)(?P<code>\d{6})(?!\d)")
_NO_TRACKED_UNDERLYING_LABELS = frozenset(
    {
        "无",
        "无跟踪标的",
        "该基金无跟踪标的",
        "本基金无跟踪标的",
        "无跟踪标的指数",
        "该基金无跟踪标的指数",
        "本基金无跟踪标的指数",
        "不适用",
    }
)
_PLACEHOLDER_TRACKED_UNDERLYING_LABELS = frozenset(
    {
        "--",
        "—",
        "暂无",
        "暂无资料",
        "暂无数据",
        "待更新",
        "待补充",
        "待定",
        "未提供",
        "未披露",
        "暂未披露",
        "暂未公布",
        "未公布",
        "不详",
        "未知",
        "未知标的",
        "unknown",
        "n/a",
        "na",
        "null",
        "none",
    }
)

Clock = Callable[[], datetime]


class EtfTrackedUnderlyingProviderError(RuntimeError):
    """A source response cannot be used as an identity fact."""

    def __init__(
        self,
        code: str,
        reason: str,
        *,
        failures: Iterable[tuple[str, str]] = (),
    ) -> None:
        self.code = code
        self.reason = reason
        self.failures = tuple(failures) or ((code, reason),)
        super().__init__(f"{code}: {reason}")


def _archive_rows(body: str) -> tuple[tuple[str, ...], ...]:
    """Return direct cells for every visible table row, including nested rows."""

    soup = BeautifulSoup(body, "html.parser")
    rows: list[tuple[str, ...]] = []
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        values = tuple(
            cell.get_text(" ", strip=True)
            for cell in cells
        )
        if values:
            rows.append(values)
    return tuple(rows)


def normalize_tracked_underlying_label(value: object) -> str:
    """Normalize only presentation whitespace; keep provider wording intact."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split())


def canonical_tracked_underlying_id(label: str) -> str:
    """Return a stable provider-scoped ID derived only from an explicit label."""

    normalized = normalize_tracked_underlying_label(label)
    if not normalized:
        raise ValueError("tracked underlying label is required")
    if is_explicit_no_tracked_underlying(normalized) or _is_placeholder_tracked_underlying(
        normalized
    ):
        raise ValueError("an unavailable tracked underlying has no canonical ID")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{EASTMONEY_TRACKED_UNDERLYING_SOURCE}:index:{digest}"


def is_explicit_no_tracked_underlying(label: str) -> bool:
    normalized = normalize_tracked_underlying_label(label)
    return normalized in _NO_TRACKED_UNDERLYING_LABELS


def _is_placeholder_tracked_underlying(label: str) -> bool:
    normalized = normalize_tracked_underlying_label(label)
    return normalized.casefold() in _PLACEHOLDER_TRACKED_UNDERLYING_LABELS


def _source_url(code: str) -> str:
    return EASTMONEY_TRACKED_UNDERLYING_URL.format(code=code)


def _code_row(rows: Iterable[tuple[str, ...]], code: str) -> tuple[str, ...] | None:
    saw_code_field = False
    for row in rows:
        for index, cell in enumerate(row):
            normalized_cell = normalize_tracked_underlying_label(cell)
            if not normalized_cell.startswith("基金代码"):
                continue
            saw_code_field = True
            suffix = normalized_cell.removeprefix("基金代码").strip(" :：")
            candidate = normalize_tracked_underlying_label(
                " ".join(filter(None, (suffix, *row[index + 1 :]))),
            )
            match = _CODE_PATTERN.search(candidate)
            if match is not None and match.group("code") == code:
                return row
    raise EtfTrackedUnderlyingProviderError(
        code,
        "provider_code_mismatch" if saw_code_field else "missing_fund_code_field",
    )


def _tracked_underlying_label(
    rows: Iterable[tuple[str, ...]],
    code: str,
) -> tuple[str, tuple[str, ...], str]:
    candidates: list[tuple[str, tuple[str, ...], str]] = []
    for row in rows:
        for index, cell in enumerate(row):
            normalized_cell = normalize_tracked_underlying_label(cell)
            if normalized_cell == "跟踪标的":
                raw_value = row[index + 1] if index + 1 < len(row) else ""
                value = normalize_tracked_underlying_label(
                    raw_value,
                )
            elif normalized_cell.startswith("跟踪标的"):
                raw_value = cell[len("跟踪标的") :].strip(" :：")
                value = normalized_cell.removeprefix("跟踪标的").strip(" :：")
            else:
                continue
            if not value:
                raise EtfTrackedUnderlyingProviderError(code, "empty_tracked_underlying_field")
            candidates.append((value, row, raw_value))
    if not candidates:
        raise EtfTrackedUnderlyingProviderError(code, "missing_tracked_underlying_field")
    labels = {value for value, _row, _raw_value in candidates}
    if len(labels) != 1:
        raise EtfTrackedUnderlyingProviderError(
            code,
            "conflicting_tracked_underlying_fields",
        )
    value, row, raw_value = candidates[0]
    if _is_placeholder_tracked_underlying(value):
        raise EtfTrackedUnderlyingProviderError(
            code,
            "placeholder_tracked_underlying_field",
        )
    return value, row, raw_value


def _record_from_response(
    *,
    code: str,
    source_url: str,
    body: str,
    observed_at: datetime,
) -> TrackedUnderlyingFactInput:
    rows = _archive_rows(body)
    code_row = _code_row(rows, code)
    label, underlying_row, raw_label = _tracked_underlying_label(rows, code)
    response_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
    raw_payload: dict[str, Any] = {
        "fund_code": code,
        "source_url": source_url,
        "provider_field": "跟踪标的",
        "tracked_underlying_label": label,
        "tracked_underlying_label_raw": raw_label,
        "code_row": list(code_row or ()),
        "underlying_row": list(underlying_row),
        "source_response_sha256": response_sha256,
    }
    if is_explicit_no_tracked_underlying(label):
        return TrackedUnderlyingFactInput(
            etf_code=code,
            external_source_id=source_url,
            source=EASTMONEY_TRACKED_UNDERLYING_SOURCE,
            provider_version=EASTMONEY_TRACKED_UNDERLYING_PROVIDER_VERSION,
            observed_at=observed_at,
            identity_state="unresolved",
            tracked_underlying_id=None,
            mapping_basis="authoritative",
            confidence="high",
            rule_version=EASTMONEY_TRACKED_UNDERLYING_RULE_VERSION,
            identity_reason=f"provider_explicit_no_underlying:{label}",
            raw_payload=raw_payload,
        )
    return TrackedUnderlyingFactInput(
        etf_code=code,
        external_source_id=source_url,
        source=EASTMONEY_TRACKED_UNDERLYING_SOURCE,
        provider_version=EASTMONEY_TRACKED_UNDERLYING_PROVIDER_VERSION,
        observed_at=observed_at,
        identity_state="resolved",
        tracked_underlying_id=canonical_tracked_underlying_id(label),
        mapping_basis="authoritative",
        confidence="high",
        rule_version=EASTMONEY_TRACKED_UNDERLYING_RULE_VERSION,
        identity_reason=f"explicit_provider_field:跟踪标的={label}",
        raw_payload=raw_payload,
    )


async def _fetch_page(
    client: httpx.AsyncClient,
    codes: tuple[str, ...],
    *,
    clock: Clock,
) -> IdentityFactProviderPage:
    records: list[TrackedUnderlyingFactInput] = []
    provider_errors: list[tuple[str, str]] = []
    for code in codes:
        if _CODE_PATTERN.fullmatch(code) is None:
            provider_errors.append((code, "invalid_etf_code"))
            continue
        url = _source_url(code)
        try:
            response = await client.get(url)
            response.raise_for_status()
            body = response.text
        except Exception as exc:  # noqa: BLE001 - redact provider details in job output
            provider_errors.append((code, f"provider_request_failed:{type(exc).__name__}"))
            continue
        try:
            records.append(
                _record_from_response(
                    code=code,
                    source_url=url,
                    body=body,
                    observed_at=clock(),
                )
            )
        except EtfTrackedUnderlyingProviderError as exc:
            provider_errors.append((code, exc.reason))
        except Exception as exc:  # noqa: BLE001 - parser failures are provider failures
            provider_errors.append((code, f"provider_parse_failed:{type(exc).__name__}"))
    if provider_errors and not records:
        code, reason = provider_errors[0]
        raise EtfTrackedUnderlyingProviderError(
            code,
            reason,
            failures=tuple(provider_errors),
        )
    return IdentityFactProviderPage(
        underlying_records=tuple(records),
        provider_errors=tuple(provider_errors),
    )


async def fetch_eastmoney_tracked_underlying_page(
    codes: Iterable[str],
    *,
    client: httpx.AsyncClient | None = None,
    clock: Clock = utcnow,
) -> IdentityFactProviderPage:
    """Fetch one bounded page of explicit tracked-underlying facts.

    ``clock`` is injectable for tests; production callers leave it at the real
    receipt clock, and no historical observation timestamp is accepted.
    """

    unique_codes = tuple(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
    if len(unique_codes) > MAX_TRACKED_UNDERLYING_PAGE_CODES:
        raise ValueError(
            f"at most {MAX_TRACKED_UNDERLYING_PAGE_CODES} ETF codes may be fetched at once"
        )
    if not unique_codes:
        return IdentityFactProviderPage()
    if client is not None:
        return await _fetch_page(client, unique_codes, clock=clock)
    async with httpx.AsyncClient(
        timeout=EASTMONEY_TRACKED_UNDERLYING_TIMEOUT_SECONDS,
        headers={"User-Agent": "FundScope/etf-identity-facts", "Accept": "text/html"},
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
    ) as owned_client:
        return await _fetch_page(owned_client, unique_codes, clock=clock)


__all__ = [
    "EASTMONEY_TRACKED_UNDERLYING_PROVIDER_VERSION",
    "EASTMONEY_TRACKED_UNDERLYING_RULE_VERSION",
    "EASTMONEY_TRACKED_UNDERLYING_SOURCE",
    "EtfTrackedUnderlyingProviderError",
    "canonical_tracked_underlying_id",
    "fetch_eastmoney_tracked_underlying_page",
    "is_explicit_no_tracked_underlying",
    "normalize_tracked_underlying_label",
]
