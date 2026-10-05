"""Tests for roles and for the realistic values the sandbox writes from public reference data."""
import csv
import io
import json
import sys
from pathlib import Path

import pytest

from schemalyser import browser, roles
from schemalyser.catalogue import Catalogue
from schemalyser.rules import SiteRules

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
REALISM = Path(__file__).resolve().parents[1] / "schemalyser" / "realism"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

CATALOGUE = (FIXTURES / "invented-catalogue.csv").read_text()


@pytest.fixture(scope="module")
def run():
    analysis = make_checks.analysis((FIXTURES / "invented-checks.csv").read_text())
    assert "roles.csv" in analysis.pack()
    assert browser.sandbox_start(CATALOGUE.encode(), make_checks.inventory_zip(analysis)) == "ok"
    browser.sandbox_build(400)

    def query(sql):
        result = json.loads(browser.sandbox_run(sql))
        assert result["status"] == "ok", result
        return result["rows"]
    return query


def test_the_reference_files_are_present_and_sound():
    weight = list(csv.DictReader(open(REALISM / "growth_weight.csv")))
    assert len(weight) == 482 and len(list(csv.DictReader(open(REALISM / "growth_stature.csv")))) == 482
    male = {int(r["age_months"]): float(r["m"]) for r in weight if r["sex"] == "1"}
    assert 3 < male[0] < 4 and 9.5 < male[12] < 11 and 30 < male[120] < 34
    vitals = list(csv.DictReader(open(REALISM / "vitals.csv")))
    for measure in ("heart_rate", "respiratory_rate"):
        bands = sorted((int(r["age_from_months"]), int(r["age_to_months"])) for r in vitals if r["measure"] == measure)
        assert bands[0][0] == 0 and all(a[1] == b[0] for a, b in zip(bands, bands[1:]))
    names = [r["name"] for r in csv.DictReader(open(REALISM / "medications.csv"))]
    assert len(names) == len(set(names)) >= 80 and "PROPOFOL" in names
    assert (REALISM / "SOURCES.md").exists()


def test_roles_are_checked_against_the_catalogue_and_the_fixed_list():
    catalogue = Catalogue.from_csv(CATALOGUE)
    rules = SiteRules(roles=[
        {"table": "PERSON_MASTER", "column": "BIRTH_TS", "role": "birth_date"},
        {"table": "PERSON_MASTER", "column": "NO_SUCH_COLUMN", "role": "sex"},
        {"table": "PERSON_MASTER", "column": "SEX_CAT", "role": "astrological_sign"},
        {"table": "OBS_READING", "column": "READ_VALUE", "role": "weight", "unit": "stone"},
        {"table": "OBS_READING", "column": "READ_VALUE", "role": "weight", "unit": "oz",
         "when": {"column": "OBS_TYPE_KEY", "equals": "=HYPERLINK(1)"}},
        "not a rule",
    ])
    found = roles.from_rules(rules, catalogue)
    assert [(r.table, r.column, r.role) for r in found] == [("PERSON_MASTER", "BIRTH_TS", "birth_date")]
    assert roles.from_csv(roles.to_csv(found), catalogue) == found


def test_every_child_is_born_before_their_first_case_and_is_a_child(run):
    (low, high), = run("""SELECT MIN(DATEDIFF(day, p.BIRTH_TS, tc.CASE_DATE)), MAX(DATEDIFF(day, p.BIRTH_TS, tc.CASE_DATE))
                          FROM THEATRE_CASE tc JOIN PERSON_MASTER p ON p.PERSON_KEY = tc.PERSON_KEY""")
    assert int(low) >= 0 and int(high) < 366 * 24
    assert {row[0] for row in run("SELECT DISTINCT SEX_CAT FROM PERSON_MASTER")} == {"1", "2"}


