"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, AUTH_TOKEN_KEY } from "@/lib/api";

export type CurrentUser = {
  id: number;
  email: string;
  display_name: string | null;
  recipient_email: string;
  is_approved: boolean;
  is_super_admin: boolean;
  created_at: string;
};

type AuthContextValue = {
  user: CurrentUser | null;
  loading: boolean;
  token: string | null;
  login: (token: string, user: CurrentUser) => void;
  logout: () => void;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const publicPaths = new Set(["/login", "/register", "/pending-approval"]);

export function AuthProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
    setToken(null);
    setUser(null);
    router.replace("/login");
  }, [router]);

  const refresh = useCallback(async () => {
    const storedToken = window.localStorage.getItem(AUTH_TOKEN_KEY);
    setToken(storedToken);
    if (!storedToken) {
      setUser(null);
      return;
    }
    try {
      const response = await api.get<CurrentUser>("/api/auth/me");
      setUser(response.data);
    } catch {
      window.localStorage.removeItem(AUTH_TOKEN_KEY);
      setToken(null);
      setUser(null);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    async function load() {
      setLoading(true);
      await refresh();
      if (mounted) {
        setLoading(false);
      }
    }
    void load();
    return () => {
      mounted = false;
    };
  }, [refresh]);

  useEffect(() => {
    if (loading) {
      return;
    }
    if (!token && !publicPaths.has(pathname)) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      return;
    }
    if (token && user && !user.is_approved && pathname !== "/pending-approval") {
      router.replace("/pending-approval");
    }
  }, [loading, pathname, router, token, user]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      token,
      login: (nextToken, nextUser) => {
        window.localStorage.setItem(AUTH_TOKEN_KEY, nextToken);
        setToken(nextToken);
        setUser(nextUser);
      },
      logout,
      refresh,
    }),
    [loading, logout, refresh, token, user]
  );

  if (loading) {
    return (
      <div className="rounded-[28px] border border-ink/10 bg-white p-8 text-sm text-ink/70">
        正在检查登录状态...
      </div>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return value;
}
