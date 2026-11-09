import { useEffect, useState, type FormEvent } from "react";
import { api, type RecordData } from "./client";
import { label, utcDateTime } from "./format";

type Dataset = {
  dataset_id: string;
  contract: string;
  provider: string;
  data_kind: string;
};
type Monitor = {
  monitor_id: string;
  contract: string;
  source: string;
  source_status: string;
};
type Saved = {
  profile_id: string;
  runtime_profile_version: number;
  kind: "provider" | "delivery";
  settings: RecordData;
  created_at: string;
};
type Defaults = {
  choices: { timeframes: string[]; variants: string[]; sessions: string[] };
};

function EnvironmentName({
  name,
  title,
  sample,
  required = false,
}: {
  name: string;
  title: string;
  sample: string;
  required?: boolean;
}) {
  return (
    <label>
      {title}
      <input
        name={name}
        placeholder={sample}
        autoComplete="off"
        spellCheck={false}
        pattern="AURUM_[A-Z0-9_]{1,80}"
        required={required}
      />
    </label>
  );
}

function clockMinutes(value: FormDataEntryValue | null) {
  if (!value) return null;
  const [hour, minute] = String(value).split(":").map(Number);
  return hour * 60 + minute;
}

export function RuntimeSettings({
  datasets,
  monitors,
}: {
  datasets: Dataset[];
  monitors: Monitor[];
}) {
  const [profiles, setProfiles] = useState<Saved[]>([]);
  const [defaults, setDefaults] = useState<Defaults>();
  const [mode, setMode] = useState("simulated");
  const [channel, setChannel] = useState("telegram");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const sources = datasets.filter(
    (item) => mode === "simulated" || item.data_kind !== "synthetic",
  );
  const liveMonitors = monitors.filter(
    (item) => item.source_status === "real_time",
  );

  useEffect(() => {
    const controller = new AbortController();
    const init = { signal: controller.signal };
    void Promise.all([
      api<Saved[]>("configuration/runtime", init),
      api<Defaults>("configuration/backtest", init),
    ])
      .then(([items, values]) => {
        if (controller.signal.aborted) return;
        setProfiles(items);
        setDefaults(values);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError((failure as Error).message);
      });
    return () => controller.abort();
  }, []);

  async function save(
    event: FormEvent<HTMLFormElement>,
    kind: "providers" | "deliveries",
  ) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const name = String(fields.get("name") ?? "");
    const env = (key: string) => fields.get(key) || null;
    const int = (key: string) => Number(fields.get(key));
    const profile =
      kind === "providers"
        ? {
            name,
            source_status: mode,
            calendar_dataset_id: fields.get("calendar_dataset_id"),
            endpoint_env: env("endpoint_env"),
            token_env: env("token_env"),
            backfill_endpoint_env: env("backfill_endpoint_env"),
            backfill_token_env: env("backfill_token_env"),
            timeframe: fields.get("timeframe"),
            variant: fields.get("variant"),
            stale_seconds: int("stale_seconds"),
          }
        : {
            name,
            channel,
            monitor_id: fields.get("monitor_id"),
            destination_env: env("destination_env"),
            token_env: env("token_env"),
            preferences: {
              timezone: fields.get("timezone"),
              quiet_start_minute: clockMinutes(fields.get("quiet_start")),
              quiet_end_minute: clockMinutes(fields.get("quiet_end")),
              min_score: int("min_score"),
              allowed_sessions: fields.getAll("sessions"),
              severities: fields.getAll("severities"),
              attempts_per_hour: int("rate"),
              max_attempts: int("attempts"),
              expiry_minutes: int("expiry"),
            },
          };
    setPending(true);
    setError("");
    setSaved("");
    try {
      const result = await api<Saved>("configuration/" + kind, {
        method: "POST",
        body: JSON.stringify({ profile }),
      });
      setProfiles((current) => [
        result,
        ...current.filter((item) => item.profile_id !== result.profile_id),
      ]);
      setSaved(
        `Saved ${label(result.kind)} profile ${result.profile_id.slice(0, 8)}.`,
      );
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="settings-workspace">
      {error && (
        <p className="notice" role="alert">
          {error}
        </p>
      )}
      {saved && (
        <p className="analytics-sample" role="status">
          {saved}
        </p>
      )}
      <div className="settings-columns">
        <section className="panel sidebar-panel">
          <p className="eyebrow">Market-data worker</p>
          <h2>Provider profile</h2>
          <form onSubmit={(event) => void save(event, "providers")}>
            <p className="muted">
              Use an authorized normalized feed. Enter environment variable
              names here and set their values in the worker environment.
            </p>
            <label>
              Provider profile name
              <input name="name" required maxLength={80} />
            </label>
            <label>
              Source class
              <select
                value={mode}
                onChange={(event) => setMode(event.target.value)}
              >
                <option value="simulated">Simulated</option>
                <option value="real_time">Real time</option>
                <option value="delayed">Delayed</option>
              </select>
            </label>
            <label>
              Reviewed calendar dataset
              <select name="calendar_dataset_id" required>
                {!sources.length && (
                  <option value="">No matching reviewed dataset</option>
                )}
                {sources.map((item) => (
                  <option key={item.dataset_id} value={item.dataset_id}>
                    {item.contract} · {item.provider} · {item.data_kind}
                  </option>
                ))}
              </select>
            </label>
            <EnvironmentName
              name="endpoint_env"
              title="WebSocket endpoint variable"
              sample="AURUM_MARKET_ENDPOINT"
              required
            />
            {mode !== "simulated" && (
              <div className="form-grid">
                <EnvironmentName
                  name="token_env"
                  title="Feed token variable"
                  sample="AURUM_STREAM_TOKEN"
                />
                <EnvironmentName
                  name="backfill_endpoint_env"
                  title="Backfill endpoint variable"
                  sample="AURUM_BACKFILL_ENDPOINT"
                />
                <EnvironmentName
                  name="backfill_token_env"
                  title="Backfill token variable"
                  sample="AURUM_BACKFILL_TOKEN"
                />
              </div>
            )}
            <div className="form-grid">
              <label>
                Analysis timeframe
                <select name="timeframe" defaultValue="5m">
                  {defaults?.choices.timeframes.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Strategy variant
                <select name="variant" defaultValue="sweep_mss_ifvg">
                  {defaults?.choices.variants.map((item) => (
                    <option key={item} value={item}>
                      {label(item)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Stale threshold seconds
                <input
                  name="stale_seconds"
                  type="number"
                  min={1}
                  max={3600}
                  defaultValue={90}
                  required
                />
              </label>
            </div>
            <button
              className="primary"
              disabled={pending || !sources.length || !defaults}
            >
              Save provider profile
            </button>
          </form>
        </section>
        <section className="panel sidebar-panel">
          <p className="eyebrow">Paper alert delivery</p>
          <h2>Notification profile</h2>
          <form onSubmit={(event) => void save(event, "deliveries")}>
            <p className="muted">
              Profiles store delivery filters. A separate foreground worker uses
              an authorized destination.
            </p>
            <label>
              Delivery profile name
              <input name="name" required maxLength={80} />
            </label>
            <label>
              Real-time monitor
              <select name="monitor_id" required>
                {!liveMonitors.length && (
                  <option value="">No real-time monitor configured</option>
                )}
                {liveMonitors.map((item) => (
                  <option key={item.monitor_id} value={item.monitor_id}>
                    {item.contract} · {item.source}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Channel
              <select
                value={channel}
                onChange={(event) => setChannel(event.target.value)}
              >
                <option value="telegram">Telegram</option>
                <option value="discord">Discord</option>
              </select>
            </label>
            <EnvironmentName
              name="destination_env"
              title="Destination variable"
              sample={
                channel === "telegram"
                  ? "AURUM_TELEGRAM_CHAT_ID"
                  : "AURUM_DISCORD_WEBHOOK"
              }
              required
            />
            {channel === "telegram" && (
              <EnvironmentName
                name="token_env"
                title="Telegram token variable"
                sample="AURUM_TELEGRAM_TOKEN"
                required
              />
            )}
            <div className="form-grid">
              <label>
                Quiet hours timezone
                <input name="timezone" defaultValue="Europe/Prague" required />
              </label>
              <label>
                Minimum experimental score
                <input
                  name="min_score"
                  type="number"
                  min={0}
                  max={100}
                  defaultValue={0}
                  required
                />
              </label>
              <label>
                Quiet starts
                <input name="quiet_start" type="time" />
              </label>
              <label>
                Quiet ends
                <input name="quiet_end" type="time" />
              </label>
              <label>
                Attempts per hour
                <input
                  name="rate"
                  type="number"
                  min={1}
                  defaultValue={20}
                  required
                />
              </label>
              <label>
                Maximum attempts
                <input
                  name="attempts"
                  type="number"
                  min={1}
                  defaultValue={4}
                  required
                />
              </label>
              <label>
                Expiry minutes
                <input
                  name="expiry"
                  type="number"
                  min={1}
                  max={10080}
                  defaultValue={15}
                  required
                />
              </label>
              <label>
                Sessions
                <select name="sessions" multiple>
                  {defaults?.choices.sessions.map((item) => (
                    <option key={item} value={item}>
                      {label(item)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Severities
                <select
                  name="severities"
                  multiple
                  defaultValue={["research", "warning", "critical"]}
                >
                  <option value="research">Research</option>
                  <option value="warning">Warning</option>
                  <option value="critical">Critical</option>
                </select>
              </label>
            </div>
            <button
              className="primary"
              disabled={pending || !liveMonitors.length}
            >
              Save notification profile
            </button>
          </form>
        </section>
      </div>
      <section className="panel lower sidebar-panel">
        <p className="eyebrow">Immutable settings history</p>
        <h2>Saved runtime profiles</h2>
        {!profiles.length && (
          <p className="muted">No runtime profiles saved.</p>
        )}
        {profiles.map((item) => (
          <details key={item.profile_id} className="settings-group">
            <summary>
              {String(item.settings.name)} · {label(item.kind)} v
              {item.runtime_profile_version} · {utcDateTime(item.created_at)}
            </summary>
            <p className="muted">
              Run from the project environment after setting the referenced
              variables.
            </p>
            <pre>{`aurum ${item.kind === "provider" ? "monitor-profile" : "deliver-profile"} ${item.profile_id}${item.kind === "delivery" ? " --watch" : ""}`}</pre>
            <pre>{JSON.stringify(item.settings, null, 2)}</pre>
          </details>
        ))}
      </section>
    </div>
  );
}
