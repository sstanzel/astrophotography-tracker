# Data Health checks — the catalog

Every anomaly the tracker looks for, in one reviewable list. Two surfaces run
them:

- **Every scan — `validate()`** (in `internal/scan.py`, re-runnable via
  `internal/validate.py`): *structural* checks — naming, dates, registry membership,
  manifests. Findings land in `validation_findings` and render as the
  dashboard's **Data Health** panel (CAL_* findings render in the coverage
  panel footer instead).
- **Occasional — `audit.py`**: *consistency* checks — the anomalies that hide
  inside well-formed sessions (mixed capture settings, double-counted frames,
  cooler failures, cross-library duplicates). Read-only report for the initial
  scan of a new library or a periodic "spring cleaning"; nothing is written
  to the DB.

Severities follow the house rule: **error** = wrong data or double-counting —
act now; **warning** = probably needs a decision; **info** = worth a look, may
be intentional. This file is the source list for the paper's future
data-quality section (see BACKLOG.md).

## Every-scan validation (`validate()` → Data Health panel)

### Tier 1 — session structure

| Code | Severity | What it looks for | Typical remedy |
|---|---|---|---|
| `EMPTY_SESSION` | error | No image frames and no Results output | Delete the shell, or restore the missing data |
| `FUTURE_DATE` | error | Session date after today | Fix the folder date |
| `DATE_MISMATCH` | error | No light was captured on the folder date (or the day after) | Rename the folder to the capture night |
| `LOCATION_UNKNOWN` | error | notes.toml location not in locations.toml | Add the site, or fix the label |
| `EMPTY_LIGHTS` | warning | Calibration frames but zero lights | Confirm the lights weren't lost; else retire |
| `MULTI_NIGHT_SPAN` | warning | Lights span >2 calendar dates | Split, or confirm frames weren't copied in |
| `UNPARSED_FITS` | warning | FITS files matching no filename grammar | Rename per the grammar, or move them out |
| `UNKNOWN_SCOPE` / `UNKNOWN_SENSOR` | warning | Session token not in the registry | Add to the registry, or fix the folder name |
| `UNPARSED_SESSION_NAME` | warning | Folder under a target not in 4-token grammar | Rename (see preflight) |
| `NOTES_MISSING` | info | No per-session notes.toml | Stamp from the template |

### Tier 2 — FITS-header cross-checks (one sampled light per session)

| Code | Severity | What it looks for | Typical remedy |
|---|---|---|---|
| `SENSOR_MISMATCH` | warning | Folder sensor ≠ FITS INSTRUME | Fix whichever is wrong |
| `LOCATION_COORD_MISMATCH` | warning | FITS site coords ≳5 km from the declared location | Fix the notes.toml location |

### Tier 3 — registry, calibration, integrations

| Code | Severity | What it looks for | Typical remedy |
|---|---|---|---|
| `CATALOG_TYPO` | error | Target catalog 'NCG' (classic NGC typo) | Rename the target folder |
| `INTEGRATION_MISSING_MEMBER` | error | Manifest lists sessions that don't exist | Fix the `[built]`/exclude lists |
| `INTEGRATION_EMPTY` | error | No resolvable member sessions | Fix the membership rule |
| `REGISTRY_MISSING` | warning | Target folder with no registry entry | Add the registry directory |
| `CAL_UNKNOWN_CAMERA` | warning | Bias/Dark camera folder not in sensor_values | Rename the folder to the registry name |
| `INTEGRATION_NO_MANIFEST` | warning | integrations/ folder without integration.toml | Scaffold with `integration.py new` |
| `INTEGRATION_KIND_MISMATCH` | warning | Declared kind ≠ what the members imply | Fix the manifest |
| `INTEGRATION_SINGLE_MEMBER` | warning | Multi-session integration with one member | Confirm intent, or add the members |
| `REGISTRY_ORPHAN` | info | Registry entry with no library folder | Fine for planned targets |
| `CAL_EMPTY` | info | Calibration folder with no frames | Delete the shell |

