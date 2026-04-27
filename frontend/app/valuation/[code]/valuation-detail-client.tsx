"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { HistoricalValuation } from "@/lib/types";

export function ValuationDetailClient({ code }: { code: string }) {
  const [metric, setMetric] = useState<"pe" | "pb">("pe");
  const history = useQuery({
    queryKey: ["valuation-history", code],
    queryFn: async () =>
      (
        await api.get<HistoricalValuation[]>(
          `/api/valuation/${code}/history?from=2016-01-01&to=2030-01-01`
        )
      ).data
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="Valuation Detail"
        title={`${code} history across the full stored window.`}
        description="Switch between PE and PB to inspect whether the current reminder signal is being driven by earnings or balance-sheet valuation."
      />
      <Panel>
        <div className="mb-6 flex flex-wrap gap-3">
          {(["pe", "pb"] as const).map((item) => (
            <button
              key={item}
              className={`rounded-full px-4 py-2 text-sm font-semibold ${
                metric === item ? "bg-ink text-white" : "border border-ink/10 bg-paper text-ink"
              }`}
              onClick={() => setMetric(item)}
            >
              {item.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="h-[28rem]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={history.data ?? []}>
              <XAxis dataKey="date" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip />
              <Line
                type="monotone"
                dataKey={metric}
                stroke={metric === "pe" ? "#b5532d" : "#1f5c4b"}
                strokeWidth={2.5}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Panel>
    </div>
  );
}
