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
    <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">{eyebrow}</p>
        <h2 className="mt-1 text-2xl font-semibold tracking-[-0.02em] text-ink">{title}</h2>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-ink/60">{description}</p>
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
  return <div className={`rounded-[12px] border border-border bg-white p-5 shadow-card ${className}`}>{children}</div>;
}

export function StatPill({
  label,
  value,
  tone = "bg-white text-ink"
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className={`rounded-[8px] border border-border px-3 py-2.5 ${tone}`}>
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-ink/55">{label}</p>
      <p className="mt-1 font-mono text-base font-semibold leading-6">{value}</p>
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
    <Panel className="border-dashed text-center">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">从这里开始</p>
      <h3 className="mt-2 text-2xl font-semibold tracking-[-0.02em] text-ink">{title}</h3>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-ink/60">{description}</p>
      <Link
        href={href}
        className="mt-5 inline-flex rounded-[6px] bg-ink px-4 py-2 text-sm font-medium text-white transition hover:bg-ink/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {label}
      </Link>
    </Panel>
  );
}
