-- =============================================================================
-- Astrophotography tracker schema  ·  Steve Stanzel  ·  rev 1 — 2026-05-19
-- =============================================================================
-- Design goals
--   - Source of truth lives in the SQLite database; xlsx and HTML are views.
--   - Disk scan is idempotent: re-running upserts rather than duplicates.
--   - Composite natural key on sessions matches the 4-token folder grammar.
--   - "Other captures" (Moon, comets, timelapse, As_misc) are recorded but
--     never roll into deep-sky integration totals.
--   - Vocabulary tables mirror /_organization/ folders; the disk is still the
--     authority — these are populated by the scan.
--   - All paths are stored relative to a library root so the same row works
--     whether the file is on stream or peak.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -- libraries -----------------------------------------------------------------
-- One row per capture library. Rows are upserted by ingest.py from config.toml
-- (the single source of truth for library paths) - not seeded here, so adding
-- a library is just a config.toml edit.
CREATE TABLE libraries (
    library_id   TEXT PRIMARY KEY,           -- short id from config.toml
    label        TEXT NOT NULL,              -- human name
    root_path    TEXT NOT NULL,              -- absolute mount path
    role         TEXT NOT NULL CHECK (role IN ('working','archive')),
    notes        TEXT
);

-- -- vocabulary tables --------------------------------------------------------
-- These mirror the controlled-vocabulary folders under _organization/.
-- The scan reads the folder names and upserts here.

CREATE TABLE scopes (
    scope         TEXT PRIMARY KEY,          -- e.g. 'RASA8', 'Redcat51', 'Pleiades111'
    is_imaging    INTEGER NOT NULL DEFAULT 0,-- 1 if used for deep-sky imaging
    from_registry INTEGER NOT NULL DEFAULT 0 -- 1 if listed in _organization/scope_values
);

CREATE TABLE sensors (
    sensor        TEXT PRIMARY KEY,          -- e.g. 'ASI2600MCAir', 'PoseidonCPro'
    is_imaging    INTEGER NOT NULL DEFAULT 0,
    short_form    TEXT,                      -- '2600MC', '585MC', 'Poseidon-C PRO'
    from_registry INTEGER NOT NULL DEFAULT 0 -- 1 if listed in _organization/sensor_values
);                                           -- (short_form is what appears in the FITS filename)

CREATE TABLE scope_sensor_combos (
    scope        TEXT NOT NULL REFERENCES scopes(scope),
    sensor       TEXT NOT NULL REFERENCES sensors(sensor),
    PRIMARY KEY (scope, sensor)
);

CREATE TABLE filters (
    filter       TEXT PRIMARY KEY,           -- short label, e.g. 'LQuadE'
    description  TEXT                        -- full descriptive name
);

