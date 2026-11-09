import { useEffect, useState, type FormEvent } from "react";
import { api, type RecordData } from "./client";
import { label, record } from "./format";

type ChoiceName =
  | "timeframes"
  | "variants"
  | "break_modes"
  | "scopes"
  | "target_modes"
  | "sessions"
  | "regimes";
type Defaults = {
  instrument: string;
  timeframe: string;
  profile: RecordData;
  choices: Record<ChoiceName, string[]>;
};
type Draft = { timeframe: string; profile: RecordData };
type Field = {
  path: string;
  title: string;
  type?: "boolean" | "number" | "optional" | "select" | "multi";
  choices?: ChoiceName;
  min?: number;
  max?: number;
};

const BASIC: Field[] = [
  {
    path: "strategy_config.variant",
    title: "Strategy variant",
    type: "select",
    choices: "variants",
  },
  {
    path: "strategy_config.scope",
    title: "Structure scope",
    type: "select",
    choices: "scopes",
  },
  { path: "config.internal.left", title: "Left pivots", min: 1, max: 10000 },
  { path: "config.internal.right", title: "Right pivots", min: 1, max: 10000 },
  {
    path: "strategy_config.risk.gross_allowance_cents",
    title: "Gross risk cents",
    min: 1,
  },
  {
    path: "strategy_config.risk.target_mode",
    title: "Target mode",
    type: "select",
    choices: "target_modes",
  },
  {
    path: "strategy_config.risk.commission_round_trip_cents",
    title: "Round-trip commission cents",
    min: 0,
  },
  {
    path: "strategy_config.risk.slippage_round_trip_ticks",
    title: "Round-trip slippage ticks",
    min: 0,
  },
];
const ADVANCED: [string, Field[]][] = [
  [
    "Structure and liquidity",
    [
      {
        path: "config.external.left",
        title: "External left pivots",
        min: 1,
        max: 10000,
      },
      {
        path: "config.external.right",
        title: "External right pivots",
        min: 1,
        max: 10000,
      },
      {
        path: "config.breaks.mode",
        title: "Break mode",
        type: "select",
        choices: "break_modes",
      },
      {
        path: "config.breaks.atr_period",
        title: "Break ATR period",
        min: 1,
        max: 10000,
      },
      {
        path: "config.breaks.atr_buffer_bps",
        title: "Break ATR buffer bps",
        min: 0,
      },
      {
        path: "config.breaks.minimum_body_atr_bps",
        title: "Minimum body / ATR bps",
        min: 0,
      },
      {
        path: "config.breaks.minimum_body_range_bps",
        title: "Minimum body / range bps",
        min: 0,
        max: 10000,
      },
      {
        path: "evidence_config.mapping.equal_tolerance_ticks",
        title: "Equal level tolerance ticks",
        min: 0,
      },
      {
        path: "evidence_config.liquidity.min_penetration_ticks",
        title: "Sweep penetration ticks",
        min: 1,
      },
      {
        path: "evidence_config.liquidity.reclaim_buffer_ticks",
        title: "Reclaim buffer ticks",
        min: 1,
      },
      {
        path: "evidence_config.liquidity.max_reclaim_bars",
        title: "Reclaim window bars",
        min: 0,
      },
    ],
  ],
  [
    "Imbalances and filters",
    [
      {
        path: "evidence_config.imbalances.min_width_ticks",
        title: "Minimum gap ticks",
        min: 1,
      },
      {
        path: "evidence_config.imbalances.inversion_closes",
        title: "Consecutive inversion closes",
        min: 1,
      },
      {
        path: "evidence_config.imbalances.max_age_bars",
        title: "Maximum gap age bars",
        min: 1,
        max: 10000,
      },
      {
        path: "strategy_config.minimum_score",
        title: "Minimum experimental score",
        min: 0,
        max: 100,
      },
      {
        path: "strategy_config.minimum_higher_alignment",
        title: "Aligned context frames",
        min: 1,
        max: 3,
      },
      {
        path: "strategy_config.minimum_volatility_bps",
        title: "Minimum volatility ratio bps",
        type: "optional",
        min: 0,
      },
      {
        path: "strategy_config.maximum_volatility_bps",
        title: "Maximum volatility ratio bps",
        type: "optional",
        min: 0,
      },
      {
        path: "strategy_config.allowed_sessions",
        title: "Allowed sessions",
        type: "multi",
        choices: "sessions",
      },
      {
        path: "strategy_config.allowed_regimes",
        title: "Allowed regimes",
        type: "multi",
        choices: "regimes",
      },
      {
        path: "strategy_config.require_clear_event_risk",
        title: "Require clear economic event risk",
        type: "boolean",
      },
    ],
  ],
  [
    "Paper execution",
    [
      {
        path: "strategy_config.stop_buffer_ticks",
        title: "Structural stop buffer ticks",
        min: 0,
      },
      {
        path: "strategy_config.risk.net_allowance_cents",
        title: "Net risk cap cents",
        type: "optional",
        min: 1,
      },
      {
        path: "strategy_config.risk.max_contracts",
        title: "Maximum contracts",
        type: "optional",
        min: 1,
      },
      {
        path: "strategy_config.risk.reward_numerator",
        title: "Reward ratio numerator",
        min: 1,
      },
      {
        path: "strategy_config.risk.reward_denominator",
        title: "Reward ratio denominator",
        min: 1,
      },
      {
        path: "execution_config.latency_minutes",
        title: "Latency minutes",
        min: 0,
        max: 1440,
      },
      {
        path: "execution_config.entry_timeout_minutes",
        title: "Entry timeout minutes",
        min: 0,
        max: 1440,
      },
      {
        path: "execution_config.round_trip_spread_ticks",
        title: "Round-trip spread ticks",
        min: 0,
      },
      {
        path: "execution_config.initial_equity_cents",
        title: "Starting equity cents",
        min: 1,
      },
      {
        path: "execution_config.carry_across_closures",
        title: "Carry positions across scheduled closures",
        type: "boolean",
      },
    ],
  ],
];
const PROFILE_FIELDS = [
  "config",
  "evidence_config",
  "context_config",
  "strategy_config",
  "execution_config",
  "economic_calendar",
];

