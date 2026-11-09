export type ReturnCount = { net_cents: number; count: number };

export function returnHistogram(returns: ReturnCount[], maximumBars = 16) {
  if (!returns.length) return [];
  const sorted = [...returns].sort(
    (left, right) => left.net_cents - right.net_cents,
  );
  if (sorted.length <= maximumBars)
    return sorted.map((item) => ({
      low: item.net_cents,
      high: item.net_cents,
      count: item.count,
    }));
  const low = sorted[0].net_cents;
  const high = sorted[sorted.length - 1].net_cents;
  const width = Math.ceil((high - low + 1) / maximumBars);
  const bins = Array.from(
    { length: Math.ceil((high - low + 1) / width) },
    (_, index) => ({
      low: low + index * width,
      high: Math.min(high, low + (index + 1) * width - 1),
      count: 0,
    }),
  );
  for (const item of sorted)
    bins[Math.floor((item.net_cents - low) / width)].count += item.count;
  return bins;
}
