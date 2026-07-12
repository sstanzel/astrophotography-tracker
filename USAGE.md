# Tracker usage — the short version

The paper explains *why*; this is *how*. Two rules carry everything:

1. **Every script that moves, writes, or deletes previews by default** — run it bare
   to see what would happen, add `--apply` to do it. `--only <substring>` limits most
   of them to matching folders.
2. **All state is derived from files.** You never edit the database; you fix folders
   and filenames, then re-run `refresh.py`.

Run everything from this folder (`_organization/tracker/`) with every capture-library
volume named in `config.toml` mounted.

---

## Fresh start (bare git clone — new user or new machine)

The repo is self-sufficient: everything personal lives *outside* it, and
`tracker/templates/` holds the blank template for each of those files.

```bash
# 1. Clone so the repo sits at <anywhere>/_organization/tracker/
#    (the scripts find _organization/ by position, one level up)
git clone git@github.com:sstanzel/astrophotography-tracker.git _organization/tracker
cd _organization/tracker

# 2. Stamp out the skeleton — registry directories + one copy of each
#    example toml (idempotent; existing files are never touched):
python3 bootstrap.py                  # --dry-run to preview

# 3. Make it yours:
#    - config.toml            -> your library paths + [mirror] (per-machine, gitignored)
#    - ../locations.toml      -> your imaging sites (coords, Bortle)
#    - ../target_goals.toml   -> lifetime hour goals
#    - ../plans.toml          -> saved ASIAIR/NINA framings
#    - ../calibration_thresholds.toml -> min frames, refresh windows, [coverage] recipe
#    - registry dirs          -> one empty directory per camera/scope/filter/target

# 4. First scan — builds tracker.db, dashboard, xlsx, and mirrors them:
python3 refresh.py
```

The registry (`../filter_values/`, `scope_values/`, `sensor_values/`,
`target folders/`) defines every legal name. Adding a new camera, scope, or target =
creating an empty directory there. Goals live in `../target_goals.toml`; calibration
freshness rules in `../calibration_thresholds.toml`.

`tracker/templates/` is also the canonical home of the per-session
`notes.toml` template (PostHaste stamps it into new session folders) and the
`integration.toml` manifest template — single copies, git-tracked; the
populated files they become live out in the libraries.

## After every capture night

```bash
# ASIAir dumps land in "<working library>/_sessions to organize/",
# one folder per session, named:  Target_id Scope Sensor YYYY-MM-DD
python3 preflight.py                  # validate the staged sessions (report only)
python3 preflight.py --apply          # file the passing ones into the library
                                      #   --force also files WARNs; FAILs never move
python3 refresh.py --notes            # rescan -> DB -> dashboard + xlsx -> mirror
                                      #   --notes back-fills moon/weather in notes.toml
```

`refresh.py --no-ingest` re-renders without rescanning (e.g. after editing a
manifest); `--no-mirror` skips copying the outputs to the `[mirror]` folder.

## What should I work on?

```bash
python3 worklist.py                   # summary of every queue
python3 worklist.py cull              # or: capture | integrate | restack | edit
python3 worklist.py masters           # calibration sets with raws but no master
                                      #   (bias skipped when the recipe doesn't require it)
python3 worklist.py coverage          # light combos needing calibration: shoot, build, or stale
python3 worklist.py all
```

Same panels as the dashboard's Work Queue — this is the CLI face.

A **`see notes`** marker on a cull / integrate / edit row means the session has
open to-dos in its `notes.toml` `[future_processing]` list — read them before
working that row (typically "this stack already failed once, here's why"). The
marker clears itself when you delete the todo line from the notes file.

## Blinking a session (culling)

Pull bad frames into the session's `Rejected/` folder — that alone marks the
session culled on the next refresh (integrating also does). The one case the
files can't show is "reviewed, kept every frame, not yet stacked": for that,
open the session's `notes.toml` and flip the pre-seeded top-level flag:

```toml
culled        = false        # -> change to true
```

Nothing ever auto-edits that flag — the tracker only reads it. New sessions
get the line from `tracker/templates/notes.toml` (stamped in by PostHaste);
every existing session's notes.toml was backfilled 2026-07-11.

## Processing a target

```bash
# Once per target+rig: scaffold a living multi-session integration
python3 new_integration.py --target "M 81 Bodes Galaxy" \
    --rig "RASA8 ASI2600MCAir" --span all --goal 50 --apply
#   --span '2026' | '2024-2026' | 'all'; omit --rig for a composite across rigs
#   --built: retroactive scaffold — the master already exists and contains exactly
#            today's matches; records them in [built] so mark_integrated.py isn't
#            needed. Never pass it before stacking.

# ...stack in WBPP / PI Magic Studio...

# After each stack: record what actually went into the master
python3 mark_integrated.py "<integration folder>" --apply     # --clear to reset

python3 refresh.py                    # dashboard now shows built/available/stale
```

