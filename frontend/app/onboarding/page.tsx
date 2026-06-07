"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";

const fundGroups = [
  ["宽基指数", "沪深300、中证500、创业板、科创50、上证50、红利指数"],
  ["行业主题", "消费、医药、银行、证券、军工、电子、计算机、传媒"],
  ["稳健资产", "短债、纯债、黄金联接"],
  ["海外市场", "纳斯达克100、标普500、恒生、中国互联网"]
];

export default function OnboardingPage() {
  const [statusText, setStatusText] = useState("");
  const applyDefault = useMutation({
    mutationFn: async (mode: "merge" | "replace") =>
      api.post(`/api/onboarding/apply-default-portfolio?force=true&mode=${mode}`),
    onSuccess: (_, mode) =>
      setStatusText(mode === "merge" ? "已合并默认研究基金池。" : "已替换为默认研究基金池。"),
    onError: () => setStatusText("自选池已经存在，请选择合并或替换。")
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="初始化"
        title="先放入一组公开基金数据，后面再慢慢筛。"
        description="系统会加入约 30 只常见基金作为研究候选池，用来拉公开净值、做回测和模拟盘；这不是买入建议，也不会创建真实交易。"
      />
      <div className="grid gap-8 lg:grid-cols-[1.1fr_0.9fr]">
        <Panel className="bg-[linear-gradient(135deg,_rgba(31,92,75,0.08),_rgba(255,255,255,0.95))]">
          <p className="text-xs uppercase tracking-[0.35em] text-accent">默认研究池</p>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            {fundGroups.map(([title, description]) => (
              <div key={title} className="rounded-[24px] border border-ink/10 bg-white/80 p-5">
                <h3 className="font-display text-2xl">{title}</h3>
                <p className="mt-3 text-sm leading-6 text-ink/65">{description}</p>
              </div>
            ))}
          </div>
        </Panel>

        <Panel>
          <p className="text-xs uppercase tracking-[0.35em] text-accent">应用方式</p>
          <h3 className="mt-3 font-display text-3xl">已有自选就合并，想重新开始就替换。</h3>
          <p className="mt-4 text-sm leading-7 text-ink/70">
            合并会保留你已有的自选基金，并补齐默认研究池。替换会把当前自选池重置为这组默认基金。
            真实买入仍然由你在支付宝手动完成。
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <button
              className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white"
              onClick={() => applyDefault.mutate("merge")}
              type="button"
            >
              合并到现有自选池
            </button>
            <button
              className="rounded-full border border-ink/10 px-5 py-3 text-sm font-semibold text-ink"
              onClick={() => applyDefault.mutate("replace")}
              type="button"
            >
              替换为默认研究池
            </button>
          </div>
          {statusText ? <p className="mt-4 text-sm text-accent">{statusText}</p> : null}
        </Panel>
      </div>
    </div>
  );
}
