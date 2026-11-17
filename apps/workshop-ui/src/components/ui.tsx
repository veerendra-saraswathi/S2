import type { ReactNode } from "react";

import { ApiError } from "../lib/api";

export function ErrorBox({ error, children }: { error: unknown; children?: ReactNode }) {
  if (!error) return null;
  const api = error instanceof ApiError ? error : null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
      <p className="font-medium">
        {api ? `${statusLabel(api.status)}: ` : ""}
        {message}
      </p>
      {api && api.reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {api.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {children}
    </div>
  );
}

function statusLabel(status: number): string {
  switch (status) {
    case 401:
      return "Signed out";
    case 403:
      return "Not allowed";
    case 404:
      return "Not found";
    case 409:
      return "Conflict";
    case 422:
      return "Invalid";
    case 503:
      return "Unavailable";
    default:
      return `Error ${status}`;
  }
}

const BADGE_TONES: Record<string, string> = {
  green: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  red: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  stone: "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300",
  blue: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
};

export function Badge({ children, tone = "stone" }: { children: ReactNode; tone?: keyof typeof BADGE_TONES }) {
  return <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${BADGE_TONES[tone]}`}>{children}</span>;
}

/** A tone for the status values the packs use. */
export function statusTone(value: unknown): keyof typeof BADGE_TONES {
  const v = String(value).toUpperCase();
  if (["READY", "DONE", "SUCCEEDED", "DELIVERED", "SUCCESS"].includes(v)) return "green";
  if (["GROUNDED", "FAILED", "DEAD", "REJECTED", "CANCELLED", "DENIED", "ERROR"].includes(v)) return "red";
  if (["IN_MISSION", "IN_PROGRESS", "PENDING_APPROVAL", "PENDING", "OPEN", "CHARGING"].includes(v)) return "amber";
  if (v === "MAINTENANCE") return "blue";
  return "stone";
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-stone-200 px-4 py-2.5 dark:border-stone-800">
          <h2 className="text-sm font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="text-sm text-stone-500">{label}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-stone-500">{children}</p>;
}

export function Button({
  children,
  kind = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: "primary" | "secondary" | "danger" }) {
  const tones = {
    primary: "bg-stone-900 text-white hover:bg-stone-700 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-stone-300",
    secondary: "border border-stone-300 hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800",
    danger: "bg-red-700 text-white hover:bg-red-600",
  };
  return (
    <button
      type="button"
      {...props}
      className={`rounded-md px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${tones[kind]} ${props.className ?? ""}`}
    >
      {children}
    </button>
  );
}

export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `${Math.max(seconds, 0)} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}
