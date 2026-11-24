# Feature request — plugin-provided scripts in Indicators, like plugin watchlists

Status: **FILED** as https://github.com/deepentropy/opentrader/issues/402 (24/11/2026) as a feature request, using
the shape of `.github/ISSUE_TEMPLATE/feature_request.yml` (label: enhancement). User explicitly approved filing and
asked to leave the presentation decision to upstream. Do not make further changes to the live issue without
instruction.

**Area:** Indicators · **Importance:** I would use it regularly · **Labels:** enhancement

---

### What do you want to do?

A plugin can already ship ready-made watchlists, which appear under the plugin's name in *Open list…*. I want the same
for **scripts**: a plugin should be able to provide an OakScript study or strategy with its **source**, and have it
appear in the Indicators dialog under the plugin's name, usable like any other script on a chart.

Only source-based scripts are requested here. How they are presented — open source, read-only, grouped under the
plugin, or anything else — is for the app to decide; this request is about the delivery channel, not the UI policy.

### Proposed behaviour

1. A plugin sends a notification on connect, and again whenever its scripts change, for example:

   ```json
   {"jsonrpc":"2.0","method":"data.scripts","params":{"scripts":[
     {"id":"pivot-study","name":"MadhaN Pivot","kind":"indicator","source":"// OakScript …"},
     {"id":"flow-study","name":"Flow","kind":"strategy","source":"// OakScript …"}
   ]}}
   ```

   Ids are namespaced with the plugin id so two plugins cannot clash — the same way plugin watchlists are keyed.
2. The app registers those ids the way it registers local OakScripts, so the chart can compile and run them, and they
   can be used in alerts and the Strategy Tester.
3. They are listed with a clear plugin attribution, and removed when the plugin disconnects or is disabled (matching
   plugin watchlists). A chart already showing one should degrade gracefully.
4. *Make a copy* to an ordinary local, editable script would be useful, but is optional for this request.
5. MCP `list_scripts` / `read_script` report plugin scripts with their group and source.

### Do you know another charting app that does this?

Yes — broker- and vendor-provided studies appear in the same indicator list as your own in most charting platforms,
grouped under the provider. The closest patterns in this app are **plugin watchlists** (provider-supplied content in a
personal list) and the **Community group of the indicator library** (studies shipped with the app, not written by the
user).

### Area of the app

Indicators (plugin protocol + indicator registry/dialog + bindings).

### How important is it for you?

I would use it regularly — several useful studies are easier to maintain in the plugin I already distribute than to
ask each user to paste source into the editor.

### Workarounds you use today

- Paste the script source into the editor on every machine — error-prone and hard to update.
- Share through the Community, which is not tied to the plugin and does not update with it.
- Serve the values as a custom series and keep a small local wrapper indicator — works, but does not distribute the
  actual study.

### Anything else

This deliberately does **not** ask for a "hidden source" or compiled-only script format. If the app later wants to show
some plugin scripts as read-only, that is a presentation decision it can make from the same delivery channel — no
extra protocol field is needed for this request. The OakScript runtime already accepts plain script source, so no new
script language or execution path is being requested; only a way for a plugin to hand the source to the app and for the
app to list and run it.
