# Backlog

Features considered — some partially designed — but not yet built. The point of this
file: future sessions can pick an item up without re-deriving the thinking. When an
item ships, move its entry to the **Done** section at the bottom (one line + date)
rather than deleting it, so "have we already talked about this?" stays answerable.

Format per item: status (`idea` → `designed` → `deferred`/`ready`), date last touched,
and enough of the design sketch to resume cold.

---

## Mono flat days: filter-aware flats + bidirectional nearest (minicam8)

**Status:** designed · 2026-07-24

Motivating case: `Mele2_data/NINA/Redcat51_minicam8/2026-07-11/` — a full-wheel
flat day with no light session that night for the rig. `FLAT/` = 33×7 filters
(L/R/G/B/H3nm/O3nm/S3nm, FlatWizard per-filter exposures 0.07–6.34s, gain 78);
the sibling `DARK/` folder is really the **dark-flats** (231 frames whose
exposure+filter match the flats one-for-one — NINA's Flat Wizard labels them
`DARK_FlatWizard`). Currently surfaces in intake's *unattached* bucket — correct
flag, nothing lost, out of crawl-phase scope. Design, decided with Steve:

- **No per-filter subfolders, ever.** WBPP groups by FITS keywords
  (FILTER/EXPTIME/GAIN/BINNING), not folders — one FLAT folder per set stands.
  Folder structure serves humans + tracker matching, never the stacker.
- **Classifier rule (intake/fits side):** a `DARK_FlatWizard` frame whose
  (exposure, filter) matches a FLAT in the same night folder is a `dark_flat`,
  not a dark — keeps 0.07–6.34s pseudo-darks out of dark-library matching.
  `frames.filter` already exists (nina_v2 grammar) — no schema change.
- **Placement:** per-session convention stands; no flat library revival. A flat
  day files into the **nearest same-rig session within ±1 day** as host
  ("morning-of" joins "morning-after" as a legal attachment — the 07-11 set's
  frames are stamped morning 07-12, host = the 2026-07-12 night); siblings use
  the existing notes pointer. No session in window → stays in intake's held
  bucket (rename from "unattached" for this case).
- **`resolve_flats()` gains:** (1) *filter-awareness* for mono — requirement =
  distinct filters of kept lights, coverage = needed ⊆ set's filters; display
  `with 07-12 (7/7)` / `nearest 07-11 (missing S3nm)`; OSC = degenerate
  one-filter case, unchanged. (2) *bidirectional nearest*: prefer strictly-before
  (unchanged), but when nothing precedes, fall back to nearest full set AFTER,
  bounded (~14d), labeled `nearest 07-11 (3d later)`; bound + enable live in
  `calibration_thresholds.toml` beside the other recipe knobs. Rationale: the
  strictly-before rule encoded dust-predates-lights, but a filter-wheel rig
  drifts slowly and a full-set anchor days later beats `none`.
- Out of scope, unchanged: coverage panel stays bias/dark only; Bias/Dark
  master conventions untouched.

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

## Mosaics: panels as a target-token suffix family (like _adjacent)

**Status:** designed · 2026-07-23 (no mosaic captured yet; build when one is planned)

Research: NINA's Framing Assistant appends **"Panel X"** to the target name, so
panel identity lands in every frame filename via the existing NINA v2 grammar
(confirmed in NINA docs). ASIAir mosaic mode runs each panel as its own
Plan-mode target; its exact suffix is undocumented — the first real ASIAir
mosaic surfaces it via TARGET_MISMATCH/quarantine (never guessed), and the
suffix family grows then. Astro-mosaic practice: panels are independent
pointings imaged across nights to COMPARABLE DEPTH (even signal at the seams
is the quality driver), 10–20% overlap, each panel integrated separately into
its own master, and the mosaic assembled from panel MASTERS (PhotometricMosaic
/ GradientMergeMosaic), not pooled raw frames. Hierarchy: field → panels →
sessions → frames.

Design — follow the frames, no new hierarchy:

1. **One target folder per mosaic field**, registry as usual. A mosaic is a
   property of how the field is shot, not a new kind of folder.
2. **Session names follow the frames**, panel suffix included:
   `SH2_240_Panel_2 Pleiades111 ASI2600MCAir 2026-11-14` under the field's
   folder. Frame files are never renamed (standing rule).
