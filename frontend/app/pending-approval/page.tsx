"use client";

import Link from "next/link";

export default function PendingApprovalPage() {
  return (
    <section className="mx-auto max-w-xl rounded-[28px] border border-ink/10 bg-white p-8 shadow-card">
      <p className="text-sm font-semibold text-accent">等待审批</p>
      <h2 className="mt-3 font-display text-3xl font-semibold">账号还不能使用</h2>
      <p className="mt-4 text-sm leading-7 text-ink/70">
        你的账号已经提交，但还需要 qje 管理员批准。批准后再登录，就能使用短线研究、买入追踪和邮件提醒。
      </p>
      <Link className="mt-8 inline-flex rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white" href="/login">
        返回登录
      </Link>
    </section>
  );
}