def test_weight_follows_age(run):
    rows = run("""SELECT CASE WHEN DATEDIFF(month, p.BIRTH_TS, r.READ_TS) < 24 THEN 'under two' ELSE 'over eight' END AS band,
                         AVG(CAST(r.READ_VALUE AS float)) / 35.274 AS kg
                  FROM OBS_READING r JOIN OBS_SHEET s ON s.SHEET_KEY = r.SHEET_KEY JOIN VISIT v ON v.VISIT_KEY = s.VISIT_KEY
                       JOIN PERSON_MASTER p ON p.PERSON_KEY = v.PERSON_KEY
                  WHERE r.OBS_TYPE_KEY = '14' AND (DATEDIFF(month, p.BIRTH_TS, r.READ_TS) < 24 OR DATEDIFF(month, p.BIRTH_TS, r.READ_TS) >= 96)
                  GROUP BY CASE WHEN DATEDIFF(month, p.BIRTH_TS, r.READ_TS) < 24 THEN 'under two' ELSE 'over eight' END""")
    kg = {band: float(value) for band, value in rows}
    assert 3 < kg["under two"] < 14 and 20 < kg["over eight"] < 80


def test_observations_and_names_are_plausible(run):
    (low, high), = run("SELECT MIN(CAST(READ_VALUE AS float)), MAX(CAST(READ_VALUE AS float)) FROM OBS_READING WHERE OBS_TYPE_KEY = '8'")
    assert 40 <= float(low) and float(high) <= 200
    (low, high), = run("SELECT MIN(CAST(READ_VALUE AS int)), MAX(CAST(READ_VALUE AS int)) FROM OBS_READING WHERE OBS_TYPE_KEY = '10'")
    assert (int(low), int(high)) == (96, 100)
    names = {row[0] for row in run("SELECT DRUG_LABEL FROM DRUG_DEF")}
    known = {r["name"] for r in csv.DictReader(open(REALISM / "medications.csv"))}
    assert names and names <= known
    (low, high), = run("SELECT MIN(DATEDIFF(minute, ANAES_START_TS, ANAES_STOP_TS)), MAX(DATEDIFF(minute, ANAES_START_TS, ANAES_STOP_TS)) FROM ANAES_RECORD")
    assert 60 <= int(low) and int(high) <= 24 * 60


def test_a_medication_filter_now_finds_rows(run):
    assert int(run("SELECT COUNT(*) FROM DRUG_DEF WHERE DRUG_LABEL LIKE 'PROPOFOL%'")[0][0]) > 0


# Tunable parameters, roles that cannot be applied, repeat anaesthetics and the sentinel date.

from schemalyser import Analysis, tuning  # noqa: E402
from schemalyser.catalogue import Catalogue as _Catalogue  # noqa: E402
from schemalyser.sandbox import Sandbox  # noqa: E402

RULES = json.loads((FIXTURES / "invented-site-rules.json").read_text())
CHECKS = (FIXTURES / "invented-checks.csv").read_text()
AIRWAYS = "SELECT ad.PLACED_TS, ad.REMOVED_TS FROM AIRWAY_DEVICE ad JOIN ANAES_RECORD ar ON ar.ANAES_KEY = ad.ANAES_KEY"


def sandbox_for(rules=None, checks=CHECKS, rows=200, extra=()):
    analysis = Analysis(CATALOGUE, json.dumps(rules if rules is not None else RULES), checks_csv=checks)
    for path in sorted(make_checks.REQUESTS.rglob("*.sql")):
        analysis.add_request(path.relative_to(make_checks.REQUESTS).as_posix(), path.read_text())
    for number, sql in enumerate(extra):
        analysis.add_request(f"extra-{number}.sql", sql)
    sandbox = Sandbox(_Catalogue.from_csv(CATALOGUE), make_checks.inventory_zip(analysis))
    return sandbox, sandbox.build(rows)


def durations(sandbox):
    return sandbox.con.execute("SELECT MIN(date_diff('minute', ANAES_START_TS, ANAES_STOP_TS)), "
                               "MAX(date_diff('minute', ANAES_START_TS, ANAES_STOP_TS)) FROM ANAES_RECORD").fetchone()


