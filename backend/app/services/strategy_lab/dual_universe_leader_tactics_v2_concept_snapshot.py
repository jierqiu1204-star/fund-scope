"""Bounded subprocess for factual Eastmoney concept constituents."""

from __future__ import annotations

import json
import sys


def main() -> int:
    themes = tuple(dict.fromkeys(item.strip() for item in sys.argv[1:] if item.strip()))
    if not 1 <= len(themes) <= 5:
        return 2
    import akshare as ak  # isolated so a stuck provider can be killed by the parent

    emitted = 0
    failures: list[str] = []
    for theme in themes:
        try:
            frame = ak.stock_board_concept_cons_em(symbol=theme)
        except Exception as exc:
            failures.append(f"{theme}:{type(exc).__name__}")
            continue
        code_column = next((name for name in ("代码", "证券代码") if name in frame.columns), None)
        if code_column is None:
            failures.append(f"{theme}:concept_code_column_missing")
            continue
        for raw_code in frame[code_column].tolist():
            code = str(raw_code).strip().split(".")[0].zfill(6)
            if len(code) != 6 or not code.isdigit():
                continue
            print(json.dumps({"theme": theme, "asset_code": code}, ensure_ascii=False))
            emitted += 1
            if emitted > 2_000:
                raise RuntimeError("concept_membership_response_too_large")
    if emitted == 0:
        summary = ",".join(failures)[:300] or "empty_provider_response"
        raise RuntimeError(f"no_registered_concept_memberships:{summary}")
    if failures:
        print(",".join(failures)[:300], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
