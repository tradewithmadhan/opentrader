strategy("EMA Cross Strategy", {
  shorttitle: "EMA Cross",
  overlay: true,
  initial_capital: 100000,
  default_qty_type: strategy.fixed,
  default_qty_value: 1,
  commission_type: strategy.commission.cash_per_order,
  commission_value: 20,
});

// A simple long-only EMA crossover strategy: enter on the up cross, exit on the down cross,
// with a stop below the entry price. Sample only — not trading advice.

const fastLen = input.int(9, "Fast EMA", { minval: 2, maxval: 200 });
const slowLen = input.int(21, "Slow EMA", { minval: 2, maxval: 400 });
const stopPct = input.float(2.0, "Stop %", { minval: 0.1, maxval: 20, step: 0.1 });

const fast = ta.ema(close, fastLen);
const slow = ta.ema(close, slowLen);

plot(fast, "Fast EMA", { color: "#2962FF", linewidth: 1 });
plot(slow, "Slow EMA", { color: "#FF9800", linewidth: 1 });

strategy.eachBar((c) => {
  const f = c.get(fast);
  const s = c.get(slow);
  const pf = c.get(fast, 1);
  const ps = c.get(slow, 1);
  if (Number.isFinite(f) && Number.isFinite(s) && Number.isFinite(pf) && Number.isFinite(ps)) {
    if (f > s && pf <= ps) strategy.entry("Long", strategy.long);
    if (f < s && pf >= ps) strategy.close("Long");
  }
  if (strategy.position_size > 0) {
    strategy.exit("Stop", { from_entry: "Long", stop: strategy.position_avg_price * (1 - stopPct / 100) });
  }
});