def test_the_defaults_are_the_table_of_parameters_and_each_has_a_provenance():
    report = tuning.table()
    assert [row["key"] for row in report] == [p.key for p in tuning.PARAMETERS]
    for row in report:
        assert row["value"] == row["default"] and row["provenance"] == "invented" and row["description"].endswith(".")
    assert tuning.Tuning()["anaesthetic_durations_minutes"] == (60, 70, 80, 90, 105, 120, 140, 165, 195, 240, 320)


def test_only_known_keys_with_sound_numbers_are_accepted():
    accepted = tuning.accept({
        "anaesthetic_durations_minutes": [45, 300], "induction_dose_share": 0.7, "death_share": 0,
        "no_such_parameter": 3, "end_tidal_co2_mean": "38", "shortest_anaesthetic_minutes": True,
        "oldest_age_days": 1e9, "youngest_age_days": 1.5, "placement_window_minutes": [15],
        "temperature_lowest_c": float("nan"), "removal_window_minutes": None,
        # A pair in the wrong order is ignored as a whole.
        "admission_before_least_minutes": 3000, "admission_before_most_minutes": 2000,
    })
    assert accepted == {"anaesthetic_durations_minutes": (45, 300), "induction_dose_share": 0.7, "death_share": 0.0}
    assert tuning.accept(["not", "a", "dictionary"]) == {}
    assert tuning.accept({"anaesthetic_durations_minutes": [60, "=1+1"]}) == {}
    # The file in the inventory is checked again when it is read.
    assert tuning.from_csv(tuning.to_csv(accepted, "2157-11-19")) == (accepted, "2157-11-19")
    hostile = "key,value\nshortest_anaesthetic_minutes,=1+1\ninduction_dose_share,7\nstill_in_place_value,=cmd\n" \
              "death_share,inf\nanaesthetic_durations_minutes,60 x\nno_such_parameter,1\n"
    assert tuning.from_csv(hostile) == ({}, None)


def test_an_override_in_the_site_rules_changes_the_durations_and_an_unsound_one_does_not():
    sandbox, built = sandbox_for(dict(RULES, tuning={"anaesthetic_durations_minutes": [400], "no_such": 1}))
    assert durations(sandbox) == (400, 400) and built["rolesNotApplied"] == []
    row = next(r for r in sandbox.tuning.report() if r["key"] == "anaesthetic_durations_minutes")
    assert row["value"] == (400,) and row["provenance"] == "site rules"
    sandbox, _ = sandbox_for(dict(RULES, tuning={"anaesthetic_durations_minutes": [0, 99999]}))
    low, high = durations(sandbox)
    assert 60 <= low and high <= 320


def test_a_role_that_cannot_be_applied_is_reported_by_name_and_never_with_the_database_error():
    rules = dict(RULES, roles=RULES["roles"] + [
        {"table": "THEATRE_CASE", "column": "CASE_DATE", "role": "medication_name"},
        {"table": "PERSON_MASTER_2", "column": "GEST_WEEKS", "role": "sex"},
    ])
    _, built = sandbox_for(rules)
    assert {"table": "THEATRE_CASE", "column": "CASE_DATE", "role": "medication_name", "reason": "database"} \
        in built["rolesNotApplied"]
    assert {"table": "PERSON_MASTER_2", "column": "GEST_WEEKS", "role": "sex", "reason": "prerequisite"} \
        in built["rolesNotApplied"]
    assert all(set(item) == {"table", "column", "role", "reason"} for item in built["rolesNotApplied"])
    # Without the start and end of the anaesthetic, the timed roles cannot be placed, and they are listed.
    rules = dict(RULES, roles=[r for r in RULES["roles"] if r["role"] not in ("anaesthetic_start", "anaesthetic_stop")])
    _, built = sandbox_for(rules)
    missing = {(item["table"], item["column"], item["role"]) for item in built["rolesNotApplied"]}
    assert ("DRUG_GIVEN", "GIVEN_TS", "administration_time") in missing
    assert ("OBS_READING", "READ_TS", "heart_rate") in missing


