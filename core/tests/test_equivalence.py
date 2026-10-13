"""Equivalence of two schemas on a stand-in: the invented world's hand-written map against a schema the page built."""
from pathlib import Path

from schemalyser import describe, equivalence, release
from schemalyser.hospital import InventedHospital, files_from

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
DATE = "2026-10-14"
AUDIT = ROOT / "core" / "schemalyser" / "rolemodel" / "neonatal_low_mean_pressure.sql"


def _page_schema(tmp_path):
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary((FIXTURES / "dictionary" / "invented-dictionary.csv").read_bytes(), (FIXTURES / "dictionary" / "invented-tables.csv").read_bytes(),
                      {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    s.choose_codes("role_reading.kind", {"52": "map_arterial", "51": "map_cuff"}, DATE)
    folder = tmp_path / "map"
    folder.mkdir()
    for name, data in s.folder_files(date=DATE).items():
        if name.startswith("map/"):
            (folder / name[4:]).write_bytes(data)
    return folder


def test_two_schemas_are_compared_part_by_part_and_by_a_question_s_answer(tmp_path):
    built = _page_schema(tmp_path)
    hospital = InventedHospital(files_from(FIXTURES / "hospital", FIXTURES / "invented-catalogue.csv"))
    map_a = release.read_schema(None, schema=built)
    map_b = release.read_schema(None, schema=FIXTURES / "map")
    found = equivalence.compare_schemas(hospital, map_a, map_b, [("neonatal", AUDIT.read_text())])
    patients = found["parts"]["role_patient"]
    assert patients["keys_a"] == patients["keys_b"] == patients["keys_shared"] > 0 and patients["keys_only_a"] == 0
    anaesthetics = found["parts"]["role_anaesthetic"]
    assert anaesthetics["keys_shared"] > 0
    readings = found["parts"]["role_reading"]
    assert readings["rows_a"] > 0 and readings["rows_b"] > 0
    # Parts the hand-written map does not supply are said to be the page's alone, and the question gives one answer each.
    assert found["parts"]["role_technique"] == {"only": "a"} and "rows_a" in found["parts"]["role_drug"]
    answer = found["questions"]["neonatal"]
    assert "rows_a" in answer and "same" in answer
    lines = equivalence.summary(found)
    assert any(line.startswith("Patients: ") and "keys in both" in line for line in lines)
    assert any(line.startswith("neonatal: ") for line in lines)
    assert not any("PERSON_MASTER" in line for line in lines)


def test_the_command_runs_and_names_nothing_unless_asked(tmp_path, capsys):
    built = _page_schema(tmp_path)
    assert equivalence.main([str(built), str(FIXTURES / "map"), "--hospital", str(FIXTURES / "hospital"),
                             "--catalogue", str(FIXTURES / "invented-catalogue.csv"), "--question", str(AUDIT)]) == 0
    out = capsys.readouterr().out
    assert "Patients: " in out and "neonatal_low_mean_pressure: " in out and "PERSON_MASTER" not in out
    assert equivalence.main([str(tmp_path / "missing"), str(FIXTURES / "map"), "--hospital", str(FIXTURES / "hospital"),
                             "--catalogue", str(FIXTURES / "invented-catalogue.csv")]) == 1


def test_an_answer_that_gives_a_row_twice_where_the_other_gives_it_once_is_not_the_same_answer():
    columns = ["kind", "value"]
    twice = (columns, [["map_arterial", "45"], ["map_arterial", "45"], ["map_cuff", "50"]])
    once = (columns, [["map_arterial", "45"], ["map_cuff", "50"]])
    found = equivalence.compare_answers(twice, once)
    assert found == {"rows_a": 3, "rows_b": 2, "rows_shared": 2, "same": False}
    assert equivalence.compare_answers(twice, (columns, list(reversed(twice[1]))))["same"]
    assert not equivalence.compare_answers(once, (["value", "kind"], once[1]))["same"]
    # A part's rows are compared the same way.
    part = equivalence.compare_rows(twice, once, ["kind"])
    assert (part["rows_a"], part["rows_b"], part["rows_shared"], part["keys_shared"]) == (3, 2, 2, 2)


def test_a_number_that_two_views_convert_by_different_arithmetic_is_the_same_value():
    # One view divides days by seven and the other multiplies by a factor; the stand-in gives both back as text that
    # differs in the last digit, and a decimal column pads its zeros. Neither is a difference in what the record says.
    columns = ["patient_key", "gestation_weeks", "birth_weight_grams"]
    a = (columns, [["1", "37.14285714285714", "629.075405"], ["2", "24.0", "1006.1237550000001"]])
    b = (columns, [["1", "37.142857142857146", "629.07540500"], ["2", "24.0", "1006.12375500"]])
    assert equivalence.compare_answers(a, b)["same"]
    assert equivalence.compare_rows(a, b, ["patient_key"])["rows_shared"] == 2
    # A key that happens to hold digits is not a number, and a different one stays different.
    assert not equivalence.compare_answers((columns, [["01", "24.0", "1.0"]]), (columns, [["1", "24.0", "1.0"]]))["same"]
    assert not equivalence.compare_answers((columns, [["1", "24.1", "1.0"]]), (columns, [["1", "24.0", "1.0"]]))["same"]
