import assert from "node:assert/strict";
import test from "node:test";
import { allowLocalRequest, loopbackUrl } from "../app/loopback.ts";

const post = (headers: Record<string, string>) =>
  new Request("http://127.0.0.1:3000/api/backtests", {
    method: "POST",
    headers,
  });

test("browser proxy accepts only same-origin JSON changes or local CLI calls", () => {
  const local = { host: "127.0.0.1:3000", "content-type": "application/json" };
  assert.equal(allowLocalRequest(post(local)), true);
  assert.equal(
    allowLocalRequest(
      post({
        ...local,
        origin: "http://127.0.0.1:3000",
        "sec-fetch-site": "same-origin",
      }),
    ),
    true,
  );
  assert.equal(
    allowLocalRequest(post({ ...local, origin: "https://example.invalid" })),
    false,
  );
  assert.equal(
    allowLocalRequest(post({ ...local, "sec-fetch-site": "cross-site" })),
    false,
  );
  assert.equal(
    allowLocalRequest(post({ ...local, "content-type": "text/plain" })),
    false,
  );
  assert.equal(
    allowLocalRequest(post({ ...local, host: "rebinding.invalid:3000" })),
    false,
  );
});

test("the bearer target must be loopback HTTP without URL credentials", () => {
  assert.equal(loopbackUrl("http://localhost:8765")?.hostname, "localhost");
  assert.equal(loopbackUrl("http://[::1]:8765")?.hostname, "[::1]");
  assert.equal(loopbackUrl("https://example.invalid:8765"), null);
  assert.equal(loopbackUrl("http://user@127.0.0.1:8765"), null);
});
