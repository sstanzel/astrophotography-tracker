-- =============================================================================
-- Astrophotography tracker — query catalog  ·  rev 1 — 2026-05-19
-- Reusable SQL for the operational questions the tracker answers.
-- All queries assume the schema in schema.sql is loaded.
-- =============================================================================

-- -- A. INVENTORY & REPORTING ------------------------------------------------

-- A1. Total kept hours, lifetime (deep-sky only)
SELECT ROUND(SUM(integration_s)/3600.0, 2) AS hours_lifetime
FROM sessions WHERE NOT is_other_capture;

-- A2. Top targets by lifetime integration
SELECT target_id, common_name, sessions, hours_lifetime, scopes_used
FROM v_target_lifetime
ORDER BY hours_lifetime DESC LIMIT 20;

-- A3. Hours per target per year (drill-in for the "M 54 this year?" question)
SELECT target_id, year, sessions, hours
FROM v_target_year_hours
ORDER BY target_id, year;

-- A4. Hours per scope+sensor rig
SELECT scope || ' + ' || sensor AS rig,
       COUNT(*) AS sessions,
       ROUND(SUM(integration_s)/3600.0, 2) AS hours
FROM sessions WHERE NOT is_other_capture
GROUP BY rig ORDER BY hours DESC;

-- A5. Hours per filter (across all rigs)
SELECT f.filter, COUNT(*) AS frames, ROUND(SUM(f.exp_s)/3600.0, 2) AS hours
FROM frames f
WHERE f.frame_type='light' AND NOT f.is_rejected AND f.filter IS NOT NULL
GROUP BY f.filter;

-- -- B. STEVE'S OPERATIONAL QUESTIONS ----------------------------------------

-- B1. Targets with oldest data that's yet to be processed
SELECT t.target_id, t.common_name,
       MIN(s.session_date) AS oldest_session,
       COUNT(*) AS sessions,
       ROUND(SUM(s.integration_s)/3600.0, 2) AS hours_accumulated,
       t.stage_integrate
FROM targets t JOIN sessions s ON s.target_id = t.target_id
WHERE NOT t.is_other_capture
  AND t.stage_integrate < 2     -- 0=not started, 1=in progress, 2=done
GROUP BY t.target_id
ORDER BY oldest_session ASC LIMIT 10;

-- B2. Targets ready for multi-session integration
--    (≥2 sessions, all calibrated, not yet integrated)
SELECT t.target_id, t.common_name,
       COUNT(s.session_id) AS calibrated_sessions,
       ROUND(SUM(s.integration_s)/3600.0, 2) AS hours_available,
       GROUP_CONCAT(DISTINCT s.scope || '+' || s.sensor) AS rigs
FROM targets t JOIN sessions s ON s.target_id = t.target_id
WHERE NOT t.is_other_capture
  AND t.stage_integrate < 2
  AND s.stage_culled = 2
GROUP BY t.target_id
HAVING COUNT(s.session_id) >= 2
ORDER BY hours_available DESC;

-- B3. Bias Masters that still need to be produced
--    (camera has raw bias sets but no generated master)
SELECT camera,
       COUNT(*)                AS raw_bias_sets,
       SUM(frame_count)        AS total_raw_frames,
       MAX(capture_date)       AS most_recent_raw
FROM calibration_masters
WHERE class='bias' AND NOT is_generated_master
  AND camera NOT IN (
      SELECT camera FROM calibration_masters
      WHERE class='bias' AND is_generated_master AND camera IS NOT NULL
  )
GROUP BY camera;

-- B4. Bias masters that are stale (raw sets exist newer than the master)
SELECT raw.camera,
       COUNT(*) AS newer_raw_sets,
       SUM(raw.frame_count) AS newer_raw_frames,
       MIN(raw.capture_date) AS oldest_newer,
       MAX(raw.capture_date) AS newest_newer,
       master.capture_date AS master_date
FROM calibration_masters raw
JOIN calibration_masters master
  ON master.camera = raw.camera
 AND master.class='bias' AND master.is_generated_master
WHERE raw.class='bias' AND NOT raw.is_generated_master
  AND raw.capture_date > master.capture_date
GROUP BY raw.camera, master.capture_date;

-- B5. Dark masters that don't exist yet for combinations I'm capturing
SELECT raw.camera, raw.temperature_c, raw.gain, raw.exp_s,
       COUNT(*) AS raw_sets, SUM(raw.frame_count) AS raw_frames
