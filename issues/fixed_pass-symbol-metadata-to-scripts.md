<!-- Filed as https://github.com/deepentropy/opentrader/issues/414 (26/11/2026), feature request, using the shape of .github/ISSUE_TEMPLATE/feature_request.yml (label: enhancement) — the account cannot add labels, so it is unlabeled. File-only marker: the published body starts after this comment and carries no Status line. -->

# Feature request — give OakScript scripts the symbol information Pine v6 exposes

> **FIXED upstream** in `c70a9458 / 414` (issue #414), adopted in this fork. Kept for reference.

**Area:** Script editor / scripts · **Importance:** I would use it regularly · **Labels:** enhancement

---

### What do you want to do?

I want an OakScript indicator or strategy to be able to read the symbol information that TradingView's Pine Script v6 exposes through `syminfo.*`, so a script can adapt to the instrument it is running on instead of relying on typed-in inputs.

The concrete case is an option-chain open-interest profile. Today I must type the index root (`NIFTY`), the expiry token (`26D01`), the strike step and the exchange into the script's settings, and change them by hand when I switch instruments. Pine scripts can use `syminfo.root`, `syminfo.ticker`, `syminfo.prefix`, `syminfo.expiration_date` and `syminfo.description` to build the chain for whatever symbol is on the chart. I also want `syminfo.sector`, `industry`, `country`, `isin`, `volumetype`, `basecurrency`, the share/analyst fields and the rest of the v6 set available to scripts that need them.

Today only 13 of Pine v6's 40 `syminfo.*` fields exist in OakScriptJS, and three of those (`basecurrency`, `root`, `description`) are not passed by this app — reading one throws a runtime error and leaves the study in the errored state. The app already has much of the data: the symbol reference lookup returns description, industry, sector, employees, currency and symbol type, and the chart data model already carries dividend, split and earnings events.

### Proposed behaviour

1. Pass every symbol field the app already knows into the script's chart context, starting with `description`, `root`, `basecurrency`, `sector`, `industry` and `employees`, plus the derivable `ticker`, `prefix`, `main_tickerid` and `country` from the full symbol name.
2. For fields the reference data does not carry (contract root/expiry, `current_contract`, `isin`, `volumetype`, `shares_outstanding_*`, `recommendations_*`, `target_price_*`), use whatever the data plugin or reference lookup can supply, and leave a field unset rather than guessing — an unset field should keep throwing, as OakScriptJS does today, not silently give a wrong value.
3. Make the values reactive: when a symbol's reference data arrives, a study that reads these fields recalculates instead of staying errored (the same behaviour fixed for other-symbol requests in #411).
4. As a follow-up, expose the dividend / split / earnings events already in the chart data model to the matching Pine data-request functions, so scripts do not have to hard-code corporate-action dates.

The companion request to `deepentropy/oakscriptJS` covers adding the library-side getters for the fields it does not expose yet; this request is about the app supplying the values it already has.

### Do you know another charting app that does this?

TradingView's Pine Script v6. Its language reference lists 40 `syminfo.*` variables, and the manual's "Chart information → Symbol information" page shows them being read directly in a script (ticker ID, symbol, prefix, type, description, ISIN, currency, tick size, point value). A script there needs no settings for the instrument's root or expiry — it reads them from `syminfo`.

### Anything else

Full list of the Pine v6 set (40): `basecurrency`, `country`, `currency`, `current_contract`, `description`, `employees`, `expiration_date`, `industry`, `isin`, `main_tickerid`, `mincontract`, `minmove`, `mintick`, `pointvalue`, `prefix`, `pricescale`, `recommendations_buy`, `recommendations_buy_strong`, `recommendations_date`, `recommendations_hold`, `recommendations_sell`, `recommendations_sell_strong`, `recommendations_total`, `root`, `sector`, `session`, `shareholders`, `shares_outstanding_float`, `shares_outstanding_total`, `target_price_average`, `target_price_date`, `target_price_estimates`, `target_price_high`, `target_price_low`, `target_price_median`, `ticker`, `tickerid`, `timezone`, `type`, `volumetype`.

The app currently supplies 10 of these. OakScriptJS currently implements 13 of them; the remaining 27 need a library change, which is in the companion request. This request is for all of them to be usable from a script on a chart.

### Workarounds you use today

Hard-coded inputs for the index root, expiry, exchange and strike step, and no way at all to read a symbol's description, sector, industry, ISIN or corporate-action calendar from a script.
