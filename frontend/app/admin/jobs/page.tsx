"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Panel, SectionHeader, StatPill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { DataStatus, JobRun } from "@/lib/types";

type JobAction = {
  key: string;
  label: string;
  description: string;
  endpoint: string;
  primary?: boolean;
};

const jobGroups: Array<{ title: string; description: string; actions: JobAction[] }> = [
  {
    title: "数据准备",
    description: "先把基金净值、指数估值和持仓快照准备好，后面的回测和模拟盘才有市场数据可用。",
    actions: [
      {
        key: "daily_fund_nav",
        label: "拉取最近净值",
        description: "更新默认基金池最近几天的基金净值。",
        endpoint: "/api/admin/jobs/daily_fund_nav/run",
        primary: true
      },
      {
        key: "fund_nav_backfill_180",
        label: "回填 180 天净值",
        description: "补齐最近半年左右的历史净值，适合先试跑策略。",
        endpoint: "/api/admin/jobs/fund_nav_backfill/run?days=180"
      },
      {
        key: "fund_nav_backfill_365",
        label: "回填 365 天净值",
        description: "补齐最近一年的历史净值，回测结果更有参考意义。",
        endpoint: "/api/admin/jobs/fund_nav_backfill/run?days=365"
      },
      {
        key: "daily_valuation",
        label: "更新指数估值",
        description: "更新 PE（市盈率）、PB（市净率）等估值数据。",
        endpoint: "/api/admin/jobs/daily_valuation/run"
      },
      {
        key: "daily_holdings_snapshot",
        label: "生成持仓快照",
        description: "根据已录入交易和最新净值，重新计算当前持仓市值。",
        endpoint: "/api/admin/jobs/daily_holdings_snapshot/run"
      }
    ]
  },
  {
    title: "策略实验室",
    description: "运行筛选评分，或者让所有已经启动的模拟盘按最新数据更新一次。",
    actions: [
      {
        key: "daily_asset_recommendations",
        label: "运行筛选评分",
        description: "生成基金候选排名和风险提示，不会产生真实交易。",
        endpoint: "/api/admin/jobs/daily_asset_recommendations/run",
        primary: true
      },
      {
        key: "daily_strategy_paper",
        label: "更新所有模拟盘",
        description: "把所有 active 状态的模拟盘更新到最新可用净值日期。",
        endpoint: "/api/admin/jobs/daily_strategy_paper/run"
      }
    ]
  },
  {
    title: "短线 ETF",
    description: "同步场内 ETF 行情，生成短线观察信号，并更新短线 ETF 模拟盘。",
    actions: [
      {
        key: "daily_short_etf_data",
        label: "同步 ETF 行情",
        description: "拉取默认 ETF 池最近一段时间的日线价格和成交额。",
        endpoint: "/api/admin/jobs/daily_short_etf_data/run",
        primary: true
      },
      {
        key: "daily_short_etf_retry_failed_data",
        label: "重试失败 ETF 数据",
        description: "只重试同步失败或数据过期的 ETF，不会重拉整个池子。",
        endpoint: "/api/admin/jobs/daily_short_etf_retry_failed_data/run"
      },
      {
        key: "daily_short_etf_signals",
        label: "生成 ETF 信号",
        description: "按趋势、流动性和追高风险生成短线观察结果。",
        endpoint: "/api/admin/jobs/daily_short_etf_signals/run"
      },
      {
        key: "daily_short_etf_reliability_evaluation",
        label: "评估 ETF 规则可靠性",
        description: "用历史日线检查短线规则的样本长度、参数稳定性、手续费影响和回撤。",
        endpoint: "/api/admin/jobs/daily_short_etf_reliability_evaluation/run"
      },
      {
        key: "daily_short_etf_paper",
        label: "更新 ETF 模拟盘",
        description: "把 active 状态的短线 ETF 模拟盘更新到最新日期。",
        endpoint: "/api/admin/jobs/daily_short_etf_paper/run"
      }
    ]
  },
  {
    title: "短线基金/ETF研究",
    description: "同步统一短线研究池，生成保守观察排序，并用大模型补充多角度研究说明。大模型只解释，不会改排名或真实操作。",
    actions: [
      {
        key: "daily_short_research_data",
        label: "同步短线研究数据",
        description: "拉取短线基金和 ETF 研究池最近一段时间的公开净值与日线数据。",
        endpoint: "/api/admin/jobs/daily_short_research_data/run",
        primary: true
      },
      {
        key: "daily_short_research_signals",
        label: "生成短线排序",
        description: "按趋势、回撤、波动和数据质量生成保守观察标签。",
        endpoint: "/api/admin/jobs/daily_short_research_signals/run"
      },
      {
        key: "daily_short_research_advisor",
        label: "生成 AI 研究报告",
        description: "为最新短线榜单生成重点观察、高位别追、谨慎等保守说明；AI 不改变榜单。",
        endpoint: "/api/admin/jobs/daily_short_research_advisor/run"
      }
    ]
  },
  {
    title: "信息辅助",
    description: "这些任务用于提醒和新闻摘要，不影响净值回填本身。",
    actions: [
      {
        key: "daily_news_fetch",
        label: "获取基金新闻",
        description: "拉取默认基金池相关新闻并尝试生成摘要。",
        endpoint: "/api/admin/jobs/daily_news_fetch/run"
      },
      {
        key: "news_summary_backfill",
        label: "重试新闻摘要",
        description: "给还没有摘要的新闻重新生成摘要。",
        endpoint: "/api/admin/jobs/news_summary_backfill/run"
      },
      {
        key: "monthly_dca_reminder",
        label: "发送定投提醒",
        description: "按设置里的金额和估值状态发送提醒邮件。",
        endpoint: "/api/admin/jobs/monthly_dca_reminder/run"
      }
    ]
  }
];

