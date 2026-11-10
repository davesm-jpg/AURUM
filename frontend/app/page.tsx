"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  EMPTY_EVIDENCE,
  OVERLAYS,
  overlayKeys,
  type Candle,
  type ChartEvidence,
  type Overlay,
} from "./chart-evidence";
import { api, type RecordData } from "./client";
import { useMonitorChart } from "./monitor-chart";
import { PriceChart } from "./price-chart";
import { label, price, record, rows, stamp } from "./format";
import { Metric } from "./metric";
import { BacktestAnalytics } from "./backtest-analytics";
import { BacktestForm } from "./backtest-form";
import { ReportSummary } from "./report-summary";
import { ReportBuilder } from "./report-builder";
import { ForwardWorkspace } from "./forward-workspace";
import { RuntimeSettings } from "./runtime-settings";
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
type Tab = "Market" | "Research" | "Forward" | "Reports" | "Settings";

const FRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];
const EMPTY_BARS: Candle[] = [];
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

export default function Dashboard() {
  const [tab, setTab] = useState<Tab>("Market");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [monitors, setMonitors] = useState<Monitor[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [monitorId, setMonitorId] = useState("");
  const [sourceMode, setSourceMode] = useState<"dataset" | "monitor">();
  const [monitorRefresh, setMonitorRefresh] = useState(0);
  const [runId, setRunId] = useState("");
  const [reportId, setReportId] = useState("");
  const [frame, setFrame] = useState("5m");
  const [history, setHistory] = useState<{ key: string; bars: Candle[] }>();
  const [events, setEvents] = useState<RecordData[]>([]);
  const [runDetail, setRunDetail] = useState<{
    key: string;
    data: RecordData;
  }>();
  const [reportDetail, setReportDetail] = useState<{
    key: string;
    data: RecordData;
  }>();
  const [error, setError] = useState("");
  const [chartError, setChartError] = useState("");
  const [chartEvidence, setChartEvidence] = useState<{
    key: string;
    data: ChartEvidence;
  }>();
  const [overlayError, setOverlayError] = useState("");
  const [overlays, setOverlays] = useState<Set<Overlay>>(new Set(overlayKeys));
  const dataset = datasets.find((item) => item.dataset_id === datasetId);
  const selectedMode =
    sourceMode ?? (datasets.length || !monitors.length ? "dataset" : "monitor");
  const showMonitor = tab === "Market" && selectedMode === "monitor";
  const monitor = monitors.find((item) => item.monitor_id === monitorId);
  const { view: monitorView, error: monitorError } = useMonitorChart(
    showMonitor ? (monitor?.monitor_id ?? "") : "",
    frame,
    monitorRefresh,
  );
  const feed = monitorView?.chart;
  const monitorEvidence = useMemo(
    () =>
      feed
        ? {
            markers: [
              ...feed.market_evidence.markers,
              ...feed.paper_evidence.markers,
            ],
            spans: [
              ...feed.market_evidence.spans,
              ...feed.paper_evidence.spans,
            ],
          }
        : EMPTY_EVIDENCE,
    [feed],
  );
  const chartKey = datasetId
    ? "datasets/" + datasetId + "/candles?limit=240&timeframe=" + frame
    : "";
  const bars = showMonitor
    ? (feed?.candles ?? EMPTY_BARS)
    : history?.key === chartKey
      ? history.bars
      : EMPTY_BARS;
  const displayedEvents = showMonitor ? (monitorView?.events ?? []) : events;
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
    !showMonitor && activeRun && bounds
      ? "runs/" + activeRun.run_id + "/chart-evidence?" + bounds
      : "";
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
      setMonitorId((current) =>
        feeds.some((item) => item.monitor_id === current)
          ? current
          : (feeds[0]?.monitor_id ?? ""),
      );
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
    if (!chartKey || showMonitor) return;
    const controller = new AbortController();
    setChartError("");
    api<Candle[]>(chartKey, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted)
          setHistory({ key: chartKey, bars: result });
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setChartError(failure.message);
      });
    return () => controller.abort();
  }, [chartKey, showMonitor]);

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
          if (!ignore) setRunDetail({ key: runId, data: result });
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
    if (reportId)
      api<RecordData>("reports/" + reportId)
        .then((result) => {
          if (!ignore) setReportDetail({ key: reportId, data: result });
        })
        .catch((failure) => {
          if (!ignore) setError(failure.message);
        });
    return () => {
      ignore = true;
    };
  }, [reportId]);

  const latest = bars.at(-1);
  const monitorBar = feed?.candles.at(-1);
  const actualSources = Object.entries(monitorBar?.source_minutes ?? {})
    .map(([kind, count]) => `${count} ${label(kind)}`)
    .join(" · ");
  const heartbeat = feed?.heartbeat_status;
  const heartbeatProblem =
    heartbeat === "overdue"
      ? "Status overdue"
      : heartbeat === "clock_ahead"
        ? "Monitor clock ahead"
        : heartbeat === "unobserved"
          ? "Awaiting first check"
          : undefined;
  const contract = showMonitor ? monitor?.contract : dataset?.contract;
  const summary = runDetail?.key === runId ? runDetail.data : undefined;
  const report = reportDetail?.key === reportId ? reportDetail.data : undefined;
  const setups = useMemo(() => {
    const unique = new Map<string, RecordData>();
    for (const event of displayedEvents)
      if (
        typeof event.setup_id === "string" &&
        event.event === "paper_setup_transition"
      )
        unique.set(event.setup_id, event);
    return Array.from(unique.values()).reverse().slice(0, 8);
  }, [displayedEvents]);
  const latestSetup = setups[0];

  function flipOverlay(item: Overlay) {
    setOverlays((current) => {
      const next = new Set(current);
      if (next.has(item)) next.delete(item);
      else next.add(item);
      return next;
    });
  }

  async function selectCompletedBacktest(id: string) {
    await refresh();
    setRunId(id);
  }

  async function selectCompletedReport(id: string) {
    await refresh();
    setReportId(id);
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
          {(
            ["Market", "Research", "Forward", "Reports", "Settings"] as Tab[]
          ).map((item) => (
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
        {tab === "Market" && (
          <label>
            Chart source
            <select
              value={selectedMode}
              onChange={(event) =>
                setSourceMode(
                  event.target.value === "monitor" ? "monitor" : "dataset",
                )
              }
            >
              <option value="dataset">Imported history</option>
              <option value="monitor">Monitor journal</option>
            </select>
          </label>
        )}
        {showMonitor ? (
          <label>
            Monitor
            <select
              value={monitorId}
              onChange={(event) => setMonitorId(event.target.value)}
            >
              {!monitors.length && (
                <option value="">No monitor configured</option>
              )}
              {monitors.map((item) => (
                <option key={item.monitor_id} value={item.monitor_id}>
                  {item.contract} · {label(item.source_status)} · {item.source}
                </option>
              ))}
            </select>
          </label>
        ) : (
          (tab === "Market" || tab === "Research") && (
            <>
              <label>
                Dataset
                <select
                  value={datasetId}
                  onChange={(event) => setDatasetId(event.target.value)}
                >
                  {!datasets.length && (
                    <option value="">No dataset imported</option>
                  )}
                  {datasets.map((item) => (
                    <option key={item.dataset_id} value={item.dataset_id}>
                      {item.contract} · {item.data_kind} ·{" "}
                      {stamp(item.last_close)}
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
                  {!availableRuns.length && (
                    <option value="">No saved runs</option>
                  )}
                  {availableRuns.map((run) => (
                    <option key={run.run_id} value={run.run_id}>
                      {run.kind} · {run.variant ?? "market structure"} ·{" "}
                      {run.run_id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )
        )}
        <button
          className="subtle"
          onClick={() => {
            if (showMonitor) setMonitorRefresh((current) => current + 1);
            void refresh();
          }}
        >
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
              value={contract ?? "MGC"}
              detail={
                showMonitor
                  ? "Applied monitor journal"
                  : dataset
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
            {showMonitor ? (
              <>
                <Metric
                  title="Feed health"
                  value={
                    monitorError
                      ? "Read unavailable"
                      : heartbeatProblem
                        ? heartbeatProblem
                        : feed
                          ? label(feed.state)
                          : "Unavailable"
                  }
                  detail={
                    feed
                      ? label(feed.configured_status) + " configured"
                      : "Awaiting monitor status"
                  }
                />
                <Metric
                  title="Exchange schedule"
                  value={label(feed?.market_status)}
                  detail={
                    feed
                      ? feed.calendar_source + " · " + feed.calendar_version
                      : "Reviewed coverage required"
                  }
                />
              </>
            ) : (
              <>
                <Metric
                  title="Source mode"
                  value="Offline research"
                  detail="Imported history"
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
                      ? dataset.candle_count.toLocaleString() +
                        " source minutes"
                      : "Reviewed coverage required"
                  }
                />
              </>
            )}
          </section>

          <div className="market-grid">
            <section className="panel chart-panel">
              <div className="panel-top">
                <div>
                  <p className="eyebrow">Price & structure</p>
                  <h2>{contract ?? "Micro Gold Futures"}</h2>
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
              {showMonitor && monitorError && (
                <p className="notice" role="alert">
                  {monitorError}
                </p>
              )}
              {!showMonitor && chartError && (
                <p className="notice">{chartError}</p>
              )}
              {showMonitor && feed?.fault && (
                <p className="notice">Feed fault · {label(feed.fault)}</p>
              )}
              {showMonitor && !!feed?.pending_minutes && (
                <p className="notice">
                  Gap recovery · {feed.pending_minutes} pending source minutes
                  are held outside the chart.
                </p>
              )}
              {showMonitor &&
                !!feed?.candles.length &&
                !feed.market_evidence_started_at && (
                  <p className="notice">
                    Market evidence capture starts with newly applied minutes.
                  </p>
                )}
              {evidenceQuery && overlayError && (
                <p className="notice">{overlayError}</p>
              )}
              <PriceChart
                key={(showMonitor ? monitorId : datasetId) + "/" + frame}
                bars={bars}
                evidence={
                  showMonitor
                    ? monitorEvidence
                    : chartEvidence?.key === evidenceQuery
                      ? chartEvidence.data
                      : EMPTY_EVIDENCE
                }
                overlays={overlays}
              />
              <div className="chart-footer">
                {(!showMonitor ||
                  !!feed?.market_evidence_started_at ||
                  !!monitorEvidence.markers.length) && (
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
                )}
                <span>
                  UTC candle close
                  {showMonitor
                    ? " · observed monitor evidence"
                    : activeRun
                      ? " · " + activeRun.timeframe + " evidence"
                      : ""}
                </span>
              </div>
            </section>

            <aside className="sidebar">
              <section className="panel sidebar-panel">
                <p className="eyebrow">Input provenance</p>
                <h2>Source & timing</h2>
                <dl className="details">
                  <dt>Provider</dt>
                  <dd>
                    {(showMonitor ? feed?.source : dataset?.provider) ?? "—"}
                  </dd>
                  {showMonitor ? (
                    <>
                      <dt>Latest bar sources</dt>
                      <dd>{actualSources || "—"}</dd>
                      <dt>Last applied close</dt>
                      <dd>{stamp(feed?.last_applied_close)} UTC</dd>
                      <dt>Status checked</dt>
                      <dd>{stamp(feed?.checked_at)} UTC</dd>
                      <dt>Alerts</dt>
                      <dd>
                        {monitorError || !feed
                          ? "Status unavailable"
                          : feed.alerts_suspended
                            ? "Suspended"
                            : "Eligible feed"}
                      </dd>
                    </>
                  ) : (
                    <>
                      <dt>Source class</dt>
                      <dd>{label(dataset?.data_kind)}</dd>
                      <dt>Coverage starts</dt>
                      <dd>{stamp(dataset?.first_open)} UTC</dd>
                    </>
                  )}
                  <dt>New York close</dt>
                  <dd>{stamp(latest?.available_at, "America/New_York")}</dd>
                  <dt>Prague close</dt>
                  <dd>{stamp(latest?.available_at, "Europe/Prague")}</dd>
                  <dt>Trade date</dt>
                  <dd>{latest?.trade_date ?? "—"}</dd>
                </dl>
              </section>
              {showMonitor && feed?.context_event && (
                <ContextPanel event={feed.context_event} />
              )}
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
                      {showMonitor && (
                        <>
                          <dt>Observed</dt>
                          <dd>{stamp(latestSetup.observed_at)} UTC</dd>
                        </>
                      )}
                    </dl>
                  </>
                ) : (
                  <p className="muted">
                    {showMonitor
                      ? "No setup has been recorded in the recent monitor journal."
                      : "Select a strategy or backtest run to inspect its setup history."}
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
                {showMonitor
                  ? "Monitor journal"
                  : activeRun
                    ? activeRun.kind + " run"
                    : "Select a saved run"}
              </span>
            </div>
            <EventTable
              events={displayedEvents
                .filter(
                  (item) =>
                    (showMonitor && item.event !== "market_context_observed") ||
                    eventGroup(item) !== null ||
                    item.event === "paper_setup_transition",
                )
                .slice(-8)
                .reverse()}
            />
          </section>
          {!!monitorView?.alerts.length && (
            <section className="panel lower">
              <div className="panel-top">
                <h2>In-app feed alerts</h2>
              </div>
              <EventTable events={monitorView.alerts} />
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
            {activeRun?.kind === "backtest" && summary ? (
              <BacktestAnalytics
                key={activeRun.run_id}
                runId={activeRun.run_id}
                summary={summary}
              />
            ) : (
              <div className="empty">
                {activeRun?.kind === "backtest"
                  ? "Loading backtest evidence…"
                  : "Select a saved backtest or start a paper run."}
              </div>
            )}
          </section>
          <BacktestForm
            datasetId={datasetId}
            replayable={dataset?.replayable === true}
            selectedRun={activeRun?.kind === "backtest" ? summary : undefined}
            onComplete={selectCompletedBacktest}
          />
          <section className="panel lower wide">
            <div className="panel-top">
              <h2>Setup history</h2>
            </div>
            <EventTable events={setups} />
          </section>
        </div>
      )}

      {tab === "Forward" && (
        <ForwardWorkspace
          monitors={monitors}
          baselines={reports.filter(
            (report) => report.kind === "chronological_validation",
          )}
          onReport={async (id) => {
            await selectCompletedReport(id);
            setTab("Reports");
          }}
        />
      )}

      {tab === "Reports" && (
        <>
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
                  {!reports.length && (
                    <option value="">No saved reports</option>
                  )}
                  {reports.map((item) => (
                    <option key={item.report_id} value={item.report_id}>
                      {label(item.kind)} · {item.report_id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {!reportId ? (
              <div className="empty">Create a research report below.</div>
            ) : !report ? (
              <div className="empty">Loading saved report…</div>
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
          <ReportBuilder
            datasetId={datasetId}
            datasets={datasets}
            runs={runs.filter((item) => item.kind === "backtest")}
            validationReports={reports.filter(
              (item) => item.kind === "chronological_validation",
            )}
            onComplete={selectCompletedReport}
          />
        </>
      )}

      {tab === "Settings" && (
        <RuntimeSettings datasets={datasets} monitors={monitors} />
      )}

      <footer className="footer">
        <span>AURUM / MGC research terminal</span>
        <span>Paper analysis only · Source labels preserved</span>
      </footer>
    </main>
  );
}

function ContextPanel({ event }: { event: RecordData }) {
  const context = record(event.context);
  return (
    <section className="panel sidebar-panel">
      <p className="eyebrow">Observed market context</p>
      <h2>Structure & regime</h2>
      <dl className="details">
        <dt>Session</dt>
        <dd>{label(context.session)}</dd>
        <dt>Regime</dt>
        <dd>{label(context.regime)}</dd>
        <dt>Primary structure</dt>
        <dd>{label(context.structure)}</dd>
        {rows(context.higher).map((item) => (
          <Fragment key={String(item.timeframe)}>
            <dt>{String(item.timeframe)} structure</dt>
            <dd title={stamp(item.available_at) + " UTC"}>
              {label(item.trend)}
            </dd>
          </Fragment>
        ))}
        <dt>Observed</dt>
        <dd>{stamp(event.observed_at)} UTC</dd>
      </dl>
      <details className="audit">
        <summary>Context evidence</summary>
        <pre>{JSON.stringify(event, null, 2)}</pre>
      </details>
    </section>
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
                  event.observed_at ??
                    event.confirmed_at ??
                    event.created_at ??
                    event.recorded_at ??
                    event.at,
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
