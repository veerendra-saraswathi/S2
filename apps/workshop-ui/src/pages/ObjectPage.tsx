import { useState } from "react";
import { Link, useParams } from "react-router";

import { LineChart } from "../components/LineChart";
import { ParamForm, type Values } from "../components/ParamForm";
import { Badge, Button, Card, Empty, ErrorBox, Loading, statusTone, timeAgo } from "../components/ui";
import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatValue, keyParameter, seriesProperties, titleOf } from "../lib/ontology";
import type { ActionExecution, ActionType, LinkType, ObjectType, PropertyType } from "../lib/types";
import { useResource } from "../lib/useResource";

export function ObjectPage() {
  const { type: typeName = "", key = "" } = useParams();
  const { api, hasRole } = useAuth();
  const type = useResource(`type:${typeName}`, () => api.objectType(typeName));
  const object = useResource(`object:${typeName}:${key}`, () => api.object(typeName, key), 10_000);
  const links = useResource("link-types", () => api.linkTypes());
  const actions = useResource("action-types", () => api.actionTypes());

  if (type.error || object.error) {
    return (
      <div className="space-y-4">
        <Breadcrumb typeName={typeName} typeLabel={type.data?.display_name} title={key} />
        <ErrorBox error={type.error ?? object.error} />
      </div>
    );
  }
  if (!type.data || !object.data) return <Loading />;
  const definition = type.data;
  const shown = displayOrder(definition.properties.filter((p) => p.status !== "deprecated" && p.data_type !== "timeseries"));
  const forThisType = (actions.data ?? []).filter((a) => a.target_object_type === typeName && a.status === "active");

  return (
    <div className="space-y-4">
      <Breadcrumb typeName={typeName} typeLabel={definition.display_name} title={titleOf(definition, object.data)} />
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">{titleOf(definition, object.data)}</h1>
          <p className="text-sm text-stone-500">
            {definition.display_name} <span className="font-mono">{object.data.primary_key}</span> · version {object.data.version} · updated{" "}
            {timeAgo(object.data.updated_at)}
            {TRACE_VIEWS[typeName] && (
              <>
                {" · "}
                <Link to={TRACE_VIEWS[typeName].path(key)} className="font-medium text-stone-700 hover:underline dark:text-stone-300">
                  {TRACE_VIEWS[typeName].label}
                </Link>
              </>
            )}
          </p>
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title="Properties">
            <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1.5 text-sm">
              {shown.map((p) => (
                <PropertyRow key={p.api_name} property={p} value={object.data!.properties[p.api_name]} />
              ))}
            </dl>
          </Card>
          {seriesProperties(definition).map((p) => (
            <SeriesCard key={p.api_name} typeName={typeName} objectKey={key} property={p} />
          ))}
        </div>
        <div className="space-y-4">
          {forThisType.length > 0 && (
            <Card title="Actions">
              <div className="space-y-3">
                {forThisType.map((action) => (
                  <ActionPanel key={action.api_name} action={action} type={definition} objectKey={key} onDone={object.reload} />
                ))}
              </div>
            </Card>
          )}
          <LinksCard type={definition} objectKey={key} linkTypes={links.data ?? []} />
          {hasRole("auditor", "ontology-admin") && <HistoryCard typeName={typeName} objectKey={key} />}
        </div>
      </div>
    </div>
  );
}

/** Types with a traceability view of their own. */
const TRACE_VIEWS: Record<string, { label: string; path: (key: string) => string }> = {
  SoftwareBuild: { label: "Gate journey", path: (key) => `/builds/${encodeURIComponent(key)}` },
  Requirement: { label: "Traceability", path: (key) => `/requirements/${encodeURIComponent(key)}` },
};

/** The key first, then the title, then the rest as the type lists them. */
function displayOrder(properties: PropertyType[]): PropertyType[] {
  const rank = (p: PropertyType) => (p.is_primary_key ? 0 : p.is_title ? 1 : 2);
  return [...properties].sort((a, b) => rank(a) - rank(b));
}

