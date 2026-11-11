import { useEffect, useState } from "react";
import { api, type RecordData } from "./client";
import { EconomicPollingStatus } from "./economic-polling-status";
import { rows, utcDateTime } from "./format";

type Capture = {
  capture_id: string;
  publisher: string;
  observed_at: string;
};

export function EconomicCapturePicker({
  onUse,
}: {
  onUse: (snapshot: RecordData) => void;
}) {
  const [open, setOpen] = useState(false);
  const [captures, setCaptures] = useState<Capture[]>();
  const [selected, setSelected] = useState<string[]>([]);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [prefixText, setPrefixText] = useState("");
  const [prepared, setPrepared] = useState<RecordData>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError("");
    api<Capture[]>("economic-captures", { signal: controller.signal })
      .then((items) => {
        if (!controller.signal.aborted) setCaptures(items);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError((failure as Error).message);
      });
    return () => controller.abort();
  }, [open]);

  const chosen = (captures ?? []).filter((item) =>
    selected.includes(item.capture_id),
  );
  const titlePrefixes = prefixText
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  const samePublisher = new Set(chosen.map((item) => item.publisher)).size <= 1;

  async function prepare() {
    setBusy(true);
    setError("");
    setPrepared(undefined);
    try {
      const snapshot = await api<RecordData>("economic-calendar/preview", {
        method: "POST",
        body: JSON.stringify({
          capture_ids: [...chosen].reverse().map((item) => item.capture_id),
          start,
          end,
          title_prefixes: titlePrefixes,
        }),
      });
      setPrepared(snapshot);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details
      className="settings-group"
      onToggle={(event) => setOpen(event.currentTarget.open)}
      onKeyDown={(event) => {
        if (event.key === "Enter" && event.target instanceof HTMLInputElement)
          event.preventDefault();
      }}
    >
      <summary>Prepare a snapshot from archived captures</summary>
      <EconomicPollingStatus active={open} />
      {error && <p className="notice" role="alert">{error}</p>}
      {!captures ? (
        !error && <p className="muted">Loading local capture receipts…</p>
      ) : !captures.length ? (
        <p className="muted">No archived captures in this store.</p>
      ) : (
        <fieldset className="settings-fields" disabled={busy}>
          <label>
            Archived captures from one publisher
            <select
              multiple
              value={selected}
              onChange={(event) => {
                setPrepared(undefined);
                setSelected(
                  Array.from(event.currentTarget.selectedOptions, (item) => item.value),
                );
              }}
            >
              {captures.map((item) => (
                <option key={item.capture_id} value={item.capture_id}>
                  {item.publisher.toUpperCase()} · {utcDateTime(item.observed_at)}{" "}·{" "}
                  {item.capture_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          {!samePublisher && (
            <p className="muted">Choose captures from one publication.</p>
          )}
          <div className="fields">
            <label>
              Snapshot starts UTC
              <input
                value={start}
                placeholder="YYYY-MM-DDTHH:MMZ"
                onChange={(event) => {
                  setPrepared(undefined);
                  setStart(event.target.value);
                }}
              />
            </label>
            <label>
              Snapshot ends UTC
              <input
                value={end}
                placeholder="YYYY-MM-DDTHH:MMZ"
                onChange={(event) => {
                  setPrepared(undefined);
                  setEnd(event.target.value);
                }}
              />
            </label>
          </div>
          <label>
            Release title prefixes, one per line
            <textarea
              rows={2}
              value={prefixText}
              onChange={(event) => {
                setPrepared(undefined);
                setPrefixText(event.target.value);
              }}
            />
          </label>
          <button
            className="subtle"
            type="button"
            disabled={
              !chosen.length ||
              chosen.length > 64 ||
              !samePublisher ||
              !start ||
              !end ||
              !titlePrefixes.length
            }
            onClick={() => void prepare()}
          >
            {busy ? "Preparing…" : "Preview archived snapshot"}
          </button>
          {prepared && (
            <>
              <p className="muted" role="status">
                {String(prepared.source)} · {rows(prepared.events).length} release
                records. Coverage is partial. Other windows remain unknown.
              </p>
              <button
                className="subtle"
                type="button"
                onClick={() => onUse(prepared)}
              >
                Use prepared snapshot
              </button>
            </>
          )}
        </fieldset>
      )}
    </details>
  );
}
