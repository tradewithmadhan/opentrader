/*
 * The OpenAlgo source's ticker-info shaping: the broker's instrumenttype (the
 * codes the search dialog's Type filter already keys on) mapped onto the app's
 * symbol/security type — the Symbol Info window's "Type" row reads it.
 */
import { describe, expect, it } from "vitest";
import { oaType, shapeTickerInfo } from "../openalgo";

describe("openalgo ticker info types", () => {
  it("maps the broker's instrument types onto the app's vocabulary", () => {
    expect(oaType("EQ", "NSE")).toBe("stock");
    expect(oaType("FUT", "NFO")).toBe("futures");
    expect(oaType("CE", "NFO")).toBe("option");
    expect(oaType("PE", "NFO")).toBe("option");
    expect(oaType("INDEX", "NSE")).toBe("index");
    // Case and padding ride along.
    expect(oaType(" fut ", "NFO")).toBe("futures");
    // The index venues back up an unknown or missing instrumenttype.
    expect(oaType(undefined, "NSE_INDEX")).toBe("index");
    expect(oaType("", "BSE_INDEX")).toBe("index");
    // Neither says: no type (the dialog hides the row).
    expect(oaType(undefined, "NSE")).toBeNull();
    expect(oaType("ZZ", "MCX")).toBeNull();
  });

  it("shaped symbol info carries the type; equities keep the logo", () => {
    const eq = shapeTickerInfo("SBIN", "NSE", { instrumenttype: "EQ", lotsize: 1 });
    expect(eq.securityType).toBe("stock");
    expect(eq.symbolType).toBe("stock");
    expect(eq.iconUrl).not.toBeNull();

    const fut = shapeTickerInfo("NIFTY", "NFO", { instrumenttype: "FUT", lotsize: 75, expiry: "19-OCT-26" });
    expect(fut.securityType).toBe("futures");
    expect(fut.symbolType).toBe("futures");
    expect(fut.pointValue).toBe(75);
    expect(fut.iconUrl).toBeNull();

    const index = shapeTickerInfo("NIFTY", "NSE_INDEX", { instrumenttype: "", lotsize: 1 });
    expect(index.securityType).toBe("index");
    expect(index.currency).toBe("INR");
  });
});
