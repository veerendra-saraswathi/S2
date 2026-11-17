import { useState } from "react";

import { Badge, Button, Card, Empty, ErrorBox, Loading, statusTone, timeAgo } from "../components/ui";
import { useAuth } from "../lib/auth";
import type { ActionExecution } from "../lib/types";
import { useResource } from "../lib/useResource";

export function ApprovalsPage() {
  const { api, hasRole, user } = useAuth();
  const pending = useResource("executions:pending", () => api.executions({ status: "pending_approval" }), 5_000);
  const recent = useResource("executions:recent", () => api.executions({ limit: 20 }), 10_000);
  const actionTypes = useResource("action-types", () => api.actionTypes());
  const names = Object.fromEntries((actionTypes.data ?? []).map((a) => [a.api_name, a.display_name]));
  const canApprove = hasRole("approver");

  function refresh() {
    pending.reload();
    recent.reload();
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Approvals</h1>
        <p className="text-sm text-stone-500">
          Actions that need a second person. {canApprove ? "You can approve or reject them, except your own." : "You need the approver role to decide them."}
        </p>
      </header>
      <Card title={`Waiting${pending.data ? ` (${pending.data.length})` : ""}`}>
        <ErrorBox error={pending.error} />
        {pending.loading && !pending.data && <Loading />}
        {pending.data && pending.data.length === 0 && <Empty>Nothing is waiting for approval.</Empty>}
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {pending.data?.map((execution) => (
            <PendingItem
              key={execution.id}
              execution={execution}
              label={names[execution.action] ?? execution.action}
              canApprove={canApprove}
              isOwn={execution.actor_id === user?.profile.sub}
              onDecided={refresh}
            />
          ))}
        </ul>
      </Card>
      <Card title="Recent actions">
        <ErrorBox error={recent.error} />
        <ul className="space-y-1.5 text-sm">
          {recent.data?.map((e) => (
            <li key={e.id} className="flex flex-wrap items-center gap-2">
              <Badge tone={statusTone(e.status)}>{e.status.replace("_", " ")}</Badge>
              <span className="font-medium">{names[e.action] ?? e.action}</span>
              <span className="text-stone-500">{describe(e.parameters)}</span>
              <span className="ml-auto text-xs text-stone-500">{timeAgo(e.created_at)}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function describe(parameters: Record<string, unknown>): string {
  return Object.entries(parameters)
    .map(([k, v]) => `${k}: ${String(v)}`)
    .join(" · ");
}

function PendingItem({
  execution,
  label,
  canApprove,
  isOwn,
  onDecided,
}: {
  execution: ActionExecution;
  label: string;
  canApprove: boolean;
  isOwn: boolean;
  onDecided: () => void;
}) {
  const { api } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  async function decide(run: () => Promise<unknown>) {
    setBusy(true);
    setError(undefined);
    try {
      await run();
      onDecided();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{label}</span>
        <span className="text-sm text-stone-500">{describe(execution.parameters)}</span>
        <span className="ml-auto text-xs text-stone-500">
          requested {timeAgo(execution.created_at)} by {isOwn ? "you" : `${execution.actor_type} ${execution.actor_id.slice(0, 8)}`}
        </span>
      </div>
      {canApprove && (
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => decide(() => api.approve(execution.id))} disabled={busy || isOwn} title={isOwn ? "You cannot approve your own request" : undefined}>
            Approve
          </Button>
          {rejecting ? (
            <form
              className="flex flex-1 gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (reason.trim()) void decide(() => api.reject(execution.id, reason.trim()));
              }}
            >
              <label className="sr-only" htmlFor={`reason-${execution.id}`}>
                Reason for rejecting
              </label>
              <input
                id={`reason-${execution.id}`}
                className="flex-1 rounded-md border border-stone-300 bg-white px-2 py-1 text-sm dark:border-stone-700 dark:bg-stone-950"
                placeholder="Reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                autoFocus
              />
              <Button kind="danger" type="submit" disabled={busy || !reason.trim()}>
                Reject
              </Button>
            </form>
          ) : (
            <Button kind="secondary" onClick={() => setRejecting(true)} disabled={busy}>
              Reject…
            </Button>
          )}
        </div>
      )}
      <ErrorBox error={error} />
    </li>
  );
}