function jobLabel(jobName: string) {
  if (jobName === "fund_nav_backfill") {
    return "历史净值回填";
  }
  const allActions = jobGroups.flatMap((group) => group.actions);
  return allActions.find((action) => action.key === jobName)?.label ?? jobName;
}

function statusLabel(status: string) {
  switch (status) {
    case "success":
      return "成功";
    case "failed":
      return "失败";
    case "running":
      return "运行中";
    default:
      return status;
  }
}

function errorText(error: unknown) {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response;
    if (response?.data?.detail) {
      return response.data.detail;
    }
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "任务运行失败";
}

function resultText(value: Record<string, unknown> | null) {
  if (!value) {
    return "还没有运行结果。";
  }
  return JSON.stringify(value, null, 2);
}

export default function AdminJobsPage() {
  const queryClient = useQueryClient();
  const [lastResult, setLastResult] = useState<{ label: string; result: Record<string, unknown> } | null>(null);

  const jobs = useQuery({
    queryKey: ["job-runs"],
    queryFn: async () => (await api.get<JobRun[]>("/api/admin/jobs")).data
  });

  const dataStatus = useQuery({
    queryKey: ["admin", "data-status"],
    queryFn: async () => (await api.get<DataStatus>("/api/admin/data-status")).data
  });

  const triggerJob = useMutation({
    mutationFn: async (action: JobAction) => (await api.post<Record<string, unknown>>(action.endpoint)).data,
    onSuccess: async (result, action) => {
      setLastResult({ label: action.label, result });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["job-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["admin", "data-status"] })
      ]);
    }
  });

  const status = dataStatus.data;

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="后台任务"
        title="把数据准备和模拟盘更新放到网页里"
        description="项目启动后，日常的基金净值回填、估值更新、筛选评分和模拟盘更新都可以在这里点按钮完成。"
      />

      <Panel className="rounded-[24px]">
        <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
          <StatPill label="基金数量" value={(status?.fund_count ?? 0).toString()} tone="bg-white text-ink" />
          <StatPill label="净值记录" value={(status?.nav_rows ?? 0).toString()} />
          <StatPill label="最早净值" value={formatDate(status?.earliest_nav_date)} tone="bg-accentSoft text-ink" />
          <StatPill label="最新净值" value={formatDate(status?.latest_nav_date)} tone="bg-accentSoft text-ink" />
          <StatPill label="策略数量" value={(status?.strategy_count ?? 0).toString()} tone="bg-white text-ink" />
          <StatPill label="模拟盘数量" value={(status?.paper_portfolio_count ?? 0).toString()} />
        </div>
        {dataStatus.isError ? (
          <p className="mt-4 rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
            数据状态读取失败：{errorText(dataStatus.error)}
          </p>
        ) : null}
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <div className="space-y-6">
          {jobGroups.map((group) => (
            <Panel key={group.title} className="rounded-[24px]">
              <div className="mb-5">
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-accent">{group.title}</p>
                <p className="mt-2 text-sm leading-6 text-ink/65">{group.description}</p>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                {group.actions.map((action) => {
                  const isRunning = triggerJob.isPending && triggerJob.variables?.key === action.key;
                  return (
                    <button
                      key={action.key}
                      className={`rounded-[18px] border p-4 text-left transition ${
                        action.primary ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink hover:border-accent"
                      } disabled:cursor-not-allowed disabled:opacity-60`}
                      disabled={triggerJob.isPending}
                      onClick={() => triggerJob.mutate(action)}
                    >
                      <span className="text-sm font-semibold">{isRunning ? "运行中..." : action.label}</span>
                      <span className={`mt-2 block text-xs leading-5 ${action.primary ? "text-white/70" : "text-ink/55"}`}>
                        {action.description}
                      </span>
                    </button>
                  );
                })}
              </div>
            </Panel>
          ))}
        </div>

        <Panel className="h-fit rounded-[24px]">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-accent">运行结果</p>
          {triggerJob.isError ? (
            <p className="mt-4 rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
              任务失败：{errorText(triggerJob.error)}
            </p>
          ) : (
            <pre className="mt-4 max-h-96 overflow-auto rounded-[18px] bg-paper p-4 text-xs leading-5 text-ink/70">
              {lastResult ? `${lastResult.label}\n${resultText(lastResult.result)}` : resultText(null)}
            </pre>
          )}
        </Panel>
      </div>

      <Panel className="rounded-[24px]">
        <div className="overflow-hidden rounded-[18px] border border-ink/10">
          <table className="min-w-full divide-y divide-ink/10 text-sm">
            <thead className="bg-paper/80">
              <tr>
                {["任务", "状态", "开始时间", "结束时间", "详情"].map((column) => (
                  <th key={column} className="px-4 py-3 text-left font-medium text-ink/55">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-ink/10 bg-white">
              {(jobs.data ?? []).map((job) => (
                <tr key={job.id}>
                  <td className="px-4 py-3 font-semibold">{jobLabel(job.job_name)}</td>
                  <td className="px-4 py-3 text-ink/65">{statusLabel(job.status)}</td>
                  <td className="px-4 py-3">{formatDate(job.started_at)}</td>
                  <td className="px-4 py-3">{formatDate(job.finished_at)}</td>
                  <td className="max-w-xl px-4 py-3 text-ink/60">
                    {job.error_message ?? JSON.stringify(job.details)}
                  </td>
                </tr>
              ))}
              {!jobs.isLoading && (jobs.data ?? []).length === 0 ? (
                <tr>
                  <td className="px-4 py-6 text-ink/55" colSpan={5}>
                    暂时还没有任务运行记录。
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
