import { allowLocalRequest, loopbackUrl } from "../../loopback";

type Params = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, { params }: Params) {
  if (!allowLocalRequest(request))
    return Response.json(
      { detail: "Local dashboard request required" },
      { status: 403 },
    );
  const { path } = await params;
  if (!path.every((part) => /^[a-z0-9_-]+$/.test(part)))
    return Response.json(
      { detail: "Unknown research endpoint" },
      { status: 404 },
    );
  const base = loopbackUrl(
    process.env.AURUM_API_BASE || "http://127.0.0.1:8765",
  );
  if (!base)
    return Response.json(
      { detail: "Local AURUM API base must use loopback HTTP" },
      { status: 503 },
    );
  const endpoint = new URL("/api/" + path.join("/"), base);
  endpoint.search = new URL(request.url).search;
  const headers = new Headers();
  const token = process.env.AURUM_API_TOKEN;
  if (token) headers.set("Authorization", "Bearer " + token);
  const lastEvent = request.headers.get("last-event-id");
  if (lastEvent && /^\d+$/.test(lastEvent))
    headers.set("Last-Event-ID", lastEvent);
  if (request.method === "POST")
    headers.set("Content-Type", "application/json");
  try {
    const response = await fetch(endpoint, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      redirect: "error",
      signal: request.signal,
    });
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Content-Type":
          response.headers.get("Content-Type") || "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json(
      { detail: "Local AURUM API unavailable" },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
