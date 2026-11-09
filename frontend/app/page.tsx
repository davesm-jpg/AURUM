"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import {
  EMPTY_EVIDENCE,
  OVERLAYS,
  overlayKeys,
  type Candle,
  type ChartEvidence,
  type Overlay,
} from "./chart-evidence";
import { PriceChart } from "./price-chart";

type RecordData = Record<string, unknown>;
type Dataset = {
  dataset_id: string;
  contract: string;
  provider: string;
  data_kind: string;
  first_open: string;
  last_close: string;
  candle_count: number;
  replayable: boolean;
};
type Run = {
  run_id: string;
  dataset_id: string;
  kind: string;
  timeframe: string;
  variant?: string;
};
type Report = { report_id: string; kind: string; created_at: string };
type Monitor = {
  monitor_id: string;
  contract: string;
  state: string;
  connected: boolean;
  source: string;
  source_status: string;
  checked_at: string | null;
};
type Tab = "Market" | "Research" | "Reports";

const FRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];
const VARIANTS = [
  "mss",
  "sweep_mss",
  "mss_fvg",
  "mss_ifvg",
  "sweep_mss_fvg",
  "sweep_mss_ifvg",
  "sweep_mss_ifvg_htf",
];
const TICK = 0.1;
const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});
const price = (ticks: number) => dollars.format(ticks * TICK);
const label = (value: unknown) =>
  typeof value === "string" ? value.replaceAll("_", " ") : "Unavailable";
const number = (value: unknown) => (typeof value === "number" ? value : null);
const record = (value: unknown): RecordData =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as RecordData)
    : {};
const rows = (value: unknown): RecordData[] =>
  Array.isArray(value) ? value.map(record) : [];
const stamp = (value: unknown, zone = "UTC") =>
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

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch("/api/" + path, { ...init, cache: "no-store" });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "The request could not be completed.",
    );
  return data as T;
}

function eventGroup(event: RecordData): string | null {
  const kind = String(event.event);
  if (kind === "swing_confirmed") return "Swings";
  if (
    kind === "mss_confirmed" ||
    kind === "bos_confirmed" ||
    kind === "trend_changed"
  )
    return "Structure";
  if (kind === "liquidity_sweep_confirmed" || kind === "liquidity_level_mapped")
    return "Liquidity";
  if (kind === "fvg_confirmed" || kind === "ifvg_confirmed") return "Gaps";
  if (kind === "paper_setup_transition" && event.state === "paper_signal")
    return "Paper plans";
  return null;
}

function eventTitle(event: RecordData): string {
  if (event.event === "paper_setup_transition") return label(event.state);
  if (event.event === "liquidity_level_mapped")
    return label(record(event.level).kind) + " level";
  if (event.event === "swing_confirmed")
    return label(event.relation) + " " + label(event.side);
  return label(event.event);
}

