import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type IPrimitivePaneView,
  type ISeriesPrimitive,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import {
  timeline,
  type Candle,
  type ChartEvidence,
  type Overlay,
} from "./chart-evidence";

const COLORS: Record<Overlay, string> = {
  swings: "#d1ae6b",
  bos: "#dfbd7c",
  mss: "#f5dca6",
  sweeps: "#73b8ee",
  equal: "#5f9dc4",
  prior_day: "#7aaaca",
  prior_week: "#809db9",
  session_levels: "#628daf",
  other_levels: "#526d82",
  fvg: "#a99bea",
  ifvg: "#cbb1ed",
  entry: "#d1ae6b",
  stop: "#e78184",
  target: "#55c7aa",
};
const TICK = 0.1;

function makeChart(host: HTMLDivElement) {
  const chart = createChart(host, {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: "#10171e" },
      textColor: "#92a0ad",
    },
    grid: {
      vertLines: { color: "#1b2731" },
      horzLines: { color: "#1b2731" },
    },
    rightPriceScale: { borderColor: "#293642" },
    timeScale: {
      borderColor: "#293642",
      timeVisible: true,
      secondsVisible: false,
    },
  });
  const candles = chart.addSeries(CandlestickSeries, {
    upColor: "#55c7aa",
    downColor: "#e78184",
    borderVisible: false,
    wickUpColor: "#55c7aa",
    wickDownColor: "#e78184",
    priceFormat: { type: "price", precision: 1, minMove: TICK },
  });
  return { chart, candles, markers: createSeriesMarkers(candles, []) };
}

type Segment = {
  from: number;
  to: number;
  lower: number;
  upper: number;
  category: Overlay;
};

function priceRanges({ chart, candles }: ReturnType<typeof makeChart>) {
  let ranges: Segment[] = [];
  let redraw: (() => void) | undefined;
  const view: IPrimitivePaneView = {
    renderer: () => ({
      draw: (target) =>
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
          context.save();
          for (const range of ranges) {
            const left = chart
              .timeScale()
              .timeToCoordinate(range.from as UTCTimestamp);
            const right = chart
              .timeScale()
              .timeToCoordinate(range.to as UTCTimestamp);
            const top = candles.priceToCoordinate(range.upper * TICK);
            const bottom = candles.priceToCoordinate(range.lower * TICK);
            if (
              left === null ||
              right === null ||
              top === null ||
              bottom === null
            )
              continue;
            if (
              right < 0 ||
              left > mediaSize.width ||
              bottom < 0 ||
              top > mediaSize.height
            )
              continue;
            const x = Math.max(0, left);
            const width = Math.max(3, Math.min(mediaSize.width, right) - x);
            const color = COLORS[range.category];
            context.strokeStyle = color;
            if (range.lower === range.upper) {
              context.globalAlpha = 0.75;
              context.beginPath();
              context.moveTo(x, top);
              context.lineTo(x + width, top);
              context.stroke();
            } else {
              context.fillStyle = color;
              context.globalAlpha = 0.15;
              context.fillRect(x, top, width, Math.max(1, bottom - top));
              context.globalAlpha = 0.65;
              context.strokeRect(x, top, width, Math.max(1, bottom - top));
            }
          }
          context.restore();
        }),
    }),
  };
  const primitive: ISeriesPrimitive = {
    attached: ({ requestUpdate }) => {
      redraw = requestUpdate;
    },
    paneViews: () => [view],
  };
  candles.attachPrimitive(primitive);
  return (segments: Segment[]) => {
    ranges = segments;
    redraw?.();
  };
}

export function PriceChart({
  bars,
  evidence,
  overlays,
}: {
  bars: Candle[];
  evidence: ChartEvidence;
  overlays: Set<Overlay>;
}) {
  const host = useRef<HTMLDivElement>(null);
  const instance = useRef<ReturnType<typeof makeChart> | null>(null);
  const drawRanges = useRef<ReturnType<typeof priceRanges> | null>(null);
  const fitted = useRef(false);
  useEffect(() => {
    if (!host.current) return;
    const state = makeChart(host.current);
    instance.current = state;
    drawRanges.current = priceRanges(state);
    fitted.current = false;
    return () => {
      instance.current = null;
      drawRanges.current = null;
      state.chart.remove();
    };
  }, []);
  useEffect(() => {
    const state = instance.current;
    if (!state) return;
    const axis = timeline(bars);
    state.candles.setData(
      bars.map((bar, index) => ({
        time: axis.closes[index] as UTCTimestamp,
        open: bar.open_ticks * TICK,
        high: bar.high_ticks * TICK,
        low: bar.low_ticks * TICK,
        close: bar.close_ticks * TICK,
      })),
    );
    if (!fitted.current && bars.length) {
      state.chart.timeScale().fitContent();
      fitted.current = true;
    }
  }, [bars]);
  useEffect(() => {
    const state = instance.current;
    if (!state) return;
    const axis = timeline(bars);
    const markers: SeriesMarker<UTCTimestamp>[] = [];
    for (const item of evidence.markers) {
      if (!overlays.has(item.category)) continue;
      const time = axis.point(item.available_at);
      if (time === null) continue;
      markers.push({
        time: time as UTCTimestamp,
        position: item.below_bar ? "belowBar" : "aboveBar",
        shape:
          item.label === "Paper signal"
            ? item.below_bar
              ? "arrowUp"
              : "arrowDown"
            : "circle",
        color: COLORS[item.category],
        text: item.label,
        size: 0.55,
      });
    }
    state.markers.setMarkers(markers);
    const ranges: Segment[] = [];
    for (const item of evidence.spans) {
      if (!overlays.has(item.category)) continue;
      const extent = axis.range(item.available_at, item.until);
      if (extent)
        ranges.push({
          ...extent,
          lower: item.lower_ticks,
          upper: item.upper_ticks,
          category: item.category,
        });
    }
    drawRanges.current?.(ranges);
  }, [bars, evidence, overlays]);
  return (
    <div
      className="chart-surface"
      ref={host}
      aria-label="Micro Gold candle chart in UTC"
    >
      {!bars.length && (
        <div className="chart-empty">No completed candles in this view.</div>
      )}
    </div>
  );
}
