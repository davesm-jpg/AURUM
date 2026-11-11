import { useEffect, useState } from "react";
import { api } from "./client";
import { label, utcDateTime } from "./format";

type Poller = {
  poller_id: string;
  definition: { publisher: string };
  state: string;
  next_due_at: string;
  last_success_at: string | null;
  last_capture_id: string | null;
  consecutive_failures: number;
  latest_attempt: { error_code: string | null } | null;
};

export function EconomicPollingStatus({ active }: { active: boolean }) {
  const [pollers, setPollers] = useState<Poller[]>();
  const [error, setError] = useState("");
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let pending = false;
    async function refresh() {
      if (pending) return;
      pending = true;
      try {
        const result = await api<Poller[]>("economic-pollers", { signal: controller.signal });
        if (!controller.signal.aborted) {
          setPollers(result);
          setError("");
        }
      } catch (failure) {
        if (!controller.signal.aborted) {
          setPollers(undefined);
          setError((failure as Error).message);
        }
      } finally {
        pending = false;
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 30_000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [active]);
  if (!active) return null;
  return (
    <section aria-label="Public calendar polling health">
      <h3>Public calendar polling</h3>
      <p className="muted">Captures need review before monitor adoption. A successful
        capture does not establish complete calendar coverage or a clear event window.</p>
      {error ? <p className="notice" role="alert">Polling status unavailable. {error}</p>
        : !pollers ? <p className="muted">Loading stored polling status…</p>
        : !pollers.length ? <p className="muted">No public calendar poller configured.
          Event coverage remains unknown.</p>
        : <div className="table-wrap"><table>
          <thead><tr><th>Publisher</th><th>Capture status</th><th>Last capture UTC</th>
            <th>Next due UTC</th><th>Failures</th></tr></thead>
          <tbody>{pollers.map((item) => <tr key={item.poller_id}>
            <td>{item.definition.publisher.toUpperCase()}</td>
            <td>{label(item.state)}{item.latest_attempt?.error_code
              ? ` · ${label(item.latest_attempt.error_code)}` : ""}</td>
            <td>{item.last_success_at ? utcDateTime(item.last_success_at) : "Unavailable"}</td>
            <td>{utcDateTime(item.next_due_at)}</td>
            <td>{item.consecutive_failures}</td>
          </tr>)}</tbody>
        </table></div>}
    </section>
  );
}
