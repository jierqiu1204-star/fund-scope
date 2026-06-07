"use client";

import { useDeferredValue, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { eventTypeLabel, formatDate } from "@/lib/format";
import type { NewsFeedResponse } from "@/lib/types";

const FILTERS = ["all", "dividend", "manager_change", "size_change", "strategy_change", "other"] as const;

export default function NewsPage() {
  const [selectedFilter, setSelectedFilter] = useState<(typeof FILTERS)[number]>("all");
  const deferredFilter = useDeferredValue(selectedFilter);
  const news = useQuery({
    queryKey: ["news", deferredFilter],
    queryFn: async () => {
      const suffix = deferredFilter === "all" ? "" : `&event_type=${deferredFilter}`;
      return (await api.get<NewsFeedResponse>(`/api/news?days=14${suffix}`)).data;
    }
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="新闻"
        title="只看和基金有关的近况。"
        description="这里把最近 14 天的基金新闻和公告做成简短摘要。摘要失败时仍会显示原始标题，避免重要信息被隐藏。"
        action={
          <div className="flex flex-wrap gap-2">
            {FILTERS.map((filter) => (
              <button
                key={filter}
                className={`rounded-full px-4 py-2 text-sm font-semibold ${
                  selectedFilter === filter ? "bg-ink text-white" : "border border-ink/10 bg-white text-ink"
                }`}
                onClick={() => setSelectedFilter(filter)}
              >
                {filter === "all" ? "全部" : eventTypeLabel(filter)}
              </button>
            ))}
          </div>
        }
      />

      <div className="space-y-6">
        {(news.data?.groups ?? []).map((group) => (
          <Panel key={group.fund_code}>
            <div className="mb-5 flex items-center justify-between">
              <div>
                <p className="text-xs uppercase tracking-[0.35em] text-accent">{group.fund_code}</p>
                <h3 className="mt-2 font-display text-3xl">最近 14 天</h3>
              </div>
              <span className="rounded-full bg-paper px-4 py-2 text-xs uppercase tracking-[0.2em] text-ink/55">
                {group.items.length} 条
              </span>
            </div>
            <div className="space-y-4">
              {group.items.map((item) => (
                <article key={item.id} className="rounded-[24px] border border-ink/10 bg-paper/80 p-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="rounded-full bg-white px-3 py-1 text-xs uppercase tracking-[0.2em] text-ink/55">
                      {formatDate(item.published_at)}
                    </span>
                    <span className="rounded-full bg-blush px-3 py-1 text-xs font-semibold text-ink">
                      {eventTypeLabel(item.event_type)}
                    </span>
                    {item.summary_status === "failed" ? (
                      <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">
                        摘要生成失败
                      </span>
                    ) : null}
                  </div>
                  <h4 className="mt-4 text-xl font-semibold">{item.title}</h4>
                  <p className="mt-2 text-sm leading-7 text-ink/70">
                    {item.summary ?? "暂无摘要，先显示原始标题。"}
                  </p>
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-4 inline-flex text-sm font-semibold text-accent hover:text-pine"
                  >
                    查看来源
                  </a>
                </article>
              ))}
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}
