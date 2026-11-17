import { useState } from "react";
import { Link, useParams } from "react-router";

import { Badge, Button, Empty, ErrorBox, Loading, statusTone } from "../components/ui";
import { useAuth } from "../lib/auth";
import { columnProperties, formatValue, titleOf } from "../lib/ontology";
import type { OntologyObject } from "../lib/types";
import { useResource } from "../lib/useResource";

const PAGE = 100;

export function TypePage() {
  const { type: typeName = "" } = useParams();
  const { api } = useAuth();
  const type = useResource(`type:${typeName}`, () => api.objectType(typeName));
  const first = useResource(`objects:${typeName}`, () => api.objects(typeName, { limit: PAGE }), 10_000);
  const [more, setMore] = useState<OntologyObject[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<unknown>();

  const objects = [...(first.data ?? []), ...more];
  const hasMore = (more.length > 0 ? more.length : first.data?.length ?? 0) === PAGE;

  async function loadMore() {
    setLoadingMore(true);
    try {
      const after = objects[objects.length - 1]?.primary_key;
      setMore([...more, ...(await api.objects(typeName, { limit: PAGE, after }))]);
    } catch (err) {
      setMoreError(err);
    } finally {
      setLoadingMore(false);
    }
  }

  const columns = type.data ? columnProperties(type.data) : [];
  const keyName = type.data?.properties.find((p) => p.is_primary_key)?.display_name ?? "Key";

  return (
    <div className="space-y-4">
      <nav className="text-sm text-stone-500">
        <Link to="/" className="hover:underline">
          Ontology
        </Link>{" "}
        / {type.data?.display_name ?? typeName}
      </nav>
      <header>
        <h1 className="text-xl font-semibold">{type.data?.display_name ?? typeName}</h1>
        {type.data?.description && <p className="text-sm text-stone-500">{type.data.description}</p>}
      </header>
      <ErrorBox error={type.error ?? first.error ?? moreError} />
      {first.loading && !first.data && <Loading />}
      {first.data && objects.length === 0 && <Empty>No objects of this type yet.</Empty>}
      {type.data && objects.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-stone-200 text-xs uppercase text-stone-500 dark:border-stone-800">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">{keyName}</th>
                {columns.map((c) => (
                  <th key={c.api_name} className="px-4 py-2 font-medium">
                    {c.display_name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {objects.map((object) => (
                <tr key={object.primary_key} className="border-b border-stone-100 last:border-0 dark:border-stone-800">
                  <td className="px-4 py-2">
                    <Link className="font-medium hover:underline" to={`/objects/${typeName}/${encodeURIComponent(object.primary_key)}`}>
                      {titleOf(type.data!, object)}
                    </Link>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">{object.primary_key}</td>
                  {columns.map((c) => {
                    const value = object.properties[c.api_name];
                    return (
                      <td key={c.api_name} className="px-4 py-2">
                        {c.constraints.enum && value !== undefined ? <Badge tone={statusTone(value)}>{String(value)}</Badge> : formatValue(value, c)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {hasMore && (
        <Button kind="secondary" onClick={loadMore} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Load more"}
        </Button>
      )}
    </div>
  );
}
