"""The source query restructured to start from the cohort, and the names of the concepts."""
import re
import shutil
import sys
from pathlib import Path

import pytest

from schemalyser import concepts, target
from schemalyser.catalogue import Catalogue
from schemalyser.translate import to_duckdb

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CONVERSION = FIXTURES / "conversion"
TARGETS = FIXTURES / "targets"
ATHENA = ROOT / "reference" / "athena"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402


@pytest.fixture(scope="module")
def answers():
    """Each invented target, run on the synthetic rows with every planted scenario, with its two source queries."""
    found = {}
    for path in sorted(TARGETS.glob("*.sql")):
        sql = path.read_text()
        run = target.run(make_checks.WORLD, CONVERSION, sql, rows=300)
        catalogue = run["conversion"].sandbox.catalogue
        found[path.stem] = (sql, run, target.source_query(CONVERSION, sql, catalogue, target_name=path.stem, blank=False),
                            target.source_draft(CONVERSION, sql, catalogue, blank=False))
    return found


def _rows(run, sql):
    converted = run["conversion"]
    return sorted(map(str, converted.con.execute(to_duckdb(sql, converted.sandbox.date_columns)[0]).fetchall()))


def test_every_invented_target_is_restructured_and_gives_exactly_the_same_answer(answers):
    for name, (sql, run, restructured, present) in answers.items():
        assert restructured["restructured"], (name, restructured["reason"])
        assert run["scenarios"]["count"] > 0
        assert _rows(run, restructured["sql"]) == _rows(run, present) == sorted(map(str, run["rows"])), name
        assert restructured["lines"] < len(present.splitlines())


def test_the_neonatal_query_reads_only_what_the_answer_needs_and_starts_from_the_cohort(answers):
    sql, run, restructured, _ = answers["neonatal_low_mean_pressure"]
    text = restructured["sql"]
    catalogue = run["conversion"].sandbox.catalogue
    read = {t for t in re.findall(r"(?:FROM|JOIN) ([A-Z_][A-Z0-9_]*) AS", text) if catalogue.table(t) is not None}
    needed = target.answer_dependencies(CONVERSION, sql, catalogue)["tables"]
    assert read and read <= needed and "WARD_DEF" not in read and "STAFF_MASTER" not in read
    assert "DENSE_RANK" not in text and "This query starts from the cohort of the question" in text
    # Every remaining ROW_NUMBER numbers within a key, or lies in the step that the cohort restricts.
    ctes = {m.group(1): m.start() for m in re.finditer(r"^(\w+) AS \(", text, re.M)}
    body = lambda name: text[ctes[name]:min([p for p in ctes.values() if p > ctes[name]] + [len(text)])]  # noqa: E731
    reading_step = next(n for n in ctes if n.startswith("step_") and "OBS_READING" in body(n))
    assert "IN (\n" in body(reading_step) and "FROM q22_neonatal" in body(reading_step)
    assert "r.OBS_TYPE_KEY IN ('51', '52')" in body(reading_step)
    for name in ctes:
        for window in re.findall(r"ROW_NUMBER\(\) OVER \((.*?)\)", body(name), re.S):
            assert "PARTITION BY" in window or name == reading_step, name
    # The cohort is worked out before the readings are read.
    assert ctes["q22_neonatal"] < ctes[reading_step]
    # No step joins another on a key cast to text, or on a number made only for the join.
    assert "CAST(tc.VISIT_KEY" not in text
    assert "5000000000 AS visit_detail_id" not in text and "5000000000 AS procedure_occurrence_id" not in text
    # The answer's small counts are blanked in the form that the page offers.
    safe = target.source_query(CONVERSION, sql, catalogue, target_name="neonatal_low_mean_pressure")
    assert "BETWEEN 1 AND 4 THEN NULL" in safe["sql"]
    assert _rows(run, safe["sql"]) == sorted(map(str, [tuple(None if i and v is not None and 1 <= v <= 4 else v
                                                              for i, v in enumerate(row)) for row in run["rows"]]))


def test_a_query_that_cannot_be_restructured_falls_back_and_says_why(answers):
    _, run, _, _ = answers["neonatal_low_mean_pressure"]
    catalogue = run["conversion"].sandbox.catalogue
    listing = ("SELECT m.measurement_id, m.value_as_number FROM omop.measurement m "
               "WHERE m.measurement_concept_id = 21490852")
    found = target.source_query(CONVERSION, listing, catalogue, target_name="listing", blank=False)
    assert not found["restructured"] and "still numbers the rows of a whole table" in found["reason"]
    assert "could not restructure this query to start from the cohort" in found["sql"]
    assert "not suitable to run on a large database" in found["sql"]
    assert _rows(run, found["sql"]) == _rows(run, target.source_draft(CONVERSION, listing, catalogue, blank=False))


def test_the_concepts_are_named_where_a_person_is_asked_about_them(tmp_path):
    assert concepts.named(CONVERSION, 21490852) == "Invasive Mean blood pressure (concept 21490852)"
    assert concepts.named(CONVERSION, 99999999) == "the concept 99999999"
    assert concepts.named(tmp_path, 21490852) == "the concept 21490852"
    rows, traced = target.checklist(make_checks.WORLD, CONVERSION, (TARGETS / "neonatal_low_mean_pressure.sql").read_text(),
                                    name="neonatal_low_mean_pressure")
    assert "(Invasive Mean blood pressure, concept 21490852)" in traced["questions"]
    item = {r["question_id"]: r for r in rows}["codes-measurement.measurement_concept_id-21492241"]
    assert "Mean blood pressure by Noninvasive (concept 21492241)" in item["question"]
    spec = target.specification(CONVERSION, (TARGETS / "neonatal_low_mean_pressure.sql").read_text(), rows, traced,
                                Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text()), "n")
    assert "Invasive Mean blood pressure, concept 21490852" in spec
    # A name that is not plain is left out.
    folder = tmp_path / "conversion"
    folder.mkdir()
    (folder / concepts.FILE).write_text("concept_id,concept_name,vocabulary_id\n1,\"two\nlines\",X\n2,$(Injected),X\n3,Plain,X\n")
    assert concepts.names(folder) == {3: "Plain"}


@pytest.mark.skipif(not (ATHENA / "CONCEPT.csv").exists(), reason="no Athena download is present")
def test_the_concept_names_are_those_of_the_athena_download(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    (folder / concepts.FILE).unlink()
    count = concepts.write(folder, ATHENA, sorted(TARGETS.glob("*.sql")))
    assert count > 20
    assert (folder / concepts.FILE).read_text() == (CONVERSION / concepts.FILE).read_text()
