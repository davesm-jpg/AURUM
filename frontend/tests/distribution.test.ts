import assert from "node:assert/strict";
import test from "node:test";
import { returnHistogram } from "../app/distribution.ts";

test("synthetic histogram retains every trade across signed integer-cent bins", () => {
  const histogram = returnHistogram(
    [
      { net_cents: 9, count: 4 },
      { net_cents: -10, count: 3 },
      { net_cents: 0, count: 2 },
      { net_cents: -1, count: 1 },
    ],
    2,
  );
  assert.deepEqual(histogram, [
    { low: -10, high: -1, count: 4 },
    { low: 0, high: 9, count: 6 },
  ]);
  assert.deepEqual(returnHistogram([{ net_cents: 0, count: 2 }]), [
    { low: 0, high: 0, count: 2 },
  ]);
  assert.deepEqual(returnHistogram([]), []);
});
