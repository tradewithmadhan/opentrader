indicator("MA Cloud", { shorttitle: "MA Cloud", overlay: true, precision: 2 });

const length = input.int(10, "EMA Length", { minval: 1, maxval: 500 });

// Band: EMA of high / EMA of low over the input length.
const emaHigh = ta.ema(high, length);
const emaLow = ta.ema(low, length);

// State driver: EMA(close,12) − EMA(close,26).
const ema12 = ta.ema(close, 12);
const ema26 = ta.ema(close, 26);
const macd = ema12.sub(ema26);
const prevMacd = macd.offset(1);

// Per-bar state machine (plain numbers inside the bar loop).
let lastState = 0;
const state = eachBar((c) => {
  const m = c.get(macd);
  const pm = c.get(prevMacd);
  let s = lastState; // carry forward when MACD is not yet valid
  if (Number.isFinite(m) && Number.isFinite(pm)) {
    if (m > 0) {
      s = m >= pm ? 0 : 1;      // posUp / posDown
    } else if (m < 0) {
      s = m <= pm ? 2 : 3;      // negUp / negDown
    } else if (pm > 0) {
      s = 1;                    // macd == 0, was positive → posDown
    } else if (pm < 0) {
      s = 3;                    // macd == 0, was negative → negDown
    }
  }
  lastState = s;
  return s;
});

// State-gated conditions: current OR previous bar in the state (matches the TV
// original). On a state-transition bar, BOTH the old and new fills are active,
// which bridges the gap between plot-pair fills so the color transitions
// smoothly instead of jumping.
const pst = state.offset(1);
const isPosUp = state.eq(0).or(pst.eq(0));
const isPosDown = state.eq(1).or(pst.eq(1));
const isNegUp = state.eq(2).or(pst.eq(2));
const isNegDown = state.eq(3).or(pst.eq(3));

const cyan = color.from_hex("#00FFFF");
const red = color.from_hex("#FF0000");
const cyanLine = color.from_hex("#00AAAA");
const redLine = color.from_hex("#AA0000");

// 8 state-gated broken lines (linebr = breaks on NaN, giving clean state gaps).
const posUpHigh = plot(isPosUp.iff(emaHigh, NaN), "Positive Up High", { color: cyanLine, linewidth: 1, style: plot.style_linebr });
const posUpLow  = plot(isPosUp.iff(emaLow,  NaN), "Positive Up Low",  { color: cyanLine, linewidth: 1, style: plot.style_linebr });
const posDownHigh = plot(isPosDown.iff(emaHigh, NaN), "Positive Down High", { color: cyanLine, linewidth: 1, style: plot.style_linebr });
const posDownLow  = plot(isPosDown.iff(emaLow,  NaN), "Positive Down Low",  { color: cyanLine, linewidth: 1, style: plot.style_linebr });
const negUpHigh = plot(isNegUp.iff(emaHigh, NaN), "Negative Up High", { color: redLine, linewidth: 1, style: plot.style_linebr });
const negUpLow  = plot(isNegUp.iff(emaLow,  NaN), "Negative Up Low",  { color: redLine, linewidth: 1, style: plot.style_linebr });
const negDownHigh = plot(isNegDown.iff(emaHigh, NaN), "Negative Down High", { color: redLine, linewidth: 1, style: plot.style_linebr });
const negDownLow  = plot(isNegDown.iff(emaLow,  NaN), "Negative Down Low",  { color: redLine, linewidth: 1, style: plot.style_linebr });

// 4 fills between the state-gated plot pairs.
fill(posUpHigh, posUpLow,  color.new(cyan, 80), "Positive Cloud Uptrend");
fill(posDownHigh, posDownLow, color.new(cyan, 90), "Positive Cloud Downtrend");
fill(negUpHigh, negUpLow,  color.new(red, 90), "Negative Cloud Uptrend");
fill(negDownHigh, negDownLow, color.new(red, 80), "Negative Cloud Downtrend");