function Breadcrumb({ typeName, typeLabel, title }: { typeName: string; typeLabel?: string; title: string }) {
  return (
    <nav className="text-sm text-stone-500">
      <Link to="/" className="hover:underline">
        Ontology
      </Link>{" "}
      /{" "}
      <Link to={`/types/${typeName}`} className="hover:underline">
        {typeLabel ?? typeName}
      </Link>{" "}
      / {title}
    </nav>
  );
}

function PropertyRow({ property, value }: { property: PropertyType; value: unknown }) {
  return (
    <>
      <dt className="text-stone-500">
        {property.display_name}
        {property.markings.length > 0 && (
          <span className="ml-1">
            {property.markings.map((m) => (
              <Badge key={m} tone="amber">
                {m}
              </Badge>
            ))}
          </span>
        )}
      </dt>
      <dd className={property.is_primary_key ? "font-mono text-xs leading-5" : ""}>
        {property.constraints.enum && value !== undefined && value !== null ? <Badge tone={statusTone(value)}>{String(value)}</Badge> : formatValue(value, property)}
        {property.source_column && value !== undefined && <span className="ml-2 text-xs text-stone-400">from {property.source_column}</span>}
      </dd>
    </>
  );
}

function SeriesCard({ typeName, objectKey, property }: { typeName: string; objectKey: string; property: PropertyType }) {
  const { api } = useAuth();
  const series = useResource(
    `series:${typeName}:${objectKey}:${property.api_name}`,
    () => api.timeseries(typeName, objectKey, property.api_name, { limit: 600 }),
    5_000,
  );
  const unavailable = series.error instanceof ApiError && (series.error.status === 404 || series.error.status === 503);
  return (
    <Card
      title={
        <span className="flex items-center gap-1.5">
          {property.display_name}
          {property.markings.map((m) => (
            <Badge key={m} tone="amber">
              {m}
            </Badge>
          ))}
        </span>
      }
      actions={<span className="text-xs text-stone-500">last hour · live</span>}
    >
      {series.data ? (
        <LineChart points={series.data.points} unit={property.unit} label={property.display_name} />
      ) : unavailable ? (
        <Empty>
          {series.error instanceof ApiError && series.error.status === 503
            ? "The telemetry store is not configured."
            : "Not available to you."}
        </Empty>
      ) : (
        <ErrorBox error={series.error} />
      )}
      {series.loading && !series.data && !series.error && <Loading />}
    </Card>
  );
}