def test_each_anaesthetic_of_a_child_with_two_holds_its_own_timed_values():
    # The check results say that there are half as many people as cases, so each child has two anaesthetics.
    lines = []
    for line in CHECKS.splitlines():
        if line.startswith(("column,PERSON_MASTER,PERSON_KEY,", "column,PERSON_MASTER_2,PERSON_KEY,")):
            line = line.replace(",600,600,0,Y", ",300,300,0,Y")
        elif line.startswith(("column,THEATRE_CASE,PERSON_KEY,", "column,VISIT,PERSON_KEY,")):
            line = line.replace(",600,600,0,Y", ",600,300,0,N")
        lines.append(line)
    sandbox, built = sandbox_for(checks="\n".join(lines) + "\n")
    assert built["rolesNotApplied"] == []
    one = lambda sql: sandbox.con.execute(sql).fetchone()  # noqa: E731
    case = "ANAES_RECORD ar JOIN THEATRE_CASE tc ON tc.CASE_KEY = ar.CASE_KEY JOIN PERSON_MASTER p ON p.PERSON_KEY = tc.PERSON_KEY"
    assert one(f"SELECT MIN(n), MAX(n) FROM (SELECT COUNT(*) AS n FROM {case} GROUP BY p.PERSON_KEY)") == (2, 2)
    # The two anaesthetics of most children do not overlap, so values placed in the wrong one would show.
    apart = one(f"""SELECT COUNT(*) FROM {case} JOIN THEATRE_CASE tc2 ON tc2.PERSON_KEY = p.PERSON_KEY AND tc2.CASE_KEY <> tc.CASE_KEY
                    JOIN ANAES_RECORD ar2 ON ar2.CASE_KEY = tc2.CASE_KEY
                    WHERE ar2.ANAES_START_TS > ar.ANAES_STOP_TS OR ar2.ANAES_STOP_TS < ar.ANAES_START_TS""")[0]
    assert apart > one("SELECT COUNT(*) FROM ANAES_RECORD")[0] // 2
    outside = """SELECT COUNT(*) FROM DRUG_GIVEN d JOIN ANAES_RECORD ar ON ar.ANAES_KEY = d.ANAES_KEY
                 WHERE d.GIVEN_TS NOT BETWEEN ar.ANAES_START_TS AND ar.ANAES_STOP_TS"""
    assert one(outside) == (0,) and one("SELECT COUNT(*) FROM DRUG_GIVEN")[0] > 0
    outside = """SELECT COUNT(*) FROM OBS_READING r JOIN OBS_SHEET s ON s.SHEET_KEY = r.SHEET_KEY
                 JOIN ANAES_RECORD ar ON ar.ANAES_KEY = s.ANAES_KEY
                 WHERE r.OBS_TYPE_KEY IN ('5', '8') AND r.READ_TS NOT BETWEEN ar.ANAES_START_TS AND ar.ANAES_STOP_TS"""
    assert one(outside) == (0,)
    # Every child is born before each of their anaesthetics.
    assert one(f"SELECT MIN(date_diff('day', p.BIRTH_TS, ar.ANAES_START_TS)) FROM {case}")[0] >= 0


def test_a_share_of_airways_is_still_in_place_on_the_sentinel_date():
    rules = dict(RULES, sentinelValues=[{"dataType": "varchar", "value": "zzz"},
                                        {"dataType": "datetime", "value": "2157-11-19"}],
                 tuning={"still_in_place_share": 0.5})
    sandbox, built = sandbox_for(rules, extra=[AIRWAYS])
    assert built["rolesNotApplied"] == []
    still, total = sandbox.con.execute("SELECT COUNT(*) FILTER (WHERE REMOVED_TS = TIMESTAMP '2157-11-19'), COUNT(*) "
                                       "FROM AIRWAY_DEVICE").fetchone()
    assert 0.3 * total < still < 0.7 * total
    # Without the share, or without a sentinel date in the rules, every airway is removed during its anaesthetic.
    for rules in (dict(rules, tuning={}), dict(RULES, tuning={"still_in_place_share": 0.5})):
        sandbox, _ = sandbox_for(rules, extra=[AIRWAYS])
        assert sandbox.con.execute("SELECT COUNT(*) FROM AIRWAY_DEVICE ad JOIN ANAES_RECORD ar ON ar.ANAES_KEY = ad.ANAES_KEY "
                                   "WHERE ad.REMOVED_TS > ar.ANAES_STOP_TS").fetchone() == (0,)


