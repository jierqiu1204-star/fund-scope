"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CurrentUser, useAuth } from "../../auth-provider";
import { api } from "@/lib/api";
import { TrackedPosition, TrackedPositionAlert } from "@/lib/types";

type AdminUser = CurrentUser & {
  last_login_at: string | null;
  detail_url: string;
  notification_configured: boolean;
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_username_masked: string | null;
  smtp_from: string | null;
  tracking_summary: Record<string, number>;
};

function formatDateTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "暂无";
}

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "暂无";
  }
  return `¥${value.toFixed(2)}`;
}

export default function AdminUsersPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const usersQuery = useQuery({
    queryKey: ["admin-users"],
    queryFn: async () => (await api.get<AdminUser[]>("/api/admin/users")).data,
    enabled: Boolean(user?.is_super_admin),
  });
  const selectedUser = useMemo(
    () => (usersQuery.data || []).find((item) => item.id === selectedUserId) || null,
    [selectedUserId, usersQuery.data]
  );
  const positionsQuery = useQuery({
    queryKey: ["admin-users", selectedUserId, "tracked-positions"],
    queryFn: async () => (await api.get<TrackedPosition[]>(`/api/admin/users/${selectedUserId}/tracked-positions`)).data,
    enabled: Boolean(user?.is_super_admin && selectedUserId),
  });
  const alertsQuery = useQuery({
    queryKey: ["admin-users", selectedUserId, "alerts"],
    queryFn: async () => (await api.get<TrackedPositionAlert[]>(`/api/admin/users/${selectedUserId}/alerts`)).data,
    enabled: Boolean(user?.is_super_admin && selectedUserId),
  });
  const approvalMutation = useMutation({
    mutationFn: async ({ userId, isApproved }: { userId: number; isApproved: boolean }) =>
      (await api.patch(`/api/admin/users/${userId}/approval`, { is_approved: isApproved })).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });

  if (!user?.is_super_admin) {
    return (
      <section className="rounded-[28px] border border-ink/10 bg-white p-8 shadow-card">
        <h2 className="font-display text-3xl font-semibold">需要管理员权限</h2>
        <p className="mt-3 text-sm text-ink/70">只有超级管理员可以审批用户和只读查看账号情况。</p>
      </section>
    );
  }

  return (
    <section className="rounded-[28px] border border-ink/10 bg-white p-8 shadow-card">
      <p className="text-sm font-semibold text-accent">后台管理</p>
      <h2 className="mt-3 font-display text-3xl font-semibold">用户审批与只读观察</h2>
      <p className="mt-3 text-sm leading-7 text-ink/70">
        新注册账号默认不可用。批准后，该用户可以登录并管理自己的买入追踪和提醒邮箱；管理员详情区只读，不修改用户持仓。
      </p>
      <div className="mt-8 grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
        <div className="overflow-hidden rounded-3xl border border-ink/10">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-paper text-ink/60">
              <tr>
                <th className="px-4 py-3">用户</th>
                <th className="px-4 py-3">收件邮箱</th>
                <th className="px-4 py-3">追踪</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3">最近登录</th>
                <th className="px-4 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {(usersQuery.data || []).map((item) => (
                <tr className="border-t border-ink/10" key={item.id}>
                  <td className="px-4 py-4">
                    <div className="font-semibold">{item.display_name || "未填写昵称"}</div>
                    <div className="text-ink/60">{item.email}</div>
                  </td>
                  <td className="px-4 py-4">
                    <div>{item.recipient_email}</div>
                    <div className="text-xs text-ink/50">SMTP {item.notification_configured ? "已配置" : "未配置"}</div>
                  </td>
                  <td className="px-4 py-4">
                    活跃 {item.tracking_summary.active || 0} / 共 {item.tracking_summary.total || 0}
                  </td>
                  <td className="px-4 py-4">
                    {item.is_super_admin ? "超级管理员" : item.is_approved ? "已批准" : "待批准"}
                  </td>
                  <td className="px-4 py-4">{formatDateTime(item.last_login_at)}</td>
                  <td className="space-x-2 px-4 py-4">
                    <button
                      className="rounded-full border border-ink/10 px-4 py-2 text-ink hover:bg-paper"
                      onClick={() => setSelectedUserId(item.id)}
                      type="button"
                    >
                      只读查看
                    </button>
                    {item.is_super_admin ? (
                      <span className="text-ink/50">不可停用</span>
                    ) : (
                      <button
                        className="rounded-full bg-ink px-4 py-2 text-white disabled:opacity-50"
                        disabled={approvalMutation.isPending}
                        onClick={() =>
                          approvalMutation.mutate({
                            userId: item.id,
                            isApproved: !item.is_approved,
                          })
                        }
                        type="button"
                      >
                        {item.is_approved ? "停用" : "批准"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <aside className="rounded-3xl border border-ink/10 bg-paper/60 p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold text-accent">只读模式</p>
              <h3 className="mt-1 text-xl font-semibold text-ink">{selectedUser?.display_name || selectedUser?.email || "选择一个用户"}</h3>
            </div>
            {selectedUser ? <span className="rounded-full bg-white px-3 py-1 text-xs text-ink/60">不能编辑持仓</span> : null}
          </div>
          {selectedUser ? (
            <>
              <div className="mt-4 grid gap-3 text-sm text-ink/70 sm:grid-cols-2">
                <div className="rounded-2xl bg-white p-3">
                  <p className="text-xs text-ink/45">邮箱</p>
                  <p className="mt-1 break-all font-medium text-ink">{selectedUser.recipient_email}</p>
                </div>
                <div className="rounded-2xl bg-white p-3">
                  <p className="text-xs text-ink/45">SMTP</p>
                  <p className="mt-1 font-medium text-ink">{selectedUser.notification_configured ? "已配置" : "未配置"}</p>
                </div>
              </div>
              <div className="mt-5">
                <p className="text-sm font-semibold text-ink">追踪持仓</p>
                <div className="mt-3 space-y-3">
                  {(positionsQuery.data || []).map((position) => (
                    <div key={position.id} className="rounded-2xl bg-white p-4 text-sm">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="font-semibold text-ink">{position.asset_name} <span className="text-ink/40">{position.asset_code}</span></p>
                          <p className="mt-1 text-ink/55">{position.status} · {position.buy_date} · {formatMoney(position.buy_amount)}</p>
                        </div>
                        <span className="rounded-full bg-paper px-2.5 py-1 text-xs text-ink/60">{position.exit_signal.label}</span>
                      </div>
                      <p className="mt-2 text-ink/70">估算盈亏：{formatMoney(position.current_snapshot.estimated_pnl)} / {position.current_snapshot.estimated_pnl_pct === null ? "暂无" : `${position.current_snapshot.estimated_pnl_pct.toFixed(2)}%`}</p>
                    </div>
                  ))}
                  {!positionsQuery.isLoading && !(positionsQuery.data || []).length ? (
                    <p className="rounded-2xl bg-white p-4 text-sm text-ink/55">暂无追踪持仓。</p>
                  ) : null}
                </div>
              </div>
              <div className="mt-5">
                <p className="text-sm font-semibold text-ink">最近提醒</p>
                <div className="mt-3 space-y-3">
                  {(alertsQuery.data || []).slice(0, 8).map((alert) => (
                    <div key={alert.id} className="rounded-2xl bg-white p-4 text-sm">
                      <p className="font-semibold text-ink">{alert.trigger_label} · {alert.alert_type}</p>
                      <p className="mt-1 text-ink/55">{alert.alert_date} · 邮件 {alert.email_status}</p>
                      <p className="mt-2 text-ink/65">{alert.reasons.slice(0, 2).join("；") || "暂无原因"}</p>
                    </div>
                  ))}
                  {!alertsQuery.isLoading && !(alertsQuery.data || []).length ? (
                    <p className="rounded-2xl bg-white p-4 text-sm text-ink/55">暂无提醒记录。</p>
                  ) : null}
                </div>
              </div>
            </>
          ) : (
            <p className="mt-4 rounded-2xl bg-white p-4 text-sm text-ink/55">点击用户行里的“只读查看”，这里会显示该账号的追踪和提醒情况。</p>
          )}
        </aside>
      </div>
    </section>
  );
}
