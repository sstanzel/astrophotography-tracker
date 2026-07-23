# Tracker usage — the short version

The paper explains *why*; this is *how*. Two rules carry everything:

1. **Every script that moves, writes, or deletes previews by default** — run it bare
   to see what would happen, add `--apply` to do it. `--only <substring>` limits most
   of them to matching folders.
2. **All state is derived from files.** You never edit the database; you fix folders
   and filenames, then re-run `refresh.py`.

Run everything from this folder (`_organization/tracker/`) with every capture-library
volume named in `config.toml` mounted.

**Layout:** the tracker root holds only the commands you run (plus `config.toml` and
the generated outputs). `internal/` is the machinery — modules the commands import and
the scripts `refresh.py` chains (scan, the two exports, populate_notes, validate);
you never run those by hand in normal use. `docs/` is reference reading: STYLE.md,
BACKLOG.md, CHECKS.md, queries.sql, and the paper PDF.

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

## Importing a capture night (intake)

CCC clones each capture device into the import area; `intake.py` turns those
device dumps into correctly-named staged session folders. The import area is
**never modified** — intake only reads it; the only writes are verified copies
into staging plus ledger rows. Everything device-specific ([[source]] roots,
[[rig]] camera→Scope+Sensor mappings with date-ranged swap overrides,
[[ignore]] never-imports) lives in `_organization/intake.toml` (template:
`templates/intake.example.toml`) — none of it in code.

```bash
python3 intake.py                     # THE PLAN (read-only): sessions to create, per-folder
                                      #   file counts + sizes, rig resolutions, dedupe against
                                      #   every mounted library, projected preflight verdicts,
                                      #   quarantine/unmapped/attention, and a census equation
                                      #   that accounts for every file (remainder must be 0)
python3 intake.py --apply             # execute it: copy → hash-verify → atomic rename →
                                      #   ledger row; stamps notes.toml + the .pxiproject
                                      #   template; prints the REAL preflight verdict per session
python3 intake.py --source air2600 --night 2026-07-08   # narrow to a source / night (--since too)
python3 intake.py --census            # classification-only census of the sources
python3 intake.py --audit             # every ledgered copy still exists, sizes match (fast)
python3 intake.py --audit --deep      # ...and re-hash everything against stored digests (slow)
python3 intake.py --reimport          # re-offer copies that vanished (deleted staging by hand)
python3 intake.py --show-config       # parsed sources, rig table, resolved paths
```

When equipment changes (camera moves to another scope, a rig's `adjacent`
role ends): **close out, don't edit** — add `to = <last night in the old
configuration>` to the existing [[rig]] entry, then add a new open-ended
entry for the new reality. Historical nights keep resolving correctly
forever. The plan names the rule behind every session (`open-ended rule` /
`dated rule … → date`), so review catches a wrong bound before any copy.

Safety model: a file is offered until a verified copy of it exists (ledger row
⇔ hash-verified copy); collisions are never overwritten (`held` + attention);
an interrupted run self-heals (`.part` leftovers cleaned, unledgered files
simply re-offer); sessions already in a library are never re-copied (with a
light-count cross-check); a same-target+night session under a different name
raises a would-duplicate warning instead of copying. Autorun/PHD2 logs copy to
the night's LAST session (`log/`) — one home per night, even when guiding
spanned targets. Long darks/bias are reported as calibration sets, not staged.
The ledger (`_organization/intake_ledger.db`) is durable primary state —
unlike tracker.db, never delete it casually.

## After every capture night

```bash
python3 intake.py                     # review the plan (see above)
python3 intake.py --apply             # stage the new sessions
python3 preflight.py                  # validate the staged sessions (report only)
python3 preflight.py --apply          # file the passing ones into the library
                                      #   --force also files WARNs; FAILs never move
python3 refresh.py --notes            # rescan -> DB -> dashboard + xlsx -> mirror
                                      #   --notes back-fills moon/weather in notes.toml
