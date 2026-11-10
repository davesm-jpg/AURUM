import { useEffect, useState, type FormEvent } from "react";
import { api, type RecordData } from "./client";
import { label, record, rows, utcDateTime } from "./format";

type ReportKind =
  | "validation"
  | "matched-control"
  | "intrabar-comparison"
  | "reconciliation"
  | "monte-carlo"
  | "paper-account";
type RunChoice = {
  run_id: string;
  variant?: string;
  timeframe: string;
  dataset_id: string;
  intrabar_ordering?: string | null;
};
type DatasetChoice = {
  dataset_id: string;
  contract: string;
  provider: string;
  data_kind: string;
};
type Defaults = {
  matched_control: RecordData;
  control_strata: string[];
  monte_carlo: RecordData;
  sampling_modes: string[];
  assumed_scenarios: RecordData[];
  account_rules: RecordData;
  account_phases: string[];
};
const TYPES: Record<ReportKind, string> = {
  validation: "Chronological validation",
  "matched-control": "Matched random control",
  "intrabar-comparison": "Intrabar execution sensitivity",
  reconciliation: "Historical data reconciliation",
  "monte-carlo": "Monte Carlo simulation",
  "paper-account": "Paper account ledger",
};

function IntegerInput({
  name,
  title,
  value,
  min,
}: {
  name: string;
  title: string;
  value: unknown;
  min?: number;
}) {
  return (
    <label>
      {title}
      <input
        type="number"
        name={name}
        step={1}
        min={min}
        defaultValue={String(value)}
        required
      />
    </label>
  );
}

function integerList(value: FormDataEntryValue | null) {
  const parts = String(value ?? "")
    .split(",")
    .map((part) => part.trim());
  if (parts.some((part) => !/^\d+$/.test(part)))
    throw new Error("Use a comma-separated list of nonnegative integers.");
  return parts.map(Number);
}

function objectJson(value: string, title: string) {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      throw new Error();
    return parsed;
  } catch {
    throw new Error(title + " must contain a JSON object.");
  }
}

