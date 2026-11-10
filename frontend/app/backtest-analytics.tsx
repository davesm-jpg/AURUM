import { useEffect, useState, type ReactNode } from "react";
import { api, type RecordData } from "./client";
import { returnHistogram } from "./distribution";
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

const GROUPS = {
  by_month: "Month",
  by_weekday: "Weekday",
  by_session: "Session",
  by_regime: "Regime",
  by_direction: "Direction",
  observation_milestones: "Sample milestones",
} as const;
type Group = keyof typeof GROUPS;
const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];
const WINDOWS = [5, 20, 50, 100];

type Format = (value: unknown) => string;
type Column = { title: string; value: (row: RecordData) => ReactNode };
const outcomeColumns: Column[] = [
  { title: "Trades", value: (row) => String(row.trades ?? 0) },
  { title: "Win rate", value: (row) => percentage(row.win_rate) },
  { title: "Net expectancy", value: (row) => money(row.expectancy_cents) },
  {
    title: "Net P&L",
    value: (row) => money(row.net_cents ?? row.net_profit_cents),
  },
];
const oneDecimal = (value: unknown) =>
  number(value)?.toLocaleString(undefined, { maximumFractionDigits: 1 }) ?? "—";
const executionColumns: Column[] = [
  {
    title: "Avg / median hold (s)",
    value: (row) =>
      oneDecimal(row.average_holding_seconds) +
      " / " +
      oneDecimal(row.median_holding_seconds),
  },
  {
    title: "Avg MFE / MAE (ticks)",
    value: (row) =>
      oneDecimal(row.average_mfe_ticks) +
      " / " +
      oneDecimal(row.average_mae_ticks),
  },
  { title: "Trade P&L SD", value: (row) => money(row.net_trade_stddev_cents) },
  { title: "Modeled costs", value: (row) => money(row.execution_cost_cents) },
  {
    title: "Ambiguous exits",
    value: (row) => String(row.ambiguous_exits ?? "—"),
  },
];

