"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/util/cn";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("card p-5", className)}>{children}</div>;
}

export function SectionTitle({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

/** 到達度バー。value は 0..1。 */
export function ProgressBar({
  value,
  accent,
  label,
}: {
  value: number;
  accent?: string;
  label?: string;
}) {
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-surface-raised ring-1 ring-inset ring-border"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label ?? "進捗"}
      >
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{
            width: `${percent}%`,
            backgroundColor: accent ? `rgb(${accent})` : "rgb(var(--color-accent))",
          }}
        />
      </div>
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="card px-4 py-3.5">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint ? <div className="mt-0.5 text-xs text-muted">{hint}</div> : null}
    </div>
  );
}

export function Banner({
  tone = "info",
  children,
}: {
  tone?: "info" | "warning" | "danger" | "success";
  children: ReactNode;
}) {
  const toneClass = {
    info: "border-border bg-surface-raised text-fg",
    warning: "border-warning/40 bg-warning/10 text-fg",
    danger: "border-danger/40 bg-danger/10 text-fg",
    success: "border-success/40 bg-success/10 text-fg",
  }[tone];
  return <div className={cn("rounded-xl border px-4 py-3 text-sm", toneClass)}>{children}</div>;
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="card flex flex-col items-center gap-1 px-6 py-12 text-center">
      <p className="font-medium">{title}</p>
      <p className="max-w-md text-sm text-muted">{description}</p>
    </div>
  );
}