-- -- targets ------------------------------------------------------------------
-- One row per top-level target folder. The canonical object.
CREATE TABLE targets (
    target_id           TEXT PRIMARY KEY,    -- 'M_81', 'NGC_1499', 'IC_2118', 'Moon_Nighttime'
                                             -- (underscore between catalog and number)
    catalog             TEXT NOT NULL,       -- 'M', 'NGC', 'IC', 'C', 'SH2', 'LDN', 'HR', 'Moon', 'As'
    number              TEXT,                -- '81', '1499', '3718 3729' (space-multi for fields)
    common_name         TEXT,
    folder_name         TEXT NOT NULL,       -- 'M 81 Bodes Galaxy'
    aka_catalog_ids     TEXT,                -- JSON array of alternate catalog IDs
    companions          TEXT,                -- JSON array of companion target_ids in same field
    is_other_capture    INTEGER NOT NULL DEFAULT 0,  -- 1 for Moon/comet/timelapse/As_misc/ASI EAA
    -- DEPRECATED (kept for backward compatibility, no longer written):
    -- pipeline stages 4-7 are now tracked per integration, not per target —
    -- on the sessions table (single-session integrations) and the integrations
    -- table (multi-session / composite). A target's status is derived from its
    -- sessions and integrations. These columns stay 0; do not rely on them.
    stage_integrate     INTEGER DEFAULT 0,
    stage_edit          INTEGER DEFAULT 0,
    stage_publish       INTEGER DEFAULT 0,
    stage_print         INTEGER DEFAULT 0,
    astrobin_url        TEXT,
    printed_at          DATE,
    notes               TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_targets_catalog       ON targets(catalog);
CREATE INDEX idx_targets_other_capture ON targets(is_other_capture);

-- -- sessions -----------------------------------------------------------------
-- One row per session folder. The 4-token natural key is enforced as UNIQUE.
CREATE TABLE sessions (
    session_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id           TEXT NOT NULL REFERENCES targets(target_id),
    scope               TEXT NOT NULL REFERENCES scopes(scope),
    sensor              TEXT NOT NULL REFERENCES sensors(sensor),
    session_date        DATE NOT NULL,
    library_id          TEXT NOT NULL REFERENCES libraries(library_id),
    folder_path         TEXT NOT NULL,       -- relative to library.root_path
    is_other_capture    INTEGER NOT NULL DEFAULT 0,  -- denormalised from targets
    -- pipeline stages. 0=not started, 1=in progress, 2=done.
    -- 1-3 are the session's own capture/prep. 4-7 belong to this session
    -- viewed AS a single-session integration (its own .pxiproject + Results).
    -- A multi-session or composite integration is a row in the integrations
    -- table instead, with its own 4-7.
    stage_capture       INTEGER DEFAULT 1,   -- 1: captured
    stage_blink_reject  INTEGER DEFAULT 0,   -- 2
    stage_calibrate     INTEGER DEFAULT 0,   -- 3
    stage_integrate     INTEGER DEFAULT 0,   -- 4  (single-session integration)
    stage_edit          INTEGER DEFAULT 0,   -- 5
    stage_publish       INTEGER DEFAULT 0,   -- 6
    stage_print         INTEGER DEFAULT 0,   -- 7
    astrobin_url        TEXT,                -- if this single-session image was published
    -- denormalised counts (refreshed on every scan)
    lights_kept         INTEGER DEFAULT 0,
    lights_rejected     INTEGER DEFAULT 0,
    flats_count         INTEGER DEFAULT 0,
    dark_flats_count    INTEGER DEFAULT 0,
    darks_count         INTEGER DEFAULT 0,
    bias_count          INTEGER DEFAULT 0,
    integration_s       REAL DEFAULT 0.0,    -- summed exp_s for kept lights, only when unit=s
    -- session-level metadata (parsed from notes.rtf if filled in)
    mount               TEXT,
    location_label      TEXT,
    bortle              INTEGER,
    moon_age_days       REAL,
    moon_phase_pct      REAL,
    guide_camera        TEXT,
    guide_scope         TEXT,
    -- PI Magic Studio initial-processing tracking. Manual: set in the session's
    -- notes.toml [processing] section, re-read (never overwritten) every ingest.
    -- machine records WHICH PC ran it; a non-null machine (or the flag = true)
    -- means done in PI Magic Studio rather than PixInsight initially.
    pi_magic_studio     INTEGER DEFAULT 0,   -- 1 if run in PI Magic Studio
    pi_magic_machine    TEXT,                -- which machine (e.g. 'MacMini', 'Alienware')
    pi_magic_date       TEXT,                -- YYYY-MM-DD it was run (optional)
    -- validation inputs (recorded by the ingest walk; feed the validate() pass)
    notes_toml_present  INTEGER DEFAULT 0,   -- 1 if {session} notes.toml exists
    unparsed_file_count INTEGER DEFAULT 0,   -- raw-capture FITS files whose name did not parse
    other_image_count   INTEGER DEFAULT 0,   -- non-FITS raws (CR3/NEF/...) — DSLR sessions
    results_file_count  INTEGER DEFAULT 0,   -- files in the {session} Results folder (kept output)
    fits_site_lat       REAL,                -- SITELAT from a sample light frame header
    fits_site_lon       REAL,                -- SITELONG from a sample light frame header
    fits_instrument     TEXT,                -- INSTRUME from a sample light frame header
    -- bookkeeping
    notes_path          TEXT,                -- path to {session_name} notes.toml
    legacy_xlsx_id      TEXT,                -- e.g. 'As_017' from the old tracker
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (target_id, scope, sensor, session_date)
);

CREATE INDEX idx_sessions_date          ON sessions(session_date);
CREATE INDEX idx_sessions_target        ON sessions(target_id);
CREATE INDEX idx_sessions_scope_sensor  ON sessions(scope, sensor);
CREATE INDEX idx_sessions_library       ON sessions(library_id);

-- -- integrations -------------------------------------------------------------
-- A multi-session or cross-rig composite integration: a folder under
--   {Target}/integrations/  with its own .pxiproject + Results + integration.toml.
-- Single-session integrations are NOT rows here — they are the session folders
-- themselves (see sessions.stage_integrate..stage_print). This table holds only
-- the integrations that combine two or more sessions.
--
-- The integration.toml manifest in the folder is the source of truth: kind,
-- member sessions, version, span, and the edited/published/printed flags.
CREATE TABLE integrations (
    integration_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id        TEXT NOT NULL REFERENCES targets(target_id),
    library_id       TEXT REFERENCES libraries(library_id),
    kind             TEXT NOT NULL CHECK (kind IN ('multi-session','composite')),
    folder_name      TEXT NOT NULL,            -- the integrations/ subfolder name
    folder_path      TEXT NOT NULL,            -- relative to the library root
    scope            TEXT,                     -- single rig; NULL for a composite
    sensor           TEXT,                     -- single rig; NULL for a composite
    span             TEXT,                     -- '2026' | '2024-2026' | 'all' | ...
    version          INTEGER NOT NULL DEFAULT 1,
    session_count    INTEGER NOT NULL DEFAULT 0,   -- denormalised AVAILABLE member count
    -- membership rule (integration.toml [membership]). 'auto' = members resolved
    -- from rig+span each ingest; 'pinned' = an explicit frozen list.
    membership_mode  TEXT NOT NULL DEFAULT 'auto' CHECK (membership_mode IN ('auto','pinned')),
    goal_hours       REAL,                     -- optional integration-hours goal (the "quest")
    built_machine    TEXT,                     -- which PC produced the current master ([built])
    -- pipeline stages 4-7 for this integration. 0=not started,1=in progress,2=done.
    -- stage_integrate is 1+ by definition (the folder exists); 4 is implicit.
    stage_integrate  INTEGER NOT NULL DEFAULT 2,
    stage_edit       INTEGER NOT NULL DEFAULT 0,
    stage_publish    INTEGER NOT NULL DEFAULT 0,
    stage_print      INTEGER NOT NULL DEFAULT 0,
    results_file_count INTEGER NOT NULL DEFAULT 0,
    astrobin_url     TEXT,
    notes            TEXT,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (folder_path)
);

CREATE INDEX idx_integrations_target ON integrations(target_id);
CREATE INDEX idx_integrations_kind   ON integrations(kind);

-- Which sessions belong to an integration. Every AVAILABLE member (resolved
-- from the rule, or the pinned list) gets a row; in_build = 1 marks the ones
-- actually stacked into the current master (integration.toml [built].sessions).
-- available_hours sums all members; built_hours sums in_build=1; the gap is the
-- "stale" signal (captured but not yet folded into the master).
CREATE TABLE integration_members (
    integration_id   INTEGER NOT NULL REFERENCES integrations(integration_id) ON DELETE CASCADE,
    session_id       INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    in_build         INTEGER NOT NULL DEFAULT 0,   -- 1 if in the current stacked master
    PRIMARY KEY (integration_id, session_id)
);

-- -- frames ------------------------------------------------------------------
-- One row per FITS file inside a session. The granularity at which exposure
-- totals and QC metrics live.
CREATE TABLE frames (
    frame_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    frame_type          TEXT NOT NULL CHECK (frame_type IN ('light','flat','dark','dark_flat','bias')),
    is_rejected         INTEGER NOT NULL DEFAULT 0,  -- 1 if under Rejected/ subdir
    -- exposure
    exp_value           REAL NOT NULL,       -- raw number
    exp_unit            TEXT NOT NULL CHECK (exp_unit IN ('s','ms')),
    exp_s               REAL NOT NULL,       -- always in seconds for SUMs
    -- capture state
    binning             TEXT NOT NULL,       -- '1x1' (NINA) or '1' (ASIAir)
    camera_short        TEXT NOT NULL,       -- '2600MC', '585MC', 'Poseidon-C PRO'
    gain                INTEGER,             -- can be negative (Moon -25)
    temp_c              REAL,
    rotation_deg        REAL,                -- nullable (only in some grammars)
    captured_at_utc     DATETIME NOT NULL,
    -- optional fields by grammar
    filter              TEXT,                -- nullable
    hfr                 REAL,                -- NINA v2 only
    rms_arcsec          REAL,                -- NINA v2 only
    -- bookkeeping
    grammar             TEXT NOT NULL CHECK (grammar IN
                          ('asiair_sci','asiair_cal','asiair_dslr',
                           'nina_legacy','nina_v2','nina_cal')),
    file_path           TEXT NOT NULL,       -- relative to library.root_path
    file_size_bytes     INTEGER,
    sequence_index      INTEGER,             -- the {idx} token
    UNIQUE (session_id, file_path)
);

CREATE INDEX idx_frames_session      ON frames(session_id);
CREATE INDEX idx_frames_type         ON frames(frame_type);
CREATE INDEX idx_frames_captured     ON frames(captured_at_utc);
CREATE INDEX idx_frames_qc_hfr_rms   ON frames(hfr, rms_arcsec);

-- -- calibration_masters ------------------------------------------------------
-- Entries from _Calibration Library/ that are NOT tied to a single session
-- (master bias, master dark, library flats kept for reuse).
CREATE TABLE calibration_masters (
    master_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id          TEXT NOT NULL REFERENCES libraries(library_id),
    class               TEXT NOT NULL CHECK (class IN ('bias','dark','flat')),
    folder_path         TEXT NOT NULL UNIQUE,   -- relative to library root
    -- identifying axes (some null depending on class)
    camera              TEXT REFERENCES sensors(sensor),
    scope               TEXT REFERENCES scopes(scope),
    temperature_c       REAL,                   -- for dark
    gain                INTEGER,                -- for dark
    exp_s               REAL,                   -- for dark
    capture_date        DATE,                   -- date the source frames were captured
    -- contents
    frame_count         INTEGER DEFAULT 0,
    total_size_bytes    INTEGER DEFAULT 0,
    is_generated_master INTEGER NOT NULL DEFAULT 0,  -- 1 for items in Bias Masters/, DARK library …/
    notes               TEXT,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cal_class       ON calibration_masters(class);
CREATE INDEX idx_cal_camera_temp ON calibration_masters(camera, temperature_c);
CREATE INDEX idx_cal_scope_sens  ON calibration_masters(scope, camera);

-- -- publications -------------------------------------------------------------
-- AstroBin posts, prints, social shares. Per-target rather than per-session
-- because publishing happens after integration.
CREATE TABLE publications (
    publication_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id           TEXT NOT NULL REFERENCES targets(target_id),
    session_id          INTEGER REFERENCES sessions(session_id),  -- set when the
                          -- publication is a single-session image rather than a
                          -- multi-session target master; NULL for the usual case
    kind                TEXT NOT NULL CHECK (kind IN ('astrobin','print','social','other')),
    url                 TEXT,
    title               TEXT,
    published_at        DATE,
    notes               TEXT
);

CREATE INDEX idx_pub_target  ON publications(target_id);
CREATE INDEX idx_pub_session ON publications(session_id);

-- -- legacy_xlsx_rows ---------------------------------------------------------
-- Verbatim ingestion of the existing Astro Acquisitions sheet so nothing is lost
-- when the scan-derived sessions don't exactly match the historical row.
-- Each row here is matched (best-effort) against a sessions.legacy_xlsx_id at
-- ingest time; unmatched rows stay here for manual reconciliation.
CREATE TABLE legacy_xlsx_rows (
    as_index            TEXT PRIMARY KEY,    -- 'As_017'
    sheet_row_data      TEXT NOT NULL,       -- JSON blob of the original cells
    matched_session_id  INTEGER REFERENCES sessions(session_id),
    matched_at          DATETIME,
    notes               TEXT
);

CREATE INDEX idx_legacy_matched ON legacy_xlsx_rows(matched_session_id);

-- -- pipeline_audit (optional, useful for history) ----------------------------
-- Append-only log of stage transitions. Lets the tracker show "M 81 moved
-- from Integrate to Stretch on 2026-05-10".
CREATE TABLE pipeline_audit (
    audit_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id           TEXT REFERENCES targets(target_id),
    session_id          INTEGER REFERENCES sessions(session_id),
    stage               TEXT NOT NULL,      -- 'capture','blink_reject','calibrate','integrate', etc.
    from_value          INTEGER,
    to_value            INTEGER NOT NULL,
    transitioned_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    by_actor            TEXT,                -- 'scan' | 'manual' | 'tracker_ui'
    notes               TEXT
);

CREATE INDEX idx_audit_target  ON pipeline_audit(target_id);
CREATE INDEX idx_audit_session ON pipeline_audit(session_id);

-- =============================================================================
-- GAP FIX 1 — calibration master lineage
-- Records which raw calibration sets were combined into each generated master.
-- Both the master and the raw sets are rows in calibration_masters; this is a
-- self-referential many-to-many link. Lets the tracker answer "exactly which
-- bias sets fed this master?" even when a master was built from a hand-picked
-- subset (e.g. skipping a known-bad session).
-- =============================================================================
CREATE TABLE calibration_master_inputs (
    master_id    INTEGER NOT NULL REFERENCES calibration_masters(master_id) ON DELETE CASCADE,
    raw_set_id   INTEGER NOT NULL REFERENCES calibration_masters(master_id),
    PRIMARY KEY (master_id, raw_set_id)
);

CREATE INDEX idx_cmi_raw ON calibration_master_inputs(raw_set_id);

-- Generation metadata lives on the master row itself; ALTER keeps the original
-- calibration_masters definition above readable.
ALTER TABLE calibration_masters ADD COLUMN generated_at        DATE;
ALTER TABLE calibration_masters ADD COLUMN generated_with      TEXT;   -- 'WBPP', 'PIMagic', etc.
ALTER TABLE calibration_masters ADD COLUMN generation_notes    TEXT;

-- =============================================================================
-- GAP FIX 2 — calibration thresholds
-- Encodes "what counts as enough" so queries can say a combo is BELOW threshold
-- or a master is DUE for refresh, rather than just reporting raw counts.
-- A threshold row with NULL camera/scope/temp/gain/exp applies as a default for
-- its class; more specific rows override. min_frames is the count needed to
-- justify (re)generating a master; refresh_days is how long before a master is
-- considered stale regardless of new raw sets.
-- =============================================================================
CREATE TABLE calibration_thresholds (
    threshold_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    class          TEXT NOT NULL CHECK (class IN ('bias','dark','flat')),
    camera         TEXT REFERENCES sensors(sensor),
    scope          TEXT REFERENCES scopes(scope),
    temperature_c  REAL,
    gain           INTEGER,
    exp_s          REAL,
    min_frames     INTEGER NOT NULL,    -- frames needed to justify a master
    refresh_days   INTEGER,             -- master older than this is stale
    notes          TEXT
);

-- Sensible defaults; Steve can override per camera/combo later.
INSERT INTO calibration_thresholds (class, min_frames, refresh_days, notes) VALUES
  ('bias', 50,  365, 'Default: 50 bias frames; refresh yearly'),
  ('dark', 25,  180, 'Default: 25 darks per temp/gain/exposure; refresh twice a year'),
  ('flat', 20,  NULL,'Default: 20 flats per rig per night; flats are per-session, no refresh');

-- =============================================================================
-- GAP FIX 3 — target acquisition goals
-- Per-target intent: how many hours Steve wants before a target is "done", an
-- optional per-filter breakdown, a priority, and an optional deadline (e.g.
-- "before the target sets for the season"). Powers progress bars in the tracker.
-- =============================================================================
CREATE TABLE target_goals (
    target_id             TEXT PRIMARY KEY REFERENCES targets(target_id),
    goal_hours            REAL,           -- total integration target
    goal_filter_breakdown TEXT,           -- JSON, e.g. {"Ha":10,"OIII":5,"SII":5}
    priority              INTEGER,        -- 1=high, 2=medium, 3=low
    target_deadline       DATE,           -- e.g. season cutoff
    notes                 TEXT
);

CREATE INDEX idx_goals_priority ON target_goals(priority);

-- =============================================================================
-- VIEWS — convenience layers over the base tables.
-- =============================================================================

-- Lifetime per-target rollup (deep-sky only — excludes "other captures")
CREATE VIEW v_target_lifetime AS
SELECT
    t.target_id,
    t.catalog || ' ' || COALESCE(t.number,'') AS catalog_id,
    t.common_name,
    COUNT(DISTINCT s.session_id)              AS sessions,
    SUM(s.lights_kept)                        AS lights_kept,
    SUM(s.lights_rejected)                    AS lights_rejected,
    ROUND(SUM(s.integration_s)/3600.0, 2)     AS hours_lifetime,
    GROUP_CONCAT(DISTINCT s.scope)            AS scopes_used,
    GROUP_CONCAT(DISTINCT s.sensor)           AS sensors_used,
    MIN(s.session_date)                       AS first_session,
    MAX(s.session_date)                       AS last_session
FROM targets t
LEFT JOIN sessions s ON s.target_id = t.target_id
WHERE t.is_other_capture = 0
GROUP BY t.target_id, t.catalog, t.number, t.common_name;

-- Per-year hours per target
CREATE VIEW v_target_year_hours AS
SELECT
    s.target_id,
    strftime('%Y', s.session_date) AS year,
    COUNT(*)                       AS sessions,
    ROUND(SUM(s.integration_s)/3600.0, 2) AS hours
FROM sessions s
WHERE s.is_other_capture = 0
GROUP BY s.target_id, year;

-- Rig coverage: hours per target per scope+sensor combo
CREATE VIEW v_rig_coverage AS
SELECT
    t.catalog || ' ' || COALESCE(t.number,'') AS target,
    s.scope || ' + ' || s.sensor              AS rig,
    COUNT(*)                                  AS sessions,
    ROUND(SUM(s.integration_s)/3600.0, 2)     AS hours
FROM sessions s
JOIN targets t USING (target_id)
WHERE t.is_other_capture = 0
GROUP BY target, rig
ORDER BY target, hours DESC;

-- Bad-frame candidates from NINA v2 captures
CREATE VIEW v_qc_candidates AS
SELECT
    s.target_id,
    s.session_date,
    f.captured_at_utc,
    f.hfr,
    f.rms_arcsec,
    f.file_path
FROM frames f
JOIN sessions s USING (session_id)
WHERE f.frame_type='light'
  AND f.is_rejected = 0
  AND (f.rms_arcsec > 1.5 OR f.hfr > 3.0)
ORDER BY s.session_date DESC, f.captured_at_utc;

-- Calibration coverage per camera (do I have masters for what I need?)
CREATE VIEW v_calibration_coverage AS
SELECT
    camera,
    class,
    COUNT(*)              AS sets,
    SUM(frame_count)      AS total_frames,
    ROUND(SUM(total_size_bytes)/1024.0/1024.0/1024.0, 2) AS total_gb,
    MIN(capture_date)     AS oldest,
    MAX(capture_date)     AS newest
FROM calibration_masters
WHERE camera IS NOT NULL
GROUP BY camera, class;

-- Per-session path to the finished image. Every session carries all seven of
-- its own stages now — stages 4-7 treat the session as a single-session
-- integration (its own .pxiproject + Results). furthest_stage gives a single
-- readable answer to "where did this session's data end up?"
CREATE VIEW v_session_pipeline AS
SELECT
    s.session_id, s.target_id, t.common_name,
    s.scope, s.sensor, s.session_date, s.library_id, s.is_other_capture,
    s.stage_capture, s.stage_blink_reject, s.stage_calibrate,
    s.stage_integrate, s.stage_edit, s.stage_publish, s.stage_print,
    CASE
      WHEN s.stage_print     = 2 THEN '7 Printed'
      WHEN s.stage_publish   = 2 THEN '6 Published'
      WHEN s.stage_edit      = 2 THEN '5 Edited'
      WHEN s.stage_integrate = 2 THEN '4 Integrated'
      WHEN s.stage_calibrate = 2 THEN '3 Calibrated'
      WHEN s.stage_blink_reject = 2 THEN '2 Blink/Reject done'
      WHEN s.stage_capture   = 1 THEN '1 Captured'
      ELSE '0 Planned'
    END AS furthest_stage
FROM sessions s JOIN targets t USING (target_id);

-- One readable row per integration (the multi-session / composite kind), with
-- its member count and furthest pipeline stage.
CREATE VIEW v_integration_overview AS
SELECT
    i.integration_id, i.target_id, t.common_name,
    i.kind, i.folder_name, i.scope, i.sensor, i.span, i.version,
    i.library_id, i.membership_mode, i.goal_hours, i.built_machine,
    COUNT(im.session_id)                                       AS sessions_available,
    SUM(COALESCE(im.in_build, 0))                              AS sessions_built,
    ROUND(SUM(s.integration_s) / 3600.0, 2)                   AS available_hours,
    ROUND(SUM(CASE WHEN im.in_build = 1 THEN s.integration_s ELSE 0 END) / 3600.0, 2)
                                                               AS built_hours,
    MAX(CASE WHEN im.in_build = 1 THEN s.session_date END)     AS data_through,
    CASE WHEN SUM(CASE WHEN im.in_build = 1 THEN 0 ELSE 1 END) > 0 THEN 1 ELSE 0 END
                                                               AS is_stale,
    CASE
      WHEN i.stage_print   = 2 THEN '7 Printed'
      WHEN i.stage_publish = 2 THEN '6 Published'
      WHEN i.stage_edit    = 2 THEN '5 Edited'
      ELSE '4 Integrated'
    END AS furthest_stage
FROM integrations i
JOIN targets t USING (target_id)
LEFT JOIN integration_members im ON im.integration_id = i.integration_id
LEFT JOIN sessions s             ON s.session_id = im.session_id
GROUP BY i.integration_id;

-- Targets with nothing published yet — no published session AND no published
-- integration. The "what still needs sharing" list.
CREATE VIEW v_targets_unpublished AS
SELECT t.target_id, t.common_name, t.folder_name
FROM targets t
WHERE t.is_other_capture = 0
  AND NOT EXISTS (SELECT 1 FROM sessions s
                  WHERE s.target_id = t.target_id AND s.stage_publish = 2)
  AND NOT EXISTS (SELECT 1 FROM integrations i
                  WHERE i.target_id = t.target_id AND i.stage_publish = 2);

-- Prune candidates: a (target, scope, sensor, span) lineage that has more than
-- one multi-session integration version. Keeping the latest, the rest are the
-- older comparison copies that can be cleaned up.
CREATE VIEW v_integration_prune AS
SELECT
    target_id, scope, sensor, span,
    COUNT(*)        AS version_count,
    MAX(version)    AS latest_version,
    GROUP_CONCAT(folder_name, ' | ') AS folders
FROM integrations
WHERE kind = 'multi-session'
GROUP BY target_id, scope, sensor, span
HAVING COUNT(*) > 1;

-- Sessions that still owe data (planned but empty)
CREATE VIEW v_empty_sessions AS
SELECT
    s.session_id,
    s.target_id,
    s.scope, s.sensor, s.session_date,
    s.library_id,
    s.folder_path
FROM sessions s
WHERE s.lights_kept = 0
  AND s.flats_count = 0
  AND s.is_other_capture = 0;

-- Calibration needs: for each raw calibration combo, resolve the applicable
-- threshold, count raw frames, find the most recent generated master, and
-- classify the combo as NO MASTER / BELOW THRESHOLD / STALE / OK.
CREATE VIEW v_calibration_needs AS
WITH raw_rollup AS (
    SELECT class, camera, scope, temperature_c, gain, exp_s,
           COUNT(*)            AS raw_sets,
           SUM(frame_count)    AS raw_frames,
           MAX(capture_date)   AS newest_raw
    FROM calibration_masters
    WHERE NOT is_generated_master
    GROUP BY class, camera, scope, temperature_c, gain, exp_s
),
master_rollup AS (
    SELECT class, camera, scope, temperature_c, gain, exp_s,
           MAX(COALESCE(generated_at, capture_date)) AS master_date
    FROM calibration_masters
    WHERE is_generated_master
    GROUP BY class, camera, scope, temperature_c, gain, exp_s
),
resolved AS (
    SELECT
        r.class, r.camera, r.scope, r.temperature_c, r.gain, r.exp_s,
        r.raw_sets, r.raw_frames, r.newest_raw,
        m.master_date,
        -- applicable threshold: most specific match wins, else class default
        COALESCE(
          (SELECT min_frames FROM calibration_thresholds t
           WHERE t.class=r.class AND t.camera IS r.camera
             AND t.temperature_c IS r.temperature_c AND t.gain IS r.gain AND t.exp_s IS r.exp_s),
          (SELECT min_frames FROM calibration_thresholds t
           WHERE t.class=r.class AND t.camera IS NULL AND t.scope IS NULL)
        ) AS min_frames,
        COALESCE(
          (SELECT refresh_days FROM calibration_thresholds t
           WHERE t.class=r.class AND t.camera IS r.camera
             AND t.temperature_c IS r.temperature_c AND t.gain IS r.gain AND t.exp_s IS r.exp_s),
          (SELECT refresh_days FROM calibration_thresholds t
           WHERE t.class=r.class AND t.camera IS NULL AND t.scope IS NULL)
        ) AS refresh_days
    FROM raw_rollup r
    LEFT JOIN master_rollup m
      ON m.class=r.class AND m.camera IS r.camera AND m.scope IS r.scope
     AND m.temperature_c IS r.temperature_c AND m.gain IS r.gain AND m.exp_s IS r.exp_s
)
SELECT
    class, camera, scope, temperature_c, gain, exp_s,
    raw_sets, raw_frames, newest_raw, master_date, min_frames, refresh_days,
    CASE
      WHEN class='flat' THEN 'N/A (per-session)'   -- flats aren't mastered into the library
      WHEN master_date IS NULL THEN 'NO MASTER'
      WHEN newest_raw > master_date THEN 'STALE (new raw)'
      WHEN refresh_days IS NOT NULL
           AND julianday(date('now')) - julianday(master_date) > refresh_days
        THEN 'STALE (age)'
      ELSE 'OK'
    END AS status,
    CASE WHEN raw_frames < min_frames THEN 1 ELSE 0 END AS below_threshold
FROM resolved;

-- Target acquisition progress: lifetime hours against the goal, with percent
-- complete and hours remaining. Powers the dashboard progress bars.
CREATE VIEW v_target_progress AS
SELECT
    t.target_id,
    t.common_name,
    g.goal_hours,
    g.priority,
    g.target_deadline,
    COALESCE(v.hours_lifetime, 0)               AS hours_so_far,
    CASE WHEN g.goal_hours > 0
         THEN ROUND(100.0 * COALESCE(v.hours_lifetime,0) / g.goal_hours, 1)
         END                                    AS percent_complete,
    CASE WHEN g.goal_hours > 0
         THEN ROUND(g.goal_hours - COALESCE(v.hours_lifetime,0), 2)
         END                                    AS hours_remaining
FROM target_goals g
JOIN targets t USING (target_id)
LEFT JOIN v_target_lifetime v USING (target_id);

-- =============================================================================
-- Data validation
-- =============================================================================
-- One row per problem found by the validate() pass (in ingest.py / validate.py).
-- The whole table is cleared and rebuilt on every validation run, so it always
-- reflects the current state of the libraries.
--
--   severity : 'error'   — almost certainly wrong, needs a fix
--              'warning' — likely wrong or incomplete, worth a look
--              'info'    — notable but not necessarily a problem
--   code     : stable machine code (e.g. 'DATE_MISMATCH') for grouping/filtering
--   scope    : what kind of thing the finding is about
--   session_id / ref_path : whichever identifies the offending item
CREATE TABLE validation_findings (
    finding_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    severity     TEXT NOT NULL,      -- 'error' | 'warning' | 'info'
    code         TEXT NOT NULL,      -- e.g. 'DATE_MISMATCH', 'EMPTY_LIGHTS'
    scope        TEXT NOT NULL,      -- 'session'|'frame'|'calibration'|'target'|'registry'
    session_id   INTEGER REFERENCES sessions(session_id) ON DELETE CASCADE,
    ref_path     TEXT,               -- folder/file path when there is no session row
    message      TEXT NOT NULL,      -- human-readable description
    detected_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_findings_severity ON validation_findings(severity);
CREATE INDEX idx_findings_code     ON validation_findings(code);
CREATE INDEX idx_findings_session  ON validation_findings(session_id);

-- Counts by severity + code, for the dashboard Data Health panel.
CREATE VIEW v_validation_summary AS
SELECT severity, code, COUNT(*) AS n
FROM validation_findings
GROUP BY severity, code
ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, code;

-- =============================================================================
-- UPSERT helpers (used by the scan)
-- =============================================================================

-- Upsert pattern for sessions (called once per session folder on each scan):
--
--   INSERT INTO sessions (target_id, scope, sensor, session_date, library_id, folder_path, ...)
--   VALUES (...)
--   ON CONFLICT (target_id, scope, sensor, session_date) DO UPDATE SET
--     library_id     = excluded.library_id,
--     folder_path    = excluded.folder_path,
--     lights_kept    = excluded.lights_kept,
--     lights_rejected= excluded.lights_rejected,
--     integration_s  = excluded.integration_s,
--     updated_at     = CURRENT_TIMESTAMP;
--
-- Upsert pattern for frames (called per file):
--
--   INSERT OR IGNORE INTO frames (session_id, file_path, frame_type, ...) VALUES (...);
--   -- (frames are immutable once ingested; deletion happens via session ON DELETE CASCADE
--   --  if a session folder ever disappears)