def test_the_site_rules_refuse_the_key_that_nothing_reads():
    with pytest.raises(ValueError):
        SiteRules.from_json('{"categoryColumns": {"suffix": "_CAT"}}')
    assert SiteRules.from_json('{"tuning": {"death_share": 0.01}}').tuning == {"death_share": 0.01}


# A skewed fan-out: most children have one hospital visit, and so one anaesthetic, and a few have many.

FANOUT = {"1 row": 300, "2 rows": 60, "3 to 5 rows": 30, "6 to 10 rows": 10}


def skewed_checks():
    """The invented check results, with people who have several visits and a fanout result that says so."""
    lines = []
    for line in CHECKS.splitlines():
        if line.startswith(("column,THEATRE_CASE,PERSON_KEY,", "column,VISIT,PERSON_KEY,")):
            line = line.replace(",600,600,0,Y", ",600,400,0,N")
        lines.append(line)
    lines += [f"fanout,VISIT,PERSON_KEY,{band},PERSON_MASTER.PERSON_KEY,{count},,," for band, count in FANOUT.items()]
    return "\n".join(lines) + "\n"


def test_a_fanout_result_skews_the_anaesthetics_of_each_child_and_the_times_still_agree():
    sandbox, built = sandbox_for(checks=skewed_checks(), rows=600)
    assert built["rolesNotApplied"] == []
    one = lambda sql: sandbox.con.execute(sql).fetchone()  # noqa: E731
    case = "ANAES_RECORD ar JOIN THEATRE_CASE tc ON tc.CASE_KEY = ar.CASE_KEY JOIN PERSON_MASTER p ON p.PERSON_KEY = tc.PERSON_KEY"
    counts = dict(sandbox.con.execute(f"""SELECT CASE WHEN n = 1 THEN '1 row' WHEN n = 2 THEN '2 rows' WHEN n <= 5 THEN '3 to 5 rows'
                                          WHEN n <= 10 THEN '6 to 10 rows' ELSE '11 or more rows' END, COUNT(*)
                                          FROM (SELECT p.PERSON_KEY, COUNT(*) AS n FROM {case} GROUP BY p.PERSON_KEY) GROUP BY 1""").fetchall())
    total, asked = sum(counts.values()), sum(FANOUT.values())
    for band, count in FANOUT.items():
        assert abs(counts.get(band, 0) / total - count / asked) < 0.03, (band, counts)
    assert "11 or more rows" not in counts
    # A case's child is always the child of its hospital visit.
    assert one("SELECT COUNT(*) FROM THEATRE_CASE tc JOIN VISIT v ON v.VISIT_KEY = tc.VISIT_KEY "
               "WHERE v.PERSON_KEY <> tc.PERSON_KEY") == (0,)
    # Every child is born before each of their anaesthetics, and dies, if at all, after the last of them.
    assert one(f"SELECT COUNT(*) FROM {case} WHERE p.BIRTH_TS >= ar.ANAES_START_TS") == (0,)
    assert one(f"SELECT COUNT(*) FROM {case} JOIN PERSON_MASTER_2 d ON d.PERSON_KEY = p.PERSON_KEY "
               "WHERE d.DEATH_TS < ar.ANAES_STOP_TS") == (0,)
    # Each anaesthetic's own timed values fall inside it, and its hospital visit around it.
    assert one("""SELECT COUNT(*) FROM DRUG_GIVEN d JOIN ANAES_RECORD ar ON ar.ANAES_KEY = d.ANAES_KEY
                  WHERE d.GIVEN_TS NOT BETWEEN ar.ANAES_START_TS AND ar.ANAES_STOP_TS""") == (0,)
    assert one("""SELECT COUNT(*) FROM OBS_READING r JOIN OBS_SHEET s ON s.SHEET_KEY = r.SHEET_KEY
                  JOIN ANAES_RECORD ar ON ar.ANAES_KEY = s.ANAES_KEY
                  WHERE r.OBS_TYPE_KEY IN ('5', '8') AND r.READ_TS NOT BETWEEN ar.ANAES_START_TS AND ar.ANAES_STOP_TS""") == (0,)
    assert one("""SELECT COUNT(*) FROM VISIT v JOIN THEATRE_CASE tc ON tc.VISIT_KEY = v.VISIT_KEY
                  JOIN ANAES_RECORD ar ON ar.CASE_KEY = tc.CASE_KEY
                  WHERE NOT (v.ADMIT_TS < ar.ANAES_START_TS AND v.DISCH_TS > ar.ANAES_STOP_TS)""") == (0,)
    # The fan-out of each parent-child join reports where it came from.
    sources = {(f["child_table"], f["child_column"], f["parent_table"]): f["provenance"] for f in sandbox.fanout}
    assert sources[("VISIT", "PERSON_KEY", "PERSON_MASTER")] == tuning.FANOUT_FROM_CHECKS
    assert sources[("DRUG_GIVEN", "ROUTE_CAT", "LK_ROUTE")] == tuning.FANOUT_UNIFORM
    # A case's patient follows the result through the case's hospital visit.
    assert sources[("THEATRE_CASE", "PERSON_KEY", "PERSON_MASTER")] == tuning.FANOUT_DERIVED
    # The same results build the same database.
    again, _ = sandbox_for(checks=skewed_checks(), rows=600)
    query = "SELECT PERSON_KEY, COUNT(*) FROM VISIT GROUP BY 1 ORDER BY 1"
    assert again.con.execute(query).fetchall() == sandbox.con.execute(query).fetchall()


