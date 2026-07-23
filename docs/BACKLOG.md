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
  staleness, and phantom-row avoidance in `internal/scan.py` all depend on this; see
  `has_master_file()` and the bias/dark walkers).
- Add a generated `_Masters/` shelf at the calibration library root: a flat folder of
  **copies** (not symlinks — Alienware/SMB may not resolve Mac symlinks) of every
  `master*` file, rebuilt by `refresh.py` (or a small `shelve_masters.py`). Leading
  `_` keeps it invisible to the scan (top-level `_`-prefixed folders are skipped).
- The shelf is a derived artifact like the dashboard — never hand-maintained, safe to
  delete, rebuilt next refresh. ~40 lines of code.
- Deferred 2026-07-11: may not be needed; revisit after actually building the first
  masters and feeling the WBPP friction (or not).

## Calibration master lineage (`calibration_master_inputs`)

**Status:** schema built, never populated · noted 2026-07-10

`internal/schema.sql:346` defines a master↔raw-set lineage table; nothing writes it. Would
record which raw sets went into a built master (the calibration analog of an
integration's `[built]` list). Note: the `DELETE + re-walk` rebuild of
`calibration_masters` each scan would need rethinking if this table gains rows
(comment at `internal/scan.py` ~line 1230). Only worth it if "what built this master?"
becomes a real question.

## Derive `[built]` from the master's XISF metadata (retire `integration.py mark`)

**Status:** idea · 2026-07-11

`[built]` is the system's one manual attestation — which sessions are physically
inside the current master. PixInsight embeds its input file list in the master's
XISF metadata, so in principle the scan could parse the master in `Results/` and
derive `[built]` the way it derives everything else: attest nothing. Would retire
`integration.py mark` (and the `--built` scaffold flag). Real work: XISF header
parsing, mapping embedded file paths back to session folders (paths differ across
machines/volumes), and PI Magic Studio may not embed the same metadata — verify
both stackers before starting. Considered 2026-07-11 when weighing whether to prefill
`[built]` at scaffold time (answer: no — a prefilled
`[built]` at scaffold time would silence the Restack signal; added `--built` for
retroactive scaffolds instead).

## README "Documents" index

**Status:** idea, deferred · 2026-07-11

A short section in README.md linking the human-facing docs (paper, USAGE.md,
STYLE.md, this file) with one-line descriptions. Deferred until there are enough
documents to need a front door; also settled then: USAGE.md stays in `tracker/`
(docs version with the code they describe; `reports/` is publication artifacts).

## Document the session-definition deviation (paper section 11 candidate)

**Status:** note for the paper - 2026-07-13

The processing literature defines a "session" as an unbroken optical-train state -
all lights collected until something invalidates the flats (camera rotation,
removal, filter change) - which can span multiple nights. The tracker's session is
target+rig+one civil night, baked into the folder grammar. Deliberate deviation:
the night is the schedulable unit, and the literature's concept (a flats-validity
epoch crossing nights) is modeled instead by the flats machinery - `here` /
`with sibling` / `nearest` resolution and the `[calibration] flats` pointer.
Worth one paragraph in the paper when next revised.

## Paper rev-3: fold in the 2026-07-11 calibration rework

**Status:** ready when next revising · 2026-07-11

The rev-2 paper still describes bias matching as per-camera, the separate
"Calibration status" dashboard panel, and pre-produced bias masters as a goal. Update
(in `build_paper.js`, per the backport rule): gain-aware bias, the `[coverage]
require_bias` recipe switch, the merged single coverage panel, masters-live-with-raws
(no `Bias Masters/` folders — also removed from the `!Camera` template), and the
newest-set-per-(camera, gain/ISO) bias retention policy. Also fold in the
data-quality checks catalog (CHECKS.md, added 2026-07-12 alongside audit.py, then named scrub.py) as the
paper's reviewable list of every anomaly the system looks for.

Added 2026-07-13: the paper also predates the layout reorg (root commands /
`internal/` / `docs/`) and the one-word verb renames (scrub->audit,
file_masters->catalog, promote_masters->promote, clean_processing->sweep,
new_integration+mark_integrated->integration new|mark, ingest->internal/scan) -
every script name the paper mentions needs the new vocabulary at rev-3.

