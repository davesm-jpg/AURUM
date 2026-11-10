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

function interval(uncertainty: unknown) {
  const bounds = record(uncertainty).expectancy_95_percentile_cents;
  return Array.isArray(bounds) && bounds.length === 2
    ? `${money(bounds[0])} to ${money(bounds[1])}`
    : "—";
}

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
              value={interval(holdout.uncertainty)}
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

function SensitivityReport({ report }: { report: RecordData }) {
  const result = record(report.result);
  const definition = record(report.definition);
  const dataset = record(result.dataset);
  const factorValue = (value: unknown) => {
    if (value === null) return "Unspecified";
    if (Array.isArray(value))
      return value.length ? value.map(label).join(", ") : "All";
    if (value && typeof value === "object") return JSON.stringify(value);
    return label(value);
  };
  return (
    <>
      <p className="muted">
        {label(result.data_kind)} · {String(dataset.provider ?? "—")} ·{" "}
        {String(dataset.contract ?? "—")}
      </p>
      <p className="muted">
        Development {utcDateTime(result.development_start)} to{" "}
        {utcDateTime(result.development_end_exclusive)}. Validation{" "}
        {utcDateTime(result.validation_start)} to{" "}
        {utcDateTime(result.validation_end_exclusive)}. Boundaries are UTC and
        period ends are exclusive.
      </p>
      <DataTable
        labelHeading={label(definition.factor)}
        items={rows(result.candidates).map((row) => [
          factorValue(row.factor_value) +
            (row.is_baseline ? " · baseline" : ""),
          row,
        ])}
        columns={[
          {
            title: "Development trades",
            value: (row) => String(record(row.development).trades ?? 0),
          },
          {
            title: "Validation trades",
            value: (row) => String(record(row.validation).trades ?? 0),
          },
          {
            title: "Validation expectancy",
            value: (row) => money(record(row.validation).expectancy_cents),
          },
          {
            title: "Difference vs baseline",
            value: (row) => money(row.validation_expectancy_difference_cents),
          },
          {
            title: "Expectancy interval",
            value: (row) => interval(row.validation_uncertainty),
          },
          {
            title: "Holm-adjusted p",
            value: (row) =>
              number(row.validation_holm_adjusted_p)?.toPrecision(3) ?? "—",
          },
        ]}
      />
      <p className="muted">
        The confidence intervals and adjusted p values come from the source
        validation comparison. The p values test each candidate’s expectancy
        against zero.
      </p>
    </>
  );
}

function IntrabarComparisonReport({ result }: { result: RecordData }) {
  return (
    <>
      <p className="muted">
        {label(result.data_kind)} · {String(result.provider ?? "—")} ·{" "}
        {String(result.contract ?? "—")} · {utcDateTime(result.first_open)} to{" "}
        {utcDateTime(result.last_close)} · Unpartitioned replay
      </p>
      <div className="metrics">
        <Metric
          title="Stop-first net P&L"
          value={money(result.stop_first_net_cents)}
          detail={`${String(result.paired_trades ?? 0)} paired trades`}
        />
        <Metric
          title="Ordered-print net P&L"
          value={money(result.trade_print_net_cents)}
          detail={`${String(result.resolved_dual_touch_exits ?? 0)} dual-touch exits resolved`}
        />
        <Metric
          title="Net difference"
          value={money(result.net_difference_cents)}
          detail={`${money(result.expectancy_difference_cents)} per paired trade`}
        />
      </div>
      <p className="muted">
        Stored prints crossed the target first in{" "}
        {String(result.target_before_stop ?? 0)} cases and the stop first in{" "}
        {String(result.stop_before_target ?? 0)} cases. Bracket prices and costs
        are modeled in both runs.
      </p>
      <DataTable
        labelHeading="Setup"
        items={rows(result.examples).map((example, index) => [
          `${index + 1}. ${String(example.setup_id).slice(0, 8)}`,
          example,
        ])}
        columns={[
          {
            title: "First bracket",
            value: (row) => label(row.trade_print_exit_reason),
          },
          {
            title: "Source print (UTC)",
            value: (row) =>
              String(record(row.bracket_touch_print).executed_at ?? "—"),
          },
          {
            title: "Net difference",
            value: (row) => money(row.net_difference_cents),
          },
        ]}
        empty="No dual-touch audit examples in this report."
      />
    </>
  );
}

