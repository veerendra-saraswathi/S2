import { Link } from "react-router";

import { Badge, Card, Empty, ErrorBox, Loading } from "../components/ui";
import { useAuth } from "../lib/auth";
import { geoProperty, seriesProperties } from "../lib/ontology";
import { useResource } from "../lib/useResource";

export function ExplorerPage() {
  const { api } = useAuth();
  const types = useResource("object-types", () => api.objectTypes());

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Ontology</h1>
        <p className="text-sm text-stone-500">The object types in your tenant. Open one to see its objects.</p>
      </header>
      <ErrorBox error={types.error} />
      {types.loading && !types.data && <Loading />}
      {types.data && types.data.length === 0 && (
        <Empty>
          No object types yet. Apply the packs with <code className="font-mono">make seed</code>.
        </Empty>
      )}
      <ul className="grid gap-3 sm:grid-cols-2">
        {types.data
          ?.filter((t) => t.status !== "deprecated")
          .map((type) => (
            <li key={type.api_name}>
              <Link to={`/types/${type.api_name}`} className="block h-full rounded-xl border border-stone-200 bg-white p-4 hover:border-stone-400 dark:border-stone-800 dark:bg-stone-900 dark:hover:border-stone-600">
                <div className="flex items-center gap-2">
                  <span aria-hidden="true" className="size-3 rounded-full" style={{ background: type.ui.color ?? "#78716c" }} />
                  <h2 className="font-medium">{type.display_name}</h2>
                  {type.markings.map((m) => (
                    <Badge key={m} tone="amber">
                      {m}
                    </Badge>
                  ))}
                </div>
                {type.description && <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">{type.description}</p>}
                <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-stone-500">
                  <span>{type.properties.filter((p) => p.status !== "deprecated").length} properties</span>
                  {seriesProperties(type).length > 0 && <span>{seriesProperties(type).length} time series</span>}
                  {geoProperty(type) && <span>on the map</span>}
                  {type.backing_dataset && <span>from {type.backing_dataset}</span>}
                </p>
              </Link>
            </li>
          ))}
      </ul>
      <Card title="Not seeing a type?">
        <p className="text-sm text-stone-600 dark:text-stone-400">
          Types marked with a marking you do not hold are not shown in object views. Ask an administrator for the
          matching <code className="font-mono">marking:</code> role.
        </p>
      </Card>
    </div>
  );
}
