"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth, CurrentUser } from "../auth-provider";
import { api } from "@/lib/api";

type LoginResponse = {
  access_token: string;
  expires_at: string;
  user: CurrentUser;
};

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
  return "登录失败，请检查邮箱和密码";
}

function errorStatus(error: unknown): number | null {
  if (
    typeof error === "object" &&
    error !== null &&
    "response" in error &&
    typeof error.response === "object" &&
    error.response !== null &&
    "status" in error.response
  ) {
    return Number(error.response.status);
  }
  return null;
}

export default function LoginPage() {
  const router = useRouter();
  const { login, user } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [nextPath, setNextPath] = useState("/short-term");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setNextPath(params.get("next") || "/short-term");
  }, []);

  useEffect(() => {
    if (user) {
      router.replace(nextPath);
    }
  }, [nextPath, router, user]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const response = await api.post<LoginResponse>("/api/auth/login", { email, password });
      login(response.data.access_token, response.data.user);
      router.replace(nextPath);
    } catch (error: unknown) {
      if (errorStatus(error) === 403) {
        router.replace("/pending-approval");
        return;
      }
      setError(errorDetail(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="mx-auto max-w-xl rounded-[28px] border border-ink/10 bg-white p-8 shadow-card">
      <p className="text-sm font-semibold text-accent">账号登录</p>
      <h2 className="mt-3 font-display text-3xl font-semibold">登录 FundScope</h2>
      <p className="mt-3 text-sm leading-7 text-ink/70">
        使用已批准的邮箱账号登录。新账号注册后需要 qje 管理员批准。
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
          密码
          <input
            className="mt-2 w-full rounded-2xl border border-ink/10 px-4 py-3"
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
          {submitting ? "正在登录..." : "登录"}
        </button>
      </form>
      <p className="mt-6 text-sm text-ink/70">
        还没有账号？{" "}
        <Link className="font-semibold text-accent" href="/register">
          去注册
        </Link>
      </p>
    </section>
  );
}