export function ActionPanel({
  action,
  type,
  objectKey,
  onDone,
}: {
  action: ActionType;
  type: ObjectType;
  objectKey: string;
  onDone: () => void;
}) {
  const { api } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [result, setResult] = useState<{ execution: ActionExecution; status: number }>();
  const keyParam = keyParameter(type, Object.keys(action.parameters.properties ?? {}));

  async function run(values: Values) {
    setBusy(true);
    setError(undefined);
    try {
      const { data, status } = await api.applyAction(action.api_name, values, crypto.randomUUID());
      setResult({ execution: data, status });
      setOpen(false);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-stone-200 p-3 dark:border-stone-800">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium">{action.display_name}</p>
          {action.requires_approval && <p className="text-xs text-stone-500">Needs a second person to approve</p>}
        </div>
        <Button kind="secondary" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Cancel" : "Start"}
        </Button>
      </div>
      {action.description && <p className="mt-1 text-xs text-stone-500">{action.description}</p>}
      {open && (
        <div className="mt-3">
          <ParamForm
            schema={action.parameters}
            fixed={keyParam ? { [keyParam]: objectKey } : {}}
            submitLabel={action.requires_approval ? "Request" : action.display_name}
            busy={busy}
            onSubmit={run}
          />
        </div>
      )}
      <div className="mt-2 space-y-2">
        <ErrorBox error={error} />
        {result && (
          <p role="status" className="text-sm">
            {result.status === 202 ? (
              <>
                <Badge tone="amber">awaiting approval</Badge> Sent to the{" "}
                <Link className="underline" to="/approvals">
                  approval queue
                </Link>
                .
              </>
            ) : (
              <>
                <Badge tone="green">done</Badge>
                {result.execution.writebacks.length > 0 && ` ${result.execution.writebacks.length} write-back(s) queued.`}
              </>
            )}
          </p>
        )}
      </div>
    </div>
  );
}

function LinksCard({ type, objectKey, linkTypes }: { type: ObjectType; objectKey: string; linkTypes: LinkType[] }) {
  const ends = linkTypes.flatMap((l) => [
    ...(l.source_object_type === type.api_name ? [{ name: l.api_name, label: l.display_name, far: l.target_object_type }] : []),
    ...(l.target_object_type === type.api_name ? [{ name: l.reverse_api_name, label: l.reverse_display_name, far: l.source_object_type }] : []),
  ]);
  if (ends.length === 0) return null;
  return (
    <Card title="Links">
      <div className="space-y-3">
        {ends.map((end) => (
          <LinkList key={end.name} type={type.api_name} objectKey={objectKey} link={end.name} label={end.label} far={end.far} />
        ))}
      </div>
    </Card>
  );
}

function LinkList({ type, objectKey, link, label, far }: { type: string; objectKey: string; link: string; label: string; far: string }) {
  const { api } = useAuth();
  const linked = useResource(`linked:${type}:${objectKey}:${link}`, () => api.linked(type, objectKey, link));
  // A link into a type the caller is not cleared for answers 404: leave it out.
  if (linked.error instanceof ApiError && linked.error.status === 404) return null;
  return (
    <div>
      <p className="text-xs font-medium uppercase text-stone-500">{label}</p>
      <ErrorBox error={linked.error} />
      {linked.data && linked.data.length === 0 && <p className="text-sm text-stone-400">none</p>}
      <ul className="text-sm">
        {linked.data?.map((o) => (
          <li key={o.primary_key}>
            <Link className="hover:underline" to={`/objects/${far}/${encodeURIComponent(o.primary_key)}`}>
              {String(Object.values(o.properties).find((v) => typeof v === "string" && v !== o.primary_key) ?? o.primary_key)}
            </Link>{" "}
            <span className="font-mono text-xs text-stone-400">{o.primary_key}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function HistoryCard({ typeName, objectKey }: { typeName: string; objectKey: string }) {
  const { api } = useAuth();
  const history = useResource(`history:${typeName}:${objectKey}`, () =>
    api.audit({ resource_type: typeName, resource_id: objectKey, limit: 20 }),
  );
  return (
    <Card title="History">
      <ErrorBox error={history.error} />
      {history.data && history.data.length === 0 && <Empty>No changes recorded.</Empty>}
      <ol className="space-y-2 text-sm">
        {history.data?.map((entry) => {
          const provenance = entry.details.provenance as { dataset?: string; branch?: string } | undefined;
          return (
            <li key={entry.seq}>
              <p>
                <span className="font-medium">{entry.event_type}</span> <span className="text-xs text-stone-500">{timeAgo(entry.occurred_at)}</span>
              </p>
              <p className="text-xs text-stone-500">
                by {entry.actor_type} {entry.actor_id.slice(0, 8)}
                {provenance?.dataset && ` · from ${provenance.dataset}@${provenance.branch ?? "main"}`}
                {entry.redacted && " · partly hidden by markings"}
              </p>
            </li>
          );
        })}
      </ol>
      <Link to={`/audit?resource_id=${encodeURIComponent(objectKey)}`} className="mt-2 block text-xs underline">
        Full audit log
      </Link>
    </Card>
  );
}

