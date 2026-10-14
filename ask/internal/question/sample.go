package question

// Block is a settled question's block, which the server's tests and the committed fixture share.
const Block = `{"title": "Red cells given to neonates under anaesthesia",
 "statement": {"cohort": "Every anaesthetic of a child under 28 days old at its start, leaving out test patients.",
               "outcome": "The volume of red cells charted during the anaesthetic, in millilitres.",
               "result": "One row with the number of anaesthetics and the total millilitres of red cells."},
 "decisions": [{"about": "who is a neonate", "choice": "under 28 days at the start", "value": 28, "unit": "days",
                "decision": "A neonate is under 28 days old at the start of the anaesthetic, counted by calendar date."},
               {"about": "which patients are left out", "choice": "test patients", "value": ["test_patient"], "unit": null,
                "decision": "Test patients are left out."}],
 "question_sql": "WITH neonatal AS (SELECT a.anaesthetic_key FROM role_anaesthetic a JOIN role_patient p ON p.patient_key = a.patient_key WHERE p.is_test = 0 AND DATEDIFF(day, p.birth_date, a.start_time) < 28)\nSELECT COUNT(*) AS anaesthetics, SUM(f.volume_ml) AS red_cells_ml FROM neonatal n JOIN role_fluid f ON f.anaesthetic_key = n.anaesthetic_key WHERE f.kind = 'red_cells'",
 "note": "Counts and volumes only.",
 "approval": {"reference": "QA-2026-014", "use": "audit"},
 "proposals": []}`

// OverCapability is a settled question's block that composes a capability of the catalogue and takes a median over
// its output, which the server's tests and the committed fixture share.
const OverCapability = `{"title": "Median minutes below a saturation of 90 in the first half hour",
 "statement": {"cohort": "Every anaesthetic of a child, leaving out test patients.",
               "outcome": "The minutes between ten and forty minutes after the start during which the saturation lay below 90 per cent.",
               "result": "One row with the median of those minutes over the anaesthetics that had a saturation reading in the window."},
 "decisions": [{"about": "the threshold", "choice": "below 90 per cent at every age", "value": 90, "unit": "per cent",
                "decision": "The threshold is a saturation below 90 per cent, the same at every age."},
               {"about": "the window", "choice": "ten to forty minutes after the start", "value": [10, 40], "unit": "minutes",
                "decision": "The window runs from ten to forty minutes after the start of the anaesthetic."}],
 "capabilities": [{"name": "hypoxaemia_burden", "version": 1,
                   "parameters": {"kinds": [["spo2", 1]], "direction": "below", "threshold_by_age_band": [[0, null, 90.0]],
                                  "window_from_minutes": 10, "window_until_minutes": 40, "reading_stands_minutes": 2}}],
 "question_sql": "WITH measured AS (SELECT h.anaesthetic_key, h.minutes_beyond FROM hypoxaemia_burden h WHERE h.minutes_beyond IS NOT NULL)\nSELECT DISTINCT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m.minutes_beyond) OVER () AS median_minutes_below_90 FROM measured m",
 "note": "The median is over anaesthetics with at least one saturation reading in the window; those with none are left out rather than counted as zero.",
 "proposals": []}`
