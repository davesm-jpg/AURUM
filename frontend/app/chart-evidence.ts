export const OVERLAYS = {
  swings: "Swings",
  bos: "BOS",
  mss: "MSS",
  sweeps: "Sweeps",
  equal: "Equal highs/lows",
  prior_day: "Prior day",
  prior_week: "Prior week",
  session_levels: "Session levels",
  other_levels: "Other levels",
  fvg: "FVG",
  ifvg: "IFVG",
  entry: "Paper entry",
  stop: "Paper invalidation",
  target: "Paper target",
} as const;

export type Overlay = keyof typeof OVERLAYS;
export const overlayKeys = Object.keys(OVERLAYS) as Overlay[];

export type ChartMarker = {
  category: Overlay;
  label: string;
  available_at: string;
  below_bar: boolean;
  sequence: number;
};
export type ChartSpan = {
  category: Overlay;
  lower_ticks: number;
  upper_ticks: number;
  available_at: string;
  until: string;
  source_sequence: number;
  end_sequence: number | null;
};
export type ChartEvidence = { markers: ChartMarker[]; spans: ChartSpan[] };
export const EMPTY_EVIDENCE: ChartEvidence = { markers: [], spans: [] };

export type Candle = {
  available_at: string;
  opened_at: string;
  trade_date: string | null;
  open_ticks: number;
  high_ticks: number;
  low_ticks: number;
  close_ticks: number;
  volume: number;
  shortened: boolean;
};

export function timeline(bars: Candle[]) {
  const opens = bars.map((bar) => Date.parse(bar.opened_at) / 1000);
  const closes = bars.map((bar) => Date.parse(bar.available_at) / 1000);

  function firstCloseAtOrAfter(seconds: number) {
    let low = 0;
    let high = closes.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (closes[middle] < seconds) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  function point(availableAt: string) {
    const seconds = Date.parse(availableAt) / 1000;
    const index = firstCloseAtOrAfter(seconds);
    return index < closes.length && seconds >= opens[index]
      ? closes[index]
      : null;
  }

  function range(startedAt: string, endedAt: string) {
    const start = Date.parse(startedAt) / 1000;
    const end = Date.parse(endedAt) / 1000;
    const first = firstCloseAtOrAfter(start);
    let last = firstCloseAtOrAfter(end);
    if (last === closes.length || end < opens[last]) last -= 1;
    return first <= last ? { from: closes[first], to: closes[last] } : null;
  }

  return { closes, point, range };
}
