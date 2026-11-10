/*
 * Settings > Data: the data source of the browser shell (sample data or an
 * OpenAlgo server) and its connection details. Desktop shells make the same
 * choice in their own settings; this tab is webOnly (see AppSettingsDialog).
 */
import { createSignal, onMount, Show } from "solid-js";
import { Section } from "./parts";
import { setSource, source } from "../../../data/sources";
import { clearBrokerCreds, getBrokerCreds, saveBrokerCreds } from "../../../data/broker-credentials";

export function DataTab() {
  const [provider, setProvider] = createSignal(source().name === "openalgo" ? "openalgo" : "sample");
  const [base, setBase] = createSignal("");
  const [ws, setWs] = createSignal("");
  const [key, setKey] = createSignal("");
  const [secret, setSecret] = createSignal("");
  onMount(() => {
    const c = getBrokerCreds("openalgo");
    if (c) {
      setBase(c.base);
      setWs(c.ws);
      setKey(c.key);
      setSecret(c.secret ?? "");
    }
  });
  const field = (label: string, value: () => string, set: (v: string) => void, type = "text") => (
    <label class="app-settings-field">
      <span class="app-settings-field-label">{label}</span>
      <input
        type={type}
        class="app-settings-input"
        value={value()}
        placeholder={label}
        autocomplete="off"
        spellcheck={false}
        onInput={(e) => set(e.currentTarget.value)}
      />
    </label>
  );
  const save = () => {
    if (provider() === "openalgo") {
      saveBrokerCreds("openalgo", { base: base(), ws: ws(), key: key(), secret: secret() });
    }
    setSource(provider());
  };
  const clear = () => {
    clearBrokerCreds("openalgo");
    setBase("");
    setWs("");
    setKey("");
    setSecret("");
  };
  return (
    <div class="app-settings-data">
      <Section label="DATA SOURCE">
        <select
          class="app-settings-input"
          value={provider()}
          onChange={(e) => setProvider(e.currentTarget.value)}
          aria-label="Data source"
        >
          <option value="sample">Sample data (built in)</option>
          <option value="openalgo">OpenAlgo broker</option>
        </select>
      </Section>
      <Show when={provider() === "openalgo"}>
        <Section label="OPENALGO BROKER">
          {field("REST base URL", base, setBase)}
          {field("WebSocket URL", ws, setWs)}
          {field("API key", key, setKey, "password")}
          {field("API secret (optional)", secret, setSecret, "password")}
          <div class="app-settings-action-row">
            <span class="app-settings-action-label">Connection</span>
            <span>
              <button type="button" class="app-settings-btn" onClick={save}>
                Save and reload
              </button>{" "}
              <button type="button" class="app-settings-btn" onClick={clear}>
                Clear
              </button>
            </span>
          </div>
          <div class="app-settings-note">
            Stored only in this browser (never shipped, never committed). Saving reloads the page.
          </div>
        </Section>
      </Show>
    </div>
  );
}
