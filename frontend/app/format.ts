import type { RecordData } from "./client";

const TICK = 0.1;
const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});
export const price = (ticks: number) => dollars.format(ticks * TICK);
export const label = (value: unknown) =>
  typeof value === "string" ? value.replaceAll("_", " ") : "Unavailable";
export const number = (value: unknown) =>
  typeof value === "number" ? value : null;
export const record = (value: unknown): RecordData =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as RecordData)
    : {};
export const rows = (value: unknown): RecordData[] =>
  Array.isArray(value) ? value.map(record) : [];
export const stamp = (value: unknown, zone = "UTC") =>
  typeof value === "string"
    ? new Intl.DateTimeFormat("en-GB", {
        timeZone: zone,
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(new Date(value))
    : "Unavailable";

export function money(cents: unknown) {
  const amount = number(cents);
  return amount === null ? "—" : dollars.format(amount / 100);
}

export function percentage(value: unknown) {
  const rate = number(value);
  return rate === null
    ? "—"
    : new Intl.NumberFormat("en-GB", {
        style: "percent",
        maximumFractionDigits: 1,
      }).format(rate);
}

export function utcDateTime(value: unknown, seconds = false) {
  if (typeof value !== "string") return "Unavailable";
  return (
    new Intl.DateTimeFormat("en-GB", {
      dateStyle: "medium",
      timeStyle: seconds ? "medium" : "short",
      timeZone: "UTC",
      hourCycle: "h23",
    }).format(new Date(value)) + " UTC"
  );
}
