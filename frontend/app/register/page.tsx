"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api";

function errorDetail(error: unknown): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "response" in error &&
    typeof error.response === "object" &&
    error.response !== null &&
    "data" in error.response &&
    typeof error.response.data === "object" &&
    error.response.data !== null &&
    "detail" in error.response.data
  ) {
    return String(error.response.data.detail);
  }
  return "注册失败，请稍后再试";
}

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await api.post("/api/auth/register", {
        email,
        display_name: displayName || null,
        password,
      });
      router.replace("/pending-approval");
    } catch (error: unknown) {
      setError(errorDetail(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="mx-auto max-w-xl rounded-[28px] border border-ink/10 bg-white p-8 shadow-card">
      <p className="text-sm font-semibold text-accent">账号注册</p>
      <h2 className="mt-3 font-display text-3xl font-semibold">申请 FundScope 账号</h2>
      <p className="mt-3 text-sm leading-7 text-ink/70">
        注册后默认不可用，需要 qje 管理员在后台批准。批准后，你的买入追踪和提醒邮箱会独立保存。
      </p>
      <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
        <label className="block text-sm font-semibold">
          邮箱
          <input
            className="mt-2 w-full rounded-2xl border border-ink/10 px-4 py-3"
            onChange={(event) => setEmail(event.target.value)}
            required
            type="email"
            value={email}
          />
        </label>
        <label className="block text-sm font-semibold">
          昵称
          <input
            className="mt-2 w-full rounded-2xl border border-ink/10 px-4 py-3"
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="可选"
            value={displayName}
          />
        </label>
        <label className="block text-sm font-semibold">
          密码
          <input
            className="mt-2 w-full rounded-2xl border border-ink/10 px-4 py-3"
            minLength={8}
            onChange={(event) => setPassword(event.target.value)}
            required
            type="password"
            value={password}
          />
        </label>
        {error ? <p className="rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p> : null}
        <button
          className="w-full rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white disabled:opacity-50"
          disabled={submitting}
          type="submit"
        >
          {submitting ? "正在提交..." : "提交注册"}
        </button>
      </form>
      <p className="mt-6 text-sm text-ink/70">
        已有账号？{" "}
        <Link className="font-semibold text-accent" href="/login">
          去登录
        </Link>
      </p>
    </section>
  );
}