function ReconciliationReport({ report }: { report: RecordData }) {
  const result = record(report.result);
  const window = record(record(report.definition).window);
  const primary = record(result.primary);
  const comparison = record(result.comparison);
  const counts = record(result.counts);
  const mismatches = record(result.ohlcv_field_mismatches);
  const count = (value: unknown) => number(value)?.toLocaleString() ?? "—";
  return (
    <>
      <p className="muted">
        {label(result.data_kind)} · {String(primary.contract ?? "—")} ·{" "}
        {utcDateTime(window.start)} to {utcDateTime(window.end)}
      </p>
      <div className="metrics">
        <Metric
          title="Comparison window"
          value={result.exact_on_window ? "Exact match" : "Review required"}
          detail={`${count(counts.compared_ohlcv)} paired minute bars`}
        />
        <Metric
          title="Expected minutes missing"
          value={`${count(counts.primary_missing)} / ${count(counts.comparison_missing)}`}
          detail="Primary / comparison"
        />
        <Metric
          title="Bars with OHLCV differences"
          value={count(counts.ohlcv_mismatch)}
          detail={`${count(counts.exact_ohlcv_matches)} exact value matches`}
        />
      </div>
      <p className="muted">
        {count(counts.calendar_open_disagreement)} minutes differ in open
        status.{" "}
        {result.trade_dates_comparable
          ? `${count(counts.trade_date_disagreement)} minutes differ in exchange date.`
          : "One or both calendar snapshots lack dated sessions."}{" "}
        {result.identical_raw_checksum === true &&
          "Both exports have the same checksum. "}
        Replay eligibility applies to each full import.
      </p>
      <DataTable
        labelHeading="Import"
        items={[
          ["Primary", primary],
          ["Comparison", comparison],
        ]}
        columns={[
          { title: "Provider", value: (item) => String(item.provider ?? "—") },
          {
            title: "Source timezone",
            value: (item) => String(item.source_timezone ?? "—"),
          },
          { title: "Label", value: (item) => label(item.timestamp_label) },
          {
            title: "Replay",
            value: (item) => (item.replayable ? "Eligible" : "Blocked"),
          },
        ]}
      />
      <DataTable
        labelHeading="Field"
        items={Object.entries(mismatches).map(([field, value]) => [
          label(field),
          { value },
        ])}
        columns={[{ title: "Differences", value: (item) => count(item.value) }]}
      />
    </>
  );
}

