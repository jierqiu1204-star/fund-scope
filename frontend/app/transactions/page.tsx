"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { startTransition, useMemo, useState } from "react";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";
import type { TransactionList } from "@/lib/types";

const defaultForm = {
  fund_code: "007339",
  action: "buy",
  amount: "500",
  shares: "",
  nav_at_trade: "1.2345",
  fee: "0",
  traded_at: "2026-05-01"
};

function actionLabel(action: string) {
  return action === "sell" ? "卖出" : "买入";
}

export default function TransactionsPage() {
  const queryClient = useQueryClient();
  const [showComposer, setShowComposer] = useState(false);
  const [form, setForm] = useState(defaultForm);
  const [csvResult, setCsvResult] = useState<string>("");

  const transactions = useQuery({
    queryKey: ["transactions"],
    queryFn: async () => (await api.get<TransactionList>("/api/transactions")).data
  });

  const totals = useMemo(() => {
    const items = transactions.data?.items ?? [];
    return {
      buys: items.filter((item) => item.action === "buy").length,
      sells: items.filter((item) => item.action === "sell").length
    };
  }, [transactions.data]);

  const createTransaction = useMutation({
    mutationFn: async () =>
      api.post("/api/transactions", {
        fund_code: form.fund_code,
        action: form.action,
        amount: form.action === "buy" ? Number(form.amount) : undefined,
        shares: form.action === "sell" ? Number(form.shares) : undefined,
        nav_at_trade: Number(form.nav_at_trade),
        fee: Number(form.fee),
        traded_at: form.traded_at
      }),
    onSuccess: async () => {
      setShowComposer(false);
      await queryClient.invalidateQueries({ queryKey: ["transactions"] });
    }
  });

  const importCsv = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return api.post("/api/transactions/import-csv", formData, {
        headers: { "Content-Type": "multipart/form-data" }
      });
    },
    onSuccess: async (response) => {
      setCsvResult(`已导入 ${response.data.inserted} 条记录。`);
      await queryClient.invalidateQueries({ queryKey: ["transactions"] });
    },
    onError: (error: unknown) => {
      setCsvResult("CSV 导入失败，请检查每一行的基金代码、净值、费用和日期。");
      console.error(error);
    }
  });

  const importAlipayCsv = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return api.post("/api/transactions/import-alipay-csv", formData, {
        headers: { "Content-Type": "multipart/form-data" }
      });
    },
    onSuccess: async (response) => {
      setCsvResult(`已导入 ${response.data.inserted} 条支付宝记录。`);
      await queryClient.invalidateQueries({ queryKey: ["transactions"] });
    },
    onError: (error: unknown) => {
      setCsvResult("支付宝 CSV 导入失败，请检查基金代码、交易类型、金额、份额、成交净值、手续费和日期。");
      console.error(error);
    }
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="交易"
        title="手动记录真实买卖，后面才能对账。"
        description="这里记录的是你真实操作过的基金交易。可以手动新增，也可以导入普通 CSV 或支付宝 CSV。"
        action={
          <button
            className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine"
            onClick={() => setShowComposer(true)}
          >
            新增交易
          </button>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[0.9fr_1.1fr]">
        <Panel>
          <SectionHeader
            eyebrow="流水"
            title={`买入 ${totals.buys} 笔 / 卖出 ${totals.sells} 笔`}
            description="这张表是你的本地交易账本。模拟盘和支付宝式对账会用它来推算真实持仓。"
          />
          <div className="overflow-hidden rounded-[24px] border border-ink/10">
            <table className="min-w-full divide-y divide-ink/10 text-sm">
              <thead className="bg-paper/80">
                <tr>
                  {["基金", "类型", "份额", "金额", "净值", "费用", "交易日期"].map((column) => (
                    <th key={column} className="px-4 py-3 text-left font-medium text-ink/55">
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-ink/10 bg-white">
                {(transactions.data?.items ?? []).map((item) => (
                  <tr key={item.id}>
                    <td className="px-4 py-3 font-semibold">{item.fund_code}</td>
                    <td className="px-4 py-3 tracking-[0.2em] text-ink/55">{actionLabel(item.action)}</td>
                    <td className="px-4 py-3">{item.shares.toFixed(2)}</td>
                    <td className="px-4 py-3">{formatCurrency(item.amount ?? 0)}</td>
                    <td className="px-4 py-3">{item.nav_at_trade.toFixed(4)}</td>
                    <td className="px-4 py-3">{formatCurrency(item.fee)}</td>
                    <td className="px-4 py-3">{formatDate(item.traded_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-6 flex flex-wrap gap-3">
            <label className="inline-flex rounded-full border border-ink/10 px-5 py-3 text-sm font-semibold text-ink transition hover:border-accent hover:text-accent">
              导入普通 CSV
              <input
                className="hidden"
                type="file"
                accept=".csv"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    importCsv.mutate(file);
                  }
                }}
              />
            </label>
            <label className="inline-flex rounded-full border border-ink/10 px-5 py-3 text-sm font-semibold text-ink transition hover:border-accent hover:text-accent">
              导入支付宝 CSV
              <input
                className="hidden"
                type="file"
                accept=".csv"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    importAlipayCsv.mutate(file);
                  }
                }}
              />
            </label>
          </div>
          <div className="mt-4">
            <p className="text-sm text-ink/60">
              普通 CSV 格式：`fund_code,action,amount_or_shares,nav,fee,traded_at`。支付宝 CSV 支持中文列：
              `基金代码,交易类型,金额,份额,成交净值,手续费,交易日期`。
            </p>
            {csvResult ? <p className="mt-3 text-sm text-accent">{csvResult}</p> : null}
          </div>
        </Panel>
      </div>

      {showComposer ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-4 backdrop-blur-sm">
          <Panel className="w-full max-w-2xl">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase tracking-[0.35em] text-accent">记录交易</p>
                <h3 className="mt-3 font-display text-3xl">新增一笔基金交易</h3>
              </div>
              <button
                className="rounded-full border border-ink/10 px-4 py-2 text-sm font-semibold"
                onClick={() => setShowComposer(false)}
              >
                关闭
              </button>
            </div>
            <div className="mt-5 grid gap-4 md:grid-cols-2">
              <label className="text-sm">
                基金代码
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.fund_code}
                  onChange={(event) => setForm((current) => ({ ...current, fund_code: event.target.value }))}
                />
              </label>
              <label className="text-sm">
                交易类型
                <select
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.action}
                  onChange={(event) => setForm((current) => ({ ...current, action: event.target.value }))}
                >
                  <option value="buy">买入</option>
                  <option value="sell">卖出</option>
                </select>
              </label>
              <label className="text-sm">
                买入金额
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.amount}
                  onChange={(event) => setForm((current) => ({ ...current, amount: event.target.value }))}
                />
              </label>
              <label className="text-sm">
                卖出份额
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.shares}
                  onChange={(event) => setForm((current) => ({ ...current, shares: event.target.value }))}
                />
              </label>
              <label className="text-sm">
                成交净值
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.nav_at_trade}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, nav_at_trade: event.target.value }))
                  }
                />
              </label>
              <label className="text-sm">
                手续费
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.fee}
                  onChange={(event) => setForm((current) => ({ ...current, fee: event.target.value }))}
                />
              </label>
              <label className="text-sm md:col-span-2">
                交易日期
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.traded_at}
                  onChange={(event) => setForm((current) => ({ ...current, traded_at: event.target.value }))}
                />
              </label>
            </div>
            <div className="mt-6 flex flex-wrap gap-3">
              <button
                className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine"
                onClick={() =>
                  startTransition(() => {
                    createTransaction.mutate();
                  })
                }
              >
                保存交易
              </button>
            </div>
          </Panel>
        </div>
      ) : null}
    </div>
  );
}
