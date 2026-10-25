export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "–" : `${(value * 100).toFixed(digits)}%`;
}

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : new Intl.NumberFormat().format(value);
}

export function relativeTime(epochSeconds: number | null | undefined, now = Date.now()): string {
  if (!epochSeconds) return "–";
  const seconds = Math.round(now / 1000 - epochSeconds);
  if (seconds < 45) return "just now";
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "minute"],
    [3600, "hour"],
    [86400, "day"],
  ];
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (let index = units.length - 1; index >= 0; index--) {
    const [size, unit] = units[index] as [number, Intl.RelativeTimeFormatUnit];
    if (seconds >= size) return format.format(-Math.round(seconds / size), unit);
  }
  return format.format(-Math.round(seconds / 60), "minute");
}

export function duration(start: number | null | undefined, end: number | null | undefined): number | null {
  if (!start) return null;
  return ((end ?? Date.now() / 1000) - start) * 1000;
}

export function pages(start: number | null | undefined, end: number | null | undefined): string {
  if (!start) return "";
  return end && end !== start ? `pp. ${start}–${end}` : `p. ${start}`;
}

export function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
