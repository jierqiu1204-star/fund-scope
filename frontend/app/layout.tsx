import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";
import { ReactNode } from "react";

import { AuthStatus } from "./auth-status";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "FundScope",
  description: "个人基金和 ETF 短线研究工具"
};

const primaryNavItems = [
  { href: "/short-term", label: "短线研究", primary: true },
  { href: "/short-term/evidence", label: "策略证据" },
  ...(process.env.NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED === "true"
    ? [{ href: "/short-term/leader-tactics", label: "Leader tactics V2" }]
    : []),
  { href: "/admin/jobs", label: "数据任务" },
  { href: "/settings/notifications", label: "设置" }
];

const advancedNavItems = [
  { href: "/portfolio", label: "资产" },
  { href: "/transactions", label: "交易" },
  { href: "/valuation", label: "估值" },
  { href: "/strategy-lab", label: "高级策略" },
  { href: "/news", label: "新闻" }
];

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-paper font-body text-ink antialiased">
        <Providers>
          <div className="mx-auto flex min-h-screen max-w-[1440px] flex-col px-4 py-4 sm:px-6 lg:px-8">
            <header className="sticky top-0 z-40 mb-6 rounded-[12px] border border-border bg-white/95 shadow-card backdrop-blur">
              <div className="flex flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
                <Link href="/short-term" className="min-w-0">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">
                    FundScope
                  </p>
                  <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <h1 className="text-xl font-semibold leading-7 tracking-[-0.02em] text-ink">
                      短线研究工作台
                    </h1>
                    <p className="text-sm text-ink/55">
                      ETF / 基金短线排序、持仓追踪与邮件提醒
                    </p>
                  </div>
                </Link>
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
                  <nav className="flex flex-wrap gap-1.5">
                    {primaryNavItems.map((item) => (
                      <Link
                        key={item.href}
                        href={item.href}
                        className={`rounded-[6px] border px-3 py-2 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                          item.primary
                            ? "border-ink bg-ink text-white hover:bg-ink/90"
                            : "border-border bg-white text-ink/75 hover:border-ink/30 hover:text-ink"
                        }`}
                      >
                        {item.label}
                      </Link>
                    ))}
                    <details className="group relative">
                      <summary className="cursor-pointer list-none rounded-[6px] border border-border bg-white px-3 py-2 text-sm font-medium text-ink/75 transition hover:border-ink/30 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
                        高级功能
                      </summary>
                      <div className="mt-2 flex flex-wrap gap-1.5 rounded-[8px] border border-border bg-white p-2 shadow-card lg:absolute lg:right-0 lg:z-50 lg:w-48">
                        {advancedNavItems.map((item) => (
                          <Link
                            key={item.href}
                            href={item.href}
                            className="rounded-[6px] px-3 py-2 text-sm font-medium text-ink/70 transition hover:bg-paper hover:text-ink"
                          >
                            {item.label}
                          </Link>
                        ))}
                      </div>
                    </details>
                  </nav>
                  <div className="lg:ml-2">
                    <AuthStatus />
                  </div>
                </div>
              </div>
            </header>
            <main className="flex-1">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
