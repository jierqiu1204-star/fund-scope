from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ParsedSummary:
    event_type: str
    summary: str


VALID_EVENT_TYPES = {"dividend", "manager_change", "size_change", "strategy_change", "other"}


def parse_summary_output(output: str) -> ParsedSummary:
    event_type, _, summary = output.partition("|")
    normalized_event_type = event_type.strip()
    if normalized_event_type not in VALID_EVENT_TYPES:
        normalized_event_type = "other"
    normalized_summary = (summary or output).strip()
    return ParsedSummary(event_type=normalized_event_type, summary=normalized_summary[:80])
