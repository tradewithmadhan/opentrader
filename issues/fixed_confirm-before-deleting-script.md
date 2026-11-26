<!-- Filed as https://github.com/deepentropy/opentrader/issues/413 (25/11/2026). File-only marker: the issue body was filed without this line, and this markdown (minus this comment) is exactly what was filed. -->

# Feature request — ask for confirmation before deleting a script

> **FIXED upstream** in `403fbeed / 413` (issue #413), adopted in this fork. Kept for reference.

**Area:** Script editor / scripts · **Importance:** I would use it regularly · **Labels:** enhancement

---

### What do you want to do?

I want a script to be deleted only after I confirm it, so a wrong click cannot throw the source away.

In the script editor, the menu that opens from the script name (top of the panel) ends with **Delete script** — a danger item directly under *Export script…* and *Import script…*. One click deletes the script immediately: it is removed from storage, taken off every chart it was added to, and dropped from the recents list. There is no confirmation, no undo and no trash — the source is gone for good, and the charts change at the same moment.

I hit this myself while cleaning up a test script: I clicked *Delete script* instead of the item above it, and the script was gone with no way back. A slip on a menu that also holds Export/Import is easy, and the cost is the whole file.

### Proposed behaviour

1. Clicking **Delete script** in the script menu opens a confirmation dialog instead of deleting at once.
2. The dialog names the script and says what will happen, for example:
   - Title: **Delete script?**
   - Text: *"My Script" will be deleted and removed from 2 charts. This cannot be undone.*
   - Main button: **Delete** (danger styling); second button: **Cancel**.
3. Nothing is deleted until **Delete** is pressed; Escape and Cancel close the dialog and keep the script.
4. After a confirmed delete, behave exactly as today (remove it from the charts, from storage and from recents) and keep the existing console line `"My Script" deleted.`.
5. If the script is not on any chart, the text simply omits the charts part ("will be deleted. This cannot be undone.").

The editor has only this one delete path, so one confirmation covers it.

### Do you know another charting app that does this?

Not a specific app — the app's own **"Clear all symbols?"** confirmation in the watchlist is exactly the pattern I mean: a title, a sentence about what is lost, a danger action and Cancel. Editors that keep a single copy of the source (no trash) normally ask before removing it; here the source cannot be recovered at all.

### Workarounds you use today

- Export the script before deleting it, then import it again if the delete was a mistake.
- Duplicate the script first and keep the copy, which leaves clutter behind.

Neither helps when the delete was accidental and the export was not made.

### Anything else

- Environment: OpenTrader desktop 0.4.33 (built 25/11/2026), Windows 10 Pro, language en.
- Where it happened: the OakScript editor panel, script menu → *Delete script*, with the script also added to one chart.
- The chart side is protected (a drawing can be undone, a study can be re-added), but a deleted script has no undo at all — which is why this one action stands out.