FROM calibration_masters raw
LEFT JOIN calibration_masters m
  ON m.camera=raw.camera
 AND m.temperature_c IS raw.temperature_c
 AND m.gain IS raw.gain
 AND m.exp_s IS raw.exp_s
 AND m.class='dark' AND m.is_generated_master
WHERE raw.class='dark' AND NOT raw.is_generated_master
  AND m.master_id IS NULL
GROUP BY raw.camera, raw.temperature_c, raw.gain, raw.exp_s;

-- B6. Dark masters that are stale (newer raw darks exist)
SELECT raw.camera, raw.temperature_c, raw.gain, raw.exp_s,
       COUNT(*) AS newer_raw_sets,
       m.capture_date AS master_made,
       MAX(raw.capture_date) AS newest_raw
FROM calibration_masters raw
JOIN calibration_masters m
  ON m.camera=raw.camera
 AND m.temperature_c IS raw.temperature_c
 AND m.gain IS raw.gain
 AND m.exp_s IS raw.exp_s
 AND m.class='dark' AND m.is_generated_master
WHERE raw.class='dark' AND NOT raw.is_generated_master
  AND raw.capture_date > m.capture_date
GROUP BY raw.camera, raw.temperature_c, raw.gain, raw.exp_s, m.capture_date;

-- -- C. QUALITY CONTROL ------------------------------------------------------

-- C1. Frames flagged as suspect (high HFR or RMS) — NINA v2 frames only
SELECT target_id, session_date, captured_at_utc, hfr, rms_arcsec
FROM v_qc_candidates LIMIT 25;

-- C2. Sessions with no captures (planned shells)
SELECT * FROM v_empty_sessions;

-- C3. Targets at each pipeline stage (stages 4-7 are per-target)
SELECT
  CASE
    WHEN stage_print=2     THEN '7 Printed'
    WHEN stage_publish=2   THEN '6 Published'
    WHEN stage_edit=2      THEN '5 Edited'
    WHEN stage_integrate=2 THEN '4 Integrated'
    WHEN stage_integrate=1 THEN '4 Integrating'
    ELSE '<= 3 (per-session)'
  END AS stage,
  COUNT(*) AS targets
FROM targets WHERE NOT is_other_capture
GROUP BY stage ORDER BY stage;

-- C5. Per-session path to the finished image — every session and how far
--     its data has travelled (capture through print).
SELECT furthest_stage, COUNT(*) AS sessions
FROM v_session_pipeline
WHERE NOT is_other_capture
GROUP BY furthest_stage ORDER BY furthest_stage;

-- C6. Sessions whose data is captured & calibrated but the target isn't integrated yet
SELECT target_id, common_name, session_date, scope, sensor
FROM v_session_pipeline
WHERE NOT is_other_capture
  AND stage_culled = 2 AND stage_integrate < 2
ORDER BY session_date;

-- C4. Frames captured this year at gain X, temp Y (ad-hoc filter)
SELECT s.target_id, s.session_date, COUNT(*) AS frames
FROM frames f JOIN sessions s USING (session_id)
WHERE f.frame_type='light' AND NOT f.is_rejected
  AND f.gain = 200 AND f.temp_c <= -19
  AND s.session_date >= date('2026-01-01')
GROUP BY s.target_id, s.session_date;

-- -- D. ACQUISITION PLANNING -------------------------------------------------

-- D1. Targets in the registry I've never imaged
SELECT t.target_id, t.folder_name
FROM targets t LEFT JOIN sessions s ON s.target_id = t.target_id
WHERE s.session_id IS NULL AND NOT t.is_other_capture;

-- D2. Targets with the fewest hours so far (candidates for more time)
SELECT target_id, common_name, sessions, hours_lifetime
FROM v_target_lifetime
WHERE sessions > 0
ORDER BY hours_lifetime ASC LIMIT 10;

-- D3. Time since I last imaged each target (overdue list)
SELECT t.target_id, t.common_name,
       MAX(s.session_date) AS last_imaged,
       CAST(julianday(date('now')) - julianday(MAX(s.session_date)) AS INTEGER) AS days_since
FROM targets t JOIN sessions s ON s.target_id=t.target_id
WHERE NOT t.is_other_capture
GROUP BY t.target_id
ORDER BY days_since DESC LIMIT 20;