## Scan: don't count frames inside PI Process/ + PI Magic/ scratch

**Status:** ready · 2026-07-12

The first audit run (2026-07-12, as scrub.py) found 3 lights counted from `PI Magic/…/Discarded/`
copies (SCRATCH_FRAME) — each also fires DUPLICATE_FRAME because the original
is still in `Light/`, so integration hours are inflated (~0.25 h today). Right
fix is at the source: the scan session walker should skip the `PI Process/`
and `PI Magic/` subtrees entirely (they are recreatable scratch, same rationale
as `sweep.py`). Keep both audit checks afterwards as regression
guards. Interim workaround: `sweep.py --apply` then re-scan.

## Graduate audit error checks into every-scan validate()

**Status:** idea · 2026-07-12

audit.py's error-severity checks (DUPLICATE_FRAME, SCRATCH_FRAME, MIXED_CAMERA,
ZERO_BYTE_FRAME, UNDERSIZED_FRAME) are cheap DB queries and catch
wrong-totals bugs — candidates for running in every scan's validate() so
they surface on the dashboard's Data Health panel, with audit.py remaining the
place for the judgment-call warnings/info (mixed settings, reject rates,
rotation drift) and the cross-library disk pass. Decide after living with the
audit for a while.

2026-07-12 per-session match columns: `resolve_flats()` (here / with sibling /
nearest / none) and `resolve_bias()` (newest camera+gain set, any date), both
surfaced on the Sessions table and stamped into notes.toml as
`flats_match`/`bias_match`.
worktree-bias-match

## Intake walk phase: undo, calibration routing, preflight chaining

**Status:** designed · 2026-07-15

Crawl (M0–M5, `intake` branch) is built and verified: config-driven sources/
rigs/ignores, census, plan with library dedupe, verified copies + ledger,
`--reimport`, `--audit [--deep]`. Deliberately deferred to walk:

- `--undo RUN_ID` — hash-check each dest against the ledger sha before
  deleting (refuse per-file if edited/culled; refuse entirely once filed into
  the library); mark rows `reverted`, drop empty session dirs from `dirs`.
  The ledger schema already records everything undo needs.
- Calibration auto-routing — long darks/bias sets (today: reported only) filed
  into `_Calibration Library/{Dark|Bias}/{Camera}/…` per the existing
  conventions. Also ad-hoc camera dumps like the `R5 calibration/` CR3 folder
  (2026-07-15) — native raws with no filename grammar, out of intake's
  session world entirely.
- `--file` — chain `preflight.py --apply` after a clean apply (one command
  from device dump to filed library).
- NINA-side guide logs: sources point at `…/NINA`, so PHD2 logs elsewhere on
  the Mele boxes aren't seen; decide whether to widen source roots or add a
  log-dir key per source.
- `$$TARGETNAME$$` + no-target lights (Mele quarantine, 160 frames as of
  2026-07-15): a config mechanism to hand-attribute a quarantined folder to a
  target/session (never guessed automatically).

Run phase (later): filename grammars + device layout profiles fully config-
driven (new capture software = config edit, not a fits_parser change);
dashboard "last import" tile from the ledger `runs` table.

## TARGET_MISMATCH: frame-target vs session-name check, library-wide

**Status:** ready · 2026-07-23

The NGC 3718/3729 case (found live 2026-07-23): a session hand-named after
the companion galaxy while every frame names the primary — caught by nothing,
because the frame-target≠folder-target comparison exists only in preflight's
staging gate, never across the existing library. Convention (decided
2026-07-23): **a session is named for the intended target — the token typed
into the capture software, carried in every frame filename — and files under
whatever folder that token resolves to in the registry; other objects sharing
the frame are not enumerated** (dual folders like "NGC 3718 3729" carry just
enough names to identify the field, and resolve by their FIRST catalog number
only). Build: a `TARGET_MISMATCH` warning in every-scan `validate()` (it is a
structural naming check per CHECKS.md's split; the walk already parses every
filename, so comparing the session token to the modal kept-light target is
nearly free), `_adjacent`-aware — strip the suffix before comparing, and fix
preflight's existing check the same way (its warning on `_adjacent` sessions
is a false positive of the naive comparison). Registry aliasing (so e.g.
NGC_3729 could resolve to the pair folder) noted as a possible later feature;
not needed under the convention above.

