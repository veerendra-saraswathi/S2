import "maplibre-gl/dist/maplibre-gl.css";

import * as maplibregl from "maplibre-gl";
// MapLibre runs its tile work in a module worker that it locates relative to its own file,
// which Vite's bundling moves. Let Vite build the worker and tell MapLibre where it is.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { Empty, ErrorBox, Loading } from "../components/ui";
import { useAuth } from "../lib/auth";
import { bounds, toFeatures, type Feature } from "../lib/geo";
import { geoProperty } from "../lib/ontology";
import { useResource } from "../lib/useResource";

// Raster tiles: OpenStreetMap by default (fine for development; see its tile usage policy),
// or any XYZ source through VITE_MAP_TILES.
const TILES = import.meta.env.VITE_MAP_TILES ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = import.meta.env.VITE_MAP_ATTRIBUTION ?? "© OpenStreetMap contributors";
const HYDERABAD: [number, number] = [78.38, 17.44];

maplibregl.setWorkerUrl(workerUrl);

export function MapPage() {
  const { api } = useAuth();
  const navigate = useNavigate();
  const types = useResource("object-types", () => api.objectTypes());
  const mapped = (types.data ?? []).filter((t) => t.status !== "deprecated" && geoProperty(t));
  const objects = useResource(
    mapped.length ? `map:${mapped.map((t) => t.api_name).join(",")}` : null,
    async () => {
      const lists = await Promise.allSettled(mapped.map((t) => api.objects(t.api_name, { limit: 1000 })));
      // A type the caller is not cleared for answers 404: it is simply not on the map.
      return mapped.flatMap((t, i) => {
        const list = lists[i];
        return list.status === "fulfilled" ? toFeatures(t, list.value) : [];
      });
    },
    10_000,
  );

  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [mapError, setMapError] = useState<string>();
  const fitted = useRef(false);

  useEffect(() => {
    if (!container.current || map.current) return;
    try {
      const instance = new maplibregl.Map({
        container: container.current,
        center: HYDERABAD,
        zoom: 11,
        style: {
          version: 8,
          sources: { base: { type: "raster", tiles: [TILES], tileSize: 256, attribution: ATTRIBUTION } },
          layers: [{ id: "base", type: "raster", source: "base" }],
        },
      });
      instance.addControl(new maplibregl.NavigationControl(), "top-right");
      instance.on("load", () => {
        instance.addSource("objects", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        instance.addLayer({
          id: "objects",
          type: "circle",
          source: "objects",
          paint: {
            "circle-radius": 7,
            "circle-color": ["get", "color"],
            "circle-stroke-width": ["match", ["get", "status"], "GROUNDED", 3, 1.5],
            "circle-stroke-color": ["match", ["get", "status"], "GROUNDED", "#dc2626", "#ffffff"],
          },
        });
        instance.on("click", "objects", (event: maplibregl.MapLayerMouseEvent) => {
          const feature = event.features?.[0];
          if (feature) {
            const { objectType, key } = feature.properties as Feature["properties"];
            navigate(`/objects/${objectType}/${encodeURIComponent(key)}`);
          }
        });
        const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
        instance.on("mousemove", "objects", (event: maplibregl.MapLayerMouseEvent) => {
          const feature = event.features?.[0];
          if (!feature) return;
          const { title, status } = feature.properties as Feature["properties"];
          instance.getCanvas().style.cursor = "pointer";
          hover
            .setLngLat(event.lngLat)
            .setText(status ? `${title} · ${status}` : title)
            .addTo(instance);
        });
        instance.on("mouseleave", "objects", () => {
          instance.getCanvas().style.cursor = "";
          hover.remove();
        });
      });
      map.current = instance;
    } catch (err) {
      setMapError(err instanceof Error ? err.message : String(err));
    }
    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, [navigate]);

  useEffect(() => {
    const instance = map.current;
    const features = objects.data;
    if (!instance || !features) return;
    const apply = () => {
      const source = instance.getSource("objects") as maplibregl.GeoJSONSource | undefined;
      source?.setData({ type: "FeatureCollection", features } as Parameters<maplibregl.GeoJSONSource["setData"]>[0]);
      const box = bounds(features);
      if (box && !fitted.current) {
        instance.fitBounds(box, { padding: 60, maxZoom: 14, duration: 0 });
        fitted.current = true;
      }
    };
    if (instance.isStyleLoaded() && instance.getSource("objects")) apply();
    else instance.once("load", apply);
  }, [objects.data]);

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Map</h1>
          <p className="text-sm text-stone-500">Every object with a position. Click one to open it. Grounded drones have a red ring.</p>
        </div>
        <ul className="flex flex-wrap gap-3 text-xs">
          {mapped.map((t) => (
            <li key={t.api_name} className="flex items-center gap-1.5">
              <span aria-hidden="true" className="size-2.5 rounded-full" style={{ background: t.ui.color ?? "#78716c" }} />
              <Link to={`/types/${t.api_name}`} className="hover:underline">
                {t.display_name}
              </Link>
            </li>
          ))}
        </ul>
      </header>
      <ErrorBox error={types.error ?? objects.error ?? (mapError ? new Error(`The map could not start: ${mapError}`) : undefined)} />
      {types.loading && !types.data && <Loading />}
      {types.data && mapped.length === 0 && <Empty>No object type has a position property.</Empty>}
      <div ref={container} className="h-[70vh] w-full overflow-hidden rounded-xl border border-stone-200 dark:border-stone-800" aria-label="Map of objects" role="region" />
      {objects.data && (
        <p className="text-xs text-stone-500">
          {objects.data.length} object(s) on the map · refreshed every 10 s
        </p>
      )}
    </div>
  );
}
