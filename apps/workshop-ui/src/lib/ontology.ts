// Small helpers over object type definitions.

import type { GeoJsonGeometry, ObjectType, OntologyObject, PropertyType } from "./types";

export function titleOf(type: ObjectType, object: OntologyObject): string {
  const title = type.properties.find((p) => p.is_title);
  const value = title ? object.properties[title.api_name] : undefined;
  return value !== undefined && value !== null && value !== "" ? String(value) : object.primary_key;
}

export function geoProperty(type: ObjectType): PropertyType | undefined {
  return type.properties.find((p) => (p.data_type === "geopoint" || p.data_type === "geoshape") && p.status !== "deprecated");
}

export function seriesProperties(type: ObjectType): PropertyType[] {
  return type.properties.filter((p) => p.data_type === "timeseries" && p.status !== "deprecated");
}

/** Properties worth a column in a table of objects: scalar, not the key or title, not deprecated. */
export function columnProperties(type: ObjectType, max = 4): PropertyType[] {
  return type.properties
    .filter(
      (p) =>
        !p.is_primary_key &&
        !p.is_title &&
        p.status !== "deprecated" &&
        !["timeseries", "geopoint", "geoshape", "media_ref"].includes(p.data_type),
    )
    .slice(0, max);
}

export function formatValue(value: unknown, property?: PropertyType): string {
  if (value === undefined || value === null) return "—";
  if (property && (property.data_type === "geopoint" || property.data_type === "geoshape")) {
    const geometry = value as GeoJsonGeometry;
    if (geometry.type === "Point" && Array.isArray(geometry.coordinates)) {
      const [lon, lat] = geometry.coordinates as number[];
      return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
    }
    return geometry.type;
  }
  if (Array.isArray(value)) return value.map((v) => formatValue(v)).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  return property?.unit ? `${text} ${property.unit}` : text;
}

/** The action parameter that names the object, by the target type's primary-key name. */
export function keyParameter(type: ObjectType | undefined, parameterNames: string[]): string | undefined {
  const key = type?.properties.find((p) => p.is_primary_key)?.api_name;
  return key && parameterNames.includes(key) ? key : undefined;
}
