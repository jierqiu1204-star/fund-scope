import Link from "next/link";
import { ReactNode } from "react";

export function SectionHeader({
  eyebrow,
  title,
  description,
  action
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div>
        <p className="text-xs uppercase tracking-[0.35em] text-accent">{eyebrow}</p>
        <h2 className="font-display text-4xl font-semibold text-ink">{title}</h2>
        <p className="mt-2 max-w-2xl text-sm text-ink/70">{description}</p>
      </div>
      {action}
    </div>
  );
}

export function Panel({
  children,
  className = ""
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-[32px] border border-ink/10 bg-white/80 p-6 shadow-card backdrop-blur ${className}`}>
      {children}
    </div>
  );
}

export function StatPill({
  label,
  value,
  tone = "bg-blush text-ink"
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className={`rounded-[24px] px-4 py-3 ${tone}`}>
      <p className="text-xs uppercase tracking-[0.25em] text-ink/55">{label}</p>
      <p className="mt-2 text-lg font-semibold">{value}</p>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  href,
  label
}: {
  title: string;
  description: string;
  href: string;
  label: string;
}) {
  return (
    <Panel className="border-dashed bg-gradient-to-br from-white via-white to-blush/60 text-center">
      <p className="text-xs uppercase tracking-[0.35em] text-accent">从这里开始</p>
      <h3 className="mt-3 font-display text-3xl">{title}</h3>
      <p className="mx-auto mt-3 max-w-xl text-sm text-ink/70">{description}</p>
      <Link
        href={href}
        className="mt-6 inline-flex rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine"
      >
        {label}
      </Link>
    </Panel>
  );
}
