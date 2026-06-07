import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";
import { ReactNode } from "react";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "FundScope",
  description: "个人基金和 ETF 短线研究工具"
};

const navItems = [
  { href: "/short-term", label: "短线研究" },
  { href: "/portfolio", label: "资产" },
  { href: "/transactions", label: "交易" },
  { href: "/valuation", label: "估值" },
  { href: "/strategy-lab", label: "高级策略" },
  { href: "/news", label: "新闻" },
  { href: "/settings/notifications", label: "设置" },
  { href: "/admin/jobs", label: "任务" }
];

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-paper font-body text-ink">
        <Providers>
          <div className="mx-auto flex min-h-screen max-w-7xl flex-col px-6 pb-12 pt-8">
            <header className="mb-10 overflow-hidden rounded-[28px] border border-ink/10 bg-white/70 shadow-card backdrop-blur">
              <div className="grid gap-8 border-b border-ink/10 bg-[radial-gradient(circle_at_top_left,_rgba(181,83,45,0.12),_transparent_35%),linear-gradient(135deg,_rgba(255,255,255,0.95),_rgba(247,243,233,0.88))] p-6 md:grid-cols-[1.4fr_0.8fr]">
                <div>
                  <p className="text-sm uppercase tracking-[0.3em] text-accent">FundScope</p>
                  <h1 className="mt-3 font-display text-4xl font-semibold md:text-5xl">
                    基金和 ETF 的短线研究台
                  </h1>
                  <p className="mt-4 max-w-2xl text-sm leading-7 text-ink/70">
                    把公开净值、ETF 日线、近期涨跌、回撤、波动和投资方向放到一个页面里。
                    系统只做研究和模拟，不连接支付宝账户，也不会自动下单。
                  </p>
                </div>
                <div className="rounded-[28px] border border-ink/10 bg-ink px-6 py-5 text-white">
                  <p className="text-xs uppercase tracking-[0.35em] text-white/60">使用原则</p>
                  <p className="mt-4 font-display text-3xl leading-tight">
                    先看数据，再看风险。标签只代表观察优先级，不代表未来收益。
                  </p>
                  <Link
                    className="mt-6 inline-flex rounded-full bg-white px-5 py-3 text-sm font-semibold text-ink transition hover:bg-accentSoft"
                    href="/short-term"
                  >
                    进入短线研究
                  </Link>
                </div>
              </div>
              <nav className="flex flex-wrap gap-3 p-4">
                {navItems.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="rounded-full border border-ink/10 bg-white px-4 py-2 text-sm transition hover:border-accent hover:text-accent"
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
            </header>
            <main className="flex-1">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