## Occasional audit (`audit.py`)

### Errors — wrong totals or corrupt data

| Code | What it looks for | Typical remedy |
|---|---|---|
| `DUPLICATE_FRAME` | Same light (capture timestamp + counter) counted twice in a session | Delete the extra copy — hours are inflated |
| `SCRATCH_FRAME` | Frames counted from `PI Process/` / `PI Magic/` scratch | `sweep.py`, then re-run `refresh.py` |
| `MIXED_CAMERA` | Two camera tokens among one session's lights | Move the foreign frames to their own session |
| `CROSS_LIBRARY_DUPLICATE` | Same session folder on more than one library (the DB's unique key hides this) | Keep one copy; archive or delete the other |
| `ZERO_BYTE_FRAME` | 0-byte frame file | Delete; re-transfer if the original exists |
| `UNDERSIZED_FRAME` | Frame <90% of the session's modal file size | Truncated transfer — delete or re-copy |

### Warnings — needs a decision

| Code | What it looks for | Typical remedy |
|---|---|---|
| `MIXED_GAIN` | Kept lights at more than one gain | Split the session, reject one set, or accept knowingly (calibration must match each gain) |
| `MIXED_BINNING` | Kept lights at more than one binning | Same treatment as mixed gain |
| `TEMP_RUNAWAY` | Cooled sensor spread >5 °C across kept lights | Check the cooler; consider culling the warm subs |
| `ALL_REJECTED` | Every light rejected — total-loss night | Reshoot or retire the session |
| `CAL_NESTED_SET` | Calibration set folder nested inside another (WBPP `master/`+`logs/` leftovers) | File the master next to the raws, remove the subfolder |
| `FLATS_HOST_EMPTY` | Sibling-flats pointer to a session with no flats | Fix the notes.toml pointer |

### Info — worth a look, may be intentional

| Code | What it looks for | Typical remedy |
|---|---|---|
| `MIXED_EXPOSURE` | Kept lights at more than one exposure | Often deliberate (HDR cores); confirm |
| `HIGH_REJECT_RATE` | ≥50% of ≥10 lights rejected | Reshoot-planning input |
| `ROTATION_DRIFT` | Camera rotation spread >2° (mod 180 — a meridian flip doesn't trip it) | Flats may not match every sub; judge per session |
| `MS_DEEPSKY_LIGHTS` | Millisecond lights in a deep-sky session | Probably misfiled planetary frames |
| `UNKNOWN_FILTER` | Frame filter token not in filter_values | Add to the registry, or fix the name |

## Adjacent surfaces (not Data Health)

- **Light↔calibration coverage** (`v_light_calibration_coverage`): per-combo
  statuses `ok` / `to build` / `to shoot` / `stale (new raw|age)` / `n/a`, plus
  `dark_low`/`bias_low`. Supply-side health lives there by design.
- **preflight.py** gates *staged* sessions (grammar, registry, duplicates)
  before they enter a library — same spirit, different moment.
- **Restack** (Work Queue): integrations whose captured data is newer than the
  built master.

## Considered, not built

- **HFR / RMS outlier detection** — the columns exist (`frames.hfr`,
  `frames.rms_arcsec`) but only NINA v2 filenames carry them; nearly all data
  is ASIAir. Revisit if NINA returns.
- **Full-frame FITS-header scan** — Tier 2 samples one header per session;
  scanning every header would catch per-frame INSTRUME/site anomalies at real
  I/O cost. The filename grammar already carries most per-frame truth.
- **notes.toml stub detection** — flag notes stamped from the template but
  never filled in (no location, no mount). Cheap; add if empty notes become
  a problem.
- **Sequence-counter gaps** — missing counters usually mean deleted-not-culled
  frames; noisy in practice (ASIAir restarts renumber), so skipped.
- **Master lineage checks** — needs the `calibration_master_inputs` table to
  be populated first (see BACKLOG.md).