def test_without_a_fanout_result_every_parent_has_the_same_number_of_children():
    sandbox, _ = sandbox_for(rows=200)
    assert sandbox.lineage is None
    assert sandbox.con.execute("SELECT MIN(n), MAX(n) FROM (SELECT PERSON_KEY, COUNT(*) AS n FROM VISIT GROUP BY 1)").fetchone() == (1, 1)
    assert {f["provenance"] for f in sandbox.fanout} == {tuning.FANOUT_UNIFORM}


def test_the_assignment_follows_the_bands_and_fits_the_rows():
    import random
    from schemalyser.sandbox import skewed
    bands = list(FANOUT.items())
    for children, parents in ((600, 600), (600, 200), (2400, 600), (37, 1000)):
        drawn = skewed(bands, children, parents, random.Random(5))
        assert len(drawn) == children and all(0 <= p < parents for p in drawn)
    drawn = skewed(bands, 6000, 6000, random.Random(5))
    sizes = {}
    for parent in drawn:
        sizes[parent] = sizes.get(parent, 0) + 1
    ones = sum(1 for n in sizes.values() if n == 1) / len(sizes)
    assert abs(ones - 0.75) < 0.02 and max(sizes.values()) <= 10
    assert skewed([("1 row", 0)], 10, 10, random.Random(1)) is None


def test_the_harness_gives_its_stand_in_a_skewed_design_and_the_sandbox_follows_it():
    from schemalyser import harness
    card = harness.scorecard(make_checks.WORLD, rows=300, include_fanout=True)
    assert "fanout" in card.split("CHECKS")[1].split("\n")[1]
    assert "checks the stand-in database could not answer: 0" in card
    line = next(row for row in card.splitlines() if row.startswith("  VISIT.PERSON_KEY -> PERSON_MASTER.PERSON_KEY"))
    asked, built = (part.split(";")[0].split("(")[0].split() for part in line.split("asked ")[1].split("built "))
    assert asked[0] != "100" and all(abs(int(a) - int(b)) <= 3 for a, b in zip(asked, built))
    assert line.endswith(f"({tuning.FANOUT_FROM_CHECKS})")


# The mean pressures that the generator adds as readings of their own.

