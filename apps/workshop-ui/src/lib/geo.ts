// The map's data: every object with a geometry, as GeoJSON, coloured by its type.

import { geoProperty, titleOf } from "./ontology";
import type { GeoJsonGeometry, ObjectType, OntologyObject } from "./types";

export type Feature = {
  type: "Feature";
  geometry: GeoJsonGeometry;
  properties: { objectType: string; key: string; title: string; color: string; status: string };
};

export const DEFAULT_COLOR = "#78716c";

export function toFeatures(type: ObjectType, objects: OntologyObject[]): Feature[] {
  const geo = geoProperty(type);
  if (!geo) return [];
  const status = type.properties.find((p) => p.api_name === "status");
  return objects.flatMap((object) => {
    const geometry = object.properties[geo.api_name] as GeoJsonGeometry | undefined;
    if (!geometry || typeof geometry !== "object" || !geometry.type) return [];
    return [
      {
        type: "Feature" as const,
        geometry,
        properties: {
          objectType: type.api_name,
          key: object.primary_key,
          title: titleOf(type, object),
          color: type.ui.color ?? DEFAULT_COLOR,
          status: status ? String(object.properties[status.api_name] ?? "") : "",
        },
      },
    ];
  });
}

/** [[west, south], [east, north]] around every point of every feature, or null. */
export function bounds(features: Feature[]): [[number, number], [number, number]] | null {
  const points: number[][] = [];
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") points.push(c as number[]);
    else if (Array.isArray(c)) c.forEach(walk);
  };
  features.forEach((f) => walk(f.geometry.coordinates));
  if (points.length === 0) return null;
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  return [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
}