export function ReportBuilder({
  runs,
  datasets,
  validationReports,
  datasetId,
  onComplete,
}: {
  runs: RunChoice[];
  datasets: DatasetChoice[];
  validationReports: { report_id: string; created_at: string }[];
  datasetId: string;
  onComplete: (reportId: string) => Promise<void>;
}) {
  const [kind, setKind] = useState<ReportKind>("validation");
  const [primaryDatasetId, setPrimaryDatasetId] = useState("");
  const [stopFirstRunId, setStopFirstRunId] = useState("");
  const [defaults, setDefaults] = useState<Defaults>();
  const [mode, setMode] = useState("scenario");
  const [trailing, setTrailing] = useState(false);
  const [calendar, setCalendar] = useState("");
  const [coverage, setCoverage] = useState<RecordData>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<Defaults>("configuration/reports", { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setDefaults(data);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError((failure as Error).message);
      });
    return () => controller.abort();
  }, []);

  async function selectedCalendar() {
    if (!datasetId) return;
    setError("");
    try {
      const dataset = await api<RecordData>("datasets/" + datasetId);
      const provenance = record(dataset.provenance);
      setCalendar(JSON.stringify(provenance.calendar, null, 2));
      setCoverage({
        start: dataset.first_open,
        end: dataset.last_close,
        contract: provenance.contract,
      });
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!defaults) return;
    const fields = new FormData(event.currentTarget);
    const int = (name: string) => Number(fields.get(name));
    setBusy(true);
    setError("");
    try {
      let request: RecordData;
      if (kind === "validation") {
        request = {
          run_ids: fields.getAll("run_ids"),
          validation_start: fields.get("validation_start"),
          holdout_start: fields.get("holdout_start"),
          embargo_minutes: int("embargo_minutes"),
          minimum_selection_trades: int("minimum_selection_trades"),
          bootstrap_draws: int("bootstrap_draws"),
          block_length: int("block_length"),
          seed: int("seed"),
          walk_train_days: int("walk_train_days"),
          walk_test_days: int("walk_test_days"),
          extra_cost_per_contract_cents: integerList(fields.get("extra_costs")),
        };
      } else if (kind === "intrabar-comparison") {
        request = {
          stop_first_run_id: fields.get("stop_first_run_id"),
          trade_print_run_id: fields.get("trade_print_run_id"),
          example_limit: int("example_limit"),
        };
      } else if (kind === "reconciliation") {
        request = {
          primary_dataset_id: fields.get("primary_dataset_id"),
          comparison_dataset_id: fields.get("comparison_dataset_id"),
          start: fields.get("start") || null,
          end: fields.get("end") || null,
          example_limit: int("example_limit"),
        };
      } else if (kind === "matched-control") {
        request = {
          validation_id: fields.get("validation_id"),
          opportunity_run_id: fields.get("opportunity_run_id"),
          strata: fields.get("strata"),
          replications: int("replications"),
          bootstrap_draws: int("bootstrap_draws"),
          block_length: int("block_length"),
          seed: int("seed"),
        };
      } else if (kind === "monte-carlo") {
        const config = Object.fromEntries(
          Object.keys(defaults.monte_carlo).map((name) => [name, int(name)]),
        );
        const scenarios =
          mode === "scenario"
            ? integerList(fields.get("win_rates")).map((rate) => ({
                win_rate_bps: rate,
                gross_win_cents: int("gross_win_cents"),
                gross_loss_cents: int("gross_loss_cents"),
                minimum_cost_cents: int("minimum_cost_cents"),
                maximum_cost_cents: int("maximum_cost_cents"),
              }))
            : [];
        request = {
          mode,
          config,
          scenarios,
          run_id: mode === "scenario" ? null : fields.get("run_id"),
          trailing: trailing
            ? {
                rules: defaults.account_rules,
                trades_per_session: int("trades_per_session"),
              }
            : null,
        };
      } else {
        request = {
          calendar: objectJson(calendar, "Calendar"),
          ledger: objectJson(String(fields.get("ledger") ?? ""), "Ledger"),
          rules: objectJson(String(fields.get("rules") ?? ""), "Account rules"),
          phase: fields.get("phase"),
        };
      }
      const report = await api<{ report_id: string }>("reports/" + kind, {
        method: "POST",
        body: JSON.stringify(request),
      });
      await onComplete(report.report_id);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const assumed = rows(defaults?.assumed_scenarios)[0] ?? {};
  const primaryDataset = datasets.find(
    (dataset) => dataset.dataset_id === primaryDatasetId,
  );
  const comparisonDatasets = datasets.filter(
    (dataset) =>
      dataset.dataset_id !== primaryDatasetId &&
      dataset.contract === primaryDataset?.contract &&
      dataset.data_kind === primaryDataset?.data_kind,
  );
  const compatible = (first: RunChoice, second: RunChoice) =>
    first.dataset_id === second.dataset_id &&
    first.timeframe === second.timeframe &&
    first.variant === second.variant;
  const orderedRuns = runs.filter(
    (run) => run.intrabar_ordering === "trade_print_sequence_v1",
  );
  const stopFirstRuns = runs.filter(
    (run) =>
      !run.intrabar_ordering &&
      orderedRuns.some((ordered) => compatible(run, ordered)),
  );
  const stopFirst = stopFirstRuns.find((run) => run.run_id === stopFirstRunId);
  const pairedRuns = stopFirst
    ? orderedRuns.filter((run) => compatible(stopFirst, run))
    : [];
  const canRun = {
    reconciliation: comparisonDatasets.length > 0,
    "intrabar-comparison": pairedRuns.length > 0,
    "matched-control": runs.length > 0 && validationReports.length > 0,
    validation: runs.length > 0,
    "paper-account": true,
    "monte-carlo": mode === "scenario" || runs.length > 0,
  }[kind];
  return (
    <section className="panel lower sidebar-panel">
      <details className="report-builder">
        <summary>Create a research report</summary>
        {error && (
          <p className="notice" role="alert">
            {error}
          </p>
        )}
        {!defaults ? (
          <p className="muted">Loading versioned report defaults…</p>
        ) : (
          <form onSubmit={submit}>
            <label>
              Report type
              <select
                value={kind}
                disabled={busy}
                onChange={(event) => {
                  setKind(event.target.value as ReportKind);
                  setError("");
                }}
              >
                {Object.entries(TYPES).map(([key, name]) => (
                  <option key={key} value={key}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <fieldset className="settings-fields" disabled={busy} key={kind}>
              {kind === "validation" && (
                <>
                  <p className="muted">
                    Use comparable runs. Choose the final holdout boundary
                    before comparing results.
                  </p>
                  <label>
                    Candidate backtests
                    <select multiple name="run_ids" required>
                      {runs.map((run) => (
                        <option key={run.run_id} value={run.run_id}>
                          {run.run_id.slice(0, 8)} · {run.variant ?? "paper"} ·{" "}
                          {run.timeframe} · Dataset {run.dataset_id.slice(0, 8)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="form-grid">
                    <label>
                      Validation starts UTC
                      <input
                        name="validation_start"
                        placeholder="YYYY-MM-DDTHH:MMZ"
                        required
                      />
                    </label>
                    <label>
                      Final holdout starts UTC
                      <input
                        name="holdout_start"
                        placeholder="YYYY-MM-DDTHH:MMZ"
                        required
                      />
                    </label>
                    <IntegerInput
                      name="embargo_minutes"
                      title="Embargo minutes"
                      value={0}
                      min={0}
                    />
                    <IntegerInput
                      name="minimum_selection_trades"
                      title="Minimum selection trades"
                      value={20}
                      min={1}
                    />
                    <IntegerInput
                      name="bootstrap_draws"
                      title="Bootstrap draws"
                      value={2000}
                      min={2}
                    />
                    <IntegerInput
                      name="block_length"
                      title="Bootstrap block trades"
                      value={5}
                      min={1}
                    />
                    <IntegerInput
                      name="walk_train_days"
                      title="Walk-forward train days"
                      value={90}
                      min={1}
                    />
                    <IntegerInput
                      name="walk_test_days"
                      title="Walk-forward test days"
                      value={30}
                      min={1}
                    />
                    <IntegerInput name="seed" title="Random seed" value={42} />
                    <label>
                      Additional cost cents per contract
                      <input
                        name="extra_costs"
                        defaultValue="0, 50, 100, 200"
                        required
                      />
                    </label>
                  </div>
                </>
              )}
              {kind === "intrabar-comparison" && (
                <>
                  <p className="muted">
                    Compare two backtests built from the same trade-print
                    dataset and settings. Only dual-touch ordering may differ.
                  </p>
                  <label>
                    Stop-first backtest
                    <select
                      name="stop_first_run_id"
                      value={stopFirstRunId}
                      onChange={(event) =>
                        setStopFirstRunId(event.target.value)
                      }
                      required
                    >
                      <option value="">Select a stop-first run</option>
                      {stopFirstRuns.map((run) => (
                        <option key={run.run_id} value={run.run_id}>
                          {label(run.variant)} · {run.timeframe} ·{" "}
                          {run.run_id.slice(0, 8)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Ordered trade-print backtest
                    <select
                      key={stopFirstRunId}
                      name="trade_print_run_id"
                      required
                    >
                      <option value="">Select an ordered run</option>
                      {pairedRuns.map((run) => (
                        <option key={run.run_id} value={run.run_id}>
                          {run.run_id.slice(0, 8)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <IntegerInput
                    name="example_limit"
                    title="Dual-touch audit examples"
                    value={20}
                    min={0}
                  />
                </>
              )}
              {kind === "reconciliation" && (
                <>
                  <p className="muted">
                    Compare imported minute bars for the same dated contract.
                    Leave both times blank to use shared calendar coverage.
                  </p>
                  <label>
                    Primary dataset
                    <select
                      name="primary_dataset_id"
                      required
                      value={primaryDatasetId}
                      onChange={(event) =>
                        setPrimaryDatasetId(event.target.value)
                      }
                    >
                      <option value="">Select a dataset</option>
                      {datasets.map((item) => (
                        <option key={item.dataset_id} value={item.dataset_id}>
                          {item.contract} · {item.provider} ·{" "}
                          {item.dataset_id.slice(0, 8)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Comparison dataset
                    <select
                      key={primaryDatasetId}
                      name="comparison_dataset_id"
                      required
                    >
                      <option value="">Select a compatible dataset</option>
                      {comparisonDatasets.map((item) => (
                        <option key={item.dataset_id} value={item.dataset_id}>
                          {item.provider} · {item.dataset_id.slice(0, 8)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="fields">
                    <label>
                      Start in UTC
                      <input name="start" placeholder="2025-03-10T13:00Z" />
                    </label>
                    <label>
                      End in UTC
                      <input name="end" placeholder="2025-03-10T14:00Z" />
                    </label>
                    <IntegerInput
                      name="example_limit"
                      title="Examples per discrepancy"
                      value={20}
                      min={0}
                    />
                  </div>
                </>
              )}
              {kind === "matched-control" && (
                <>
                  <p className="muted">
                    Use a saved validation selection and a predeclared
                    opportunity variant with the same data and execution
                    assumptions. Admission rates are fitted on development
                    signals.
                  </p>
                  <label>
                    Chronological validation report
                    <select name="validation_id" required>
                      <option value="">Select a report</option>
                      {validationReports.map((item) => (
                        <option key={item.report_id} value={item.report_id}>
                          {item.report_id.slice(0, 8)} ·{" "}
                          {utcDateTime(item.created_at)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Opportunity backtest
                    <select name="opportunity_run_id" required>
                      <option value="">Select a run</option>
                      {runs.map((item) => (
                        <option key={item.run_id} value={item.run_id}>
                          {label(item.variant)} · {item.timeframe} ·{" "}
                          {item.run_id.slice(0, 8)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="fields">
                    <label>
                      Admission groups
                      <select
                        name="strata"
                        defaultValue={String(defaults.matched_control.strata)}
                      >
                        {defaults.control_strata.map((group) => (
                          <option key={group} value={group}>
                            {label(group)}
                          </option>
                        ))}
                      </select>
                    </label>
                    {(
                      [
                        ["replications", "Control replications", 1],
                        ["bootstrap_draws", "Bootstrap draws", 2],
                        ["block_length", "Bootstrap block trades", 1],
                        ["seed", "Random seed", undefined],
                      ] as const
                    ).map(([name, title, min]) => (
                      <IntegerInput
                        key={name}
                        name={name}
                        title={title}
                        value={defaults.matched_control[name]}
                        min={min}
                      />
                    ))}
                  </div>
                </>
              )}
              {kind === "monte-carlo" && (
                <>
                  <label>
                    Sampling method
                    <select
                      value={mode}
                      onChange={(event) => setMode(event.target.value)}
                    >
                      {defaults.sampling_modes.map((item) => (
                        <option key={item} value={item}>
                          {label(item)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {mode === "scenario" ? (
                    <div className="form-grid">
                      <label>
                        Assumed win rates in bps
                        <input
                          name="win_rates"
                          defaultValue={defaults.assumed_scenarios
                            .map((item) => item.win_rate_bps)
                            .join(", ")}
                          required
                        />
                      </label>
                      <IntegerInput
                        name="gross_win_cents"
                        title="Gross win cents"
                        value={assumed.gross_win_cents}
                        min={1}
                      />
                      <IntegerInput
                        name="gross_loss_cents"
                        title="Gross loss cents"
                        value={assumed.gross_loss_cents}
                        min={1}
                      />
                      <IntegerInput
                        name="minimum_cost_cents"
                        title="Minimum round-trip cost cents"
                        value={assumed.minimum_cost_cents}
                        min={0}
                      />
                      <IntegerInput
                        name="maximum_cost_cents"
                        title="Maximum round-trip cost cents"
                        value={assumed.maximum_cost_cents}
                        min={0}
                      />
                    </div>
                  ) : (
                    <label>
                      Backtest to resample
                      <select name="run_id" required>
                        {runs.map((run) => (
                          <option key={run.run_id} value={run.run_id}>
                            {run.run_id.slice(0, 8)} · {run.variant ?? "paper"}{" "}
                            · {run.timeframe}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <div className="form-grid">
                    <IntegerInput
                      name="paths"
                      title="Paths"
                      value={defaults.monte_carlo.paths}
                      min={1}
                    />
                    <IntegerInput
                      name="trades"
                      title="Trades per path"
                      value={defaults.monte_carlo.trades}
                      min={1}
                    />
                    <IntegerInput
                      name="block_length"
                      title="Block length"
                      value={defaults.monte_carlo.block_length}
                      min={1}
                    />
                    <IntegerInput
                      name="seed"
                      title="Random seed"
                      value={defaults.monte_carlo.seed}
                    />
                    <IntegerInput
                      name="initial_equity_cents"
                      title="Initial equity cents"
                      value={defaults.monte_carlo.initial_equity_cents}
                      min={1}
                    />
                    <IntegerInput
                      name="loss_allowance_cents"
                      title="Loss allowance cents"
                      value={defaults.monte_carlo.loss_allowance_cents}
                      min={1}
                    />
                  </div>
                  <label className="checkbox-setting">
                    <input
                      type="checkbox"
                      checked={trailing}
                      onChange={(event) => setTrailing(event.target.checked)}
                    />
                    Apply reference trailing floor at simulated session closes
                  </label>
                  {trailing && (
                    <IntegerInput
                      name="trades_per_session"
                      title="Assumed trades per session"
                      value={5}
                      min={1}
                    />
                  )}
                </>
              )}
              {kind === "paper-account" && (
                <>
                  <p className="muted">
                    Use explicit paper marks and a reviewed exchange calendar.
                    The saved report preserves the ledger and exact rule
                    profile.
                  </p>
                  <label>
                    Account phase
                    <select name="phase">
                      {defaults.account_phases.map((item) => (
                        <option key={item} value={item}>
                          {label(item)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="subtle"
                    type="button"
                    disabled={!datasetId}
                    onClick={() => void selectedCalendar()}
                  >
                    Use selected dataset calendar
                  </button>
                  {coverage && (
                    <p className="muted">
                      {String(coverage.contract)} ·{" "}
                      {utcDateTime(coverage.start)} to{" "}
                      {utcDateTime(coverage.end)}
                    </p>
                  )}
                  <label>
                    Exchange calendar JSON
                    <textarea
                      name="calendar"
                      rows={6}
                      spellCheck={false}
                      value={calendar}
                      onChange={(event) => setCalendar(event.target.value)}
                      required
                    />
                  </label>
                  <label>
                    Paper ledger JSON
                    <textarea
                      name="ledger"
                      rows={8}
                      spellCheck={false}
                      placeholder='{"data_kind":"paper_marks","source":"paper ledger","started_at":"…","actions":[]}'
                      required
                    />
                  </label>
                  <label>
                    Account rules JSON
                    <textarea
                      name="rules"
                      rows={8}
                      spellCheck={false}
                      defaultValue={JSON.stringify(
                        defaults.account_rules,
                        null,
                        2,
                      )}
                      required
                    />
                  </label>
                </>
              )}
            </fieldset>
            <button className="primary" disabled={busy || !canRun}>
              {busy ? "Building research report…" : "Save research report"}
            </button>
          </form>
        )}
      </details>
    </section>
  );
}