-- D4. Targets where I've used only ONE rig (might want a second perspective)
SELECT target_id, common_name, sessions, scopes_used, sensors_used
FROM v_target_lifetime
WHERE scopes_used IS NOT NULL
  AND instr(scopes_used, ',') = 0
  AND sessions >= 2
ORDER BY hours_lifetime DESC;

-- -- E. RIG USAGE ------------------------------------------------------------

-- E1. Sessions per rig over the last 90 days
SELECT scope||'+'||sensor AS rig, COUNT(*) AS sessions_last_90d,
       ROUND(SUM(integration_s)/3600.0, 2) AS hours_last_90d
FROM sessions
WHERE session_date >= date('now','-90 day')
GROUP BY rig ORDER BY sessions_last_90d DESC;

-- E2. Rigs that haven't been used in a while
SELECT scope||'+'||sensor AS rig,
       MAX(session_date) AS last_used,
       CAST(julianday(date('now')) - julianday(MAX(session_date)) AS INTEGER) AS days_since
FROM sessions GROUP BY rig ORDER BY days_since DESC;

-- -- F. DATA VOLUME ----------------------------------------------------------

-- F1. Total data per target (requires frames.file_size_bytes populated)
SELECT s.target_id,
       COUNT(f.frame_id) AS frames,
       ROUND(SUM(f.file_size_bytes)/1024.0/1024.0/1024.0, 2) AS gigabytes
FROM frames f JOIN sessions s USING (session_id)
GROUP BY s.target_id
HAVING gigabytes > 0
ORDER BY gigabytes DESC;

-- F2. Storage by library
SELECT s.library_id,
       COUNT(*) AS sessions,
       ROUND(SUM(f.file_size_bytes)/1024.0/1024.0/1024.0, 2) AS gb
FROM sessions s LEFT JOIN frames f USING (session_id)
GROUP BY s.library_id;

-- -- G. CALIBRATION HEALTH ---------------------------------------------------

-- G1. Calibration coverage by camera and class
SELECT * FROM v_calibration_coverage ORDER BY camera, class;

-- G2. Cameras I'm imaging with at temp/gain combos with NO matching dark masters
SELECT DISTINCT f.camera_short, f.temp_c, f.gain,
       (SELECT sensor FROM sensors WHERE short_form = f.camera_short) AS sensor
FROM frames f
WHERE f.frame_type='light' AND NOT f.is_rejected
  AND NOT EXISTS (
    SELECT 1 FROM calibration_masters m
    WHERE m.class='dark'
      AND m.camera = (SELECT sensor FROM sensors WHERE short_form = f.camera_short)
      AND m.temperature_c = f.temp_c
      AND m.gain = f.gain
      AND m.is_generated_master = 1
  );

-- G3. Per-session flat coverage — sessions missing their flats
SELECT s.target_id, s.session_date, s.scope, s.sensor
FROM sessions s
WHERE NOT s.is_other_capture
  AND s.flats_count = 0
  AND s.stage_culled < 2
ORDER BY s.session_date;

-- -- H. FIELD & ALIAS QUERIES ------------------------------------------------

-- H1. Field-name targets and their companion catalog IDs
SELECT target_id, folder_name, companions, aka_catalog_ids
FROM targets
WHERE companions IS NOT NULL OR aka_catalog_ids IS NOT NULL;

-- H2. All hours on a target including aliases
--    (e.g. include hours filed under NGC 1909 when querying IC 2118 Witch Head)
SELECT t.target_id, t.common_name,
       ROUND(SUM(s.integration_s)/3600.0, 2) AS hours
FROM targets t JOIN sessions s ON s.target_id = t.target_id
WHERE t.target_id = 'IC_2118'
   OR (t.aka_catalog_ids IS NOT NULL AND t.aka_catalog_ids LIKE '%NGC 1909%');

-- -- I. PUBLICATION & SHARING ------------------------------------------------

-- I1. Targets I have integrated but not yet published
SELECT t.target_id, t.common_name, t.stage_stretch, t.stage_edit, t.stage_publish
FROM targets t
WHERE t.stage_integrate = 2 AND t.stage_publish < 2 AND NOT t.is_other_capture
ORDER BY t.common_name;

-- I2. Targets posted on AstroBin
SELECT target_id, common_name, astrobin_url
FROM targets WHERE astrobin_url IS NOT NULL;