## [capture] record in notes.toml: reject counts that survive deletion

**Status:** ready · 2026-07-23

Prompted by NGC_1499 Redcat51 ASI585MCPro 2026-01-05 — a total-loss night
(41 lights, all rejected, 0 kept) kept as a record of the attempt. The
tracker is file-derived, so deleting rejected raws to reclaim space would
erase the reject statistics on the next scan. Design (decided 2026-07-23):
`populate_notes.py` stamps a tracker-owned `[capture]` section per session —
`lights_captured` (high-water of kept+rejected, NEVER decreased),
`lights_kept`, `lights_rejected`, `kept_exposure_hours` — refreshed each
`refresh --notes` exactly like flats_match/bias_match, and left untouched
once a session has zero lights on disk. Reject-rate analytics (percent by
rig / camera / month) then need only [capture] + the session name; a
report/dashboard surface for that is a separate later item. Note sweep.py
never touches Rejected/ — the risk is only ever a future hand deletion,
which is what the stamp survives.

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

- **Master file naming convention + `file_masters.py`** — adopted/shipped 2026-07-12.
  The proposed self-describing names are now the convention, enforced by the new
  reusable `file_masters.py` (preview/`--apply`): after any WBPP run on a Bias/Dark
  set it moves `master/master….xisf` up next to the raws renamed to
  `masterBias_{Camera}_{gain###|ISO####}_{date}.xisf` /
  `masterDark_{Camera}_{exp}_{gain###|ISO####}_{temp}_{date}.xisf` (tokens from the
  tree position; date falls back to the newest frame stamp for ASIAir-named sets)
  and sweeps the `master/`+`logs/` WBPP scratch that ingest would otherwise see as
  phantom sets. First run filed the two 2026-07-11 ASI585MCPro -10C dark masters
  and swept 3 leftover bias `logs/` folders. Still to record in the paper's
  calibration section at the next revision (see rev-3 item).
- **Bias match column + notes.toml match stamping** — shipped 2026-07-12, designed
  the same day (parity ask: flats got nearest-match logic, bias deserved the same).
  `resolve_bias()` derives a per-session bias suggestion: `here` → notes pointer
  (`[calibration] bias = "<library set>"`, trailing-path form accepted; a stale
  pointer falls through to auto) → `master` → `raws` → `none`, matching the
  session's camera (sensor) + most-common kept-light gain against
  `_Calibration Library/Bias` and picking the **newest set regardless of date**
  (decided with Steve: bias is time-stable — no dust/rotation constraint like
  flats' strictly-before rule — and a restack today would load the best bias
  owned now). Informational regardless of `require_bias`; the coverage panel
  still owns "is bias needed". Faces: Sessions-table **Bias** column
  (`master 2026-04-18`), xlsx Bias Source/Location, and — new for flats too —
  `populate_notes.py` stamps tracker-owned `flats_match`/`bias_match` keys into
  every session's `[calibration]` (refreshed each run, inserted when missing).
  First run: 206 master / 16 none (no bias data: PoseidonCPro, minicam8, and a
  Gain80 ASI585MCPro night with no Gain80 set).

- **Closest-flat logic in `resolve_flats()`** — shipped 2026-07-12. Sibling
  matching widened from same-date-only to same day **or the day after** (the
  next-morning-flats ±1-day convention the retired `file_flats.py` pass used but
  the runtime resolver never learned) — 10 sessions gained a `with sibling`
  match. Sessions with no match get the new **`nearest`** status: `flats_ref`
  names the most recent same-rig flat set strictly *before* the capture date
  (later sets never match — dust/rotation state must predate the lights);
  dashboard shows `nearest M_44 (5d prior)` with the day gap, xlsx passes
  source/ref through verbatim. Distribution moved from 117 here / 58 with
  sibling / 47 none to 117 / 68 / 32 nearest / 5 none (the 5: spotting scope,
  Borealis, 6se R5, the Tsuchinshan-ATLAS one-off, and one 2024 CanonR5 night
  whose only flats came 9 days later).

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
