// build_paper.js — generate the astrophotography data-organization paper (.docx)
// Run:  node build_paper.js [--out PATH]
// Requires:  npm install docx
// Regenerate after the tracker numbers change; edit the STATS block below.

const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Footer, AlignmentType, LevelFormat, HeadingLevel, BorderStyle,
  WidthType, ShadingType, PageNumber, PageBreak
} = require("docx");

// --------------------------------------------------------------------------
// STATS — current library figures. Update these from the tracker database
// (v_target_lifetime and the summary queries) when they change.
// --------------------------------------------------------------------------
const STATS = {
  asOf: "May 2026",
  deepSkyHours: "691.95",
  deepSkySessions: 201,
  otherCaptureSessions: 5,
  distinctTargets: 78,
  targetsImaged: 66,
  keptLights: "9,785",
  rejectedLights: "840",
  calibrationGB: "376",
  calibrationSets: 221,
  streamHours: "335.93", streamSessions: 97,
  peakHours: "356.02", peakSessions: 104,
};

const ARIAL = "Arial";
const MONO  = "Menlo";
const border = { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC" };
const cellBorders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };

function p(text, opts = {}) {
  const runs = Array.isArray(text) ? text : [new TextRun({ text, font: ARIAL, ...opts.run })];
  return new Paragraph({ children: runs, spacing: { after: 120, line: 300 }, ...opts.par });
}
function h1(t) { return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text: t, font: ARIAL })] }); }
function h2(t) { return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text: t, font: ARIAL })] }); }
function bullet(t) {
  return new Paragraph({ numbering: { reference: "bullets", level: 0 },
    children: [new TextRun({ text: t, font: ARIAL })], spacing: { after: 80, line: 280 } });
}
function bulletRich(runs) {
  return new Paragraph({ numbering: { reference: "bullets", level: 0 },
    children: runs, spacing: { after: 80, line: 280 } });
}
function code(t) {
  return new Paragraph({ children: [new TextRun({ text: t, font: MONO, size: 20 })],
    spacing: { after: 120, line: 260 }, shading: { fill: "F5F5F5", type: ShadingType.CLEAR } });
}
function codeBlock(lines) {
  return lines.map(l => new Paragraph({ children: [new TextRun({ text: l, font: MONO, size: 20 })],
    spacing: { after: 20, line: 240 }, shading: { fill: "F5F5F5", type: ShadingType.CLEAR } }));
}
function hCell(t, w) {
  return new TableCell({ borders: cellBorders, margins: cellMargins,
    width: { size: w, type: WidthType.DXA },
    shading: { fill: "EAEFF5", type: ShadingType.CLEAR },
    children: [new Paragraph({ children: [new TextRun({ text: t, font: ARIAL, bold: true })] })] });
}
function bCell(t, w) {
  return new TableCell({ borders: cellBorders, margins: cellMargins,
    width: { size: w, type: WidthType.DXA },
    children: [new Paragraph({ children: [new TextRun({ text: t, font: ARIAL })] })] });
}
function table(widths, hdr, rows) {
  return new Table({ width: { size: widths.reduce((a,b)=>a+b,0), type: WidthType.DXA },
    columnWidths: widths,
    rows: [ new TableRow({ tableHeader: true, children: hdr.map((t,i)=>hCell(t, widths[i])) }),
      ...rows.map(r => new TableRow({ children: r.map((t,i)=>bCell(String(t), widths[i])) })) ] });
}

const children = [];

// ---- Cover --------------------------------------------------------------
children.push(new Paragraph({ alignment: AlignmentType.CENTER,
  children: [new TextRun({ text: "An astrophotography data-organization system", font: ARIAL, bold: true, size: 44 })],
  spacing: { before: 1200, after: 240 } }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER,
  children: [new TextRun({ text: "Controlled vocabularies, a session-keyed folder template, a dual-library archive, and a database-backed tracker", font: ARIAL, italics: true, size: 26 })],
  spacing: { after: 240 } }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER,
  children: [new TextRun({ text: `Steve Stanzel  ·  ${STATS.asOf}  ·  draft for peer review`, font: ARIAL, size: 22, color: "555555" })],
  spacing: { after: 960 } }));

// ---- Abstract -----------------------------------------------------------
children.push(h2("Abstract"));
children.push(p(`This paper describes the system I use to organize an astrophotography dataset that currently spans ${STATS.deepSkySessions} deep-sky imaging sessions and ${STATS.deepSkyHours} hours of kept integration on ${STATS.targetsImaged} actively imaged targets, captured with four telescopes and four cameras over two years. The aim is to make the dataset legible — to me, to my processing tools, and to anyone reviewing the work — without relying on a heavyweight digital asset manager.`));
children.push(p("The system has four parts. A controlled-vocabulary registry of empty directories defines the only legal names for filters, telescopes, cameras, and target objects. A Post Haste template materializes each capture night into an identical per-session scaffold so PixInsight and PIMagic Studio always find what they need. A shared calibration library holds master flats, darks, and bias frames at the camera and scope-plus-camera level so they can be reused across many target sessions. And a database-backed tracker reads the ASIAir and NINA filename grammars to answer the questions a serious imager asks: how many hours on a given target this year and lifetime, which scope-camera combinations have imaged it, where each session sits in the processing pipeline, and which calibration masters need to be produced or refreshed."));
children.push(p("The paper documents the convention, the source-of-truth files it depends on, and the management toolchain built around it. It is written so other astrophotographers can evaluate the approach and adapt the parts that suit their own workflow."));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ---- 1 ------------------------------------------------------------------
children.push(h1("1.  Why a written convention"));
children.push(p("Astrophotography data degrades quietly. The night you captured M 81 is unambiguous in the moment, but a year later, with more sessions piled on top of it from several telescope-camera combinations, you can be reduced to opening FITS headers to remember what was what. The convention here is the smallest set of rules that, applied consistently, lets a script — or a future version of yourself — answer questions about the capture tree without opening a single image."));
children.push(p("Three questions motivated the design:"));
children.push(bullet("How many hours of usable integration do I have on a given target, this year and lifetime?"));
children.push(bullet("Which scope-camera combinations have I imaged a given target with, and which still owe me data?"));
children.push(bullet("Where in the processing pipeline — calibration, integration, stretch, edit, publish — is each session right now?"));
children.push(p("If the folder tree cannot answer those questions mechanically, the structure is not doing its job. The rest of this paper is the structure that can."));