```

`refresh.py --no-scan` re-renders without rescanning (e.g. after editing a
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
python3 integration.py new --target "M 81 Bodes Galaxy" \
    --rig "RASA8 ASI2600MCAir" --span all --goal 50 --apply
#   --span '2026' | '2024-2026' | 'all'; omit --rig for a composite across rigs
#   --built: retroactive scaffold — the master already exists and contains exactly
#            today's matches; records them in [built] so the mark step isn't
#            needed. Never pass it before stacking.

# ...stack in WBPP / PI Magic Studio...

# After each stack: record what actually went into the master
python3 integration.py mark "<integration folder>" --apply     # --clear to reset

python3 refresh.py                    # dashboard now shows built/available/stale
```

Single-night sessions need no scaffolding — a session with files in its `Results/`
folder is already "integrated"; the method (PixInsight / PI Magic) is auto-detected
from which working folder was used.

## Building calibration masters (Bias + Dark)

`worklist.py masters` lists the library sets with raws but no master. Point
WBPP at a set folder and stack it — WBPP drops its output *inside* the set as
`master/master….xisf` + a `logs/` folder, which is not where masters live
(the convention is master-next-to-raws; the scan would see those subfolders as
phantom sets). After any WBPP run(s):

```bash
python3 catalog.py               # preview what would be filed
python3 catalog.py --apply       # file + rename the masters, sweep WBPP scratch
python3 refresh.py                    # tracker picks up the new masters
```

For every Bias/Dark set holding WBPP output it moves the master up next to
the raws, renamed to the convention (every token comes from the set's place
in the tree; ASIAir-named sets with no date in the folder name are dated by
their newest frame):

    masterBias_ASI585MCPro_gain200_2026-02-10.xisf
    masterDark_ASI585MCPro_300s_gain0_-10C_2024-12-19.xisf

then deletes the emptied `master/` and the `logs/` folders (WBPP scratch —
recreatable by re-running the stack). Safe to re-run any time; it also
renames a WBPP-named master sitting loose in a set folder. Batch-friendly:
stack as many sets as you like, then one `catalog.py --apply` files
them all.

## Reclaiming space (safe by construction)

```bash
python3 promote.py --apply           # copy keepers (master + .psd) into Results/
python3 sweep.py --apply             # then empty PI Process/ + PI Magic/ scratch
#   sweep refuses any folder whose keeper isn't in Results yet;
#   or use --promote to copy-then-clean in one pass
```

## The action log (what did an --apply actually do?)

Every mutating script — `intake.py`, `preflight.py`, `catalog.py`,
`promote.py`, `sweep.py` — appends what its `--apply` run did
(one line per move / rename / copy / delete, with full paths) to

    _organization/dev/actions.log

Previews and no-op applies write nothing. Append-only plain text, a few
hundred bytes per run — safe to delete whenever; grep it to answer "what
touched my files?" after the terminal scrollback is gone:

```bash
grep masterDark "../dev/actions.log"      # from the tracker folder
```

Note the arrows in printed output and the log are `→` (U+2192), never ASCII
`->` — a pasted `->` line acts as a shell redirection and creates a stray
empty file named like the destination (the 2026-07-12 incident).

## Where are a session's flats? Which bias set matches?

Flats are per-session — always in a session folder, never in a library. The
Sessions table (dashboard + xlsx) has a **Flats** column, recomputed every
scan: `here` (flat frames in the session folder — the convention), `with M_44`
(a shared-flat night; the sibling session named in the cell holds the set — the
xlsx "Flats Location" column has the full folder name), `nearest M_44 (5d prior)`
(no flats shot for this session; the closest same-rig set strictly *before* the
capture date — dust/rotation state must predate the lights), or `none` (the rig
has no flats on or before that date).

Bias is the opposite — reusable and library-hosted — so the **Bias** column
suggests the nearest match from `_Calibration Library/Bias`: the **newest** set
for the session's camera + gain, any date (bias is time-stable; when you restack
today you load the best bias you own now). Values: `master 2026-04-18` (the set
holds a built master), `raws 2026-02-10` (raws on hand, master not built), or
`none` (no matching set — also the answer when the session's gain has no set,
e.g. a Gain80 night against a Gain0/100/200/252 library). The column is
informational and ignores the `require_bias` recipe: it answers "if I want a
bias, which one?" — the coverage panel still decides whether bias is *needed*.

On a shared-flat night, point the flat-less sessions at the holder in each
one's `notes.toml`; the `bias` key pins a session to a specific library set:

