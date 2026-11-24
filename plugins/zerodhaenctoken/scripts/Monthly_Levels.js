indicator("Monthly Levels (Expiry H/L)", { shorttitle: "Monthly Levels", overlay: true, precision: 2 });

const historyEnabled = input.bool(true, "Show History Levels");
const currentLevelEnabled = input.bool(true, "Show Current Expiry Line");

const EXPIRY_DATES = new Set([
  '29-JAN-2015', '26-FEB-2015', '26-MAR-2015', '30-APR-2015', '28-MAY-2015', '25-JUN-2015',
  '30-JUL-2015', '27-AUG-2015', '24-SEP-2015', '29-OCT-2015', '26-NOV-2015', '31-DEC-2015',
  '28-JAN-2016', '25-FEB-2016', '31-MAR-2016', '28-APR-2016', '26-MAY-2016', '30-JUN-2016',
  '28-JUL-2016', '25-AUG-2016', '29-SEP-2016', '27-OCT-2016', '24-NOV-2016', '29-DEC-2016',
  '25-JAN-2017', '23-FEB-2017', '30-MAR-2017', '27-APR-2017', '25-MAY-2017', '29-JUN-2017',
  '27-JUL-2017', '31-AUG-2017', '28-SEP-2017', '26-OCT-2017', '30-NOV-2017', '28-DEC-2017',
  '25-JAN-2018', '22-FEB-2018', '28-MAR-2018', '26-APR-2018', '31-MAY-2018', '28-JUN-2018',
  '26-JUL-2018', '30-AUG-2018', '27-SEP-2018', '25-OCT-2018', '29-NOV-2018', '27-DEC-2018',
  '31-JAN-2019', '28-FEB-2019', '28-MAR-2019', '25-APR-2019', '30-MAY-2019', '27-JUN-2019',
  '25-JUL-2019', '29-AUG-2019', '19-SEP-2019', '26-SEP-2019', '03-OCT-2019', '10-OCT-2019',
  '17-OCT-2019', '24-OCT-2019', '31-OCT-2019', '07-NOV-2019', '14-NOV-2019', '28-NOV-2019',
  '05-DEC-2019', '26-DEC-2019', '30-JAN-2020', '27-FEB-2020', '26-MAR-2020', '30-APR-2020',
  '28-MAY-2020', '25-JUN-2020', '30-JUL-2020', '27-AUG-2020', '24-SEP-2020', '29-OCT-2020',
  '26-NOV-2020', '31-DEC-2020', '28-JAN-2021', '25-FEB-2021', '25-MAR-2021', '29-APR-2021',
  '27-MAY-2021', '24-JUN-2021', '29-JUL-2021', '26-AUG-2021', '30-SEP-2021', '28-OCT-2021',
  '25-NOV-2021', '30-DEC-2021', '27-JAN-2022', '24-FEB-2022', '31-MAR-2022', '28-APR-2022',
  '26-MAY-2022', '30-JUN-2022', '28-JUL-2022', '25-AUG-2022', '29-SEP-2022', '27-OCT-2022',
  '24-NOV-2022', '29-DEC-2022', '25-JAN-2023', '23-FEB-2023', '29-MAR-2023', '27-APR-2023',
  '25-MAY-2023', '28-JUN-2023', '29-JUN-2023', '27-JUL-2023', '31-AUG-2023', '28-SEP-2023',
  '19-OCT-2023', '26-OCT-2023', '30-NOV-2023', '28-DEC-2023', '25-JAN-2024', '29-FEB-2024',
  '28-MAR-2024', '25-APR-2024', '30-MAY-2024', '27-JUN-2024', '25-JUL-2024', '29-AUG-2024',
  '26-SEP-2024', '31-OCT-2024', '28-NOV-2024', '26-DEC-2024', '30-JAN-2025', '27-FEB-2025',
  '27-MAR-2025', '24-APR-2025', '29-MAY-2025', '26-JUN-2025', '31-JUL-2025', '28-AUG-2025',
  '30-SEP-2025', '28-OCT-2025', '25-NOV-2025', '30-DEC-2025', '27-JAN-2026', '24-FEB-2026',
  '30-MAR-2026', '30-APR-2026', '28-MAY-2026', '25-JUN-2026', '30-JUL-2026',
  '27-AUG-2026', '24-SEP-2026', '29-OCT-2026', '26-NOV-2026', '31-DEC-2026',
  '28-JAN-2027', '25-FEB-2027', '31-MAR-2027', '29-APR-2027', '27-MAY-2027',
  '24-JUN-2027', '29-JUL-2027', '26-AUG-2027', '30-SEP-2027', '28-OCT-2027',
  '25-NOV-2027', '30-DEC-2027',
]);

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

// IST trading date string "DD-MMM-YYYY" for a bar time. Daily NSE bars are stamped 00:00 IST of
// the trading day (= 18:30 UTC the previous day), so the +5.5h shift is needed: without it an
// expiry date matches the bar whose UTC date is that day, i.e. the NEXT trading day on the chart.
// Intraday bars (03:45–10:00 UTC) stay on the same date after the shift, so both intervals work.
const IST_OFFSET_SEC = 5.5 * 3600;
const utcDate = (t) => {
  const d = new Date((t + IST_OFFSET_SEC) * 1000);
  return String(d.getUTCDate()).padStart(2, '0') + '-' + MONTHS[d.getUTCMonth()] + '-' + d.getUTCFullYear();
};