3. **A recognized panel-suffix family**, exactly like ADJACENT_SUFFIX:
   `_Panel_<n>` (NINA) + the ASIAir form when observed. Consumed at existing
   seams only: `target_base()` strips it (preflight resolution +
   TARGET_MISMATCH quiet); the twin guard treats different panels sharing a
   folder+night as legitimate (same panel id under two names still flags);
   intake needs ZERO changes (distinct tokens already make distinct sessions;
   shared flats attach per the sibling convention).
4. **Per-panel stacking = the existing living integration**, one per panel:
   `[membership]` gains optional `panel = <n>` (rig + span as today). Built /
   available / stale / goal machinery works per panel unchanged.
5. **Assembly = new integration `kind = "mosaic"`** whose members are the
   PANEL INTEGRATIONS (their masters) — the one new mechanism
   (integration-of-integrations). Complete when every panel built; stale when
   any panel stale; data_through = oldest panel's. Hours never re-counted
   (panel sessions already count toward the target). Kinds today derive as
   multi-session/composite at internal/scan.py ~1935; `mosaic` joins as a
   declared kind with member-integration resolution.
6. **Dashboard: panel balance** — the payoff feature: per-panel kept-hours
   table with min–max spread, and Capture-more saying "Panel 3 is 2.1 h
   behind". Panel goal defaults to target goal ÷ panel count; explicit
   overrides only if needed later.

Deliberately NOT designed in: per-panel registry entries, a panel subfolder
level, coordinate awareness, synonym lists, an "is a mosaic" flag — the panel
tokens in the frames are the entire signal.

Phases: (1) first mosaic planned → suffix family + target_base + twin guard;
(2) first stacking → membership panel filter + panel-balance table;
(3) first assembly → kind="mosaic".

## Open design questions (paper §11)

**Status:** ideas, undecided · as of rev-2 paper 2026-07-10

Tracked in the paper; listed here so the backlog is one-stop:

- ~~Per-session flats vs a shared-by-date flat library.~~ **Decided 2026-07-12:
  per-session** — see the flats-with-sessions entry in Done.
- ~~Field-name target folders (e.g. widefield mosaics) vs per-member linking.~~
  **Designed 2026-07-23: one field folder + panel-suffix sessions** — see the
  Mosaics entry above.
- Whether to add temp/gain to session folder names.
- AstroBin vs print: one pipeline stage or two (currently two: Published, Printed).
- Session naming by civil vs astronomical (noon-to-noon) date.

---

## Done

- **Session-row lifecycle (2026-07-23)** — SESSION_MISSING / INTEGRATION_MISSING
  warnings for rows whose folders vanished from every SCANNED library (unmounted
  libraries never touched — field/travel-safe; cross-library moves self-heal via
  the library-agnostic natural key: configure destination, mount once, refresh,
  park). Deletion only ever via the explicit `forget.py "<session>"` verb
  (refuses while the folder exists anywhere mounted, warns about unchecked
  offline libraries, logs to actions.log). Rename/re-target findings hint at the
  same-rig/night/frame-count successor. Enabler shipped too: integration_method
  now stamps into notes.toml [processing] (populate_notes) and the scan reads it
  back when working folders are gone — tracker.db is fully derived again
  (198 Methods blanked by the 2026-07-23 rebuild predate the stamp; hand-restore
  if any matter).

- **TARGET_MISMATCH (2026-07-23)** — every-scan validate() warning: session token ≠
  the target its light frames name, compared via a canonical key (separators/case/
  `_adjacent` stripped, so 2024-era 'M31' == 'M_31' and 'SH 2- 108' == 'SH2_108').
  preflight's frame-vs-folder warning made adjacent-aware the same day. First
  library-wide run: 7 real findings incl. a full duplicate night (SH2 223 data
  copied into an SH2 233 session, 307 identical files, double-counted hours).
- **[capture] notes.toml record (2026-07-23)** — populate_notes stamps tracker-owned
  lights_captured (high-water, never decreased) / lights_kept / lights_rejected /
  kept_exposure_hours per session; frozen once a session has no lights on disk, so
  reject statistics survive any future deletion of the raws. 218 sessions stamped
  on the first run (NGC_1499 total-loss night: 41/0/41). Percent-rejected analytics
  by rig/camera/month remain a future report surface.

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
