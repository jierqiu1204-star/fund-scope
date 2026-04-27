"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";

export default function OnboardingPage() {
  const [statusText, setStatusText] = useState("");
  const applyDefault = useMutation({
    mutationFn: async (mode: "merge" | "replace") =>
      api.post(`/api/onboarding/apply-default-portfolio?force=true&mode=${mode}`),
    onSuccess: (_, mode) => setStatusText(`Default portfolio applied in ${mode} mode.`),
    onError: () => setStatusText("The watchlist already exists. Choose merge or replace.")
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="Onboarding"
        title="Start with a lazy-DCA default, then adapt deliberately."
        description="FundScope seeds a broad starter mix without creating any fake transactions, so the first real order still belongs to you."
      />
      <div className="grid gap-8 lg:grid-cols-[1.1fr_0.9fr]">
        <Panel className="bg-[linear-gradient(135deg,_rgba(31,92,75,0.08),_rgba(255,255,255,0.95))]">
          <p className="text-xs uppercase tracking-[0.35em] text-accent">Default Mix</p>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            {[
              ["007339", "沪深 300", "40%"],
              ["001052", "标普 500", "30%"],
              ["270042", "纳斯达克 100", "20%"],
              ["000198", "货币基金", "10%"]
            ].map(([code, name, weight]) => (
              <div key={code} className="rounded-[24px] border border-ink/10 bg-white/80 p-5">
                <p className="text-xs uppercase tracking-[0.35em] text-accent">{code}</p>
                <h3 className="mt-3 font-display text-2xl">{name}</h3>
                <p className="mt-2 text-sm text-ink/60">Target weight {weight}</p>
              </div>
            ))}
          </div>
        </Panel>

        <Panel>
          <p className="text-xs uppercase tracking-[0.35em] text-accent">Apply Mode</p>
          <h3 className="mt-3 font-display text-3xl">Merge if you’re experimenting. Replace if you want a clean sheet.</h3>
          <p className="mt-4 text-sm leading-7 text-ink/70">
            Merge preserves current watchlist rows and overlays the starter allocation. Replace resets the watchlist to the default four funds.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <button
              className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white"
              onClick={() => applyDefault.mutate("merge")}
            >
              Merge Existing Watchlist
            </button>
            <button
              className="rounded-full border border-ink/10 px-5 py-3 text-sm font-semibold text-ink"
              onClick={() => applyDefault.mutate("replace")}
            >
              Replace Watchlist
            </button>
          </div>
          {statusText ? <p className="mt-4 text-sm text-accent">{statusText}</p> : null}
        </Panel>
      </div>
    </div>
  );
}
