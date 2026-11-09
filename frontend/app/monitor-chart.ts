import { useEffect, useState } from "react";
import type { Candle, ChartEvidence } from "./chart-evidence";
import { api, type RecordData } from "./client";

type FeedClass = "real_time" | "delayed" | "historical" | "simulated";

type MonitorChart = {
  monitor_id: string;
  contract: string;
  source: string;
  configured_status: FeedClass;
  state: string;
  connected: boolean;
  checked_at: string | null;
  last_applied_close: string | null;
  last_received: string | null;
  pending_minutes: number;
  fault: string | null;
  alerts_suspended: boolean;
  stale_after_seconds: number;
  market_status: "open" | "closed" | "unavailable";
  calendar_source: string;
  calendar_version: string;
  analysis_timeframe: string;
  candles: (Candle & { source_minutes: Partial<Record<FeedClass, number>> })[];
  market_evidence_started_at: string | null;
  market_evidence: ChartEvidence;
  paper_evidence: ChartEvidence;
  context_event: RecordData | null;
};

type MonitorView = {
  chart: MonitorChart;
  events: RecordData[];
  alerts: RecordData[];
};

export function useMonitorChart(
  id: string,
  timeframe: string,
  refresh: number,
) {
  const [value, setValue] = useState<{ key: string; data: MonitorView }>();
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const key = id ? "monitors/" + id : "";
  const chartKey = key ? key + "/chart?limit=240&timeframe=" + timeframe : "";
  useEffect(() => {
    if (!chartKey) return;
    const controller = new AbortController();
    const init = { signal: controller.signal };
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const [chart, journal, market, alerts] = await Promise.all([
          api<MonitorChart>(chartKey, init),
          api<RecordData[]>(key + "/events?latest=true&limit=80", init),
          api<RecordData[]>(key + "/market-events?latest=true&limit=80", init),
          api<RecordData[]>(key + "/alerts?limit=5", init),
        ]);
        if (controller.signal.aborted) return;
        const eventTime = (item: RecordData) =>
          String(item.observed_at ?? item.at ?? item.confirmed_at ?? "");
        const events = [...journal, ...market].sort((left, right) =>
          eventTime(left).localeCompare(eventTime(right)),
        );
        setValue({ key: chartKey, data: { chart, events, alerts } });
        setFailure(undefined);
      } catch (error) {
        if (controller.signal.aborted) return;
        setFailure({ key: chartKey, message: (error as Error).message });
      }
      timer = setTimeout(() => void load(), 15000);
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [chartKey, key, refresh]);
  return {
    view: value?.key === chartKey ? value.data : undefined,
    error: failure?.key === chartKey ? failure.message : "",
  };
}
