indicator("Volume Surge", { shorttitle: "Vol Surge", overlay: false });

// Marks bars whose volume is at least `mult` times its simple average.
// Pane study (overlay: false), so it plots in its own pane.

const length = input.int(20, "Average length", { minval: 2, maxval: 200 });
const mult = input.float(2.0, "Surge multiple", { minval: 1.0, maxval: 10.0, step: 0.1 });

const avg = ta.sma(volume, length);
const ratio = volume.div(avg);
const surge = ratio.gte(mult);

plot(ratio, "Volume / average", { color: "#90A4AE" });
plotshape(surge, "Surge", {
  style: "triangleup",
  location: "bottom",
  color: "#FF9800",
  size: "small",
});
