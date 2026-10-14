"""The examination's static stage: the service's packages and the five capabilities as packages are well formed, a
package whose declared columns differ from its SQL's is refused with a sentence, and the command prints the report."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from schemalyser import capability, question, rolemap
from schemalyser.examination import __main__ as examination_main
from schemalyser.examination import static

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "packages"
PLANTED = json.loads((capability.FOLDER / "capabilities" / "planted.json").read_text())


def base():
    return json.loads((FIXTURES / "neonatal-low-mean-pressure.question.json").read_text(encoding="utf-8"))


def capability_package(name):
    """A capability of the catalogue composed as a package, reading its columns per anaesthetic and counting them."""
    entry = rolemap.capabilities()[name]
    columns = entry["columns"]
    held = base()
    held["title"] = f"The capability {name} over every anaesthetic"
    held["capabilities"] = [{"name": name, "version": entry["version"], "parameters": PLANTED[name]["parameters"]}]
    held["question_sql"] = (f"WITH chosen AS (SELECT a.anaesthetic_key FROM role_anaesthetic a JOIN role_patient p ON p.patient_key = a.patient_key WHERE p.is_test = 0)\n"
                            f"SELECT COUNT(*) AS anaesthetics, COUNT(x.{columns[1]}) AS with_a_value FROM chosen c JOIN {name} x ON x.anaesthetic_key = c.anaesthetic_key")
    versions = rolemap.part_versions()
    held["parts"] = [{"name": n, "version": versions[n], "admission": rolemap.admission(n, "question")} for n in ("role_anaesthetic", "role_patient", "role_reading")]
    held["examinable"] = {"oracle": "none", "because": "The capability's own held-out cases judge it."}
    return held


def write(tmp_path, name, held):
    path = tmp_path / f"{name}.question.json"
    path.write_text(json.dumps(held, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def test_the_service_s_packages_and_the_five_capabilities_as_packages_are_well_formed(tmp_path):
    for fixture in ("ask-fake-backend.question.json", "ask-fake-backend-capability.question.json", "neonatal-low-mean-pressure.question.json"):
        report = static.run(FIXTURES / fixture)
        assert report["level"] == "well formed", (fixture, static.sentences(report))
        assert report["failed"] == [] and report["form"] == [] and report["dialect_version"] == 1
        assert report["package"]["sha256"] and report["package"]["title"]
    report = static.run(FIXTURES / "ask-fake-backend.question.json")
    assert report["admission"] == {"ceiling": ["role_fluid"], "drafts": []} and "admitted with a ceiling" in report["says"]
    for name in capability.with_sql():
        path = write(tmp_path, name, capability_package(name))
        report = static.run(path)
        assert report["level"] == "well formed", (name, static.sentences(report))


def test_declared_columns_that_differ_from_the_sql_s_are_refused_with_a_sentence(tmp_path):
    held = base()
    held["examinable"] = {"cohort": {"grain": "anaesthetic", "age_days": [0, 27], "exclude": ["test_patient", "no_start", "stop_before_start"]},
                          "outcome": {"part": "role_reading", "kinds": ["map_arterial", "map_cuff"], "measure": "count"},
                          "result": {"statistic": "count", "over": "anaesthetic",
                                     "columns": ["minutes_below_40", "anaesthetics", "died_within_90_days", "children", "children_died_within_90_days"]}}
    assert static.run(write(tmp_path, "right", held))["level"] == "well formed"
    held["examinable"]["result"]["columns"] = ["minutes_below_40", "anaesthetics", "deaths"]
    report = static.run(write(tmp_path, "wrong", held))
    assert report["level"] == "not well formed" and report["failed"] == ["columns"]
    assert static.sentences(report)[0] == ("The package breaks the rule that the columns the package declares in its examinable result are the columns its SQL returns, in that order: "
                                           "the examinable result declares the columns minutes_below_40, anaesthetics, deaths, and the SQL returns minutes_below_40, anaesthetics, died_within_90_days, children, children_died_within_90_days.")
    assert report["says"] == "The package is not well formed: it breaks 1 rule, columns."


def test_a_package_that_lacks_the_two_part_form_or_breaks_the_policy_is_not_well_formed_and_a_draft_is_read_only_when_admitted(tmp_path):
    rows = base()
    rows["question_sql"] = "SELECT r.anaesthetic_key, r.value FROM role_reading r WHERE r.kind = 'map_arterial'"
    rows["parts"] = [{"name": "role_reading", "version": 1, "admission": "admitted"}]
    report = static.run(write(tmp_path, "rows", rows))
    assert report["level"] == "not well formed" and report["failed"] == [] and report["form"] == ["cohort"]
    assert "lacks the two-part form" in report["says"]
    draft = base()
    draft["question_sql"] = ("WITH chosen AS (SELECT a.anaesthetic_key, a.patient_key FROM role_anaesthetic a JOIN role_patient p ON p.patient_key = a.patient_key WHERE p.is_test = 0)\n"
                             "SELECT COUNT(*) AS results FROM chosen c JOIN role_lab l ON l.patient_key = c.patient_key WHERE l.kind = 'haemoglobin'")
    draft["parts"] = [{"name": n, "version": 1, "admission": rolemap.admission(n, "question")} for n in ("role_anaesthetic", "role_lab", "role_patient")]
    path = write(tmp_path, "draft", draft)
    assert static.run(path)["failed"] == ["waits"]
    admitted = static.run(path, admit="draft")
    assert admitted["level"] == "well formed" and admitted["admission"]["drafts"] == ["role_lab"] and "a draft admitted for this examination only" in admitted["says"]


def test_the_command_prints_the_report_and_its_exit_code_says_the_level(tmp_path, capsys):
    assert examination_main.main(["run", str(FIXTURES / "neonatal-low-mean-pressure.question.json")]) == 0
    assert "The package is well formed" in capsys.readouterr().out
    held = base()
    held["parts"] = held["parts"][:1]
    path = write(tmp_path, "short", held)
    assert examination_main.main(["run", str(path)]) == 1
    out = capsys.readouterr().out
    assert "the parts are exactly the parts the SQL reads" in out and "not well formed" in out
    assert examination_main.main(["run", str(path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["level"] == "not well formed"


# Stage 4, the planted cases under a fresh transform.

from schemalyser.examination import planted  # noqa: E402


def test_the_neonatal_audit_and_the_five_capabilities_pass_their_planted_cases_under_a_fresh_transform():
    audit_sql = rolemap.AUDIT.read_text(encoding="utf-8")
    for seed in (1, 2):
        report = planted.run_case(audit_sql, "neonatal_low_mean_pressure", seed=seed)
        assert report["outcome"] == "passed", report["says"]
        assert report["compared"]["per_anaesthetic"]["rows_a"] == report["compared"]["per_anaesthetic"]["rows_shared"] > 10
    for name in capability.with_sql():
        report = planted.run_case(capability.fill(name, PLANTED[name]["parameters"]), name, seed=3)
        assert report["outcome"] == "passed", (name, report["says"])
    # The transform is fresh: the keys and dates of the case are not the ones the files hold.
    transform = planted.Transform(5)
    moved = transform.rows({"role_anaesthetic": [["990002400", "P1", "2024-05-01 08:00:00", "2024-05-01 09:00:00"]]})
    (key, patient, start, stop), = moved["role_anaesthetic"]
    assert key != "990002400" and patient != "P1" and start != "2024-05-01 08:00:00" and start < stop
    assert transform.key("990002400") == key and transform.answer(["anaesthetic_key", "minutes"], [["990002400", 10]]) == [[key, 10]]


def test_a_question_that_names_a_planted_key_is_refused_by_the_policy_and_fails_under_the_transform():
    from schemalyser import rolepolicy
    # A question that keeps to the planted keys: a no-op on the files as written, and nothing once the keys are fresh.
    hard = rolemap.AUDIT.read_text(encoding="utf-8").replace("AND  DATEDIFF(day, p.birth_date, a.start_time) < 28",
                                                             "AND  DATEDIFF(day, p.birth_date, a.start_time) < 28 AND a.anaesthetic_key LIKE '990%'")
    found = rolepolicy.check(hard)
    assert "dialect" in found["failed"]
    assert any("compares a key with a literal" in f for r in found["rules"] if r["id"] == "dialect" for f in r["fragments"])
    assert "dialect" in rolepolicy.check("SELECT COUNT(*) AS n FROM role_patient p WHERE p.patient_key IN ('P1', 'P2')")["failed"]
    # Were the policy evaded, the planted stage fails the question, because the key it names is not in the transformed case.
    report = planted.run_case(hard, "neonatal_low_mean_pressure", seed=1)
    assert report["outcome"] == "failed" and "does not give the held-out answer" in report["says"]


def test_a_case_whose_files_no_longer_match_the_register_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "private"
    (root / "saturation_below_90").mkdir(parents=True)
    rows = (planted.PUBLIC_ROWS / "saturation_below_90" / "rows.json").read_bytes()
    expected = (planted.HELD_OUT / "questions" / "saturation_below_90" / "expected.json").read_bytes()
    # A copy of the saturation case under another name in the private root, registered in a register of its own.
    case = root / "saturation_below_90"
    (case / "rows.json").write_bytes(rows)
    (case / "expected.json").write_bytes(expected)
    monkeypatch.setenv("SCHEMALYSER_EXAMINATION", str(root))
    monkeypatch.setattr(planted, "PUBLIC_ROWS", tmp_path / "nowhere")
    register = tmp_path / "register.json"
    entry = planted.register("saturation_below_90", "question", path=register, date="2026-10-14")
    assert set(entry["files"]) == {"rows", "expected"}
    kind, held_rows, held_expected = planted.load_case("saturation_below_90", register)
    assert kind == "question" and set(held_rows) >= {"role_anaesthetic", "role_reading"} and held_expected["question"] == "saturation_below_90"
    (case / "rows.json").write_bytes(rows.replace(b"97", b"96", 1))
    with pytest.raises(planted.CaseError, match="has changed since it was registered on 2026-10-14"):
        planted.load_case("saturation_below_90", register)
    report = planted.run_case("SELECT 1 AS one", "saturation_below_90", register_path=register)
    assert report["outcome"] == "refused" and "no longer matches the register" in report["says"]
    with pytest.raises(planted.CaseError, match="is not in the register"):
        planted.load_case("unknown_case", register)


def test_the_committed_register_names_the_repository_s_cases_as_their_files_are():
    held = planted.read_register()["cases"]
    assert set(held) == {"neonatal_low_mean_pressure", "saturation_below_90", *capability.with_sql()}
    for name, entry in held.items():
        assert entry == planted.register_entry(name, entry["kind"], entry["date"]), name


def test_the_command_runs_a_package_against_a_case_and_registers_one(tmp_path, capsys, monkeypatch):
    assert examination_main.main(["run", str(FIXTURES / "neonatal-low-mean-pressure.question.json"), "--case", "neonatal_low_mean_pressure", "--seed", "4"]) == 0
    out = capsys.readouterr().out
    assert "The package is well formed" in out and "gives the held-out answer of the case neonatal_low_mean_pressure" in out
    monkeypatch.setattr(planted, "REGISTER", tmp_path / "register.json")
    assert examination_main.main(["register", "hypothermia", "--kind", "capability"]) == 0
    assert "registered as a capability case" in capsys.readouterr().out
    assert "hypothermia" in planted.read_register(tmp_path / "register.json")["cases"]


def test_the_workspace_denies_the_private_examiner_root():
    from schemalyser import workspace
    assert workspace._denied("reference/examination/questions/x/rows.json")



# Stage 2, synthetic execution.

from schemalyser.examination import shadow  # noqa: E402


def test_the_six_questions_run_on_the_shadow_at_two_seeds_in_two_orders_and_give_one_answer(tmp_path):
    for fixture in ("neonatal-low-mean-pressure.question.json", "ask-fake-backend.question.json", "ask-fake-backend-capability.question.json"):
        report = shadow.run(FIXTURES / fixture)
        assert report["outcome"] == "passed", (fixture, report["says"])
        assert len(report["runs"]) == 4 and all(r["ran"] and r["rows"] > 0 for r in report["runs"])
    for name in capability.with_sql():
        report = shadow.run(write(tmp_path, name, capability_package(name)))
        assert report["outcome"] == "passed", (name, report["says"])


def test_a_question_without_a_total_order_a_wrong_column_or_an_empty_answer_fails_synthetic_execution():
    # A row chosen among ties: the first reading of each anaesthetic by no order but the key, whose value depends on
    # the order the rows were inserted in.
    ties = ("WITH picked AS (SELECT r.anaesthetic_key, r.value, ROW_NUMBER() OVER (PARTITION BY r.anaesthetic_key ORDER BY r.anaesthetic_key) AS n "
            "FROM role_reading r WHERE r.kind = 'map_cuff') SELECT SUM(p.value) AS first_values FROM picked p WHERE p.n = 1")
    report = shadow.run_sql(ties, seeds=(1,))
    assert report["outcome"] == "failed" and "differs between insertion orders" in report["says"]
    report = shadow.run_sql("SELECT COUNT(*) AS anaesthetics FROM role_anaesthetic a", declared=["patients"], seeds=(1,))
    assert report["outcome"] == "failed" and "declares patients" in report["says"]
    report = shadow.run_sql("SELECT COUNT(*) AS n FROM role_fluid f WHERE f.kind = 'plasma' AND f.volume_ml < 0", seeds=(1,))
    assert report["outcome"] == "failed" and "returns nothing" in report["says"]
    report = shadow.run_sql("SELECT r.nothing FROM role_reading r", seeds=(1,))
    assert report["outcome"] == "failed" and "did not run" in report["says"]
    assert examination_main.main(["run", str(FIXTURES / "ask-fake-backend.question.json")]) == 0



# Stage 3, the three metamorphic checks.

from schemalyser.examination import metamorphic  # noqa: E402

RED_CELLS = ("WITH neonatal AS (SELECT a.anaesthetic_key FROM role_anaesthetic a JOIN role_patient p ON p.patient_key = a.patient_key "
             "WHERE p.is_test = 0 AND a.start_time IS NOT NULL AND DATEDIFF(day, p.birth_date, a.start_time) BETWEEN 0 AND 27)\n"
             "SELECT COUNT(DISTINCT n.anaesthetic_key) AS neonatal_anaesthetics, COUNT(DISTINCT f.anaesthetic_key) AS given_red_cells, "
             "SUM({volume}) AS red_cells_ml FROM neonatal n LEFT JOIN role_fluid f ON f.anaesthetic_key = n.anaesthetic_key AND f.kind = 'red_cells'")
EXAMINABLE = {"cohort": {"grain": "anaesthetic", "age_days": [0, 27], "exclude": ["test_patient", "no_start"]},
              "outcome": {"part": "role_fluid", "kinds": ["red_cells"], "forms": ["amount"], "measure": "sum", "of": "volume_ml",
                          "duplicates": "keep", "amended": "replaced", "absent": "zero"},
              "result": {"statistic": "sum", "over": "anaesthetic", "columns": ["neonatal_anaesthetics", "given_red_cells", "red_cells_ml"]}}


def test_a_correct_question_passes_the_three_checks_and_each_wrong_one_fails_only_its_check():
    # Correct: the amounts summed, each administration counting, and a row that a correction amends replaced by it.
    correct = RED_CELLS.format(volume="CASE WHEN f.volume_form = 'amount' AND NOT EXISTS (SELECT 1 FROM role_fluid g WHERE g.amends_key = f.fluid_key) THEN f.volume_ml END")
    report = metamorphic.run_sql(correct, EXAMINABLE, seed=1, sample=4)
    assert report["outcome"] == "passed", report["says"]
    assert all(report["checks"][c]["outcome"] == "passed" for c in ("unrelated", "duplicates", "moved"))
    # Wrong in one way each.
    no_test_filter = correct.replace("WHERE p.is_test = 0 AND ", "WHERE ")
    report = metamorphic.run_sql(no_test_filter, EXAMINABLE, seed=1, sample=4)
    assert report["outcome"] == "failed" and [c for c, v in report["checks"].items() if v["outcome"] == "failed"] == ["unrelated"], report["says"]
    # Wrong: every red cells row summed, running totals and corrections with the amounts.
    every_row = RED_CELLS.format(volume="f.volume_ml")
    report = metamorphic.run_sql(every_row, EXAMINABLE, seed=1, sample=4)
    assert report["outcome"] == "failed" and [c for c, v in report["checks"].items() if v["outcome"] == "failed"] == ["duplicates"], report["says"]
    assert "a correction that amends a row of role_fluid" in report["says"]
    by_time = ("WITH neonatal AS (SELECT a.anaesthetic_key, a.start_time, a.stop_time FROM role_anaesthetic a JOIN role_patient p ON p.patient_key = a.patient_key "
               "WHERE p.is_test = 0 AND a.start_time IS NOT NULL AND DATEDIFF(day, p.birth_date, a.start_time) BETWEEN 0 AND 27)\n"
               "SELECT COUNT(DISTINCT n.anaesthetic_key) AS neonatal_anaesthetics, COUNT(DISTINCT f.anaesthetic_key) AS given_red_cells, "
               "SUM(CASE WHEN f.volume_form = 'amount' AND NOT EXISTS (SELECT 1 FROM role_fluid g WHERE g.amends_key = f.fluid_key) THEN f.volume_ml END) AS red_cells_ml "
               "FROM neonatal n LEFT JOIN role_fluid f ON f.given_time >= n.start_time AND f.given_time <= n.stop_time AND f.kind = 'red_cells'")
    report = metamorphic.run_sql(by_time, EXAMINABLE, seed=1, sample=4)
    assert report["outcome"] == "failed" and "moved" in [c for c, v in report["checks"].items() if v["outcome"] == "failed"], report["says"]
    assert "by time rather than by key" in report["says"]


def test_the_six_questions_pass_the_metamorphic_checks_on_two_seeds(tmp_path):
    for fixture in ("neonatal-low-mean-pressure.question.json", "ask-fake-backend.question.json", "ask-fake-backend-capability.question.json"):
        for seed in (1, 2):
            report = metamorphic.run(FIXTURES / fixture, seed=seed, sample=3)
            assert report["outcome"] == "passed", (fixture, seed, report["says"])
    for name in capability.with_sql():
        report = metamorphic.run(write(tmp_path, name, capability_package(name)), seed=2, sample=3)
        assert report["outcome"] == "passed", (name, report["says"])
