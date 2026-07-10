# Tracker display style guide

Conventions for everything the tracker shows a human — the HTML dashboard,
`worklist.py`, the generated xlsx, and any future report. The database always
stores full-precision values; these rules are about **display only**. Decided
with Steve 2026-07-10.

## Target ID vs Name

- **Target ID** is the short identifier (`M_81`, `NGC_1499`) — the same token
  used in session folder names. **Name** is the expanded description
  ("Bodes Galaxy").
- A table with **one** target column shows the **Target ID**, labeled
  `Target`.
- A table with **both** labels them **`Target ID`** and **`Name`** — never
  a Name column labeled "Target".

## Full words in headers

- Column headers use the **full word**: `Rejected`, `Library`, `Hours`,
  `Priority`, `Available`, `Exposure (s)`, `Temperature`. Abbreviate only
  when space is genuinely at a premium and the full label doesn't fit —
  not out of habit.
- Units go in parentheses after the word: `Exposure (s)`, `Built (hours)`,
  `Temperature (°C)`.

## Report in pipeline-flow order

Detail sections — dashboard panels, `worklist.py all` lists, xlsx sheets —
follow the seven processing steps: **Planned → Captured → Culled →
Integrated → Edited → Published → Printed.** Calibration capture and
master-building are planning-stage *actions* (a new camera means new plans,
registry updates, and new bias/darks), so in action lists they sit up front
with planning: capture → coverage → masters → cull → integrate → restack →
edit. Two standing exceptions:

- **Summaries come first**: the KPI cards, then Integration by year,
  Top targets by lifetime hours, and the Published/Printed ledgers sit above
  the detail sections.
- **Reference/diagnostic panels go last**: Calibration status,
  Light↔calibration coverage, QC candidates, and Data health close the page.

## Emphasis: no shouting

- **ALL CAPS and red are reserved for act-now urgency** (data-loss risk,
  validation *errors*). Routine statuses and to-dos are lowercase:
  `ok`, `to build`, `to shoot`, `no master`, `stale (new raw)`.
- Pill/fill colors: green for fine, soft amber/yellow for work-to-do,
  red **only** for errors. A queue of chores is not an alarm.

## Exposure seconds

- Drop the decimals when they are zero: `300`, not `300.0`.
- Show decimals only when they carry information (flats, dark-flats, short
  planetary/lunar/solar subs — e.g. `0.5`, `2.5`). The `.0` noise comes from
  ASIAir/NINA filenames; it stays in the DB, not on screen.

## Hours

- **Summaries and totals** (KPIs, per-target lifetime, goals, yearly rollups,
  coverage combos) round to the **nearest whole hour**: `753`, not `752.67`.
  Nobody plans around the minutes in a lifetime total.
- **Per-item breakdowns** that are meaningfully sub-hour (a single session,
  an edit-queue image, an integration's built/available/behind) use
  **hours + minutes**: `2h 30m`, `45m`, `14h`. Never raw decimal hours.
- **xlsx exception:** columns that feed live SUMIF/SUM formulas (the Sessions
  sheet's Integration column) stay numeric so the workbook keeps recalculating;
  totals derived from them display with a whole-hour number format.

## Where the formatters live

- Dashboard: `fmtH` / `fmtHM` / `fmtExp` in `export_html.py`'s JS.
- CLI: `fmt_h` / `fmt_hm` / `fmt_exp` in `worklist.py`.
- Statuses are lowercase **at the source** — the `v_calibration_needs` and
  `v_light_calibration_coverage` views — so every face inherits them.

## Written documents (the paper)

The same spirit extends to prose deliverables generated from this repo
(`build_paper.js`):

- **The narrative flow start-to-finish is part of the design** — sections
  must build in reading order; edits that improve flow outrank local wording.
- **Infrastructure names stay in configuration, not prose.** Network share
  names and mount paths belong in `config.toml` (they must be there for the
  system to run); documents refer to volumes by role — "the working volume",
  "the lifetime archive".
- **Order tables and lists by importance to the reader** (scopes and sensors
  before filters), not by accident of the source.
- The `.docx` is generated output. Hand edits are welcome as review, but they
  must be backported into `build_paper.js` — and the principle behind each
  edit applied across the whole document — or the next regeneration loses them.
