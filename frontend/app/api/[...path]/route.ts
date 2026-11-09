type Params = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, { params }: Params) {
  const { path } = await params;
  const base = process.env.AURUM_API_BASE || "http://127.0.0.1:8765";
  const endpoint = new URL(
    "/api/" + path.map(encodeURIComponent).join("/"),
    base,
  );
  endpoint.search = new URL(request.url).search;
  const headers = new Headers();
  const token = process.env.AURUM_API_TOKEN;
  if (token) headers.set("Authorization", "Bearer " + token);
  if (request.method === "POST")
    headers.set("Content-Type", "application/json");
  try {
    const response = await fetch(endpoint, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
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
