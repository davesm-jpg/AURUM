export type RecordData = Record<string, unknown>;

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.method === "POST") headers.set("Content-Type", "application/json");
  const response = await fetch("/api/" + path, {
    ...init,
    headers,
    cache: "no-store",
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = data?.detail;
    if (Array.isArray(detail)) {
      const messages = detail.slice(0, 3).map((issue) => {
        const path = Array.isArray(issue.loc)
          ? issue.loc.slice(1).join(".")
          : "";
        return [path, issue.msg].filter(Boolean).join(" — ");
      });
      throw new Error(messages.join(". "));
    }
    throw new Error(
      typeof detail === "string"
        ? detail
        : "The request could not be completed.",
    );
  }
  return data as T;
}
