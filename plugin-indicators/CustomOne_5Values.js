indicator("CustomOne 5 values", { shorttitle: "CustomOne 5", overlay: false, precision: 2 });

// The plugin serves five sample values in the OHLCV slots of <symbol>_CUSTOMONE.
// One request.security reads all five as a tuple; a separate call per value is not needed.
const source = syminfo.tickerid + "_CUSTOMONE";
const [v1, v2, v3, v4, v5] = request.security(source, "", () => [open, high, low, close, volume]);

plot(v1, "Custom 1", { color: color.blue });
plot(v2, "Custom 2", { color: color.green });
plot(v3, "Custom 3", { color: color.orange });
plot(v4, "Custom 4", { color: color.red });
plot(v5, "Custom 5", { color: color.purple });
