import { useCallback, useEffect, useState, type FormEvent } from "react";
import { DataTable } from "./backtest-analytics";
import { api, type RecordData } from "./client";
import { label, money, utcDateTime } from "./format";
import { Metric } from "./metric";

type Run = {
  forward_id: string;
  contract: string;
  data_kind: string;
  started_at: string;
  cursor: string | null;
  pending_setup_id: string | null;
  open_setup_id: string | null;
  caught_up: boolean;
  event_count: number;
  monitor_state: string;
  source: string;
};
type Monitor = {
  monitor_id: string;
  contract: string;
  source: string;
  source_status: string;
};
type Baseline = { report_id: string; created_at: string };

export function ForwardWorkspace({
  monitors,
  baselines,
  onReport,
}: {
  monitors: Monitor[];
  baselines: Baseline[];
  onReport: (reportId: string) => Promise<void>;
}) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState("");
  const [journal, setJournal] = useState<{
    id: string;
    run: Run;
    events: RecordData[];
  }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const eligible = monitors.filter((item) =>
    ["real_time", "simulated"].includes(item.source_status),
  );
  const active = journal?.id === selected ? journal : undefined;
  const inventory = useCallback(async () => {
    const result = await api<Run[]>("forward-runs");
    setRuns(result);
    setSelected((current) =>
      result.some((run) => run.forward_id === current)
        ? current
        : (result[0]?.forward_id ?? ""),
    );
  }, []);

  const loadJournal = useCallback(async (id: string, signal?: AbortSignal) => {
    const [run, events] = await Promise.all([
      api<Run>("forward-runs/" + id, { signal }),
      api<RecordData[]>(
        "forward-runs/" + id + "/events?latest=true&limit=100",
        { signal },
      ),
    ]);
    if (!signal?.aborted)
      setJournal((current) =>
        current?.id === id && (current.run.cursor ?? "") > (run.cursor ?? "")
          ? current
          : { id, run, events },
      );
  }, []);

  useEffect(() => {
    void inventory().catch((failure) => setError((failure as Error).message));
  }, [inventory]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        await loadJournal(selected, controller.signal);
      } catch (failure) {
        if (!controller.signal.aborted) setError((failure as Error).message);
      }
      if (!controller.signal.aborted)
        timer = setTimeout(() => void poll(), 15000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selected, loadJournal]);

  async function action(operation: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const monitor = eligible.find(
      (item) => item.monitor_id === fields.get("monitor"),
    );
    if (!monitor) return;
    void action(async () => {
      const run = await api<Run>("forward-runs", {
        method: "POST",
        body: JSON.stringify({
          monitor_id: monitor.monitor_id,
          synthetic: monitor.source_status === "simulated",
          execution_config: {
            latency_minutes: Number(fields.get("latency")),
            entry_timeout_minutes: Number(fields.get("timeout")),
            round_trip_spread_ticks: Number(fields.get("spread")),
          },
        }),
      });
      await inventory();
      setSelected(run.forward_id);
    });
  }

  function report(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    if (!selected) return;
    void action(async () => {
      const result = await api<{ report_id: string }>("reports/forward", {
        method: "POST",
        body: JSON.stringify({
          forward_id: selected,
          period: fields.get("period"),
          trade_date: fields.get("trade_date"),
          baseline_validation_id: fields.get("baseline") || null,
        }),
      });
      await onReport(result.report_id);
    });
  }

  const run = active?.run;
  return (
    <div className="research-grid">
      <section className="panel lower">
        <div className="panel-top">
          <div>
            <p className="eyebrow">Observed monitor journal</p>
            <h2>Forward paper outcomes</h2>
          </div>
          <label>
            Forward run
            <select
              value={selected}
              disabled={busy}
              onChange={(event) => setSelected(event.target.value)}
            >
              {!runs.length && <option value="">No forward run</option>}
              {runs.map((item) => (
                <option key={item.forward_id} value={item.forward_id}>
                  {item.contract} · {label(item.data_kind)} ·{" "}
                  {item.forward_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && (
          <p className="notice" role="alert">
            {error}
          </p>
        )}
        {run ? (
          <>
            <div className="analytics-sample">
              <strong>{label(run.data_kind)}</strong>
              <p>
                {run.source} · Registered {utcDateTime(run.started_at)}
              </p>
              <p>Last processed source minute {utcDateTime(run.cursor)}</p>
            </div>
            <div className="metrics contained">
              <Metric
                title="Monitor state"
                value={label(run.monitor_state)}
                detail={
                  run.caught_up
                    ? "At applied checkpoint"
                    : "Applied minutes waiting"
                }
              />
              <Metric
                title="Paper order"
                value={run.pending_setup_id ? "Pending" : "—"}
                detail={run.pending_setup_id?.slice(0, 8) ?? "No pending entry"}
              />
              <Metric
                title="Paper position"
                value={run.open_setup_id ? "Open" : "—"}
                detail={run.open_setup_id?.slice(0, 8) ?? "No modeled position"}
              />
              <Metric
                title="Audit events"
                value={String(run.event_count)}
                detail="Most recent 100 shown"
              />
            </div>
            <section className="analytics-section">
              <button
                className="subtle"
                type="button"
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await api<Run>("forward-runs/" + selected + "/sync", {
                      method: "POST",
                    });
                    await loadJournal(selected);
                  })
                }
              >
                Synchronize next applied batch
              </button>
              <p className="muted">
                A foreground forward-sync worker can advance the journal
                continuously.
              </p>
            </section>
            <DataTable
              labelHeading="Audit event"
              items={(active?.events ?? [])
                .slice()
                .reverse()
                .map((item) => [
                  `${label(item.event)} · ${String(item.sequence)}`,
                  item,
                ])}
              columns={[
                {
                  title: "Observed UTC",
                  value: (item) => utcDateTime(item.recorded_at, true),
                },
                {
                  title: "Setup",
                  value: (item) => String(item.setup_id ?? "").slice(0, 8),
                },
                {
                  title: "Reason",
                  value: (item) =>
                    typeof item.reason === "string" ? label(item.reason) : "—",
                },
                { title: "Net P&L", value: (item) => money(item.net_cents) },
                {
                  title: "Evidence",
                  value: (item) => (
                    <details>
                      <summary>Inspect</summary>
                      <pre>{JSON.stringify(item, null, 2)}</pre>
                    </details>
                  ),
                },
              ]}
              empty="No forward outcomes recorded at this checkpoint."
            />
          </>
        ) : (
          <div className="empty">
            {selected
              ? "Loading the forward journal…"
              : "Register a forward run from a configured monitor."}
          </div>
        )}
      </section>
      <aside className="sidebar">
        <section className="panel sidebar-panel">
          <p className="eyebrow">Forward configuration</p>
          <h2>Register paper run</h2>
          <form onSubmit={start}>
            <label>
              Monitor
              <select
                name="monitor"
                required
                disabled={!eligible.length || busy}
              >
                {!eligible.length && (
                  <option value="">No eligible monitor</option>
                )}
                {eligible.map((item) => (
                  <option key={item.monitor_id} value={item.monitor_id}>
                    {item.contract} · {label(item.source_status)} ·{" "}
                    {item.source}
                  </option>
                ))}
              </select>
            </label>
            <p className="muted">
              Registration starts at the current applied cursor. Simulated input
              creates a synthetic verification run.
            </p>
            <div className="form-grid">
              <label>
                Latency minutes
                <input
                  name="latency"
                  type="number"
                  min={0}
                  max={1440}
                  defaultValue={0}
                  required
                />
              </label>
              <label>
                Entry timeout minutes
                <input
                  name="timeout"
                  type="number"
                  min={0}
                  max={1440}
                  defaultValue={5}
                  required
                />
              </label>
              <label>
                Round-trip spread ticks
                <input
                  name="spread"
                  type="number"
                  min={0}
                  defaultValue={0}
                  required
                />
              </label>
            </div>
            <button className="primary" disabled={busy || !eligible.length}>
              Register forward paper run
            </button>
          </form>
        </section>
        {run && (
          <section className="panel sidebar-panel">
            <p className="eyebrow">Frozen journal checkpoint</p>
            <h2>Forward research report</h2>
            <form onSubmit={report}>
              <label>
                Exchange trade date
                <input name="trade_date" type="date" required />
              </label>
              <label>
                Period
                <select name="period">
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </label>
              <label>
                Historical validation baseline
                <select name="baseline">
                  <option value="">Prior forward period only</option>
                  {baselines.map((item) => (
                    <option key={item.report_id} value={item.report_id}>
                      {item.report_id.slice(0, 8)} ·{" "}
                      {utcDateTime(item.created_at)}
                    </option>
                  ))}
                </select>
              </label>
              <p className="muted">
                An optional holdout must predate registration and use matching
                strategy and cost settings.
              </p>
              <button className="primary" disabled={busy}>
                Save forward report
              </button>
            </form>
          </section>
        )}
      </aside>
    </div>
  );
}