Single-night sessions need no scaffolding — a session with files in its `Results/`
folder is already "integrated"; the method (PixInsight / PI Magic) is auto-detected
from which working folder was used.

## Reclaiming space (safe by construction)

```bash
python3 promote_masters.py --apply    # copy keepers (master + .psd) into Results/
python3 clean_processing.py --apply   # then empty PI Process/ + PI Magic/ scratch
#   clean refuses any folder whose keeper isn't in Results yet;
#   or use --promote to copy-then-clean in one pass
```

## Where are a session's flats?

Flats are per-session — always in a session folder, never in a library. The
Sessions table (dashboard + xlsx) has a **Flats** column, recomputed every
ingest: `here` (flat frames in the session folder — the convention), `with M_44`
(a shared-flat night; the sibling session named in the cell holds the set — the
xlsx "Flats Location" column has the full folder name), or `none` (no flats
exist for that night+rig).

On a shared-flat night, point the flat-less sessions at the holder in each
one's `notes.toml`:

```toml
[calibration]
flats = "M_44 Redcat51 ASI585MCPro 2026-02-09"   # session folder holding the set
```

Without a pointer the tracker still resolves the sibling automatically (same
rig, same night, has flats) — the pointer just makes it explicit and survives
renames of the detection logic.

(The one-time `file_flats.py` pass — run 2026-07-12 — moved the legacy
`_Flat older/` flat library into the session folders and stamped the pointers;
the script was retired afterwards and lives in git history.)

## Spring cleaning (the deep Data Health scrub)

```bash
python3 scrub.py                      # summary + every finding
python3 scrub.py --summary            # check-by-check counts only
python3 scrub.py --no-fs              # skip the cross-library disk pass
```

`ingest.py`'s built-in validation runs every refresh and checks *structure*
(naming, dates, registry, manifests). `scrub.py` is the occasional physical:
*consistency* anomalies inside well-formed sessions — mixed gain/exposure/
binning, double-counted or scratch-folder frames, cooler runaway, total-loss
nights, nested calibration sets, sessions duplicated across libraries.
Read-only (never writes the DB or touches the libraries); run after a big
filing pass, on the initial ingest of a new library, or a couple of times a
season. Every check — both surfaces — is cataloged with severities and
remedies in **CHECKS.md**. Exit code 1 when any error-severity finding exists.

## Recording publishes & prints

No script — append a block to the session's or integration's `notes.toml` /
`integration.toml`, then `refresh.py`:

```toml
[[published]]
kind  = "astrobin"            # astrobin | social | other
url   = "https://astrobin.com/xyz/"
title = "M 81 — 8.5h RASA8"
date  = "2026-05-10"

[[printed]]
title = "Canon Selphy 4x6"
date  = "2026-05-12"
```

---

## Script reference

| Script | What it does | Key flags |
|---|---|---|
| `refresh.py` | The one command: ingest → dashboard + xlsx → mirror | `--no-ingest`, `--no-mirror`, `--notes` |
| `preflight.py` | Validate staged sessions; file the passing ones | `--apply`, `--force`, `--staging`, `--library` |
| `worklist.py` | Print the Work Queue | `summary` (default) \| `capture coverage masters cull integrate restack edit all` |
| `new_integration.py` | Scaffold a living multi-session integration | `--target`, `--rig`, `--span`, `--goal`, `--built`, `--apply` |
| `mark_integrated.py` | Record sessions stacked into a master | `<dir>`, `--apply`, `--clear` |
| `promote_masters.py` | Copy keepers into `Results/` | `--apply`, `--only` |
| `clean_processing.py` | Empty PI scratch folders (keeper-safe) | `--apply`, `--only`, `--promote` |
| `populate_notes.py` | Back-fill moon/weather into notes.toml (usually via `refresh --notes`) | `--dry-run`, `--no-weather`, `--only` |
| `ingest.py` | Scan → parse → SQLite only (no exports/mirror) | `--config`, `--no-validate`, `--quiet` |
| `export_html.py` / `export_xlsx.py` | Regenerate one output from the DB | `--db`, `--out` |
| `validate.py` | Re-run the validation pass against the existing DB | `--db` |
| `scrub.py` | Deep Data Health scrub (consistency anomalies; see CHECKS.md) | `--summary`, `--no-fs`, `--db` |
