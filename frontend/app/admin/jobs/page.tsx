"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { JobRun } from "@/lib/types";

export default function AdminJobsPage() {
  const queryClient = useQueryClient();
  const jobs = useQuery({
    queryKey: ["job-runs"],
    queryFn: async () => (await api.get<JobRun[]>("/api/admin/jobs")).data
  });

  const triggerJob = useMutation({
    mutationFn: async (jobName: string) => api.post(`/api/admin/jobs/${jobName}/run`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["job-runs"] });
    }
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="Admin"
        title="A compact ledger for scheduled work."
        description="Every important recurring task can be replayed on demand. Recent runs stay visible with timestamps, status, and failure detail."
        action={
          <div className="flex flex-wrap gap-3">
            <button
              className="rounded-full bg-ink px-4 py-2 text-sm font-semibold text-white"
              onClick={() => triggerJob.mutate("monthly_dca_reminder")}
            >
              Run Monthly Reminder
            </button>
            <button
              className="rounded-full border border-ink/10 px-4 py-2 text-sm font-semibold text-ink"
              onClick={() => triggerJob.mutate("news_summary_backfill")}
            >
              Retry News Summaries
            </button>
          </div>
        }
      />

      <Panel>
        <div className="overflow-hidden rounded-[24px] border border-ink/10">
          <table className="min-w-full divide-y divide-ink/10 text-sm">
            <thead className="bg-paper/80">
              <tr>
                {["Job", "Status", "Started", "Finished", "Details"].map((column) => (
                  <th key={column} className="px-4 py-3 text-left font-medium text-ink/55">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-ink/10 bg-white">
              {(jobs.data ?? []).map((job) => (
                <tr key={job.id}>
                  <td className="px-4 py-3 font-semibold">{job.job_name}</td>
                  <td className="px-4 py-3 uppercase tracking-[0.2em] text-ink/55">{job.status}</td>
                  <td className="px-4 py-3">{formatDate(job.started_at)}</td>
                  <td className="px-4 py-3">{formatDate(job.finished_at)}</td>
                  <td className="px-4 py-3 text-ink/60">
                    {job.error_message ?? JSON.stringify(job.details)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
