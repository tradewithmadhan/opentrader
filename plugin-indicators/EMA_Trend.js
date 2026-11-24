indicator("EMA Trend", { shorttitle: "EMA Trend", overlay: true, precision: 2 });

// Two EMAs and the crossover markers. A small, readable sample of an
// OakScript study that a plugin can ship through data.scripts.

const fastLen = input.int(20, "Fast EMA", { minval: 2, maxval: 200 });
const slowLen = input.int(50, "Slow EMA", { minval: 2, maxval: 400 });

const fast = ta.ema(close, fastLen);
const slow = ta.ema(close, slowLen);

plot(fast, "Fast EMA", { color: "#2962FF", linewidth: 1 });
plot(slow, "Slow EMA", { color: "#FF9800", linewidth: 1 });

const up = ta.crossover(fast, slow);
const down = ta.crossunder(fast, slow);

plotshape(up, "Cross Up", {
  style: "triangleup",
  location: "belowbar",
  color: "#26A69A",
  size: "small",
});

plotshape(down, "Cross Down", {
  style: "triangledown",
  location: "abovebar",
  color: "#EF5350",
  size: "small",
});
