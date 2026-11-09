import {
  CostTable,
  DataTable,
  OutcomeTable,
  TrendPlot,
} from "./backtest-analytics";
import type { RecordData } from "./client";
import {
  label,
  money,
  number,
  percentage,
  record,
  rows,
  utcDateTime,
} from "./format";
import { Metric } from "./metric";

function MonteCarloReport({ result }: { result: RecordData }) {
  const source = record(result.source);
  const simulations = rows(result.simulations);
  const scenarios: [string, RecordData][] = simulations.map((sim, index) => {
    const assumed = number(record(sim.scenario).win_rate_bps);
    const title =
      assumed === null ? label(sim.mode) : `${assumed / 100}% assumed wins`;
    return [`${index + 1}. ${title}`, sim];
  });
  return (
    <section className="analytics-section">
      <h3>Simulated equity and survival</h3>
      <p className="muted">
        {source.data_kind === "assumed"
          ? "Assumed outcomes · Zero market observations"
          : `Resampled backtest · ${label(source.data_kind)} · Run ${String(source.backtest_run_id ?? "").slice(0, 8)}`}
      </p>
      <DataTable
        labelHeading="Scenario"
        items={scenarios}
        columns={[
          {
            title: "Paths",
            value: (row) => String(record(row.config).paths ?? "—"),
          },
          {
            title: "Trades / path",
            value: (row) => String(record(row.config).trades ?? "—"),
          },
          {
            title: "Observations",
            value: (row) => String(row.observations ?? 0),
          },
          {
            title: "5th percentile equity",
            value: (row) =>
              money(record(record(row.ending_equity_cents).quantiles)["5"]),
          },
          {
            title: "Median equity",
            value: (row) => money(record(row.ending_equity_cents).median),
          },
          {
            title: "Mean equity",
            value: (row) => money(record(row.ending_equity_cents).mean),
          },
          {
            title: "95th percentile equity",
            value: (row) =>
              money(record(record(row.ending_equity_cents).quantiles)["95"]),
          },
        ]}
      />
      <DataTable
        labelHeading="Scenario"
        items={scenarios}
        columns={[
          {
            title: "Breach rate",
            value: (row) => percentage(row.breach_probability),
          },
          {
            title: "Median drawdown",
            value: (row) => money(record(row.maximum_drawdown_cents).median),
          },
          {
            title: "Median losing streak",
            value: (row) =>
              String(record(row.maximum_losing_streak).median ?? "—"),
          },
          {
            title: "Median trades to breach",
            value: (row) =>
              String(record(row.time_to_breach_trades).median ?? "—"),
          },
          ...[100, 500, 1000].map((horizon) => ({
            title: `Survive ${horizon}`,
            value: (row: RecordData) =>
              percentage(record(row.survival)[horizon]),
          })),
        ]}
      />
    </section>
  );
}

