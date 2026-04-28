"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { LatestRecommendationsResponse, RecommendationItem } from "@/lib/types";

type AssetType = "fund" | "stock";

const tabs: Array<{ assetType: AssetType; label: string; description: string }> = [
  {
    assetType: "fund",
    label: "Fund candidates",
    description: "Rule-based fund screening using valuation, portfolio fit, cost, risk, size, and news flags."
  },
  {
    assetType: "stock",
    label: "Stock watchlist",
    description: "Observation-oriented stock screening using quality, valuation, momentum, risk, liquidity, and diversity."
  }
];

function scoreTone(score: number) {
  if (score >= 75) {
    return "bg-pine text-white";
  }
  if (score >= 55) {
    return "bg-accentSoft text-ink";
  }
  return "bg-blush text-ink";
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "n/a";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toString() : value.toFixed(2);
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(", ") : "none";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function CandidateCard({ item }: { item: RecommendationItem }) {
  const missingMetrics = item.data_freshness.missing_metrics;

  return (
    <Panel className="space-y-5 rounded-[24px]">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="rounded-full bg-paper px-3 py-1 text-xs font-semibold text-ink/60">#{item.rank}</span>
            <span className="rounded-full border border-ink/10 px-3 py-1 text-xs font-semibold text-ink/70">
              {item.safe_label}
            </span>
          </div>
          <h3 className="mt-3 text-xl font-semibold text-ink">
            {item.asset_name} <span className="text-sm font-medium text-ink/45">{item.asset_code}</span>
          </h3>
          <p className="mt-2 text-sm leading-6 text-ink/65">{formatValue(item.rationale.summary)}</p>
        </div>
        <div className={`min-w-24 rounded-[18px] px-4 py-3 text-center ${scoreTone(item.total_score)}`}>
          <p className="text-xs uppercase tracking-[0.22em] opacity-70">Score</p>
          <p className="mt-1 text-2xl font-semibold">{item.total_score.toFixed(1)}</p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {Object.entries(item.score_breakdown).map(([key, value]) => (
          <div key={key} className="rounded-[18px] border border-ink/10 bg-paper/70 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-ink/45">{key.replaceAll("_", " ")}</p>
            <div className="mt-3 flex items-end justify-between gap-3">
              <span className="text-xl font-semibold">{formatValue(value.score)}</span>
              <span className="text-xs text-ink/50">weight {formatValue(value.weight)}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-[18px] border border-ink/10 p-4">
          <p className="text-xs uppercase tracking-[0.2em] text-ink/45">Risk flags</p>
          <p className="mt-2 text-sm text-ink/70">{item.risk_flags.length ? item.risk_flags.join(", ") : "none"}</p>
        </div>
        <div className="rounded-[18px] border border-ink/10 p-4">
          <p className="text-xs uppercase tracking-[0.2em] text-ink/45">Missing metrics</p>
          <p className="mt-2 text-sm text-ink/70">{formatValue(missingMetrics)}</p>
        </div>
      </div>
    </Panel>
  );
}

export default function RecommendationsPage() {
  const [assetType, setAssetType] = useState<AssetType>("fund");
  const recommendations = useQuery({
    queryKey: ["recommendations", assetType],
    queryFn: async () =>
      (await api.get<LatestRecommendationsResponse>(`/api/recommendations/latest?asset_type=${assetType}`)).data
  });

  const currentTab = tabs.find((tab) => tab.assetType === assetType) ?? tabs[0];

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="Recommendations"
        title="Ranked research candidates, with the math visible."
        description="FundScope turns stored metrics into repeatable screening results. Rankings are explainable snapshots for manual review."
      />

      <div className="flex flex-wrap gap-3">
        {tabs.map((tab) => (
          <button
            key={tab.assetType}
            className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
              assetType === tab.assetType
                ? "border-ink bg-ink text-white"
                : "border-ink/10 bg-white text-ink hover:border-accent"
            }`}
            onClick={() => setAssetType(tab.assetType)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <Panel className="rounded-[24px]">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.3em] text-accent">{currentTab.label}</p>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-ink/70">{currentTab.description}</p>
            <p className="mt-3 max-w-3xl text-xs leading-5 text-ink/50">{recommendations.data?.disclaimer}</p>
          </div>
          {recommendations.data?.run ? (
            <div className="rounded-[18px] bg-paper px-4 py-3 text-sm text-ink/70">
              <p className="font-semibold text-ink">Run #{recommendations.data.run.id}</p>
              <p>As of {formatDate(recommendations.data.run.as_of_date)}</p>
              <p>Status {recommendations.data.run.status}</p>
            </div>
          ) : null}
        </div>
      </Panel>

      {recommendations.isLoading ? (
        <Panel className="rounded-[24px] text-sm text-ink/60">Loading recommendation snapshot…</Panel>
      ) : null}

      {recommendations.isError ? (
        <Panel className="rounded-[24px] border-accent/40 text-sm text-accent">
          Recommendation data is unavailable. Check the API service and job history.
        </Panel>
      ) : null}

      {!recommendations.isLoading && !recommendations.isError && recommendations.data?.items.length === 0 ? (
        <Panel className="rounded-[24px] border-dashed text-sm text-ink/65">
          No recommendation snapshot exists yet. Run the asset recommendation job from Admin Jobs after metrics are
          available.
        </Panel>
      ) : null}

      <div className="space-y-4">
        {(recommendations.data?.items ?? []).map((item) => (
          <CandidateCard key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
}