export function DataTable({
  labelHeading,
  items,
  columns,
  empty = "No qualifying observations.",
}: {
  labelHeading: string;
  items: [string, RecordData][];
  columns: Column[];
  empty?: string;
}) {
  if (!items.length) return <p className="muted analytics-empty">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">{labelHeading}</th>
            {columns.map((column) => (
              <th scope="col" key={column.title}>
                {column.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map(([name, row]) => (
            <tr key={name}>
              <th scope="row">{name}</th>
              {columns.map((column) => (
                <td key={column.title}>{column.value(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function OutcomeTable({
  title,
  groups,
  heading = "Group",
  format = label,
}: {
  title: string;
  groups: unknown;
  heading?: string;
  format?: (name: string) => string;
}) {
  return (
    <section className="analytics-section">
      <h3>{title}</h3>
      <DataTable
        labelHeading={heading}
        items={Object.entries(record(groups)).map(([name, row]) => [
          format(name),
          record(row),
        ])}
        columns={outcomeColumns}
      />
    </section>
  );
}

export function CostTable({ evidence }: { evidence: unknown }) {
  return (
    <section className="analytics-section">
      <h3>Cost sensitivity</h3>
      <p className="muted">
        Adds cost per contract while holding simulated fills fixed.
      </p>
      <DataTable
        labelHeading="Additional cost"
        items={rows(evidence).map((item) => [
          money(item.extra_cost_per_contract_cents),
          item,
        ])}
        columns={outcomeColumns}
      />
    </section>
  );
}

export function TrendPlot({
  title,
  values,
  format,
  color,
  basis = "Closed-trade order",
}: {
  title: string;
  values: number[];
  format: Format;
  color: string;
  basis?: string;
}) {
  if (!values.length)
    return (
      <figure className="trend-plot">
        <figcaption>{title}</figcaption>
        <p className="muted">Awaiting observations.</p>
      </figure>
    );
  let min = values[0];
  let max = values[0];
  for (const value of values) {
    min = Math.min(min, value);
    max = Math.max(max, value);
  }
  const y = (value: number) =>
    min === max ? 60 : 110 - ((value - min) / (max - min)) * 100;
  const line = values
    .map(
      (value, index) =>
        `${values.length === 1 ? 300 : 8 + (index * 584) / (values.length - 1)},${y(value)}`,
    )
    .join(" ");
  return (
    <figure className="trend-plot">
      <figcaption>
        {title} <strong>{format(values.at(-1))}</strong>
      </figcaption>
      <svg
        viewBox="0 0 600 120"
        role="img"
        aria-label={`${title}, low ${format(min)}, high ${format(max)}`}
      >
        <path
          d="M8 110 H592 M8 60 H592 M8 10 H592"
          stroke="#293642"
          fill="none"
        />
        {values.length === 1 ? (
          <circle cx="300" cy={y(values[0])} r="4" fill={color} />
        ) : (
          <polyline
            points={line}
            stroke={color}
            fill="none"
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      <small>
        Low {format(min)} · High {format(max)} · {basis}
      </small>
    </figure>
  );
}

export function measure(data: unknown, field: string) {
  return rows(data).flatMap((point) => {
    const value = number(point[field]);
    return value === null ? [] : [value];
  });
}

function Distribution({ evidence }: { evidence: unknown }) {
  const distribution = rows(evidence).flatMap((item) => {
    const net = number(item.net_cents);
    const count = number(item.count);
    return net === null || count === null ? [] : [{ net_cents: net, count }];
  });
  const bars = returnHistogram(distribution);
  const max = Math.max(1, ...bars.map((item) => item.count));
  return (
    <section className="analytics-section">
      <h3>Net trade return distribution</h3>
      {bars.length ? (
        <ol className="histogram">
          {bars.map((item) => {
            const range =
              item.low === item.high
                ? money(item.low)
                : `${money(item.low)} to ${money(item.high)}`;
            return (
              <li key={item.low}>
                <span>{range}</span>
                <meter
                  min={0}
                  max={max}
                  value={item.count}
                  aria-label={`${range} observations`}
                />
                <strong>{item.count}</strong>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="muted">No completed trades.</p>
      )}
    </section>
  );
}

export function BacktestAnalytics({
  runId,
  summary,
}: {
  runId: string;
  summary: RecordData;
}) {
  const [windowSize, setWindowSize] = useState(20);
  const [group, setGroup] = useState<Group>("by_session");
  const [details, setDetails] = useState<{
    windowSize: number;
    data: RecordData;
  }>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    api<RecordData>(`backtests/${runId}/analytics?window=${windowSize}`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted) setDetails({ windowSize, data });
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError((failure as Error).message);
      });
    return () => controller.abort();
  }, [runId, windowSize]);
  const results = record(summary.summary);
  const stats = record(results.performance);
  const evidence =
    details?.windowSize === windowSize ? details.data : undefined;
  const diagnostics = record(evidence?.execution_diagnostics);
  const groupDiagnostics = record(diagnostics[group]);
  const hasGroupDiagnostics = Object.keys(groupDiagnostics).length > 0;
  const scoredTrades = number(evidence?.scored_trades) ?? 0;
  const scoreRange = (name: string) =>
    `${name}–${name === "90" ? 100 : Number(name) + 9}`;
  const gain = number(stats.profit_factor);
  const equity = measure(stats.equity_curve, "equity_cents");
  const drawdown = measure(stats.equity_curve, "drawdown_cents");
  const initialEquity = number(stats.starting_equity_cents);
  if (equity.length && initialEquity !== null) equity.unshift(initialEquity);
  if (drawdown.length) drawdown.unshift(0);
  return (
    <>
      <div className="analytics-sample">
        <strong>{label(summary.data_kind)} · Unpartitioned replay</strong>
        <p>
          {utcDateTime(results.first_open)} to {utcDateTime(results.last_close)}
        </p>
        <p>
          {String(summary.contract ?? "MGC")} · Strategy v
          {String(summary.strategy_version ?? "—")} · Run {runId.slice(0, 8)}
        </p>
      </div>
      <div className="metrics contained">
        <Metric
          title="Net P&L"
          value={money(stats.net_profit_cents)}
          detail="After modeled costs"
        />
        <Metric
          title="Trades"
          value={String(stats.trades ?? 0)}
          detail={`${String(results.signal_count ?? 0)} qualified signals`}
        />
        <Metric
          title="Expectancy"
          value={money(stats.expectancy_cents)}
          detail="Net per completed trade"
        />
        <Metric
          title="Profit factor"
          value={gain === null ? "—" : gain.toFixed(2)}
          detail="Net wins divided by net losses"
        />
        <Metric
          title="Win rate"
          value={percentage(stats.win_rate)}
          detail={`${String(stats.wins ?? 0)} wins · ${String(stats.losses ?? 0)} losses`}
        />
        <Metric
          title="Drawdown"
          value={money(stats.max_drawdown_cents)}
          detail="Closed-trade equity"
        />
      </div>
      <div className="trend-grid">
        <TrendPlot
          title="Simulated equity"
          values={equity}
          format={money}
          color="#55c7aa"
        />
        <TrendPlot
          title="Drawdown from prior peak"
          values={drawdown}
          format={money}
          color="#e78184"
        />
      </div>
      <section className="analytics-section">
        <div className="analytics-header">
          <h3>Rolling results</h3>
          <label>
            Window
            <select
              value={windowSize}
              onChange={(event) => setWindowSize(Number(event.target.value))}
            >
              {WINDOWS.map((size) => (
                <option key={size} value={size}>
                  {size} trades
                </option>
              ))}
            </select>
          </label>
        </div>
        {error ? (
          <p className="notice" role="alert">
            {error}
          </p>
        ) : !evidence ? (
          <p className="muted">Loading diagnostics…</p>
        ) : !rows(evidence.rolling_outcomes).length ? (
          <p className="muted">
            Requires {windowSize} completed trades. This replay has{" "}
            {String(evidence.completed_trades)}.
          </p>
        ) : (
          <div className="trend-grid">
            <TrendPlot
              title="Rolling win rate"
              values={measure(evidence.rolling_outcomes, "win_rate")}
              format={percentage}
              color="#73b8ee"
            />
            <TrendPlot
              title="Rolling net expectancy"
              values={measure(evidence.rolling_outcomes, "expectancy_cents")}
              format={money}
              color="#d1ae6b"
            />
          </div>
        )}
      </section>
      <section className="analytics-section">
        <div className="analytics-header">
          <h3>Performance breakdown</h3>
          <label>
            Group by
            <select
              value={group}
              onChange={(event) => setGroup(event.target.value as Group)}
            >
              {Object.entries(GROUPS).map(([key, name]) => (
                <option key={key} value={key}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <DataTable
          labelHeading={GROUPS[group]}
          items={Object.entries(record(stats[group])).map(([name, value]) => [
            group === "by_weekday"
              ? (WEEKDAYS[Number(name) - 1] ?? name)
              : label(name),
            { ...record(value), ...record(groupDiagnostics[name]) },
          ])}
          columns={
            hasGroupDiagnostics
              ? [...outcomeColumns, ...executionColumns]
              : outcomeColumns
          }
          empty={
            group === "observation_milestones"
              ? "The replay has fewer than 100 completed trades."
              : "No qualifying observations."
          }
        />
        {hasGroupDiagnostics && (
          <p className="muted analytics-note">
            MFE and MAE are modeled favorable and adverse excursion. Trade
            P&amp;L SD is sample dispersion of completed net outcomes.
            {group === "by_month" && (
              <>
                {" "}
                {String(diagnostics.profitable_months ?? 0)} of{" "}
                {String(diagnostics.months_with_trades ?? 0)} active months were
                profitable. Monthly net P&amp;L SD{" "}
                {money(diagnostics.monthly_net_stddev_cents)}.
              </>
            )}
          </p>
        )}
      </section>
      <Distribution evidence={stats.net_return_distribution_cents} />
      {evidence && (
        <>
          <OutcomeTable
            title={`Experimental score bands · ${scoredTrades} scored ${scoredTrades === 1 ? "trade" : "trades"}`}
            groups={evidence.by_score_decile}
            heading="Score band"
            format={scoreRange}
          />
          <p className="analytics-note muted">
            Score bands summarize outcomes within this replay. The score is an
            experimental ranking.
          </p>
          <CostTable evidence={evidence.cost_sensitivity_fixed_fills} />
          <section className="analytics-section">
            <h3>Qualified setup frequency</h3>
            <DataTable
              labelHeading="Session"
              items={Object.entries(record(evidence.signals_by_session)).map(
                ([name, count]) => [label(name), { count }],
              )}
              columns={[
                { title: "Signals", value: (row) => String(row.count) },
              ]}
              empty="No setups qualified."
            />
            <details className="audit">
              <summary>Daily signal counts and replay diagnostics</summary>
              <pre>{JSON.stringify(evidence, null, 2)}</pre>
            </details>
          </section>
        </>
      )}
      <details className="audit">
        <summary>Versioned assumptions and statistics</summary>
        <pre>{JSON.stringify(summary, null, 2)}</pre>
      </details>
    </>
  );
}
