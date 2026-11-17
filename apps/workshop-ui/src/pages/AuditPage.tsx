import { useState } from "react";
import { useSearchParams } from "react-router";

import { Badge, Button, Card, Empty, ErrorBox, Loading, statusTone, timeAgo } from "../components/ui";
import { useAuth } from "../lib/auth";
import type { AuditEntry } from "../lib/types";
import { useResource } from "../lib/useResource";

export function AuditPage() {
  const { api } = useAuth();
  const [params, setParams] = useSearchParams();
  const eventType = params.get("event_type") ?? "";
  const resourceId = params.get("resource_id") ?? "";
  const [draft, setDraft] = useState({ eventType, resourceId });
  const entries = useResource(`audit:${eventType}:${resourceId}`, () =>
    api.audit({ event_type: eventType || undefined, resource_id: resourceId || undefined, limit: 100 }),
  );
  const verification = useResource("audit:verify", () => api.verifyAudit());

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Audit log</h1>
          <p className="text-sm text-stone-500">Every change and decision, in a hash chain per tenant.</p>
        </div>
        {verification.data && (
          <Badge tone={verification.data.verified ? "green" : "red"}>
            {verification.data.verified
              ? `chain verified · ${verification.data.entries} entries`
              : `chain broken at entry ${verification.data.first_bad_seq}`}
          </Badge>
        )}
      </header>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const next = new URLSearchParams();
          if (draft.eventType) next.set("event_type", draft.eventType);
          if (draft.resourceId) next.set("resource_id", draft.resourceId);
          setParams(next);
        }}
      >
        <label className="text-sm">
          Event
          <input className="mt-1 block rounded-md border border-stone-300 bg-white px-2 py-1 text-sm dark:border-stone-700 dark:bg-stone-950" placeholder="object.modified" value={draft.eventType} onChange={(e) => setDraft({ ...draft, eventType: e.target.value })} />
        </label>
        <label className="text-sm">
          Resource
          <input className="mt-1 block rounded-md border border-stone-300 bg-white px-2 py-1 text-sm dark:border-stone-700 dark:bg-stone-950" placeholder="DRN-0003" value={draft.resourceId} onChange={(e) => setDraft({ ...draft, resourceId: e.target.value })} />
        </label>
        <Button type="submit" kind="secondary">
          Filter
        </Button>
      </form>
      <ErrorBox error={entries.error ?? verification.error} />
      {entries.loading && !entries.data && <Loading />}
      {entries.data && entries.data.length === 0 && <Empty>No entries match.</Empty>}
      {entries.data && entries.data.length > 0 && (
        <Card>
          <ol className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {entries.data.map((entry) => (
              <EntryRow key={entry.seq} entry={entry} />
            ))}
          </ol>
        </Card>
      )}
    </div>
  );
}

function EntryRow({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false);
  const provenance = entry.details.provenance as { dataset?: string; branch?: string; snapshot_id?: string } | undefined;
  return (
    <li className="py-2">
      <button type="button" className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="w-12 font-mono text-xs text-stone-400">#{entry.seq}</span>
        <Badge tone={statusTone(entry.outcome)}>{entry.outcome}</Badge>
        <span className="font-medium">{entry.event_type}</span>
        {entry.resource_type && (
          <span className="text-stone-500">
            {entry.resource_type} {entry.resource_id ?? "(hidden)"}
          </span>
        )}
        {provenance?.dataset && <Badge tone="blue">from {provenance.dataset}</Badge>}
        {entry.redacted && <Badge tone="amber">redacted</Badge>}
        <span className="ml-auto text-xs text-stone-500">
          {entry.actor_type} · {timeAgo(entry.occurred_at)}
        </span>
      </button>
      {open && <pre className="mt-2 overflow-x-auto rounded bg-stone-100 p-2 text-xs dark:bg-stone-950">{JSON.stringify(entry.details, null, 2)}</pre>}
    </li>
  );
}