function fieldValue(profile: RecordData, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>((value, part) => record(value)[part], profile);
}

function changeField(profile: RecordData, path: string, value: unknown) {
  const next = structuredClone(profile);
  const parts = path.split(".");
  let parent = next;
  for (const part of parts.slice(0, -1)) {
    const child = record(parent[part]);
    parent[part] = child;
    parent = child;
  }
  parent[parts[parts.length - 1]] = value;
  return next;
}

function FieldInput({
  field,
  value,
  options,
  update,
}: {
  field: Field;
  value: unknown;
  options: string[];
  update: (value: unknown) => void;
}) {
  if (field.type === "boolean")
    return (
      <label className="checkbox-setting">
        <input
          type="checkbox"
          checked={value === true}
          onChange={(event) => update(event.target.checked)}
        />
        {field.title}
      </label>
    );
  if (field.type === "select" || field.type === "multi") {
    const multi = field.type === "multi";
    return (
      <label>
        {field.title}
        <select
          multiple={multi}
          value={
            multi
              ? Array.isArray(value)
                ? value.map(String)
                : []
              : String(value ?? "")
          }
          onChange={(event) =>
            update(
              multi
                ? Array.from(event.target.selectedOptions, (item) => item.value)
                : event.target.value,
            )
          }
        >
          {options.map((item) => (
            <option key={item} value={item}>
              {label(item)}
            </option>
          ))}
        </select>
        {multi && <small>Empty includes all.</small>}
      </label>
    );
  }
  return (
    <label>
      {field.title}
      <input
        type="number"
        step={1}
        min={field.min}
        max={field.max}
        value={typeof value === "number" ? value : ""}
        required={field.type !== "optional"}
        onChange={(event) =>
          update(event.target.value === "" ? null : Number(event.target.value))
        }
      />
    </label>
  );
}