function MatchedControlReport({ report }: { report: RecordData }) {
  const result = record(report.result);
  const definition = record(report.definition);
  const reference = record(result.reference);
  const net = record(result.control_net_distribution_cents);
  const expectancy = record(result.control_expectancy_distribution_cents);
  const dataset = record(result.dataset);
  return (
    <>
      <p className="muted">
        {label(result.data_kind)} · {label(dataset.provider)} ·{" "}
        {String(dataset.contract ?? "—")} ·{" "}
        {utcDateTime(definition.holdout_start)} to{" "}
        {utcDateTime(definition.holdout_end)}
      </p>
      <div className="metrics">
        <Metric
          title="Reference net expectancy"
          value={money(reference.expectancy_cents)}
          detail={`${String(reference.trades ?? 0)} trades · ${label(definition.reference_variant)}`}
        />
        <Metric
          title="Median control expectancy"
          value={money(expectancy.p50)}
          detail={`${String(expectancy.replications ?? 0)} replications with trades`}
        />
        <Metric
          title="Median control net P&L"
          value={money(net.p50)}
          detail={`${String(net.replications ?? 0)} replications · ${label(definition.opportunity_variant)}`}
        />
      </div>
      <p className="muted">
        Reference expectancy interval {interval(result.reference_bootstrap)}.
        Control expectancy spans {money(expectancy.p05)} to{" "}
        {money(expectancy.p95)} from the 5th to the 95th percentile.{" "}
        {String(result.holdout_controls_without_positive_admission ?? 0)} of{" "}
        {String(result.holdout_control_opportunities ?? 0)} holdout
        opportunities had zero fitted admission. All arms start flat and use the
        same execution costs.
      </p>
      <p className="muted">
        Control replications share the same holdout and sample from a structural
        opportunity variant. Treat the comparison as conditional on that variant
        and period.
      </p>
      <DataTable
        labelHeading="Development group"
        items={rows(result.fit).map((item) => [
          Array.isArray(item.stratum)
            ? item.stratum.map(label).join(" · ")
            : "—",
          item,
        ])}
        columns={[
          {
            title: "Reference signals",
            value: (item) => String(item.development_reference_signals ?? 0),
          },
          {
            title: "Opportunities",
            value: (item) => String(item.development_opportunities ?? 0),
          },
          {
            title: "Admission rate",
            value: (item) =>
              percentage(item.admission_rate) +
              (item.reference_rate_unattainable ? " · capped" : ""),
          },
        ]}
      />
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

function ForwardReport({ report }: { report: RecordData }) {
  const result = record(report.result);
  const definition = record(report.definition);
  const current = record(result.period);
  const prior = record(result.previous_period);
  const outcomes = record(current.outcomes);
  const coverage = record(current.coverage);
  const historical = record(result.historical_holdout);
  const comparison: [string, RecordData][] = [
    [
      "Prior forward period",
      {
        ...record(prior.outcomes),
        delta: record(record(result.prior_period_change).difference)
          .expectancy_cents,
        date: prior.first_trade_date,
      },
    ],
  ];
  if (historical.report_id)
    comparison.push([
      "Historical holdout",
      {
        ...record(historical.outcomes),
        delta: record(record(result.historical_holdout_change).difference)
          .expectancy_cents,
        date: utcDateTime(historical.holdout_start),
      },
    ]);
  return (
    <>
      <section className="analytics-section">
        <h3>Forward paper period</h3>
        <p className="muted">
          {label(result.data_kind)} · {String(result.contract)} ·{" "}
          {String(result.source)}
        </p>
        <p className="muted">
          {label(definition.period)} exchange trade dates{" "}
          {String(current.first_trade_date)} through{" "}
          {String(current.end_trade_date_exclusive)} exclusive.
        </p>
        <p className="muted">
          Applied minutes {String(coverage.applied_forward_minutes ?? 0)} of{" "}
          {String(coverage.reviewed_minutes ?? 0)} reviewed ·{" "}
          {String(coverage.execution_eligible_minutes ?? 0)} eligible.
        </p>
        <div className="metrics contained">
          <Metric
            title="Qualified setups"
            value={String(current.qualified_signals ?? 0)}
            detail={`${String(current.entries_filled ?? 0)} simulated fills`}
          />
          <Metric
            title="Completed trades"
            value={String(outcomes.trades ?? 0)}
            detail={`${String(current.unresolved_outcomes ?? 0)} unresolved excluded`}
          />
          <Metric
            title="Net expectancy"
            value={money(outcomes.expectancy_cents)}
            detail="Per completed paper trade"
          />
          <Metric
            title="Bootstrap interval"
            value={interval(current.uncertainty)}
            detail="95% net expectancy interval"
          />
          <Metric
            title="Net P&L"
            value={money(outcomes.net_cents)}
            detail="Closed trades after modeled costs"
          />
          <Metric
            title="Period drawdown"
            value={money(current.period_max_closed_trade_drawdown_cents)}
            detail="Closed trades within this period"
          />
        </div>
      </section>
      <section className="analytics-section">
        <h3>Descriptive comparison</h3>
        <p className="muted">
          Changes use completed trades in each separately labeled sample.
        </p>
        <DataTable
          labelHeading="Comparison sample"
          items={comparison}
          columns={[
            { title: "Starts", value: (row) => String(row.date ?? "—") },
            { title: "Trades", value: (row) => String(row.trades ?? 0) },
            { title: "Win rate", value: (row) => percentage(row.win_rate) },
            {
              title: "Net expectancy",
              value: (row) => money(row.expectancy_cents),
            },
            {
              title: "Current minus comparison",
              value: (row) => money(row.delta),
            },
          ]}
        />
      </section>
      <OutcomeTable
        title="Forward performance by regime"
        groups={outcomes.by_regime}
        heading="Regime"
      />
      <OutcomeTable
        title="Forward experimental score bands"
        groups={outcomes.by_score_decile}
        heading="Score band"
        format={(name) => `${name}–${name === "90" ? 100 : Number(name) + 9}`}
      />
    </>
  );
}

export function ReportSummary({ report }: { report: RecordData }) {
  const result = record(report.result);
  if (report.kind === "monte_carlo")
    return <MonteCarloReport result={result} />;
  if (report.kind === "chronological_validation")
    return <ValidationReport result={result} />;
  if (report.kind === "matched_random_control")
    return <MatchedControlReport report={report} />;
  if (report.kind === "dataset_reconciliation")
    return <ReconciliationReport report={report} />;
  if (report.kind === "intrabar_ordering_comparison")
    return <IntrabarComparisonReport result={result} />;
  if (report.kind === "parameter_sensitivity")
    return <SensitivityReport report={report} />;
  if (report.kind === "paper_account") return <AccountReport result={result} />;
  if (report.kind === "forward_paper_research")
    return <ForwardReport report={report} />;
  return (
    <p className="muted">Inspect the saved definition and evidence below.</p>
  );
}