// The same colour with zero alpha: a line drawn in it is invisible (the legend still shows the
// colour, see resetTransparency in the app).
const invisible = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, 0)`;
};

// Expiry starts (first bar index + date string) and the sorted expiry dates, filled by the high-side
// makeLevel pass; the drawing block uses them to stop the current line at the next expiry.
const expiryBarIdx = [];
const expiryDates = [];
const allExpiryDates = [...EXPIRY_DATES].sort((a, b) => parseExpiry(a) - parseExpiry(b));

function parseExpiry(s) {
  const [dd, mmm, yyyy] = s.split('-');
  return Date.UTC(Number(yyyy), MONTHS.indexOf(mmm), Number(dd));
}
/** Trading days (Mon–Fri) strictly between two expiry date strings, used to project a future bar index. */
function expiryGap(from, to) {
  const a = parseExpiry(from), b = parseExpiry(to), DAY = 86400000;
  let n = 0;
  for (let t = a + DAY; t <= b; t += DAY) {
    const wd = new Date(t).getUTCDay();
    if (wd !== 0 && wd !== 6) n++;
  }
  return n;
}

// Build one level series (high-side or low-side) with its own date state machine.
// Each call recomputes the same expiry-day logic independently — high and low stay consistent.
// The level is published on every bar, including the first bar of a new expiry: that bar's colour
// is clear when the level differs from the previous bar's (Pine:
// color = level != level[1] ? na : color), so the jump into it is not drawn and the first bar of the
// new period is kept. Earlier this returned NaN on that bar (a real gap), which dropped the bar.
const makeLevel = (useHigh, hex) => {
  let lastDate = null;
  let active = NaN;
  let tracking = false;
  let prev = NaN;
  const colors = [];
  const series = eachBar((c) => {
    const day = utcDate(c.time);
    if (lastDate !== day) {
      lastDate = day;
      if (EXPIRY_DATES.has(day)) {
        active = useHigh ? c.high : c.low;
        tracking = true;
        if (useHigh) {
          expiryBarIdx.push(c.i);
          expiryDates.push(day);
        }
      } else {
        tracking = false;
      }
    }
    if (tracking) {
      if (useHigh) {
        if (c.high > active) active = c.high;
      } else {
        if (c.low < active) active = c.low;
      }
    }
    const changed = Number.isFinite(prev) && active !== prev;
    colors.push(changed ? invisible(hex) : hex);
    prev = active;
    return active;
  });
  return { series, colors };
};

const highLevel = makeLevel(true, "#00FF00");
const lowLevel = makeLevel(false, "#FF0000");
const highSeries = highLevel.series;
const lowSeries = lowLevel.series;

if (currentLevelEnabled) {
  let hl = null, ll = null, hLab = null, lLab = null;
  // One line per side: the current expiry's level, from that expiry's first bar to the next one's.
  // Each line ends with a price label (the TV source's `trackPrice: true`), placed just above/below
  // the line so the text sits beside it rather than on top of it.
  const place = (hVal, lVal, start, end) => {
    if (hl !== null) line.delete(hl);
    if (ll !== null) line.delete(ll);
    if (hLab !== null) label.delete(hLab);
    if (lLab !== null) label.delete(lLab);
    hl = line.new(start, hVal, end, hVal, "bar_index", "none", color.from_hex("#00FF00"), "solid", 1);
    ll = line.new(start, lVal, end, lVal, "bar_index", "none", color.from_hex("#FF0000"), "solid", 1);
    const gap = Math.max((hVal - lVal) * 0.06, 0.01);
    hLab = label.new(end, hVal + gap, hVal.toFixed(2), "bar_index", "price", "#00000000", "label_left", color.from_hex("#00FF00"), "normal", "right");
    lLab = label.new(end, lVal - gap, lVal.toFixed(2), "bar_index", "price", "#00000000", "label_left", color.from_hex("#FF0000"), "normal", "right");
  };
  eachBar((c) => {
    const hVal = c.get(highSeries);
    const lVal = c.get(lowSeries);
    if (!Number.isFinite(hVal) && !Number.isFinite(lVal)) return;
    // First bar with a level, or the first bar of a new expiry: start a fresh line there.
    const startIdx = expiryBarIdx.length && expiryBarIdx[expiryBarIdx.length - 1] <= c.i
      ? expiryBarIdx[expiryBarIdx.length - 1]
      : c.i;
    // The next expiry's first bar in the loaded data, else a projection past the last bar.
    const k = expiryBarIdx.findIndex((i) => i > c.i);
    let endIdx;
    if (k >= 0) {
      endIdx = expiryBarIdx[k];
    } else {
      // Current expiry is the last in the data: project the next expiry's bar from this expiry's
      // first bar, not from the current bar (which is already days into the period).
      const d = expiryDates[expiryDates.length - 1];
      const at = d ? allExpiryDates.indexOf(d) : -1;
      const nextDay = at >= 0 ? allExpiryDates[at + 1] : undefined;
      endIdx = nextDay ? startIdx + expiryGap(d, nextDay) : c.i + 20;
    }
    // Re-place on every bar: the right end tracks the next expiry, the level follows the rolling
    // H/L of the expiry day. Deleting first keeps only one line per side.
    place(hVal, lVal, startIdx, endIdx);
  });
}

if (historyEnabled) {
  plot(highSeries, "Monthly High Level", {
    color: highLevel.colors,
    linewidth: 1,
    style: plot.style_linebr,
  });
  plot(lowSeries, "Monthly Low Level", {
    color: lowLevel.colors,
    linewidth: 1,
    style: plot.style_linebr,
  });
}