def test_a_cuff_mean_is_written_beside_each_charted_pressure_and_agrees_with_it():
    sandbox, built = sandbox_for(rows=200)
    assert built["rolesNotApplied"] == []
    con = sandbox.con
    charted = con.execute("SELECT SHEET_KEY, SEQ, READ_TS, READ_VALUE FROM OBS_READING WHERE OBS_TYPE_KEY = '5' "
                          "AND READ_VALUE LIKE '%/%' ORDER BY ALL").fetchall()
    means = con.execute("SELECT SHEET_KEY, SEQ, READ_TS, CAST(READ_VALUE AS INTEGER) FROM OBS_READING "
                        "WHERE OBS_TYPE_KEY = '51' ORDER BY ALL").fetchall()
    assert charted and len(means) == len(charted)
    for (sheet, seq, at, text), mean in zip(charted, means):
        systolic, diastolic = (float(part) for part in text.split("/"))
        expected = min(120, max(20, round(diastolic + (systolic - diastolic) / 3)))
        assert mean == (sheet, seq, at, expected)
    # The rows that the generator adds are counted among the rows that it built.
    assert built["rows"] == sum(con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in sandbox.tables)


def test_an_arterial_mean_is_charted_every_minute_through_a_share_of_the_anaesthetics():
    sandbox, _ = sandbox_for(rows=400)
    con = sandbox.con
    found = con.execute("""
        SELECT a.ANAES_KEY, COUNT(*), MIN(r.READ_TS) = a.ANAES_START_TS, MAX(r.READ_TS) <= a.ANAES_STOP_TS,
               MAX(date_diff('minute', a.ANAES_START_TS, r.READ_TS)) + 1 = COUNT(*), MIN(CAST(r.READ_VALUE AS INTEGER)),
               MAX(CAST(r.READ_VALUE AS INTEGER))
        FROM   OBS_READING r JOIN OBS_SHEET s ON s.SHEET_KEY = r.SHEET_KEY JOIN ANAES_RECORD a ON a.ANAES_KEY = s.ANAES_KEY
        WHERE  r.OBS_TYPE_KEY = '52' GROUP BY a.ANAES_KEY, a.ANAES_START_TS, a.ANAES_STOP_TS""").fetchall()
    anaesthetics = con.execute("SELECT COUNT(*) FROM ANAES_RECORD").fetchone()[0]
    assert 0.08 < len(found) / anaesthetics < 0.25
    for _, count, from_start, within, every_minute, low, high in found:
        assert count > 30 and from_start and within and every_minute and 20 <= low and high <= 120
    # A visible minority of the readings lies below 40, so that a question about low pressure has an answer.
    low, total = con.execute("SELECT SUM(CASE WHEN CAST(READ_VALUE AS INTEGER) < 40 THEN 1 ELSE 0 END), COUNT(*) "
                             "FROM OBS_READING WHERE OBS_TYPE_KEY IN ('51', '52')").fetchone()
    assert 0 < low < total / 2
    # A reading type that the generator adds is not drawn among the listed values, so no other row holds its code.
    assert con.execute("SELECT COUNT(*) FROM OBS_READING WHERE OBS_TYPE_KEY = '52' AND READ_TS IS NULL").fetchone()[0] == 0


def test_the_generator_adds_no_mean_pressure_unless_the_check_results_list_its_code():
    sandbox, built = sandbox_for(checks=None, rows=200)
    assert sandbox.con.execute("SELECT COUNT(*) FROM OBS_READING WHERE OBS_TYPE_KEY IN ('51', '52')").fetchone()[0] == 0
    missing = {(item["role"], item["reason"]) for item in built["rolesNotApplied"]}
    assert {("cuff_mean_pressure", "prerequisite"), ("arterial_mean_pressure", "prerequisite")} <= missing
    # The share of anaesthetics with an arterial line is a tunable parameter, which the site rules may set.
    rules = dict(RULES, tuning={"arterial_line_share": 0})
    sandbox, _ = sandbox_for(rules, rows=200)
    assert sandbox.con.execute("SELECT COUNT(*) FROM OBS_READING WHERE OBS_TYPE_KEY = '52'").fetchone()[0] == 0
    assert tuning.BY_KEY["arterial_line_share"].default == 0.15
