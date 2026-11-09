import assert from "node:assert/strict";
import test from "node:test";
import { timeline, type Candle } from "../app/chart-evidence.ts";

const at = (minute: string) => `2025-03-10T13:${minute}:00Z`;
const second = (minute: string) => Date.parse(at(minute)) / 1000;

function candle(open: string, close: string): Candle {
  return {
    opened_at: at(open),
    available_at: at(close),
    trade_date: "2025-03-10",
    open_ticks: 29000,
    high_ticks: 29000,
    low_ticks: 29000,
    close_ticks: 29000,
    volume: 1,
    shortened: false,
  };
}

test("synthetic chart points use the first containing completed bar", () => {
  const axis = timeline([
    candle("00", "05"),
    candle("05", "10"),
    candle("20", "25"),
  ]);
  assert.equal(axis.point(at("05")), second("05"));
  assert.equal(axis.point(at("06")), second("10"));
  assert.equal(axis.point(at("15")), null);
  assert.equal(axis.point(at("26")), null);
});

test("synthetic price ranges clip at the window and never bridge an ended gap", () => {
  const axis = timeline([candle("05", "10"), candle("20", "25")]);
  assert.deepEqual(axis.range(at("01"), at("22")), {
    from: second("10"),
    to: second("25"),
  });
  assert.deepEqual(axis.range(at("09"), at("15")), {
    from: second("10"),
    to: second("10"),
  });
  assert.equal(axis.range(at("11"), at("19")), null);
  assert.equal(axis.range(at("26"), at("28")), null);
  assert.equal(timeline([]).range(at("01"), at("02")), null);
});
