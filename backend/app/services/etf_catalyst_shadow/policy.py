from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from app.services.etf_research_evidence import stable_contract_hash

CATALYST_SOURCE_POLICY_ID = "etf_catalyst_sources_v1"
CATALYST_TAXONOMY_VERSION = "etf_theme_taxonomy_v1"
CATALYST_SHADOW_CONTRACT_VERSION = "etf_catalyst_shadow_v1"
CATALYST_RANKING_WEIGHT = 0.0
MAX_FETCH_ITEMS = 20
MAX_OPERATION_SECONDS = 50.0


@dataclass(frozen=True)
class CatalystSourceDefinition:
    source_id: str
    source_class: str
    allowed_domain: str
    endpoint: str
    timezone: str = "Asia/Shanghai"
    cadence: str = "trading_day_15_20"
    parser_version: str = "catalyst_html_v1"

    def validate(self) -> None:
        host = (urlsplit(self.endpoint).hostname or "").lower()
        domain = self.allowed_domain.lower()
        if self.source_class != "official":
            raise ValueError("initial catalyst allowlist is official-only")
        if not host or not (host == domain or host.endswith(f".{domain}")):
            raise ValueError("source endpoint must match its allowed domain")
        if self.timezone != "Asia/Shanghai":
            raise ValueError("initial catalyst timezone must be Asia/Shanghai")

    @property
    def contract_hash(self) -> str:
        self.validate()
        return stable_contract_hash(asdict(self))


APPROVED_CATALYST_SOURCES: tuple[CatalystSourceDefinition, ...] = (
    CatalystSourceDefinition(
        source_id="gov_policy",
        source_class="official",
        allowed_domain="gov.cn",
        endpoint="https://sousuo.www.gov.cn/zcwjk/",
    ),
    CatalystSourceDefinition(
        source_id="ndrc_policy",
        source_class="official",
        allowed_domain="ndrc.gov.cn",
        endpoint="https://www.ndrc.gov.cn/xxgk/wjk/",
    ),
    CatalystSourceDefinition(
        source_id="miit_policy",
        source_class="official",
        allowed_domain="miit.gov.cn",
        endpoint="https://www.miit.gov.cn/zwgk/zcwj/index.html",
    ),
    CatalystSourceDefinition(
        source_id="csrc_announcement",
        source_class="official",
        allowed_domain="csrc.gov.cn",
        endpoint="https://www.csrc.gov.cn/csrc/c101954/common_list.shtml",
    ),
    CatalystSourceDefinition(
        source_id="sse_announcement",
        source_class="official",
        allowed_domain="sse.com.cn",
        endpoint="https://www.sse.com.cn/disclosure/announcement/general/",
    ),
    CatalystSourceDefinition(
        source_id="szse_announcement",
        source_class="official",
        allowed_domain="szse.cn",
        endpoint="https://www.szse.cn/disclosure/notice/general/",
    ),
)


def catalyst_source_policy_payload() -> dict[str, Any]:
    for source in APPROVED_CATALYST_SOURCES:
        source.validate()
    return {
        "policy_id": CATALYST_SOURCE_POLICY_ID,
        "timezone": "Asia/Shanghai",
        "cadence": "trading_day_15_20",
        "fetch_policy": {
            "worker_count": 1,
            "maximum_items_per_source": MAX_FETCH_ITEMS,
            "operation_timeout_seconds": MAX_OPERATION_SECONDS,
            "retry_within_session": False,
        },
        "raw_content_retention": {
            "unreferenced_days": 180,
            "verified_or_studied": "indefinite",
            "receipt_metadata_hash_and_lineage": "indefinite",
        },
        "sources": [asdict(source) for source in APPROVED_CATALYST_SOURCES],
        "event_study_sufficiency": {
            "minimum_verified_direct_events_per_cohort": 30,
            "minimum_common_support_coverage": 0.8,
            "minimum_chronological_folds": 3,
            "minimum_declared_regimes": 2,
            "minimum_positive_fold_ratio": 2 / 3,
            "maximum_exclusion_rate": 0.2,
            "primary_endpoint": "five_session_net_matched_excess",
            "confidence": 0.95,
            "multiplicity_method": "holm_bonferroni",
        },
        "ranking_policy": {
            "catalyst_weight": CATALYST_RANKING_WEIGHT,
            "scoring_change_requires_separate_proposal": True,
        },
    }


def catalyst_source_policy_hash() -> str:
    return stable_contract_hash(catalyst_source_policy_payload())