function EquityPlot({ points }: { points: RecordData[] }) {
  const data = points
    .map((point) => number(point.equity_cents))
    .filter((value): value is number => value !== null);
  if (data.length < 2)
    return (
      <div className="empty small">
        An equity curve needs at least two closed paper trades.
      </div>
    );
  const min = Math.min(...data);
  const range = Math.max(1, Math.max(...data) - min);
  const line = data
    .map(
      (value, index) =>
        (index * 600) / (data.length - 1) +
        "," +
        (120 - (104 * (value - min)) / range),
    )
    .join(" ");
  return (
    <svg
      viewBox="0 0 600 136"
      role="img"
      aria-label="Closed-trade equity curve"
    >
      <polyline
        points={line}
        fill="none"
        stroke="#55c7aa"
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export default function Dashboard() {
  const [tab, setTab] = useState<Tab>("Market");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [monitors, setMonitors] = useState<Monitor[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [runId, setRunId] = useState("");
  const [reportId, setReportId] = useState("");
  const [frame, setFrame] = useState("5m");
  const [bars, setBars] = useState<Candle[]>([]);
  const [events, setEvents] = useState<RecordData[]>([]);
  const [summary, setSummary] = useState<RecordData>({});
  const [report, setReport] = useState<RecordData>({});
  const [alerts, setAlerts] = useState<RecordData[]>([]);
  const [variant, setVariant] = useState("sweep_mss_ifvg");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [chartError, setChartError] = useState("");
  const [chartEvidence, setChartEvidence] = useState<{
    key: string;
    data: ChartEvidence;
  }>();
  const [overlayError, setOverlayError] = useState("");
  const [overlays, setOverlays] = useState<Set<Overlay>>(new Set(overlayKeys));
  const dataset = datasets.find((item) => item.dataset_id === datasetId);
  const availableRuns = useMemo(
    () => runs.filter((run) => run.dataset_id === datasetId),
    [runs, datasetId],
  );
  const activeRun = availableRuns.find((run) => run.run_id === runId);
  const bounds = bars.length
    ? new URLSearchParams({
        start: bars[0].opened_at,
        through: bars[bars.length - 1].available_at,
      })
    : null;
  const evidenceQuery =
    activeRun && bounds
      ? "runs/" + activeRun.run_id + "/chart-evidence?" + bounds
      : "";
  const monitor = dataset
    ? monitors.find((item) => item.contract === dataset.contract)
    : monitors[0];

  const refresh = useCallback(async () => {
    try {
      const [data, audit, analysis, feeds] = await Promise.all([
        api<Dataset[]>("datasets"),
        api<Run[]>("runs"),
        api<Report[]>("reports"),
        api<Monitor[]>("monitors"),
      ]);
      setDatasets(data);
      setRuns(audit);
      setReports(analysis);
      setMonitors(feeds);
      setDatasetId((current) => current || data[0]?.dataset_id || "");
      setReportId((current) => current || analysis[0]?.report_id || "");
      setError("");
    } catch (failure) {
      setError((failure as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      void refresh();
    }, 15000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    setRunId((current) =>
      availableRuns.some((run) => run.run_id === current)
        ? current
        : availableRuns[0]?.run_id || "",
    );
  }, [availableRuns]);

  useEffect(() => {
    if (!datasetId) return;
    let ignore = false;
    setBars([]);
    setChartError("");
    api<Candle[]>(
      "datasets/" + datasetId + "/candles?limit=240&timeframe=" + frame,
    )
      .then((result) => {
        if (!ignore) setBars(result);
      })
      .catch((failure) => {
        if (!ignore) setChartError(failure.message);
      });
    return () => {
      ignore = true;
    };
  }, [datasetId, frame]);

  useEffect(() => {
    if (!evidenceQuery) return;
    let ignore = false;
    setOverlayError("");
    api<ChartEvidence>(evidenceQuery)
      .then((data) => {
        if (!ignore) setChartEvidence({ key: evidenceQuery, data });
      })
      .catch((failure) => {
        if (!ignore) setOverlayError(failure.message);
      });
    return () => {
      ignore = true;
    };
  }, [evidenceQuery]);

  useEffect(() => {
    let ignore = false;
    setEvents([]);
    setSummary({});
    if (runId) {
      api<RecordData[]>("runs/" + runId + "/events?latest=true&limit=500")
        .then((result) => {
          if (!ignore) setEvents(result);
        })
        .catch((failure) => {
          if (!ignore) setError(failure.message);
        });
      const endpoint = activeRun?.kind === "backtest" ? "backtests/" : "runs/";
      api<RecordData>(endpoint + runId)
        .then((result) => {
          if (!ignore) setSummary(result);
        })
        .catch((failure) => {
          if (!ignore) setError(failure.message);
        });
    }
    return () => {
      ignore = true;
    };
  }, [runId, activeRun?.kind]);

  useEffect(() => {
    let ignore = false;
    setReport({});
    if (reportId)
      api<RecordData>("reports/" + reportId)
        .then((result) => {
          if (!ignore) setReport(result);
        })
        .catch((failure) => {
          if (!ignore) setError(failure.message);
        });
    return () => {
      ignore = true;
    };
  }, [reportId]);

  useEffect(() => {
    const id = monitor?.monitor_id;
    if (!id) return;
    let ignore = false;
    setAlerts([]);
    const load = () =>
      api<RecordData[]>("monitors/" + id + "/alerts?limit=5")
        .then((result) => {
          if (!ignore) setAlerts(result);
        })
        .catch((failure) => {
          if (!ignore) setError(failure.message);
        });
    void load();
    const timer = setInterval(() => {
      void load();
    }, 15000);
    return () => {
      ignore = true;
      clearInterval(timer);
    };
  }, [monitor?.monitor_id]);

  const latest = bars.at(-1);
  const performance = record(record(summary.summary).performance);
  const setups = useMemo(() => {
    const unique = new Map<string, RecordData>();
    for (const event of events)
      if (
        typeof event.setup_id === "string" &&
        event.event === "paper_setup_transition"
      )
        unique.set(event.setup_id, event);
    return Array.from(unique.values()).reverse().slice(0, 8);
  }, [events]);
  const latestSetup =
    setups.find((item) => item.state === "paper_signal") ?? setups[0];

  function flipOverlay(item: Overlay) {
    setOverlays((current) => {
      const next = new Set(current);
      if (next.has(item)) next.delete(item);
      else next.add(item);
      return next;
    });
  }

  async function runBacktest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!datasetId) return;
    setBusy(true);
    setError("");
    const fields = new FormData(event.currentTarget);
    try {
      const result = await api<RecordData>("backtests", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: datasetId,
          variant,
          timeframe: frame === "1m" ? "5m" : frame,
          gross_risk_cents: Number(fields.get("risk")),
          commission_cents: Number(fields.get("commission")),
          slippage_ticks: Number(fields.get("slippage")),
          pivot_left: Number(fields.get("left")),
          pivot_right: Number(fields.get("right")),
          break_mode: fields.get("break"),
          target_mode: fields.get("target"),
        }),
      });
      await refresh();
      setRunId(String(result.run_id));
      setTab("Research");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <header className="masthead">
        <div className="brand">
          <div className="monogram" aria-hidden="true">
            Au
          </div>
          <div>
            <h1>AURUM</h1>
            <p>Micro Gold Research</p>
          </div>
        </div>
        <nav aria-label="Workspace">
          {(["Market", "Research", "Reports"] as Tab[]).map((item) => (
            <button
              key={item}
              className={tab === item ? "selected" : ""}
              aria-pressed={tab === item}
              onClick={() => setTab(item)}
            >
              {item}
            </button>
          ))}
        </nav>
        <span className="paper-tag">
          <span /> Paper research
        </span>
      </header>

      <section className="selection" aria-label="Active research selection">
        <label>
          Dataset
          <select
            value={datasetId}
            onChange={(event) => setDatasetId(event.target.value)}
          >
            {!datasets.length && <option value="">No dataset imported</option>}
            {datasets.map((item) => (
              <option key={item.dataset_id} value={item.dataset_id}>
                {item.contract} · {item.data_kind} · {stamp(item.last_close)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Evidence run
          <select
            value={runId}
            onChange={(event) => setRunId(event.target.value)}
          >
            {!availableRuns.length && <option value="">No saved runs</option>}
            {availableRuns.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.kind} · {run.variant ?? "market structure"} ·{" "}
                {run.run_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <button className="subtle" onClick={() => void refresh()}>
          Refresh
        </button>
      </section>

      {error && (
        <p className="notice" role="alert">
          {error}
        </p>
      )}
      {tab === "Market" && (
        <>
          <section className="metrics" aria-label="Market overview">
            <Metric
              title="Dated contract"
              value={dataset?.contract ?? "MGC"}
              detail={
                dataset
                  ? label(dataset.data_kind) + " dataset"
                  : "No imported dataset"
              }
            />
            <Metric
              title="Latest close"
              value={latest ? price(latest.close_ticks) : "—"}
              detail={
                latest
                  ? stamp(latest.available_at) + " UTC"
                  : "Awaiting completed bars"
              }
            />
            <Metric
              title="Feed health"
              value={monitor ? label(monitor.state) : "Offline research"}
              detail={
                monitor
                  ? label(monitor.source_status) + " · " + monitor.source
                  : "No monitor configured"
              }
            />
            <Metric
              title="Dataset quality"
              value={
                dataset
                  ? dataset.replayable
                    ? "Replay ready"
                    : "Replay blocked"
                  : "—"
              }
              detail={
                dataset
                  ? dataset.candle_count.toLocaleString() + " source minutes"
                  : "Reviewed coverage required"
              }
            />
          </section>

          <div className="market-grid">
            <section className="panel chart-panel">
              <div className="panel-top">
                <div>
                  <p className="eyebrow">Price & structure</p>
                  <h2>{dataset?.contract ?? "Micro Gold Futures"}</h2>
                </div>
                <div className="frame-picker" aria-label="Candle timeframe">
                  {FRAMES.map((item) => (
                    <button
                      key={item}
                      className={frame === item ? "active" : ""}
                      aria-pressed={frame === item}
                      onClick={() => setFrame(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              </div>
              {chartError && <p className="notice">{chartError}</p>}
              {evidenceQuery && overlayError && (
                <p className="notice">{overlayError}</p>
              )}
              <PriceChart
                key={datasetId + "/" + frame}
                bars={bars}
                evidence={
                  chartEvidence?.key === evidenceQuery
                    ? chartEvidence.data
                    : EMPTY_EVIDENCE
                }
                overlays={overlays}
              />
              <div className="chart-footer">
                <div className="overlays">
                  {overlayKeys.map((item) => (
                    <button
                      key={item}
                      className={overlays.has(item) ? "on" : ""}
                      aria-pressed={overlays.has(item)}
                      onClick={() => flipOverlay(item)}
                    >
                      {OVERLAYS[item]}
                    </button>
                  ))}
                </div>
                <span>
                  UTC candle close
                  {activeRun ? " · " + activeRun.timeframe + " evidence" : ""}
                </span>
              </div>
            </section>

            <aside className="sidebar">
              <section className="panel sidebar-panel">
                <p className="eyebrow">Input provenance</p>
                <h2>Source & timing</h2>
                <dl className="details">
                  <dt>Provider</dt>
                  <dd>{dataset?.provider ?? "—"}</dd>
                  <dt>Source class</dt>
                  <dd>{label(dataset?.data_kind)}</dd>
                  <dt>Coverage starts</dt>
                  <dd>{stamp(dataset?.first_open)} UTC</dd>
                  <dt>New York close</dt>
                  <dd>{stamp(latest?.available_at, "America/New_York")}</dd>
                  <dt>Prague close</dt>
                  <dd>{stamp(latest?.available_at, "Europe/Prague")}</dd>
                  <dt>Trade date</dt>
                  <dd>{latest?.trade_date ?? "—"}</dd>
                </dl>
              </section>
              <section className="panel sidebar-panel">
                <p className="eyebrow">Research desk</p>
                <h2>Latest setup</h2>
                {latestSetup ? (
                  <>
                    <span className="chip">{label(latestSetup.state)}</span>
                    <p className="setup-direction">
                      {label(latestSetup.direction)}
                    </p>
                    <dl className="details">
                      <dt>Session</dt>
                      <dd>{label(record(latestSetup.context).session)}</dd>
                      <dt>Regime</dt>
                      <dd>{label(record(latestSetup.context).regime)}</dd>
                      <dt>Confirmed</dt>
                      <dd>{stamp(latestSetup.confirmed_at)} UTC</dd>
                    </dl>
                  </>
                ) : (
                  <p className="muted">
                    Select a strategy or backtest run to inspect its setup
                    history.
                  </p>
                )}
              </section>
            </aside>
          </div>

          <section className="panel lower">
            <div className="panel-top">
              <div>
                <p className="eyebrow">Point-in-time audit</p>
                <h2>Recent evidence</h2>
              </div>
              <span className="muted">
                {activeRun ? activeRun.kind + " run" : "Select a saved run"}
              </span>
            </div>
            <EventTable
              events={events
                .filter(
                  (item) =>
                    eventGroup(item) !== null ||
                    item.event === "paper_setup_transition",
                )
                .slice(-8)
                .reverse()}
            />
          </section>
          {alerts.length > 0 && (
            <section className="panel lower">
              <div className="panel-top">
                <h2>In-app feed alerts</h2>
              </div>
              <EventTable events={alerts} />
            </section>
          )}
        </>
      )}

      {tab === "Research" && (
        <div className="research-grid">
          <section className="panel lower">
            <div className="panel-top">
              <div>
                <p className="eyebrow">Closed-trade results</p>
                <h2>Backtest performance</h2>
              </div>
              {dataset && <span className="chip">{dataset.data_kind}</span>}
            </div>
            {activeRun?.kind === "backtest" ? (
              <>
                <div className="metrics contained">
                  <Metric
                    title="Net P&L"
                    value={money(performance.net_profit_cents)}
                    detail="After modeled costs"
                  />
                  <Metric
                    title="Trades"
                    value={String(performance.trades ?? 0)}
                    detail="Qualifying observations"
                  />
                  <Metric
                    title="Expectancy"
                    value={money(performance.expectancy_cents)}
                    detail="Net per trade"
                  />
                  <Metric
                    title="Drawdown"
                    value={money(performance.max_drawdown_cents)}
                    detail="Closed-trade equity"
                  />
                </div>
                <div className="equity">
                  <EquityPlot points={rows(performance.equity_curve)} />
                </div>
                <details className="audit">
                  <summary>Versioned assumptions and statistics</summary>
                  <pre>{JSON.stringify(summary, null, 2)}</pre>
                </details>
              </>
            ) : (
              <div className="empty">
                Select a saved backtest or start a paper run.
              </div>
            )}
          </section>
          <section className="panel sidebar-panel">
            <p className="eyebrow">Experiment settings</p>
            <h2>Paper backtest</h2>
            <form onSubmit={runBacktest}>
              <label>
                Strategy variant
                <select
                  value={variant}
                  onChange={(event) => setVariant(event.target.value)}
                >
                  {VARIANTS.map((item) => (
                    <option key={item} value={item}>
                      {label(item)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="form-grid">
                <label>
                  Left pivots
                  <input
                    name="left"
                    type="number"
                    min="1"
                    max="50"
                    defaultValue="3"
                    required
                  />
                </label>
                <label>
                  Right pivots
                  <input
                    name="right"
                    type="number"
                    min="1"
                    max="50"
                    defaultValue="3"
                    required
                  />
                </label>
                <label>
                  Risk (cents)
                  <input
                    name="risk"
                    type="number"
                    min="1"
                    defaultValue="5000"
                    required
                  />
                </label>
                <label>
                  Commission (cents)
                  <input
                    name="commission"
                    type="number"
                    min="0"
                    defaultValue="160"
                    required
                  />
                </label>
                <label>
                  Slippage (ticks)
                  <input
                    name="slippage"
                    type="number"
                    min="0"
                    defaultValue="1"
                    required
                  />
                </label>
                <label>
                  Target
                  <select name="target">
                    <option value="fixed">Fixed R</option>
                    <option value="structural">Structural</option>
                  </select>
                </label>
              </div>
              <label>
                Break mode
                <select name="break">
                  <option value="close">Close</option>
                  <option value="wick">Wick</option>
                  <option value="buffered_close">Buffered close</option>
                  <option value="displacement_close">Displacement close</option>
                </select>
              </label>
              <button
                className="primary"
                disabled={busy || !dataset?.replayable}
              >
                {busy ? "Replaying dataset…" : "Run paper backtest"}
              </button>
            </form>
          </section>
          <section className="panel lower wide">
            <div className="panel-top">
              <h2>Setup history</h2>
            </div>
            <EventTable events={setups} />
          </section>
        </div>
      )}

      {tab === "Reports" && (
        <section className="panel lower">
          <div className="panel-top">
            <div>
              <p className="eyebrow">Validation & scenarios</p>
              <h2>Saved research reports</h2>
            </div>
            <label>
              Report
              <select
                value={reportId}
                onChange={(event) => setReportId(event.target.value)}
              >
                {!reports.length && <option value="">No saved reports</option>}
                {reports.map((item) => (
                  <option key={item.report_id} value={item.report_id}>
                    {label(item.kind)} · {item.report_id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {!reportId ? (
            <div className="empty">
              Save a validation, Monte Carlo or account report through the
              research CLI.
            </div>
          ) : (
            <div className="report">
              <span className="chip">{label(report.kind)}</span>
              <ReportSummary report={report} />
              <details className="audit">
                <summary>Report assumptions and evidence</summary>
                <pre>{JSON.stringify(report, null, 2)}</pre>
              </details>
            </div>
          )}
        </section>
      )}

      <footer className="footer">
        <span>AURUM / MGC research terminal</span>
        <span>Paper analysis only · Source labels preserved</span>
      </footer>
    </main>
  );
}

function Metric({
  title,
  value,
  detail,
}: {
  title: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="metric">
      <p>{title}</p>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function EventTable({ events }: { events: RecordData[] }) {
  if (!events.length)
    return (
      <div className="empty small">No matching events in the selected run.</div>
    );
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Event</th>
            <th>Scope / direction</th>
            <th>Available UTC</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event, index) => (
            <tr key={String(event.event_id ?? event.alert_id ?? index)}>
              <td>{eventTitle(event)}</td>
              <td>{label(event.scope ?? event.direction ?? event.state)}</td>
              <td>
                {stamp(
                  event.confirmed_at ?? event.created_at ?? event.recorded_at,
                )}
              </td>
              <td>
                <details>
                  <summary>Inspect</summary>
                  <pre>{JSON.stringify(event, null, 2)}</pre>
                </details>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function money(cents: unknown) {
  const amount = number(cents);
  return amount === null ? "—" : dollars.format(amount / 100);
}

function ReportSummary({ report }: { report: RecordData }) {
  const result = record(report.result);
  const final = record(result.final_state);
  const sims = rows(result.simulations);
  const holdout = record(record(result.holdout).summary);
  if (Object.keys(final).length)
    return (
      <div className="metrics contained">
        <Metric
          title="Paper balance"
          value={money(final.balance_cents)}
          detail={label(final.phase)}
        />
        <Metric
          title="Loss floor"
          value={money(final.loss_floor_cents)}
          detail={label(final.breach_metric)}
        />
        <Metric
          title="Remaining allowance"
          value={money(final.remaining_to_floor_cents)}
          detail={label(final.status)}
        />
        <Metric
          title="Observed marks"
          value={String(result.observed_marks ?? 0)}
          detail={label(result.data_kind)}
        />
      </div>
    );
  if (sims.length)
    return (
      <div className="metrics contained">
        {sims.slice(0, 6).map((sim, index) => {
          const scenario = record(sim.scenario);
          const rate = number(scenario.win_rate_bps);
          const breach = number(sim.breach_probability);
          return (
            <Metric
              key={index}
              title={
                rate === null ? label(sim.mode) : rate / 100 + "% assumed wins"
              }
              value={money(record(sim.ending_equity_cents).median)}
              detail={
                breach === null
                  ? "Conditional scenario"
                  : (breach * 100).toFixed(1) + "% modeled breach"
              }
            />
          );
        })}
      </div>
    );
  if (Object.keys(holdout).length)
    return (
      <div className="metrics contained">
        <Metric
          title="Holdout net"
          value={money(holdout.net_cents)}
          detail={label(result.data_kind)}
        />
        <Metric
          title="Holdout trades"
          value={String(holdout.trades ?? 0)}
          detail="Excluded from selection"
        />
        <Metric
          title="Holdout expectancy"
          value={money(holdout.expectancy_cents)}
          detail="Net per observation"
        />
      </div>
    );
  return (
    <p className="muted">Inspect the saved definition and evidence below.</p>
  );
}
