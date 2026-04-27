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
      setCsvResult(`Imported ${response.data.inserted} rows.`);
      await queryClient.invalidateQueries({ queryKey: ["transactions"] });
    },
    onError: (error: unknown) => {
      setCsvResult("CSV import failed. Check row-level errors from the API.");
      console.error(error);
    }
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="Transactions"
        title="A ledger built for manual discipline."
        description="Every buy and redemption stays explicit. The form is minimal, the table is audit-friendly, and CSV import preserves atomic validation."
        action={
          <button
            className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine"
            onClick={() => setShowComposer(true)}
          >
            Add Transaction
          </button>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[0.9fr_1.1fr]">
        <Panel>
          <SectionHeader
            eyebrow="Ledger"
            title={`Buys ${totals.buys} · Sells ${totals.sells}`}
            description="This table mirrors the backend order records. It is intentionally plain so anomalies are visible."
          />
          <div className="overflow-hidden rounded-[24px] border border-ink/10">
            <table className="min-w-full divide-y divide-ink/10 text-sm">
              <thead className="bg-paper/80">
                <tr>
                  {["Fund", "Action", "Shares", "Amount", "NAV", "Fee", "Trade Date"].map((column) => (
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
                    <td className="px-4 py-3 uppercase tracking-[0.2em] text-ink/55">{item.action}</td>
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
          <div className="mt-6">
            <label className="inline-flex rounded-full border border-ink/10 px-5 py-3 text-sm font-semibold text-ink transition hover:border-accent hover:text-accent">
              Upload CSV
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
            <p className="mt-4 text-sm text-ink/60">
              Buy rows use `amount`, redemption rows use `shares`. CSV schema:
              `fund_code,action,amount_or_shares,nav,fee,traded_at`.
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
                <p className="text-xs uppercase tracking-[0.35em] text-accent">Record Order</p>
                <h3 className="mt-3 font-display text-3xl">Add a new transaction</h3>
              </div>
              <button
                className="rounded-full border border-ink/10 px-4 py-2 text-sm font-semibold"
                onClick={() => setShowComposer(false)}
              >
                Close
              </button>
            </div>
            <div className="mt-5 grid gap-4 md:grid-cols-2">
              <label className="text-sm">
                Fund Code
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.fund_code}
                  onChange={(event) => setForm((current) => ({ ...current, fund_code: event.target.value }))}
                />
              </label>
              <label className="text-sm">
                Action
                <select
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.action}
                  onChange={(event) => setForm((current) => ({ ...current, action: event.target.value }))}
                >
                  <option value="buy">buy</option>
                  <option value="sell">sell</option>
                </select>
              </label>
              <label className="text-sm">
                Amount
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.amount}
                  onChange={(event) => setForm((current) => ({ ...current, amount: event.target.value }))}
                />
              </label>
              <label className="text-sm">
                Shares
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.shares}
                  onChange={(event) => setForm((current) => ({ ...current, shares: event.target.value }))}
                />
              </label>
              <label className="text-sm">
                NAV
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.nav_at_trade}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, nav_at_trade: event.target.value }))
                  }
                />
              </label>
              <label className="text-sm">
                Fee
                <input
                  className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                  value={form.fee}
                  onChange={(event) => setForm((current) => ({ ...current, fee: event.target.value }))}
                />
              </label>
              <label className="text-sm md:col-span-2">
                Trade Date
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
                Save Transaction
              </button>
            </div>
          </Panel>
        </div>
      ) : null}
    </div>
  );
}
