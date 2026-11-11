/** Explicit locale and timezone keep server rendering independent of host defaults. */
export function formatDisplayTime(
  value: string | number,
  timeZone: string,
  kind: "axis" | "time" | "date" = "time",
): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    hour: "2-digit",
    minute: "2-digit",
    ...(kind === "axis" ? {} : { second: "2-digit" }),
    ...(kind === "date" ? { year: "numeric", month: "short", day: "numeric" } as const : {}),
  }).format(new Date(value));
}
