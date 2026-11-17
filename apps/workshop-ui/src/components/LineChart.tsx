// A dependency-free line chart for one time series.

export type Point = { time: string; value: number };

export type Geometry = { path: string; min: number; max: number; first: number; last: number };

/** SVG path for points scaled into width x height (with the y axis pointing up). */
export function chartGeometry(points: Point[], width: number, height: number, pad = 2): Geometry | null {
  if (points.length === 0) return null;
  const times = points.map((p) => new Date(p.time).getTime());
  const values = points.map((p) => p.value);
  const first = Math.min(...times);
  const last = Math.max(...times);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const x = (t: number) => pad + (last === first ? (width - 2 * pad) / 2 : ((t - first) / (last - first)) * (width - 2 * pad));
  const y = (v: number) => height - pad - ((v - min) / (max - min)) * (height - 2 * pad);
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(times[i]).toFixed(1)},${y(p.value).toFixed(1)}`)
    .join(" ");
  return { path, min: Math.min(...values), max: Math.max(...values), first, last };
}

export function LineChart({
  points,
  unit,
  width = 520,
  height = 120,
  label,
}: {
  points: Point[];
  unit?: string | null;
  width?: number;
  height?: number;
  label: string;
}) {
  const geometry = chartGeometry(points, width, height);
  if (!geometry) return <p className="text-sm text-stone-500">No readings in this window.</p>;
  const latest = points[points.length - 1];
  const fmt = (v: number) => `${Number(v.toFixed(3))}${unit ? ` ${unit}` : ""}`;
  return (
    <figure className="space-y-1">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${label}: ${points.length} readings, latest ${fmt(latest.value)}`}
        className="h-28 w-full text-sky-600 dark:text-sky-400"
        preserveAspectRatio="none"
      >
        <path d={geometry.path} fill="none" stroke="currentColor" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="flex justify-between text-xs text-stone-500">
        <span>
          min {fmt(geometry.min)} · max {fmt(geometry.max)}
        </span>
        <span>
          latest <strong className="text-stone-800 dark:text-stone-200">{fmt(latest.value)}</strong> at{" "}
          {new Date(latest.time).toLocaleTimeString()}
        </span>
      </figcaption>
    </figure>
  );
}
