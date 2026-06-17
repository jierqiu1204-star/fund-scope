"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CurrentUser, useAuth } from "../../auth-provider";
import { api } from "@/lib/api";

type AdminUser = CurrentUser & {
  last_login_at: string | null;
};

export default function AdminUsersPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const usersQuery = useQuery({
    queryKey: ["admin-users"],
    queryFn: async () => (await api.get<AdminUser[]>("/api/admin/users")).data,
    enabled: Boolean(user?.is_super_admin),
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
        <p className="mt-3 text-sm text-ink/70">只有 qje 超级管理员可以审批用户。</p>
      </section>
    );
  }

  return (
    <section className="rounded-[28px] border border-ink/10 bg-white p-8 shadow-card">
      <p className="text-sm font-semibold text-accent">后台管理</p>
      <h2 className="mt-3 font-display text-3xl font-semibold">用户审批</h2>
      <p className="mt-3 text-sm leading-7 text-ink/70">
        新注册账号默认不可用。批准后，该用户可以登录并管理自己的买入追踪和提醒邮箱。
      </p>
      <div className="mt-8 overflow-hidden rounded-3xl border border-ink/10">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="bg-paper text-ink/60">
            <tr>
              <th className="px-4 py-3">用户</th>
              <th className="px-4 py-3">收件邮箱</th>
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
                <td className="px-4 py-4">{item.recipient_email}</td>
                <td className="px-4 py-4">
                  {item.is_super_admin ? "超级管理员" : item.is_approved ? "已批准" : "待批准"}
                </td>
                <td className="px-4 py-4">{item.last_login_at ? new Date(item.last_login_at).toLocaleString() : "暂无"}</td>
                <td className="px-4 py-4">
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
    </section>
  );
}
