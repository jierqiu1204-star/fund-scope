"use client";

import Link from "next/link";

import { useAuth } from "./auth-provider";

export function AuthStatus() {
  const { user, logout } = useAuth();
  if (!user) {
    return (
      <Link className="rounded-full bg-ink px-4 py-2 text-sm font-semibold text-white" href="/login">
        登录
      </Link>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <span className="rounded-full bg-accentSoft px-4 py-2 text-ink">
        {user.display_name || user.email}
      </span>
      {user.is_super_admin ? (
        <Link className="rounded-full border border-ink/10 bg-white px-4 py-2" href="/admin/users">
          用户审批
        </Link>
      ) : null}
      <button
        className="rounded-full border border-ink/10 bg-white px-4 py-2 text-ink/70 transition hover:text-accent"
        onClick={logout}
        type="button"
      >
        退出
      </button>
    </div>
  );
}
