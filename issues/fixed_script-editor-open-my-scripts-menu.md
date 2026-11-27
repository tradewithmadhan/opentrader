<!-- Filed as https://github.com/deepentropy/opentrader/issues/419 (27/11/2026), feature request, using the shape of .github/ISSUE_TEMPLATE/feature_request.yml (label: enhancement) — the account cannot add labels, so it is unlabeled. File-only marker: the published body starts after this comment and carries no Status line. -->

> **FIXED upstream** in `cff88899 / 419` (issue #419), adopted in this fork. Kept for reference.

### What do you want to do?

I want to open any of my saved scripts from the script editor itself. The dropdown on the script name has *Make a copy…*, *Rename…*, *Share to Community…*, *Create new indicator* and *Built-in…*, but no item that opens the list of **my** scripts. *Built-in…* opens a picker for the built-in sources; I want the same thing for my own saved scripts.

I keep many scripts (drafts and finished ones). The only in-editor list is a short "Recently used" section, so a script I have not opened lately cannot be found from this menu.

### Proposed behaviour

1. Add **My scripts…** (or *Open script…*) to the script-name dropdown, next to *Built-in…*.
2. It opens a searchable list of all scripts saved in the app, with their names, not just the recently used ones. A draft is listed like any other script.
3. Selecting a script opens it in the editor and makes it the current script, the same way a selection from *Built-in…* or *Recently used* works today. The current script is highlighted in the list.
4. Keep the existing actions where they are; this is an additional way to open a script, not a replacement for "Recently used", import/export or the chart's indicator list.

### Do you know another charting app that does this?

I don't know another charting app with this exact menu. The model is the app's own *Built-in…* picker, which already opens a list of sources to choose from; I want the same for my saved scripts.

### Anything else

The app already knows the full set of saved scripts — the editor uses it when starting up and when stepping through scripts — so this is about exposing that list in the menu. The screenshot of the current menu shows only the five items listed above, with no "My scripts" entry.

### Workarounds you use today

Opening a script from the chart's study list when one is on a chart, or from the "Recently used" section if I opened it recently. Otherwise I create a copy or use the coding agent's `read_script`/`write_script` tools.