function ValidationReport({ result }: { result: RecordData }) {
  const holdout = record(result.holdout);
  const summary = record(holdout.summary);
  const uncertainty = record(holdout.uncertainty);
  const folds: [string, RecordData][] = rows(
    result.walk_forward_before_holdout,
  ).map((fold) => [
    utcDateTime(fold.test_start),
    {
      ...record(fold.test),
      test_end: fold.test_end,
      selected: fold.selected_run_id,
      training: fold.training_sample_count,
    },
  ]);
  const candidates: [string, RecordData][] = Object.entries(
    record(result.candidates),
  ).map(([key, value]) => [
    key.slice(0, 8),
    {
      ...record(record(value).validation),
      development: record(record(value).development).trades,
      adjusted_p: record(result.validation_holm_adjusted_p)[key],
    },
  ]);
  return (
    <>
      <section className="analytics-section">
        <h3>Chronological validation</h3>
        <p className="muted">
          {label(result.data_kind)} · {utcDateTime(result.first_open)} to{" "}
          {utcDateTime(result.last_close)}
        </p>
        <p className="muted">
          Validation starts {utcDateTime(result.validation_start)}. Final
          holdout starts {utcDateTime(result.holdout_start)}. Embargo{" "}
          {String(result.embargo_minutes ?? 0)} minutes.
        </p>
        <DataTable
          labelHeading="Candidate"
          items={candidates}
          columns={[
            {
              title: "Development trades",
              value: (row) => String(row.development ?? "—"),
            },
            {
              title: "Validation trades",
              value: (row) => String(row.trades ?? "—"),
            },
            {
              title: "Validation expectancy",
              value: (row) => money(row.expectancy_cents),
            },
            {
              title: "Holm-adjusted p",
              value: (row) => number(row.adjusted_p)?.toFixed(3) ?? "—",
            },
          ]}
        />
      </section>
      <section className="analytics-section">
        <h3>Walk-forward windows before the holdout</h3>
        <DataTable
          labelHeading="Test start"
          items={folds}
          empty="No complete walk-forward windows fit the pre-holdout period."
          columns={[
            { title: "Test end", value: (row) => utcDateTime(row.test_end) },
            {
              title: "Selected run",
              value: (row) =>
                typeof row.selected === "string"
                  ? row.selected.slice(0, 8)
                  : "None",
            },
            {
              title: "Training trades",
              value: (row) => String(row.training ?? "—"),
            },
            { title: "Test trades", value: (row) => String(row.trades ?? "—") },
            {
              title: "Test expectancy",
              value: (row) => money(row.expectancy_cents),
            },
            { title: "Test net P&L", value: (row) => money(row.net_cents) },
          ]}
        />
      </section>
      <section className="analytics-section">
        <h3>Final holdout</h3>
        {holdout.used_for_selection === false ? (
          <div className="metrics contained">
            <Metric
              title="Holdout net"
              value={money(summary.net_cents)}
              detail={label(result.data_kind)}
            />
            <Metric
              title="Holdout trades"
              value={String(summary.trades ?? 0)}
              detail="Excluded from selection"
            />
            <Metric
              title="Holdout expectancy"
              value={money(summary.expectancy_cents)}
              detail="Net per observation"
            />
            <Metric
              title="Bootstrap interval"
              value={
                number(uncertainty.lower_cents) === null
                  ? "—"
                  : `${money(uncertainty.lower_cents)} to ${money(uncertainty.upper_cents)}`
              }
              detail="95% net expectancy interval"
            />
          </div>
        ) : (
          <p className="muted">
            No candidate met the validation selection threshold.
          </p>
        )}
      </section>
      {holdout.used_for_selection === false && (
        <>
          <OutcomeTable
            title="Holdout experimental score bands"
            groups={summary.by_score_decile}
            heading="Score band"
            format={(name) =>
              `${name}–${name === "90" ? 100 : Number(name) + 9}`
            }
          />
          <CostTable evidence={holdout.cost_sensitivity_fixed_fills} />
        </>
      )}
    </>
  );
}

function AccountReport({ result }: { result: RecordData }) {
  const final = record(result.final_state);
  const timeline = rows(result.timeline);
  const states = timeline.map((entry) => record(entry.state));
  function amounts(field: string) {
    return states.flatMap((entry) => {
      const amount = number(entry[field]);
      return amount === null ? [] : [amount];
    });
  }
  return (
    <section className="analytics-section">
      <h3>Paper account ledger</h3>
      <p className="muted">
        {label(result.data_kind)} · {label(result.measurement)}
      </p>
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
      <div className="trend-grid">
        <TrendPlot
          title="Ledger equity"
          values={amounts("equity_cents")}
          format={money}
          color="#55c7aa"
          basis="Ledger action order"
        />
        <TrendPlot
          title="Trailing floor"
          values={amounts("loss_floor_cents")}
          format={money}
          color="#e78184"
          basis="Ledger action order"
        />
      </div>
    </section>
  );
}

export function ReportSummary({ report }: { report: RecordData }) {
  const result = record(report.result);
  if (report.kind === "monte_carlo")
    return <MonteCarloReport result={result} />;
  if (report.kind === "chronological_validation")
    return <ValidationReport result={result} />;
  if (report.kind === "paper_account") return <AccountReport result={result} />;
  return (
    <p className="muted">Inspect the saved definition and evidence below.</p>
  );
}
