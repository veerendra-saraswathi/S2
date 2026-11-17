import { Link, useSearchParams } from "react-router";

import { Badge, Card, Empty, ErrorBox, Loading, timeAgo } from "../components/ui";
import { useAuth } from "../lib/auth";
import { formatValue, titleOf } from "../lib/ontology";
import type { Evidence, ObjectType, TriageQueue } from "../lib/types";
import { useResource } from "../lib/useResource";
import { ActionPanel } from "./ObjectPage";

const SEVERITY_TONE = { critical: "red", high: "red", medium: "amber", low: "stone" } as const;
const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 } as const;
const OPS: Record<string, string> = { eq: "=", ne: "≠", lt: "<", le: "≤", gt: ">", ge: "≥", in: "in" };

export function sortQueues(queues: TriageQueue[]): TriageQueue[] {
  return [...queues].sort(
    (a, b) => SEVERITY_ORDER[a.rule.severity] - SEVERITY_ORDER[b.rule.severity] || b.count - a.count || a.rule.display_name.localeCompare(b.rule.display_name),
  );
}

export function describeEvidence(e: Evidence, type?: ObjectType): string {
  const property = type?.properties.find((p) => p.api_name === e.property);
  const name = property?.display_name ?? e.property;
  const actual = typeof e.actual === "number" ? Number(e.actual.toFixed(3)) : e.actual;
  return `${name} ${formatValue(actual, property)} ${OPS[e.op] ?? e.op} ${Array.isArray(e.expected) ? e.expected.join(", ") : String(e.expected)}`;
}

export function TriagePage() {
  const { api } = useAuth();
  const [params, setParams] = useSearchParams();
  const queues = useResource("triage:queues", () => api.triageQueues(), 10_000);
  const sorted = sortQueues(queues.data ?? []);
  const selected = params.get("queue") ?? sorted.find((q) => q.count > 0)?.rule.api_name ?? sorted[0]?.rule.api_name ?? null;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Triage</h1>
        <p className="text-sm text-stone-500">Objects that meet a rule right now, with the readings that put them there. Queues update every 10 s.</p>
      </header>
      <ErrorBox error={queues.error} />
      {queues.loading && !queues.data && <Loading />}
      {queues.data && queues.data.length === 0 && (
        <Empty>
          No triage rules you can see. Rules are defined in ontology packs (<code className="font-mono">triage_rules</code>).
        </Empty>
      )}
      {sorted.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
          <nav aria-label="Queues">
            <ul className="space-y-1">
              {sorted.map((q) => {
                const active = q.rule.api_name === selected;
                return (
                  <li key={q.rule.api_name}>
                    <button
                      type="button"
                      onClick={() => setParams({ queue: q.rule.api_name })}
                      aria-current={active ? "true" : undefined}
                      className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm ${
                        active ? "border-stone-400 bg-white dark:border-stone-500 dark:bg-stone-900" : "border-transparent hover:bg-stone-100 dark:hover:bg-stone-900"
                      }`}
                    >
                      <span className="flex-1">
                        <span className="block font-medium">{q.rule.display_name}</span>
                        <span className="text-xs text-stone-500">{q.rule.object_type}</span>
                      </span>
                      <Badge tone={SEVERITY_TONE[q.rule.severity]}>{q.rule.severity}</Badge>
                      <span className={`min-w-6 text-right font-semibold tabular-nums ${q.count === 0 ? "text-stone-400" : ""}`}>{q.count}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>
          {selected && <QueueView name={selected} onChanged={queues.reload} />}
        </div>
      )}
    </div>
  );
}

function QueueView({ name, onChanged }: { name: string; onChanged: () => void }) {
  const { api } = useAuth();
  const queue = useResource(`triage:${name}`, () => api.triageQueue(name), 10_000);
  const typeName = queue.data?.rule.object_type;
  const type = useResource(typeName ? `type:${typeName}` : null, () => api.objectType(typeName!));
  const actions = useResource("action-types", () => api.actionTypes());

  if (queue.error) return <ErrorBox error={queue.error} />;
  if (!queue.data) return <Loading />;
  const { rule, items, warning } = queue.data;
  const offered = (actions.data ?? []).filter((a) => rule.actions.includes(a.api_name));

  return (
    <Card
      title={
        <span>
          {rule.display_name} <span className="font-normal text-stone-500">· {items?.length ?? 0}</span>
        </span>
      }
    >
      {rule.description && <p className="mb-3 text-sm text-stone-600 dark:text-stone-400">{rule.description}</p>}
      {warning && <p className="mb-3 text-sm text-amber-700 dark:text-amber-400">{warning}</p>}
      {items && items.length === 0 && <Empty>Nothing in this queue right now.</Empty>}
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {items?.map(({ object, evidence }) => (
          <li key={object.primary_key} className="space-y-2 py-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <Link className="font-medium hover:underline" to={`/objects/${rule.object_type}/${encodeURIComponent(object.primary_key)}`}>
                {type.data ? titleOf(type.data, object) : object.primary_key}
              </Link>
              <span className="font-mono text-xs text-stone-400">{object.primary_key}</span>
              <ul className="flex flex-wrap gap-x-3 text-sm text-stone-600 dark:text-stone-400">
                {evidence.map((e) => (
                  <li key={e.property}>
                    {describeEvidence(e, type.data)}
                    {e.observed_at && <span className="text-xs text-stone-400"> · {timeAgo(e.observed_at)}</span>}
                  </li>
                ))}
              </ul>
            </div>
            {type.data && offered.length > 0 && (
              <div className="grid gap-2 md:grid-cols-2">
                {offered.map((action) => (
                  <ActionPanel key={action.api_name} action={action} type={type.data!} objectKey={object.primary_key}
                    onDone={() => {
                      queue.reload();
                      onChanged();
                    }}
                  />
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