// ---- 2 ------------------------------------------------------------------
children.push(h1("2.  Goals and principles"));
children.push(p("Five principles shape every decision below:"));
children.push(bulletRich([new TextRun({ text: "Self-describing folders. ", bold: true, font: ARIAL }), new TextRun({ text: "Every directory name tells a parser the target, the rig, and the night without opening anything inside it.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "Controlled vocabularies. ", bold: true, font: ARIAL }), new TextRun({ text: "Filter, telescope, sensor, and target labels are drawn from a fixed registry of empty directories. The list of valid values is itself a folder, so the filesystem is the authority.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "One scaffold per night. ", bold: true, font: ARIAL }), new TextRun({ text: "Post Haste materializes the same per-session subtree every time, so the processing tools always find what they expect.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "Filename metadata, not sidecar metadata. ", bold: true, font: ARIAL }), new TextRun({ text: "ASIAir encodes exposure, gain, sensor, binning, datetime, and temperature in each light frame's name; NINA encodes the same plus focus and guiding quality. The system reads that rather than reinventing it.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "Calibration that scales. ", bold: true, font: ARIAL }), new TextRun({ text: "Master darks and biases live with the camera, not with each target. Flats live with the rig and the night. A shared library indexes both.", font: ARIAL })]));
children.push(p("Together these make the dataset structurally queryable — exposure totals come from filenames, with no FITS-header parsing required for the common case."));

// ---- 3 ------------------------------------------------------------------
children.push(h1("3.  The two capture libraries"));
children.push(p("Captures live on two physical volumes that share an identical internal structure:"));
children.push(bulletRich([new TextRun({ text: "stream — the working volume. ", bold: true, font: ARIAL }), new TextRun({ text: "Current-year imaging on a fast external SSD. New sessions land here and most active processing happens here.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "peak — the lifetime archive. ", bold: true, font: ARIAL }), new TextRun({ text: "Prior-year sessions on a Synology NAS. Sessions migrate from stream to peak after a year is closed out.", font: ARIAL })]));
children.push(p("Both libraries share the same internal layout: a top-level structure organized by target and the per-session scaffold. The controlled-vocabulary registry (`_organization/`, described next) lives only on the working volume — it is a single authority, not duplicated. The tracker scans both libraries, so a query for lifetime hours on a target aggregates across volumes transparently."));

children.push(h2("3.1  The registry: _organization/"));
children.push(p("The registry is the only deliberately empty part of the tree. Each subdirectory under `_organization/` is a vocabulary, and the directory's contents are the legal values for that vocabulary. They are intentionally empty — the names are the data. A small number of `!`-prefixed example directories are kept as templates: when a new scope or camera is added, the template subtree is duplicated and renamed, giving the new rig the same complete structure as the existing ones."));
children.push(table([2400, 900, 6060], ["Vocabulary", "Members", "Purpose"], [
  ["filter_values/", "12", "Every filter used on any rig, each with a short label and a descriptive suffix."],
  ["scope_values/", "9", "Every telescope and lens used for any kind of astro work."],
  ["sensor_values/", "10", "Every camera. “Sensor” is the term most astro tooling uses, though it is only the imaging chip of the camera."],
  ["scope+sensor_values/", "16", "The combinations of imaging scope and imaging camera that pair together. Only these appear in deep-sky session folder names."],
  ["target folders/", "74", "The catalog identifier and common name for every object imaged or planned. The folder is created in the registry first, then a copy appears in a capture library the night it is imaged."],
]));
children.push(p("The full vocabulary lists are reproduced in Appendix A."));

children.push(h2("3.2  Library root"));
children.push(p("Every directly visible directory at a library root is a target — one folder per object, named with the catalog identifier and common name (for example, “M 81 Bodes Galaxy”). The exceptions are the `_organization` registry (working volume only), the `_Calibration Library` (Section 7), an `ASI EAA` folder for live-stacking output, and the “other captures” buckets (Section 8). Inside each target folder the children are session folders, one per night per rig."));

// ---- 4 ------------------------------------------------------------------
children.push(h1("4.  The per-session scaffold"));
children.push(p("Each imaging night is materialized by Post Haste from a single template, which expands into the following structure:"));
children.push(...codeBlock([
  "{Target_id} {Scope} {Sensor} {YYYY-MM-DD}/",
  "├── Light/                                  ← raw light frames",
  "├── Rejected/                               ← blink-rejected lights",
  "├── Flat {Scope}_{Sensor} {Date}/",
  "│   ├── Flat/                               ← that night's flats",
  "│   └── Dark Flat/                          ← matching dark flats",
  "├── PI Process/                             ← PixInsight intermediates",
  "├── PI Magic/                               ← PIMagic Studio outputs",
  "├── {session_name}.pxiproject/              ← PixInsight project",
  "├── {session_name} Results/                 ← final stretched output",
  "└── {session_name} notes.rtf                ← per-session metadata sheet",
]));
children.push(p("Three things are opinionated by design. First, in-session calibration is limited to flats and dark flats; both depend on the night's focuser position and dust state, so they live with the lights they belong to. Master darks and biases live with the camera in the shared calibration library (Section 7). Second, every session has its own PixInsight project — projects are tied to a single night, and combining across nights is a higher-level workflow (Section 9). Third, the notes sheet prompts for every metadata field the tracker may want if filename parsing is ever insufficient: target identifier and common name, date, time, location, moon age and phase, sky brightness (Bortle), mount, camera, gain, pixel size, guide camera and scope, telescope focal length and aperture, exposure counts by frame type, integration time, and an AstroBin link."));

// ---- 5 ------------------------------------------------------------------
children.push(h1("5.  Naming conventions"));

children.push(h2("5.1  Target folder name"));
children.push(p("A target folder's name is the catalog designation, a space, and the common name:"));
children.push(code("M 81 Bodes Galaxy"));
children.push(code("NGC 1499 California Nebula"));
children.push(code("IC 2118 Witch Head Nebula"));
children.push(p("All catalogs except Sharpless use a single space between catalog and number (M ##, NGC ###, IC ###, C ##, LDN ###, HR ####). Sharpless objects use the compact form SH2 ### with no space between “SH” and “2” — a deliberate exception that keeps folder names from becoming unnecessarily long. The common name is Title Case for a proper noun (“California Nebula”) and lower case for a descriptive label (“barred spiral galaxy”)."));
children.push(p("When a single field contains more than one named object — the Leo Triplet, NGC 3718/3729, Markarian's Chain — the folder is named after the field, not split into per-object folders. Companion targets are recorded in the tracker's metadata rather than the filesystem. This avoids dummy session folders and keeps per-night integration straightforward."));

children.push(h2("5.2  Session folder name"));
children.push(p("A session folder is one acquisition night with one telescope and one camera. The name is four whitespace-delimited tokens:"));
children.push(code("{Target_id_with_underscore} {Scope} {Sensor} {YYYY-MM-DD}"));
children.push(p("Examples:"));
children.push(code("M_81 RASA8 ASI2600MCAir 2026-04-19"));
children.push(code("NGC_1499 Pleiades111 ASI2600MCAir 2026-02-26"));
children.push(p("The target identifier is joined with an underscore at the session level so the four tokens are unambiguous to a parser. The date is the calendar date the session started in local time. The natural key for a session is the tuple of target identifier, scope, sensor, and date; across the full dataset that tuple is unique with no collisions, which is what lets the tracker ingest and re-ingest without duplicating rows."));

children.push(h2("5.3  Light-frame filename grammars"));
children.push(p("Two capture systems write subtly different filenames: ASIAir on the deep-sky rigs, and NINA on the Player One camera. Each has a science variant (carrying a target name) and a calibration variant (without one). The tracker's parser recognizes all five:"));
children.push(table([1900, 7460], ["Grammar", "Example"], [
  ["ASIAir science", "Light_NGC 3718_120.0s_Bin1_585MC_gain252_20260424-011157_74deg_-18.7C_LQuadE_0054.fit"],
  ["ASIAir calibration", "Flat_108.3ms_Bin1_585MC_gain200_20260309-084517_156deg_-19.5C_0008.fit"],
  ["NINA science", "LIGHT_M 106_300.00s_Bin1x1_Poseidon-C PRO_gain125_2026-05-12_22-56-19_288.99deg_-20.00C__HFR2.83_RMS0.42_LQuadE_0001.fits"],
  ["NINA calibration", "FLAT__1.20s_Bin1x1_Poseidon-C PRO_gain125_2026-05-12_18-00-00_288.99deg_-20.00C__HFR_RMS_LQuadE_0001.fits"],
  ["NINA legacy", "LIGHT_M 106_300.00s_1x1_Poseidon-C PRO_125_2026-05-12_23-13-37_288.99_-20.10__0003.fits"],
]));
children.push(p("In every grammar each field is structurally extractable: target (when present), exposure, binning, camera, gain, datetime, optional rotation angle, sensor temperature, optional filter, and a sequence counter. Because the data is in the filename, totalling integration on a target reduces to walking the light frames, parsing each name, and summing. The NINA science grammar additionally carries HFR (a focus-quality measure) and RMS (guiding error in arcseconds), which lets the tracker flag poor frames from filenames alone. The reference patterns are in Appendix B."));

// ---- 6 ------------------------------------------------------------------
children.push(h1("6.  Calibration: the per-session model"));
children.push(p("In-session calibration is limited to two frame types: flats and dark flats. Both depend on the night's focuser position, the camera's rotational alignment, and the dust state of the optical train, so they live next to the lights they belong to in a subdirectory named for the rig and the date. Master darks — indexed by camera, gain, exposure, and temperature — and bias frames — indexed by camera and gain — are independent of the target and the night. They live in the shared calibration library described next."));

// ---- 7 ------------------------------------------------------------------
children.push(h1("7.  The shared calibration library"));
children.push(p(`Calibration data that can be reused across sessions lives in a \`_Calibration Library\` on the working volume — a single shared resource referenced by processing regardless of which volume a session's light frames sit on. It is large — roughly ${STATS.calibrationGB} GB across ${STATS.calibrationSets} calibration sets — and grows monotonically: master darks and biases are captured once per camera-temperature-gain combination and then reused for years.`));
children.push(p("The structure is organized around hardware rather than targets:"));
children.push(...codeBlock([
  "_Calibration Library/",
  "├── Bias/{Camera}/Bias {Date}/                          ← dated bias sets",
  "│   └── Bias Masters/                                   ← generated masters",
  "├── Dark/{Camera}/{Temp}/{Gain}/{Exposure}/Dark {Date}/ ← dated dark sets",
  "├── Flat/{Scope}_{Sensor}/Flat {Scope}_{Sensor} {Date}/ ← per-rig per-night flats",
  "└── !scope_sensor/ , !Camera/ , !camera/ , !session/    ← duplicate-and-rename templates",
]));
children.push(p("The `!`-prefixed directories are working templates: adding a new camera or scope means duplicating the relevant template subtree and renaming it, so a new rig inherits the same complete temperature, gain, and exposure scaffold as the existing ones. Bias and dark frames are kept as raw dated sets; generated masters are stored alongside and the tracker records which raw sets fed each master, so a master built from a hand-picked subset is fully traceable."));

// ---- 8 ------------------------------------------------------------------
children.push(h1("8.  “Other captures” — the non-conforming category"));
children.push(p("Not every astro session goes through the four standard imaging rigs. Lunar imaging through a visual telescope, comets and asteroids, aurora and time-lapse work with DSLRs and action cameras, and one-off test shoots all share a property: they do not fit the four-token session-name grammar, and their frame counts would distort deep-sky integration averages. They live in a separate set of top-level buckets and are deliberately exempt from the conventions above:"));
children.push(table([3000, 6360], ["Bucket", "Contents"], [
  ["Asteroids Comets/", "Comet and asteroid captures."],
  ["Moon Daytime/", "Daytime lunar imaging."],
  ["Moon Nighttime/", "Nighttime lunar imaging and lunar eclipses."],
  ["As_Tl_Astrophotography Timelapse/", "Time-lapses — star trails, aurora, and similar."],
  ["As_misc/", "One-off, test, and miscellaneous captures."],
  ["ASI EAA/", "Live electronically-assisted-astronomy output and operational logs."],
]));
children.push(p("The tracker still records these sessions and their metadata, but does not add their frames to the deep-sky integration totals; they are surfaced separately. Named-star sessions of single bright stars are not “other captures” — they use a standard imaging rig and follow the normal grammar."));

// ---- 9 ------------------------------------------------------------------
children.push(h1("9.  Processing pipeline and stage tracking"));
children.push(p("Each session moves through a seven-stage pipeline. Stages 1 to 3 act on a single session; stage 4 promotes the work to the target level when sessions are combined; stages 5 to 7 act on the target."));
children.push(table([600, 2200, 6560], ["#", "Stage", "Definition"], [
  ["1", "Capture", "Lights, flats, and dark flats acquired and written into the session folder."],
  ["2", "Blink / Reject", "Visual review; poor frames moved into the session's Rejected folder. NINA's embedded focus and guiding figures let the tracker propose candidates."],
  ["3", "Calibrate", "WBPP (PixInsight) or PIMagic applies master darks, bias, and the session's own flats and dark flats."],
  ["4", "Integrate", "Calibrated lights from one or more sessions are registered and stacked into a single master light for the target. Work scope moves from session to target."],
  ["5", "Edit", "All non-linear processing as one stage: gradient removal, deconvolution and noise reduction, the non-linear stretch, star removal and recombination, color grading, and final compositing in PixInsight and Photoshop."],
  ["6", "Publish", "Posting the finished image — AstroBin, social, web — with the link recorded."],
  ["7", "Print", "Producing a physical print. Tracked separately from Publish: a published image is not necessarily printed, and a print can follow long after."],
]));
children.push(p("Stages 1 to 3 are recorded per session; stages 4 to 7 are recorded per target, because integration combines several sessions into one image that is then edited, published, and printed once. Stretch and star removal, which were once tracked as their own steps, are folded into a single Edit stage — in practice they are one continuous non-linear-processing pass, and splitting them added bookkeeping without insight."));
children.push(p("Every session still needs a clear path to those final stages: the question “did this night's data ever reach a published or printed image?” must be answerable. The tracker resolves it by joining each session to its parent target's downstream state. A session view reports the session's own three stages plus the target's integrate, edit, publish, and print state, and collapses them into a single furthest-stage value running from “Captured” through “Printed.” The pipeline can therefore be read two ways: down the per-target column to see where a target stands, or across a session's row to trace one night's data all the way to the wall."));
children.push(p("Publish and Print are deliberately separate stages with separate records. The tracker keeps a publications table — one row per posting or print, each carrying its kind (AstroBin, print, social), date, and link. A publication is normally tied to a target's combined master, but it can instead be tied to a single session when one night is posted on its own, so first-light images and full deep integrations are both tracked without being conflated."));

// ---- 10 -----------------------------------------------------------------
children.push(h1("10.  The management toolchain"));
children.push(p("Four small scripts, kept together in the registry, turn the folder convention into a queryable system. They are deliberately plain — Python and SQLite, no services to run — so they remain easy to read, audit, and modify."));
children.push(bulletRich([new TextRun({ text: "The filename parser ", bold: true, font: ARIAL }), new TextRun({ text: "recognizes all five filename grammars and exposes each field — exposure, gain, filter, temperature, focus and guiding quality — as structured data.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "The schema ", bold: true, font: ARIAL }), new TextRun({ text: "is a SQLite database: tables for libraries, targets, sessions, frames, the calibration library, master-to-raw lineage, calibration thresholds, acquisition goals, publications, and a pipeline audit log, plus views for the common questions.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "The ingest script ", bold: true, font: ARIAL }), new TextRun({ text: "walks both libraries, parses every session folder and FITS filename, walks the calibration library, and upserts the database. It is idempotent: re-running after a capture night refreshes the data without duplicating it.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "The export script ", bold: true, font: ARIAL }), new TextRun({ text: "regenerates a multi-tab Excel workbook from the database — a summary, all sessions, a per-target rollup with acquisition-goal progress, calibration status, and a quality-control candidate list.", font: ARIAL })]));
children.push(p("Because the database is the single source of truth and the scan is idempotent, the workbook and any other view are always reproducible from the folders on disk. The questions the system is built to answer include: lifetime and per-year integration on any target; which scope-camera combinations have covered a target; which targets have the oldest unprocessed data; which targets have enough calibrated sessions to integrate; which bias and dark masters still need to be produced or have gone stale; and which individual frames should be reviewed for focus or guiding quality. Representative queries are in Appendix C."));
children.push(p("The database schema sketch:"));
children.push(...codeBlock([
  "targets(target_id PK, catalog, number, common_name, companions[],",
  "        is_other_capture, pipeline stages 4-7, astrobin_url, ...)",
  "sessions(session_id PK, target_id FK, scope, sensor, session_date,",
  "         library, folder_path, pipeline stages 1-3, frame counts, ...)",
  "         UNIQUE(target_id, scope, sensor, session_date)",
  "frames(frame_id PK, session_id FK, frame_type, exp_s, gain, temp_c,",
  "       captured_at_utc, filter, hfr, rms_arcsec, rejected, ...)",
  "calibration_masters(...) · calibration_master_inputs(...) ·",
  "calibration_thresholds(...) · target_goals(...) · publications(...)",
]));

// ---- 11 -----------------------------------------------------------------
children.push(h1("11.  Worked example: M 81 Bodes Galaxy"));
children.push(p("M 81 is the most heavily imaged target in the combined library: 18 sessions across four telescope-camera combinations and both volumes, totalling 71.5 hours of kept integration. The folder structure at the M 81 level looks like this (excerpted):"));
children.push(...codeBlock([
  "peak (lifetime archive):",
  "  M 81 Bodes Galaxy/",
  "    M_81 Pleiades111 ASI2600MCAir 2025-01-26",
  "    M_81 Redcat51 ASI585MCPro 2026-02-10",
  "    …",
  "",
  "stream (working volume):",
  "  M 81 Bodes Galaxy/",
  "    M_81 HAC125DX ASI585MCPro 2026-04-18 / 19",
  "    M_81 RASA8 ASI2600MCAir 2026-04-18 / 19 / 20 / 22 / 23",
  "    M_81 Redcat51 ASI585MCPro 2026-02-26 / 03-04 / 03-08 / … / 03-27",
]));
children.push(p("Three things follow from this layout. Integration time per rig is a grouping query against the frame table. The eventual master stack of M 81 is not in any session folder; it belongs at the target level, which is where the later pipeline stages operate. And the natural session key stays unique even though four scope-camera combinations have all imaged M 81 — so the tracker can re-scan freely without collisions."));

// ---- 12 -----------------------------------------------------------------
children.push(h1("12.  Current library state"));
children.push(p(`The figures below are produced by the tracker from a scan of both libraries, as of ${STATS.asOf}.`));
children.push(table([5300, 1700, 1700, 660], ["Metric", "stream", "peak", "Total"], [
  ["Deep-sky sessions", String(STATS.streamSessions), String(STATS.peakSessions), String(STATS.deepSkySessions)],
  ["Kept integration (hours)", STATS.streamHours, STATS.peakHours, STATS.deepSkyHours],
  ["Distinct targets (both libraries)", "", "", String(STATS.distinctTargets)],
  ["Targets with kept light frames", "", "", String(STATS.targetsImaged)],
  ["Kept light frames", "", "", STATS.keptLights],
  ["Rejected light frames", "", "", STATS.rejectedLights],
  ["Other-capture sessions", "", "", String(STATS.otherCaptureSessions)],
  ["Calibration library", "", "", `${STATS.calibrationGB} GB · ${STATS.calibrationSets} sets`],
]));
children.push(p("Top ten deep-sky targets by lifetime integration:"));
children.push(table([3600, 1100, 800, 3860], ["Target", "Hours", "Sess.", "Scopes used"], [
  ["M 81 Bodes Galaxy", "71.5", "18", "HAC125DX, Pleiades111, RASA8, Redcat51"],
  ["M 44 Beehive Cluster", "47.5", "16", "Pleiades111, RASA8, Redcat51"],
  ["NGC 1499 California Nebula", "34.3", "9", "Pleiades111, Redcat51"],
  ["M 31 Andromeda Galaxy", "29.5", "10", "Pleiades111, Redcat51"],
  ["IC 342 Caldwell 5", "27.8", "6", "Redcat51"],
  ["IC 1805 Heart Nebula", "23.7", "4", "Pleiades111, Redcat51"],
  ["M 42 Orion Nebula", "22.8", "9", "Pleiades111, Redcat51"],
  ["M 66 Leo Triplet", "21.5", "6", "HAC125DX, Pleiades111"],
  ["M 97 Owl Nebula", "20.9", "5", "RASA8"],
  ["M 33 Triangulum Galaxy", "20.1", "6", "Pleiades111, Redcat51"],
]));
children.push(p("Imaging by year: 36 sessions and 105.5 hours in 2024, 38 sessions and 123.9 hours in 2025, 127 sessions and 462.6 hours in 2026 to date. Filters recorded in light-frame names: LPI 615 frames, LQuadE 594, LQuadEnhance 169; the remaining sessions are inferred from rig configuration."));

// ---- 13 -----------------------------------------------------------------
children.push(h1("13.  Open questions for reviewers"));
children.push(p("This is the section I would most like other astrophotographers to push on:"));
children.push(bullet("Per-session flats versus a per-rig flat library tagged by date. The session-local approach keeps each session self-contained but duplicates flats taken back-to-back on the same focuser position. Is there a clean rule for when flats should be promoted to a shared library?"));
children.push(bullet("Field-name target folders. Keeping “M 66 Leo Triplet” as a single folder makes acquisition obvious but complicates a query like “all integration on M 65”. Is a companions list the right abstraction, or should multi-object fields be linked into each member's folder?"));
children.push(bullet("Sensor temperature and gain in the folder name. They are not there today — only date, scope, and sensor. Would adding them earn their keep, or is filename-level metadata enough?"));
children.push(bullet("Focus and guiding quality in the filename. NINA can embed HFR and guiding RMS per frame; ASIAir does not. Is this useful enough to be worth a per-session quality log when the capture software will not put it in the filename?"));
children.push(bullet("Time-zone discipline. Session folders use the local civil date; FITS timestamps are UTC. Is there an accepted convention for naming a session by its astronomical (noon-to-noon) date instead?"));

// ---- Appendix A ---------------------------------------------------------
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(h1("Appendix A.  Controlled vocabularies"));
children.push(h2("A.1  Telescopes and lenses (9)"));
["HAC125DX","NexStar6SE","Pleiades111","RASA8","RF100-500","RF70-200","Redcat51","WO50","ZWO30"].forEach(s => children.push(bullet(s)));
children.push(p("HAC125DX, Pleiades111, RASA8, and Redcat51 are the four imaging telescopes that pair with imaging cameras for deep-sky work. NexStar6SE is used for visual and lunar; the RF and Sony lenses cover landscape and time-lapse; WO50 is a finder; ZWO30 is the guidescope."));
children.push(h2("A.2  Cameras (10)"));
["ASI174MM","ASI220MMmini","ASI2600MCAir","ASI432MM","ASI585MCPro","Canon5Div","CanonR5","PoseidenCPro","SonyA7Riv","minicam8"].forEach(s => children.push(bullet(s)));
children.push(p("ASI2600MCAir, ASI585MCPro, PoseidenCPro, and minicam8 are the imaging cameras paired with deep-sky scopes. The mono ZWO cameras are reserved for narrowband and guiding. The Canon and Sony bodies cover landscape, time-lapse, and “other captures”."));
children.push(h2("A.3  Imaging scope + sensor combinations (16)"));
["HAC125DX_ASI2600MCAir","HAC125DX_ASI585MCPro","HAC125DX_PoseidenCPro","HAC125DX_minicam8","Pleiades111_ASI2600MCAir","Pleiades111_ASI585MCPro","Pleiades111_PoseidenCPro","Pleiades111_minicam8","RASA8_ASI2600MCAir","RASA8_ASI585MCPro","RASA8_PoseidenCPro","RASA8_minicam8","Redcat51_ASI2600MCAir","Redcat51_ASI585MCPro","Redcat51_PoseidenCPro","Redcat51_minicam8"].forEach(s => children.push(bullet(s)));
children.push(h2("A.4  Filters (12)"));
["B - QHY","H EVO - QHY","Ha-Hb-Oiii - Celestron RASA8","IR - ZWO","L - QHY","LPI - Light Pollution Imaging - Celestron RASA8","LQuadE - Optolong L-Quad Enhance Broadband Light Pollution","LUltDual - Optolong L-Ultimate Dual Bandpass Light Pollution Reduction Imaging","LeHDual - Optolong L-eNhance Dual Bandpass Light Pollution Reduction","O EVO - QHY","R - QHY","S EVO - QHY"].forEach(s => children.push(bullet(s)));

// ---- Appendix B ---------------------------------------------------------
children.push(h1("Appendix B.  Reference patterns for the filename grammars"));
children.push(p("The five grammars are tried in order; the first match wins. All matches expose: frame type, exposure value and unit, camera, gain, datetime, optional rotation, temperature, optional filter, and a sequence index. The NINA science grammar additionally exposes HFR and RMS."));
children.push(h2("B.1  ASIAir science"));
children.push(...codeBlock([
  "^(?P<type>Light)_(?P<target>.+?)_(?P<exp>[\\d.]+)(?P<unit>s|ms)_",
  " Bin(?P<bin>\\d+)_(?P<cam>[^_]+)_gain(?P<gain>-?\\d+)_",
  " (?P<dt>\\d{8}-\\d{6})_(?:(?P<rot>-?[\\d.]+)deg_)?(?P<temp>-?[\\d.]+)C",
  " (?:_(?P<filter>[A-Za-z][\\w]*))?_(?P<idx>\\d+)\\.(?P<ext>fit|fits|xisf)$",
]));
children.push(h2("B.2  ASIAir calibration"));
children.push(p("Identical to B.1 but the type token is Flat / Dark / Bias / DarkFlat and there is no target token."));
children.push(h2("B.3  NINA science (current pattern)"));
children.push(...codeBlock([
  "^(?P<type>LIGHT|DARK|FLAT|BIAS|DARKFLAT)_(?P<target>.*?)_",
  " (?P<exp>[\\d.]+)s_Bin(?P<binx>\\d+)x(?P<biny>\\d+)_(?P<cam>.+?)_",
  " gain(?P<gain>-?\\d+)_(?P<date>\\d{4}-\\d{2}-\\d{2})_(?P<time>\\d{2}-\\d{2}-\\d{2})_",
  " (?P<rot>-?[\\d.]+)deg_(?P<temp>-?[\\d.]+)C__",
  " HFR(?P<hfr>[\\d.]*)_RMS(?P<rms>[\\d.]*)_(?P<filter>\\w*)_",
  " (?P<idx>\\d+)\\.(?P<ext>fit|fits|xisf)$",
]));
children.push(p("This pattern is the NINA default with the literal anchors Bin, gain, deg, and C added so the structure mirrors ASIAir. The double underscore before HFR is a deliberate visual marker between physical metadata and quality-of-capture metadata. HFR, RMS, and the filter may all be empty when not populated; the parser handles either case. A legacy NINA variant without those anchors is also recognized for the small number of sessions captured before the pattern was standardized."));

// ---- Appendix C ---------------------------------------------------------
children.push(h1("Appendix C.  Representative tracker queries"));
children.push(...codeBlock([
  "-- Lifetime and this-year integration on a target:",
  "SELECT t.common_name,",
  "  SUM(CASE WHEN NOT f.rejected THEN f.exp_s END)/3600.0 AS hours_lifetime,",
  "  SUM(CASE WHEN NOT f.rejected",
  "           AND strftime('%Y', f.captured_at_utc)='2026'",
  "           THEN f.exp_s END)/3600.0 AS hours_2026",
  "FROM targets t JOIN sessions s USING (target_id)",
  "JOIN frames f USING (session_id)",
  "WHERE t.common_name LIKE 'M 81%' AND NOT s.is_other_capture",
  "GROUP BY t.common_name;",
]));
children.push(p(" "));
children.push(...codeBlock([
  "-- Targets ready for multi-session integration:",
  "SELECT t.common_name, COUNT(s.session_id) AS calibrated_sessions,",
  "       ROUND(SUM(s.integration_s)/3600.0,2) AS hours_available",
  "FROM targets t JOIN sessions s USING (target_id)",
  "WHERE NOT t.is_other_capture AND t.stage_integrate < 2",
  "  AND s.stage_calibrate = 2",
  "GROUP BY t.target_id HAVING COUNT(s.session_id) >= 2",
  "ORDER BY hours_available DESC;",
]));
children.push(p(" "));
children.push(...codeBlock([
  "-- Bias masters that still need to be produced:",
  "SELECT camera, COUNT(*) AS raw_sets, SUM(frame_count) AS raw_frames",
  "FROM calibration_masters",
  "WHERE class='bias' AND NOT is_generated_master",
  "  AND camera NOT IN (SELECT camera FROM calibration_masters",
  "                     WHERE class='bias' AND is_generated_master)",
  "GROUP BY camera;",
]));

// ---- Appendix D ---------------------------------------------------------
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(h1("Appendix D.  The per-session notes sheet"));
children.push(p("Every session folder contains a notes sheet generated from the Post Haste template (currently a `notes.rtf` file). It is intended as a small metadata stub — a record of the things only a per-session observation can capture, useful both when the data is processed later and when the library is queried. Its current fields are listed below, each assessed against what the filenames and the tracker already provide."));
children.push(table([2500, 1700, 5160], ["Field", "Already known?", "Best source"], [
  ["Target identifier", "Yes — folder + filename", "Redundant; auto-fill from folder name"],
  ["Target common name", "Yes — target folder", "Redundant; auto-fill from target folder"],
  ["Date", "Yes — folder + filename", "Redundant; auto-fill"],
  ["Time", "Yes — filename", "Redundant; auto-fill"],
  ["Location", "No", "Manual, or a geotag; changes rarely"],
  ["Moon age", "No", "Auto: astronomical calculation from the date"],
  ["Moon phase / illumination", "No", "Auto: astronomical calculation from the date"],
  ["Bortle (sky brightness)", "No", "Manual per site, or an SQM reading if logged"],
  ["Mount", "No", "FITS header, else rig configuration"],
  ["Camera", "Yes — filename", "Redundant; auto-fill"],
  ["Camera gain", "Yes — filename", "Redundant; auto-fill"],
  ["Camera pixel size", "No", "FITS header (XPIXSZ)"],
  ["Guide camera / guide scope", "No", "Rig configuration or NINA equipment profile"],
  ["Telescope / lens", "Yes — folder scope token", "Redundant; auto-fill"],
  ["Focal length", "No", "FITS header (FOCALLEN), else rig configuration"],
  ["Aperture", "No", "Rig configuration"],
  ["Exposure counts by frame type", "Yes — filename scan", "Redundant; the tracker computes these"],
  ["Integration time", "Yes — filename scan", "Redundant; the tracker computes this"],
  ["AstroBin link", "No", "Belongs in the tracker's publications table"],
  ["Other / free notes", "No", "Manual — the genuinely irreplaceable field"],
]));
children.push(p("Roughly half of the template's fields duplicate information that the filename grammar or the tracker already carries. Re-typing them by hand is error-prone: a mistyped camera or date in the notes then contradicts the filename and the folder, and there is no way to tell which is right. The recommendation is to shrink the sheet to the fields that filenames cannot carry, and to auto-populate even those wherever a source exists:"));
children.push(bulletRich([new TextRun({ text: "From FITS headers — ", bold: true, font: ARIAL }), new TextRun({ text: "mount, pixel size, focal length, and often the guide configuration are written into the headers by ASIAir and NINA. A header read at ingest time fills these with no typing.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "From an astronomical calculation — ", bold: true, font: ARIAL }), new TextRun({ text: "moon age, phase, and illumination are a deterministic function of the session date and location, and can be computed at ingest rather than looked up by hand.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "From a local weather log — ", bold: true, font: ARIAL }), new TextRun({ text: "cloud cover, transparency, seeing, temperature, dewpoint, and humidity are the data most worth capturing per session and least available anywhere else. A weather-station log or an online weather history keyed on date and location can supply them.", font: ARIAL })]));
children.push(bulletRich([new TextRun({ text: "Manual, and worth it — ", bold: true, font: ARIAL }), new TextRun({ text: "the Bortle rating for the site and free-text observing notes: equipment quirks, passing cloud, satellite trails, what to try differently next time. This is what a notes sheet is actually for.", font: ARIAL })]));
children.push(p("A second recommendation concerns format. RTF is awkward for a program to read reliably. If the notes sheet is to be machine-ingestible — so the tracker can pull weather and moon context into the database — a structured plain-text format serves better: a strict one-key-per-line “Field: value” grammar, or a small JSON or TOML sidecar named to match the session. The content stays human-readable, and the tracker gains a dependable parse."));

// ---- Appendix E ---------------------------------------------------------
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(h1("Appendix E.  Other approaches to astrophotography data organization"));
children.push(p("The structure described in this paper is one point in a wide design space. The approaches below are the ones most discussed in amateur communities such as Cloudy Nights and the AstroBin forums, followed by how two professional observatories handle the same problem. They are offered as a reference for readers weighing the trade-offs."));

children.push(h2("E.1  Date-first hierarchy"));
children.push(p("Folders are organized by night first and target second: a dated folder per session, each holding that night's lights and calibration frames. This is the out-of-the-box behavior of N.I.N.A., whose default file pattern groups by image type and date, and it mirrors how terrestrial photographers organize shoots. It is the simplest thing to do at capture time — a night's work lands in one place. Its weakness is the question this paper opens with: a single target's data scatters across many date folders, so total integration time per target is tedious to compute and a target's history is never in one view."));

children.push(h2("E.2  Target-first hierarchy"));
children.push(p("Folders are organized by target first, with dated session subfolders inside. This is the most frequently recommended structure in amateur forums, on the reasoning that imagers return to the same targets across seasons and years, so the target is the durable unit. Total integration per target is immediate and a target's whole history sits in one folder. It is the family this paper's system belongs to. Its cost is discipline: session subfolders must be named consistently or the advantage erodes — which is exactly the problem a controlled vocabulary and a fixed template are there to solve."));

children.push(h2("E.3  Capture / Library / Processing separation"));
children.push(p("The top level is split by how often data changes rather than by target: a Capture tree written every session, a Library tree of master calibration data that changes rarely, and a Processing tree rewritten on every processing run. The appeal is backup strategy — capture data can be moved to slow archive storage once calibrated, while the small, volatile Processing tree is backed up often. The cost is that one target's material spreads across three trees, so the scheme needs an index to reassemble it. This paper's design adopts the idea in part: calibration lives in its own library, but capture and target data stay unified."));

children.push(h2("E.4  Frame-type-first within target"));
children.push(p("Within each target, folders are organized by frame type and then by filter — Light, Dark, Flat, Dark Flat, each subdivided by filter — with rich filenames carrying date, temperature, filter, and exposure. This layout feeds PixInsight's WeightedBatchPreprocessing script with minimal pointing and clicking, since the script consumes frame-type folders directly. It optimizes for the calibration step, at the cost of deeper nesting and a structure organized around a processing tool rather than around the observation."));

children.push(h2("E.5  Target-plus-attempt"));
children.push(p("Each target folder contains numbered or dated “attempt” folders, and each attempt is a self-contained mini-project with its own lights, calibration, finals, and processing-application files. This suits imagers who reprocess often and want each reprocessing reproducible in isolation. The weak point is that the boundary of an “attempt” is subjective — when several nights are combined it is unclear which attempt owns them — which is the ambiguity this paper's split between per-session and per-target stages is designed to remove."));

children.push(h2("E.6  Professional practice: Hubble and JWST"));
children.push(p("The Hubble Space Telescope names every dataset with a nine-character “IPPPSSOOT” rootname that encodes the instrument, proposal, observation set, and observation in fixed-width positions; each file is the rootname plus a suffix indicating its processing level — raw, calibrated, drizzled, and so on. JWST uses a comparable scheme of program and observation identifiers. In both cases the filenames are deliberately machine-oriented — the documentation states plainly that they are not designed to be scientist-friendly — and all human searching is done through the Mikulski Archive for Space Telescopes (MAST), a relational archive that indexes the opaque filenames by target name, program, instrument, exposure type, and date."));
children.push(p("The professional lesson is the separation of two concerns: a rigid, machine-parseable identity encoded in the filename, and a separate searchable layer — a database — built on top of it. This paper's system follows the same division: self-describing folder and file names provide the rigid identity, and the SQLite tracker provides the searchable layer. The deliberate difference is one of scale. An amateur archive of a few hundred targets can afford folder names that are human-readable as well as machine-parseable, so the imager gets both and rarely needs to consult a database for routine work. The database earns its place for the aggregate questions — integration totals, calibration coverage, pipeline state — that no single folder name can answer on its own."));

children.push(h1("Acknowledgements"));
children.push(p("This is a working draft describing a system still in active development. Comments, corrections, and counter-arguments from other astrophotographers are welcome — that is the purpose of circulating it."));

// ---- Document -----------------------------------------------------------
const doc = new Document({
  styles: { default: { document: { run: { font: ARIAL, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: ARIAL, color: "1F3A5F" },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: ARIAL, color: "2E5C8A" },
        paragraph: { spacing: { before: 240, after: 140 }, outlineLevel: 1 } },
    ] },
  numbering: { config: [{ reference: "bullets",
    levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 },
      margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [
        new TextRun({ text: `Astrophotography data-organization system  ·  Steve Stanzel  ·  ${STATS.asOf}  ·  `, font: ARIAL, size: 18, color: "888888" }),
        new TextRun({ children: [PageNumber.CURRENT], font: ARIAL, size: 18, color: "888888" }),
      ] })] }) },
    children,
  }],
});

const outArg = process.argv.indexOf("--out");
const out = outArg > -1 ? process.argv[outArg + 1]
          : "Astrophotography_v2_organization_paper.docx";
Packer.toBuffer(doc).then(buf => { fs.writeFileSync(out, buf); console.log("Wrote:", out, buf.length, "bytes"); });
