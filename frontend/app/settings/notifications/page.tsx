"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { NotificationSettings } from "@/lib/types";

type NotificationForm = ReturnType<typeof defaultForm>;
type TextFieldKey = Exclude<keyof NotificationForm, "allow_full_exit">;

const fields: Array<[string, TextFieldKey]> = [
  ["收件邮箱", "recipient_email"],
  ["每月提醒日", "reminder_day"],
  ["参考指数", "reference_index_code"],
  ["基础月投入金额", "base_monthly_amount"],
  ["ETF账户资金", "etf_trading_capital"],
  ["SMTP 服务器", "smtp_host"],
  ["SMTP 端口", "smtp_port"],
  ["SMTP 用户名", "smtp_username"],
  ["发件人", "smtp_from"],
  ["SMTP 密码", "smtp_password"]
];

function defaultForm() {
  return {
    recipient_email: "19535838578@163.com",
    reminder_day: "1",
    reference_index_code: "CSI300",
    base_monthly_amount: "833",
    etf_trading_capital: "10000",
    allow_full_exit: true,
    smtp_host: "smtp.163.com",
    smtp_port: "465",
    smtp_username: "19535838578@163.com",
    smtp_from: "FundScope <19535838578@163.com>",
    smtp_password: ""
  };
}

export default function NotificationSettingsPage() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["notification-settings"],
    queryFn: async () => (await api.get<NotificationSettings>("/api/settings/notifications")).data
  });
  const [form, setForm] = useState(defaultForm());
  const [statusText, setStatusText] = useState("");

  useEffect(() => {
    if (!settings.data) {
      return;
    }
    setForm((current) => ({
      ...current,
      recipient_email: settings.data.recipient_email,
      reminder_day: String(settings.data.reminder_day),
      reference_index_code: settings.data.reference_index_code ?? "CSI300",
      base_monthly_amount: String(settings.data.base_monthly_amount),
      etf_trading_capital: String(settings.data.etf_trading_capital ?? 10000),
      allow_full_exit: settings.data.allow_full_exit ?? true,
      smtp_host: settings.data.smtp_host ?? "",
      smtp_port: String(settings.data.smtp_port ?? 587),
      smtp_username: settings.data.smtp_username ?? "",
      smtp_from: settings.data.smtp_from ?? ""
    }));
  }, [settings.data]);

  const saveSettings = useMutation({
    mutationFn: async () =>
      api.put("/api/settings/notifications", {
        recipient_email: form.recipient_email,
        reminder_day: Number(form.reminder_day),
        reference_index_code: form.reference_index_code,
        base_monthly_amount: Number(form.base_monthly_amount),
        etf_trading_capital: Number(form.etf_trading_capital),
        allow_full_exit: form.allow_full_exit,
        smtp_host: form.smtp_host,
        smtp_port: Number(form.smtp_port),
        smtp_username: form.smtp_username,
        smtp_from: form.smtp_from,
        smtp_password: form.smtp_password || undefined
      }),
    onSuccess: async () => {
      setStatusText("设置已保存。");
      await queryClient.invalidateQueries({ queryKey: ["notification-settings"] });
    }
  });

  const testSend = useMutation({
    mutationFn: async () =>
      api.post("/api/settings/notifications/test-send", {
        smtp_host: form.smtp_host,
        smtp_port: Number(form.smtp_port),
        smtp_username: form.smtp_username,
        smtp_password: form.smtp_password,
        smtp_from: form.smtp_from,
        recipient_email: form.recipient_email
      }),
    onSuccess: () => setStatusText("测试邮件已发送，请检查收件箱。"),
    onError: () => setStatusText("SMTP 登录或发送失败，请检查授权码、端口和发件邮箱。")
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="设置"
        title="定投提醒和邮件发送配置。"
        description="这里配置每月提醒日、参考指数、基础投入金额和 SMTP 邮件服务器。163 邮箱需要填写授权码，不要填写网页登录密码。"
      />

      <Panel className="max-w-4xl">
        <div className="grid gap-4 md:grid-cols-2">
          {fields.map(([label, key]) => (
            <label key={key} className="text-sm">
              {label}
              <input
                className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                type={key === "smtp_password" ? "password" : "text"}
                value={form[key]}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    [key]: event.target.value
                  }))
                }
              />
            </label>
          ))}
          <label className="flex items-center gap-3 rounded-[8px] border border-ink/10 bg-paper px-4 py-3 text-sm">
            <input
              type="checkbox"
              checked={form.allow_full_exit}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  allow_full_exit: event.target.checked
                }))
              }
            />
            允许清仓建议
          </label>
        </div>
        <div className="mt-6 flex flex-wrap gap-3">
          <button
            className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine"
            onClick={() => saveSettings.mutate()}
          >
            保存设置
          </button>
          <button
            className="rounded-full border border-ink/10 px-5 py-3 text-sm font-semibold text-ink transition hover:border-accent hover:text-accent"
            onClick={() => testSend.mutate()}
          >
            发送测试邮件
          </button>
        </div>
        {statusText ? <p className="mt-4 text-sm text-accent">{statusText}</p> : null}
      </Panel>
    </div>
  );
}
