"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { NotificationSettings } from "@/lib/types";

export default function NotificationSettingsPage() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["notification-settings"],
    queryFn: async () => (await api.get<NotificationSettings>("/api/settings/notifications")).data
  });
  const [form, setForm] = useState({
    recipient_email: "owner@example.com",
    reminder_day: "1",
    reference_index_code: "CSI300",
    base_monthly_amount: "833",
    smtp_host: "smtp.example.com",
    smtp_port: "587",
    smtp_username: "mailer@example.com",
    smtp_from: "FundScope <mailer@example.com>",
    smtp_password: ""
  });
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
        smtp_host: form.smtp_host,
        smtp_port: Number(form.smtp_port),
        smtp_username: form.smtp_username,
        smtp_from: form.smtp_from,
        smtp_password: form.smtp_password || undefined
      }),
    onSuccess: async () => {
      setStatusText("Settings saved.");
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
    onSuccess: () => setStatusText("Test email accepted."),
    onError: () => setStatusText("SMTP test failed.")
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="Settings"
        title="Monthly reminder settings, kept explicit."
        description="SMTP transport, reminder cadence, base amount, and reference index live together so the DCA rule is auditable."
      />

      <Panel className="max-w-4xl">
        <div className="grid gap-4 md:grid-cols-2">
          {[
            ["Recipient", "recipient_email"],
            ["Reminder Day", "reminder_day"],
            ["Reference Index", "reference_index_code"],
            ["Base Amount", "base_monthly_amount"],
            ["SMTP Host", "smtp_host"],
            ["SMTP Port", "smtp_port"],
            ["SMTP Username", "smtp_username"],
            ["SMTP From", "smtp_from"],
            ["SMTP Password", "smtp_password"]
          ].map(([label, key]) => (
            <label key={key} className="text-sm">
              {label}
              <input
                className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3"
                type={key === "smtp_password" ? "password" : "text"}
                value={form[key as keyof typeof form]}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    [key]: event.target.value
                  }))
                }
              />
            </label>
          ))}
        </div>
        <div className="mt-6 flex flex-wrap gap-3">
          <button
            className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine"
            onClick={() => saveSettings.mutate()}
          >
            Save Settings
          </button>
          <button
            className="rounded-full border border-ink/10 px-5 py-3 text-sm font-semibold text-ink transition hover:border-accent hover:text-accent"
            onClick={() => testSend.mutate()}
          >
            Send Test Email
          </button>
        </div>
        {statusText ? <p className="mt-4 text-sm text-accent">{statusText}</p> : null}
      </Panel>
    </div>
  );
}