export function BacktestForm({
  datasetId,
  replayable,
  selectedRun,
  onComplete,
}: {
  datasetId: string;
  replayable: boolean;
  selectedRun?: RecordData;
  onComplete: (runId: string) => Promise<void>;
}) {
  const [defaults, setDefaults] = useState<Defaults>();
  const [draft, setDraft] = useState<Draft>();
  const [jsonText, setJsonText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<Defaults>("configuration/backtest", { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        setDefaults(data);
        setDraft({ timeframe: data.timeframe, profile: data.profile });
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError((failure as Error).message);
      });
    return () => controller.abort();
  }, []);

  function loadSaved() {
    if (!selectedRun || typeof selectedRun.timeframe !== "string") return;
    setDraft({
      timeframe: selectedRun.timeframe,
      profile: Object.fromEntries(
        PROFILE_FIELDS.map((field) => [field, selectedRun[field]]),
      ),
    });
    setJsonText(null);
    setError("");
  }

  function applyJson() {
    if (jsonText === null) return;
    try {
      const profile: unknown = JSON.parse(jsonText);
      if (
        profile === null ||
        typeof profile !== "object" ||
        Array.isArray(profile)
      )
        throw new Error("Profile JSON must be an object.");
      setDraft(
        (current) => current && { ...current, profile: profile as RecordData },
      );
      setJsonText(null);
      setError("");
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft || !datasetId || !replayable || jsonText !== null) return;
    setBusy(true);
    setError("");
    try {
      const run = await api<{ run_id: string }>("backtests", {
        method: "POST",
        body: JSON.stringify({ dataset_id: datasetId, ...draft }),
      });
      await onComplete(run.run_id);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function renderField(field: Field) {
    if (!draft || !defaults) return null;
    return (
      <FieldInput
        key={field.path}
        field={field}
        value={fieldValue(draft.profile, field.path)}
        options={field.choices ? defaults.choices[field.choices] : []}
        update={(value) =>
          setDraft(
            (current) =>
              current && {
                ...current,
                profile: changeField(current.profile, field.path, value),
              },
          )
        }
      />
    );
  }

  return (
    <section className="panel sidebar-panel">
      <p className="eyebrow">Versioned experiment settings</p>
      <h2>Paper backtest</h2>
      {error && (
        <p className="notice" role="alert">
          {error}
        </p>
      )}
      {draft && defaults ? (
        <form onSubmit={submit} noValidate>
          <p className="muted">
            Instrument {defaults.instrument}. Every run preserves its full
            profile and costs.
          </p>
          <div className="form-actions">
            <button
              type="button"
              className="subtle"
              disabled={!selectedRun || busy}
              onClick={loadSaved}
            >
              Use selected run
            </button>
            <button
              type="button"
              className="subtle"
              disabled={busy}
              onClick={() => {
                setDraft({
                  timeframe: defaults.timeframe,
                  profile: defaults.profile,
                });
                setJsonText(null);
                setError("");
              }}
            >
              Reset defaults
            </button>
          </div>
          <fieldset
            className="settings-fields"
            disabled={busy || jsonText !== null}
          >
            <label>
              Analysis timeframe
              <select
                value={draft.timeframe}
                onChange={(event) => {
                  const timeframe = event.target.value;
                  setDraft((current) => current && { ...current, timeframe });
                }}
              >
                {defaults.choices.timeframes.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <div className="form-grid">{BASIC.map(renderField)}</div>
            {ADVANCED.map(([title, fields]) => (
              <details className="settings-group" key={title}>
                <summary>{title}</summary>
                <div className="form-grid">{fields.map(renderField)}</div>
              </details>
            ))}
          </fieldset>
          {jsonText === null ? (
            <button
              type="button"
              className="subtle"
              disabled={busy}
              onClick={() =>
                setJsonText(JSON.stringify(draft.profile, null, 2))
              }
            >
              Edit full profile JSON
            </button>
          ) : (
            <>
              <label>
                Versioned profile JSON
                <textarea
                  spellCheck={false}
                  value={jsonText}
                  onChange={(event) => setJsonText(event.target.value)}
                  rows={12}
                />
              </label>
              <div className="form-actions">
                <button type="button" className="subtle" onClick={applyJson}>
                  Apply JSON
                </button>
                <button
                  type="button"
                  className="subtle"
                  onClick={() => {
                    setJsonText(null);
                    setError("");
                  }}
                >
                  Discard JSON edit
                </button>
              </div>
            </>
          )}
          <button
            className="primary"
            disabled={busy || !replayable || jsonText !== null}
          >
            {busy ? "Replaying dataset…" : "Run paper backtest"}
          </button>
        </form>
      ) : (
        !error && <p className="muted">Loading versioned defaults…</p>
      )}
    </section>
  );
}
