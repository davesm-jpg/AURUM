const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

export function loopbackUrl(value: string): URL | null {
  try {
    const url = new URL(value);
    return url.protocol === "http:" &&
      LOOPBACK.has(url.hostname) &&
      !url.username &&
      !url.password
      ? url
      : null;
  } catch {
    return null;
  }
}

export function allowLocalRequest(request: Request) {
  const host = request.headers.get("host");
  const local =
    host && /^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?$/i.test(host)
      ? loopbackUrl("http://" + host)
      : null;
  if (!local) return false;
  if (request.method === "GET") return true;
  const origin = request.headers.get("origin");
  const site = request.headers.get("sec-fetch-site");
  const contentType = request.headers
    .get("content-type")
    ?.split(";")[0]
    .trim()
    .toLowerCase();
  return (
    (origin === null || origin === local.origin) &&
    (site === null || site === "same-origin" || site === "none") &&
    contentType === "application/json"
  );
}