```toml
[calibration]
flats = "M_44 Redcat51 ASI585MCPro 2026-02-09"   # session folder holding the set
bias  = "Bias/ASI2600MCAir/Gain100/2026-04-18"   # optional pin; else newest match
```

Without a pointer the tracker still resolves the sibling automatically (same
rig, same night, has flats) — the pointer just makes it explicit and survives
renames of the detection logic.

Both resolved matches are also stamped into each session's `notes.toml` as
`[calibration] flats_match` / `bias_match` by `populate_notes.py` (usually via
`refresh.py --notes`). Those two keys are tracker-owned: refreshed on every run
from the last scan's DB, and inserted when a file predates them. The
`flats`/`bias` pointers are yours; the `*_match` lines are the tracker's answer
— don't hand-edit them.

(The one-time `file_flats.py` pass — run 2026-07-12 — moved the legacy
`_Flat older/` flat library into the session folders and stamped the pointers;
the script was retired afterwards and lives in git history. Likewise the
one-time `fix_rotfirst_names.py` pass — run 2026-07-15 — renamed the 5,294
rot-first frames of the Dec 2025 – Mar 2026 ASIAir epoch to the
timestamp-first grammar so Blink sorts chronologically.)

## Spring cleaning (the deep Data Health audit)

```bash
python3 audit.py                      # summary + every finding
python3 audit.py --summary            # check-by-check counts only
python3 audit.py --no-fs              # skip the cross-library disk pass
```

`internal/scan.py`'s built-in validation runs every refresh and checks *structure*
(naming, dates, registry, manifests). `audit.py` is the occasional physical:
*consistency* anomalies inside well-formed sessions — mixed gain/exposure/
binning, double-counted or scratch-folder frames, cooler runaway, total-loss
nights, nested calibration sets, sessions duplicated across libraries.
Read-only (never writes the DB or touches the libraries); run after a big
filing pass, on the initial scan of a new library, or a couple of times a
season. Every check — both surfaces — is cataloged with severities and
remedies in **docs/CHECKS.md**. Exit code 1 when any error-severity finding exists.

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

Commands at the tracker root — the ones you run:

| Script | What it does | Key flags |
|---|---|---|
| `refresh.py` | The one command: scan → dashboard + xlsx → mirror | `--no-scan`, `--no-mirror`, `--notes` |
| `intake.py` | Plan-first importer: device dumps → staged session folders (the step before preflight) | `--apply`, `--show-config`, `--reimport` |
| `preflight.py` | Validate staged sessions; file the passing ones | `--apply`, `--force`, `--staging`, `--library` |
| `worklist.py` | Print the Work Queue | `summary` (default) \| `capture coverage masters cull integrate restack edit all` |
| `integration.py` | Living multi-session integrations: `new` scaffolds, `mark` records what you stacked | `new --target/--rig/--span/--goal/--built/--apply` · `mark <dir> --apply/--clear` |
| `catalog.py` | File WBPP-built Bias/Dark masters next to their raws (rename to convention, sweep `master/`+`logs/`) | `--apply` |
| `promote.py` | Copy keepers into `Results/` | `--apply`, `--only` |
| `sweep.py` | Empty PI scratch folders (keeper-safe) | `--apply`, `--only`, `--promote` |
| `audit.py` | Deep Data Health audit (consistency anomalies; see docs/CHECKS.md) | `--summary`, `--no-fs`, `--db` |
| `bootstrap.py` | Fresh start: stamp out the `_organization/` skeleton | `--dry-run` |

Machinery in `internal/` — chained by refresh or imported; run directly only when debugging:

| Script | What it does | Key flags |
|---|---|---|
| `internal/scan.py` | Scan → parse → SQLite only (no exports/mirror) | `--config`, `--no-validate`, `--quiet` |
| `internal/export_html.py` / `internal/export_xlsx.py` | Regenerate one output from the DB | `--db`, `--out` |
| `internal/populate_notes.py` | Back-fill moon/weather + stamp flats/bias matches into notes.toml (usually via `refresh --notes`) | `--dry-run`, `--no-weather`, `--only`, `--db` |
| `internal/validate.py` | Re-run the validation pass against the existing DB | `--db` |

The five mutating scripts (`intake`, `preflight`, `catalog`, `promote`,
`sweep`) log every `--apply` action to `_organization/dev/actions.log`
(see "The action log" above).
