# Backlog

Features considered — some partially designed — but not yet built. The point of this
file: future sessions can pick an item up without re-deriving the thinking. When an
item ships, move its entry to the **Done** section at the bottom (one line + date)
rather than deleting it, so "have we already talked about this?" stays answerable.

Format per item: status (`idea` → `designed` → `deferred`/`ready`), date last touched,
and enough of the design sketch to resume cold.

---

## Masters shelf (`_Masters/` derived WBPP convenience folder)

**Status:** designed, deferred · 2026-07-11

Problem: setting up WBPP means burrowing into deep set folders
(`Dark/{Camera}/{Temp}/{Gain}/{Exp}/{date}/`) and picking one `master*` file out of
100+ raws. Design decided:

- Canonical master stays **in its set folder next to the raws** (unchanged — pairing,
  staleness, and phantom-row avoidance in `ingest.py` all depend on this; see
  `has_master_file()` and the bias/dark walkers).
- Add a generated `_Masters/` shelf at the calibration library root: a flat folder of
  **copies** (not symlinks — Alienware/SMB may not resolve Mac symlinks) of every
  `master*` file, rebuilt by `refresh.py` (or a small `shelve_masters.py`). Leading
  `_` keeps it invisible to ingest (top-level `_`-prefixed folders are skipped).
- The shelf is a derived artifact like the dashboard — never hand-maintained, safe to
  delete, rebuilt next refresh. ~40 lines of code.
- Deferred 2026-07-11: may not be needed; revisit after actually building the first
  masters and feeling the WBPP friction (or not).

## Master file naming convention

**Status:** proposed, not yet adopted · 2026-07-11

Encode full params in the master filename at build time so the file is
self-describing in WBPP and anywhere it gets copied:

    masterBias_ASI2600MCAir_gain100_2026-02-10.xisf
    masterDark_ASI2600MCAir_300s_gain100_-10C_2026-02-10.xisf

`has_master_file()` only requires the `master` prefix, so this is free for the
tracker. WBPP auto-classifies `master*` files. Adopt when building the first masters;
then record in the paper's calibration section.

## Calibration master lineage (`calibration_master_inputs`)

**Status:** schema built, never populated · noted 2026-07-10

`schema.sql:346` defines a master↔raw-set lineage table; nothing writes it. Would
record which raw sets went into a built master (the calibration analog of an
integration's `[built]` list). Note: the `DELETE + re-walk` rebuild of
`calibration_masters` each ingest would need rethinking if this table gains rows
(comment at `ingest.py` ~line 1230). Only worth it if "what built this master?"
becomes a real question.

## Derive `[built]` from the master's XISF metadata (retire `mark_integrated.py`)

**Status:** idea · 2026-07-11

`[built]` is the system's one manual attestation — which sessions are physically
inside the current master. PixInsight embeds its input file list in the master's
XISF metadata, so in principle ingest could parse the master in `Results/` and
derive `[built]` the way it derives everything else: attest nothing. Would retire
`mark_integrated.py` (and the `--built` scaffold flag). Real work: XISF header
parsing, mapping embedded file paths back to session folders (paths differ across
machines/volumes), and PI Magic Studio may not embed the same metadata — verify
both stackers before starting. Considered 2026-07-11 when weighing whether to merge
`mark_integrated.py` into `new_integration.py` (answer: no — merged, a prefilled
`[built]` at scaffold time would silence the Restack signal; added `--built` for
retroactive scaffolds instead).

## README "Documents" index

**Status:** idea, deferred · 2026-07-11

A short section in README.md linking the human-facing docs (paper, USAGE.md,
STYLE.md, this file) with one-line descriptions. Deferred until there are enough
documents to need a front door; also settled then: USAGE.md stays in `tracker/`
(docs version with the code they describe; `reports/` is publication artifacts).

## Paper rev-3: fold in the 2026-07-11 calibration rework

**Status:** ready when next revising · 2026-07-11

The rev-2 paper still describes bias matching as per-camera, the separate
"Calibration status" dashboard panel, and pre-produced bias masters as a goal. Update
(in `build_paper.js`, per the backport rule): gain-aware bias, the `[coverage]
require_bias` recipe switch, the merged single coverage panel, masters-live-with-raws
(no `Bias Masters/` folders — also removed from the `!Camera` template), and the
newest-set-per-(camera, gain/ISO) bias retention policy.

## Open design questions (paper §11)

**Status:** ideas, undecided · as of rev-2 paper 2026-07-10

Tracked in the paper; listed here so the backlog is one-stop:

- ~~Per-session flats vs a shared-by-date flat library.~~ **Decided 2026-07-12:
  per-session** — see the flats-with-sessions entry in Done.
- Field-name target folders (e.g. widefield mosaics) vs per-member linking.
- Whether to add temp/gain to session folder names.
- AstroBin vs print: one pipeline stage or two (currently two: Published, Printed).
- Session naming by civil vs astronomical (noon-to-noon) date.

---

## Done

- **Flats with sessions + Flats column (paper §11 question resolved)** — shipped
  2026-07-12. Decision: flats are per-session everywhere; the shared-by-date flat
  library is retired completely. `file_flats.py` (one-time; retired after running,
  in git history at 1e9570b) moved 83 legacy `_Flat older/` sets (~166 GB) into
  their matching sessions (rig+date, ±1 day for next-morning sets), stamped 53
  shared-night sibling pointers (`[calibration] flats = "<host session>"` in
  notes.toml — new template section), and deleted 2 MD5-verified duplicate sets.
  The 2 orphan sets (2024-11-21 + 2026-03-27 Redcat51_ASI585MCPro, no matching
  session) were then deleted along with `_Flat older/` itself; all flat-library
  scanning (`_Calibration Library/Flat/` + `_Flat older/`) was removed from
  ingest. Tracker side: `resolve_flats()` derives a per-session flats location
  (`here` / `with sibling` / `none`) every ingest; shown as the Sessions-table
  **Flats** column (dashboard + xlsx, which also gets Flats Location).
- **Work Queue "see notes" marker** — shipped 2026-07-11, same day it was designed.
  Sessions with open `[future_processing]` to-dos show a `see notes` marker in the
  To cull / To integrate / To edit lists (dashboard + `worklist.py`), so a session
  that already failed a stack (the motivating case: `M_82 Redcat51 ASI585MCPro
  2025-02-16`, then `M_101 Redcat51 ASI585MCPro 2024-12-07`, both failed in PI Magic
  Studio on the Alienware leaving no file trace in the session folder) warns before
  a blind re-run. No new flag — a join against the existing `processing_todos` table;
  the marker clears itself when the todo line is deleted from notes.toml.
- **Gain-aware bias tracking + bias-optional coverage** — shipped 2026-07-11.
  `detect_set_gain()` parses gain/ISO tokens into `calibration_masters.gain`; coverage
  matches bias per (camera, gain); `[coverage] require_bias` in
  `calibration_thresholds.toml` (set false — matched darks + dark-flats recipe) shows
  bias as `n/a` and drops bias from the build-masters queue.
- **Merge Calibration status panel into Coverage** — shipped 2026-07-11. Status panel
  removed; coverage statuses gained `stale (new raw|age)`, `dark_low`/`bias_low`
  min-frames flags, and the CAL_* findings footer. `v_calibration_needs` retained
  (feeds staleness, the xlsx Calibration sheet, and the KPI).
