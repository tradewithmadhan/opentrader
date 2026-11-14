/*
 * Ticker reference + snapshot commands — back the WatchlistDetail panel and
 * the symbol search. The bodies are shared with the web server
 * (`opentrader_core::commands`); the returned shapes are vendor-neutral.
 */
use crate::data::provider::Provider;
use crate::data::session::SymbolSession;
use crate::data::types::{Snapshot, SymbolSearchResult, TickerInfo};
use crate::plugins::data as plugin;
use opentrader_core::commands as core;
use tauri::{AppHandle, State};

#[tauri::command]
#[specta::specta]
pub async fn get_ticker_info(app: AppHandle, provider: State<'_, Provider>, symbol: String) -> Result<TickerInfo, String> {
    // A data plugin's symbol (plugins/data.rs), here and in the commands below.
    if let Some(r) = plugin::route(&app, &symbol) {
        return plugin::ticker_info(&app, &r).await;
    }
    Ok(core::ticker_info(&provider, &symbol).await?)
}

/// Trading sessions of a symbol (time zone, regular / extended hours,
/// holidays) — read before its first bars, so every session-dependent view
/// follows the symbol's own market.
#[tauri::command]
#[specta::specta]
pub async fn get_symbol_session(app: AppHandle, provider: State<'_, Provider>, symbol: String) -> Result<SymbolSession, String> {
    if let Some(r) = plugin::route(&app, &symbol) {
        return plugin::session(&app, &r).await;
    }
    Ok(core::symbol_session(&provider, &symbol).await?)
}

#[tauri::command]
#[specta::specta]
pub async fn get_ticker_snapshot(app: AppHandle, provider: State<'_, Provider>, symbol: String) -> Result<Snapshot, String> {
    if let Some(r) = plugin::route(&app, &symbol) {
        return plugin::snapshot(&app, &r).await;
    }
    Ok(core::ticker_snapshot(&provider, &symbol).await?)
}

/// Symbol-search typeahead over the provider's reference universe. `type_filter`
/// is an optional security type (e.g. "etf") from the dialog's Type chip.
#[tauri::command]
#[specta::specta]
pub async fn search_tickers(
    app: AppHandle,
    provider: State<'_, Provider>,
    query: String,
    type_filter: Option<String>,
) -> Result<Vec<SymbolSearchResult>, String> {
    // The data plugins search too: they filter on `type_filter` themselves (their search rows
    // name each row's type, so a plugin that cannot serve the chosen type returns nothing).
    let (own, plugins) =
        tokio::join!(core::search_tickers(&provider, &query, type_filter.as_deref()), plugin::search(&app, &query, type_filter.as_deref()));
    match own {
        Ok(mut rows) => {
            rows.extend(plugins);
            Ok(rows)
        }
        Err(_) if !plugins.is_empty() => Ok(plugins),
        Err(e) => Err(e.into()),
    }
}

/// Economy tab of the symbol search: the series matching `query` (the most
/// popular for an empty query) within a source agency and / or a category.
#[tauri::command]
#[specta::specta]
pub async fn search_economic(
    query: String,
    source: Option<String>,
    category: Option<String>,
) -> Result<Vec<SymbolSearchResult>, String> {
    Ok(core::search_economic(&query, source.as_deref(), category.as_deref()))
}

/// The source agencies of the economic series catalog, most series first.
#[tauri::command]
#[specta::specta]
pub async fn economic_sources() -> Result<Vec<String>, String> {
    Ok(core::economic_sources())
}
