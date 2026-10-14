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
 "proposals": []}`
