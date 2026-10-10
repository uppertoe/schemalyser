"""The saved hospital schema as the evidence store that docs/contract.md describes, on the invented dictionary only.

Each save is a new immutable version that names the version it was made from and the contract it was made against.
The journal only ever grows: a query offered again, a result pasted again and a confirmation withdrawn each append an
entry that names the one it supersedes. Every binding, link and code translation carries five dimensions of evidence
kept apart, each with what established it and the hashes of what it rested on, so that a change to any of them leaves
that evidence stale with its reason, and readiness is derived from them and never stored. A route through several
tables is saved as a named normalisation, and the result of an evidence request returns through the import, which
checks it against the request before anything is recorded.
"""
import json
import zipfile
import io

import pytest

from schemalyser import browser, describe, evidence, feasibility, normalise, rolemap, roleshadow
from schemalyser.describe import __main__ as describe_main
from test_describe import DATE, DICTIONARY, TABLES, tables_result

PATH = {"form": "path", "about": "role_anaesthetic.patient_key", "column": "PERSON_KEY",
        "steps": [{"from": "CASE_KEY", "table": "THEATRE_CASE", "to": "CASE_KEY"}, {"from": "VISIT_KEY", "table": "VISIT", "to": "VISIT_KEY"}]}
# The route of a drug is a kind in version 1.1, so rows joined into one text are tried on the size of a device.
JOINED = {"form": "joined", "about": "role_device.size", "on_table": "AIRWAY_DEVICE", "on_column": "ANAES_KEY", "table": "ANAES_EVENT",
          "link": "ANAES_KEY", "text": "EVENT_TYPE_KEY", "order": "SEQ", "separator": " "}
PATIENT_LINK = "role_anaesthetic.patient_key -> role_patient.patient_key"


def fresh():
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    return s


def opened(files):
    s = describe.Describe()
    s.version = "test"
    s.restore(files)
    return s


# A6: versions and the journal.

def test_each_save_is_a_new_version_that_names_its_parent_the_time_and_the_contract_it_was_made_against():
    s = fresh()
    first = roleshadow.save(s, DATE)
    settings = json.loads(first["settings.json"])
    assert settings["schema_id"] == evidence.content_id(first)[:16] == settings["content_sha256"][:16]
    assert settings["parent_id"] is None and settings["lineage"] == [] and settings["saved"]
    assert settings["contract"] == {"version": rolemap.contract()["version"], "parts": rolemap.part_hashes()}
    # The file's name carries the version, so that no save reuses the name of another.
    assert s.file_name() == f"hospital-schema-{settings['schema_id']}.schemalyser.zip"
    s.confirm("role_patient.birth_date", "yes", date=DATE)
    second = json.loads(roleshadow.save(s, DATE)["settings.json"])
    assert second["schema_id"] != settings["schema_id"] and second["parent_id"] == settings["schema_id"]
    assert second["lineage"] == [settings["schema_id"]]
    # A sitting opened from a version carries on from it, and its next save names it as the parent.
    again = opened(s.folder_files(DATE))
    assert again.identity["schema_id"] == second["schema_id"]
    again.confirm("role_patient.death_date", "not sure", date=DATE)
    third = json.loads(roleshadow.save(again, DATE)["settings.json"])
    assert third["parent_id"] == second["schema_id"] and third["lineage"] == [settings["schema_id"], second["schema_id"]]


def test_the_journal_only_ever_grows_and_a_query_or_result_given_again_supersedes_the_earlier_entry():
    s = fresh()
    s.tables_query("5")
    s.read_tables(tables_result())
    before = s.log.entries()
    # The same query offered again is the same offer, and appends nothing.
    s.tables_query("5")
    assert s.log.entries() == before
    # A result pasted again is a new entry that names the one it replaces, which stays as it was.
    s.read_tables(tables_result({"OBS_READING": 25_000_000}))
    after = s.log.entries()
    assert after[:len(before)] == before
    results = [e for e in after if e["kind"] == "result returned"]
    assert len(results) == 2 and results[1]["supersedes"] == results[0]["id"]
    # A count offered again with different text supersedes the earlier offer.
    s.count_queries(2024)
    s.confirm("role_anaesthetic.patient_key", "no", "THEATRE_CASE.PERSON_KEY", date=DATE)
    s.count_queries(2024)
    offers = [e for e in s.log.entries() if e["kind"] == "query offered" and e["payload"]["name"] == "count-coverage_by_year"]
    assert len(offers) == 2 and offers[1]["supersedes"] == offers[0]["id"]
    # Every entry has the fields of the contract, with its scope.
    for entry in s.log.entries():
        assert set(entry) == {"id", "sequence", "time", "kind", "actor", "provenance", "scope", "supersedes", "payload"}
        assert entry["provenance"] in evidence.PROVENANCES and entry["scope"]["hospital"] == "not recorded"
        assert "schema_id" in entry["scope"]
    assert [e["sequence"] for e in s.log.entries()] == list(range(1, len(s.log) + 1))
    # The journal refuses an entry whose order or content has been changed.
    entries = s.log.entries()
    entries[2]["sequence"] = 9
    with pytest.raises(evidence.EvidenceError):
        evidence.Journal(entries)
    with pytest.raises(evidence.EvidenceError):
        s.log.append("answer", {}, actor="a", provenance="a guess", scope={"hospital": "x", "schema_id": None})


def test_a_withdrawn_confirmation_stays_in_the_journal_and_the_entry_that_withdraws_it_names_it():
    s = fresh()
    s.confirm("role_patient.is_test", "yes", date=DATE)
    yes = s.log.latest("answer", about="role_patient.is_test")
    roleshadow.correction_keep(s, {"form": "derived", "about": "role_patient.is_test", "table": "PERSON_MASTER",
                       "column": "TEST_PERSON_FLAG", "derive": {"form": "flag", "values": ["Y"]}}, date=DATE)
    kept = s.log.latest("correction kept", about="role_patient.is_test")
    assert kept["supersedes"] == yes["id"] and s.log.get(yes["id"]) == yes
    assert [c["entry"] for c in s.confirmations if c["attribute"] == "role_patient.is_test"] == [kept["id"]]
    # A count's period is its scope: the years that the coverage by year returned.
    s.count_queries(2024)
    s.read_count("coverage_by_year", "start_year\tanaesthetics\twith_patient\twith_birth_date\twith_death_date\ttest_patients\t"
                 "with_stop\tstop_before_start\n2023\t400\t400\t400\t20\t0\t390\t0\n2024\t300\t300\t300\t10\t0\t290\t0\n", DATE)
    result = s.log.latest("result returned", name="count-coverage_by_year")
    assert result["scope"]["period"] == {"from": 2023, "to": 2024}


# A5 and A24: the dimensions, the actor, and invalidation binding by binding.

def test_each_binding_carries_five_dimensions_each_set_only_by_what_establishes_it():
    s = fresh()
    held = s.dimensions_of("bindings", "role_patient.birth_date")
    assert set(held) == set(evidence.DIMENSIONS) and all(v is None for v in held.values())
    # Saving writes nothing that was not recorded: no test has run, so nothing is tested, and nothing is present.
    s.folder_files(DATE)
    assert all(v is None for v in s.dimensions_of("bindings", "role_patient.birth_date").values())
    s.confirm("role_patient.birth_date", "yes", date=DATE, actor="Dr A. Example")
    confirmed = s.dimensions["bindings"]["role_patient.birth_date"]["confirmed"]
    answer = s.log.latest("answer", about="role_patient.birth_date")
    assert confirmed["by"] == "Dr A. Example" == answer["actor"] and confirmed["entry"] == answer["id"] and confirmed["date"] == DATE
    # Present is set by the result of the tables and columns query, which it names.
    s.tables_query()
    s.read_tables(tables_result())
    present = s.dimensions["bindings"]["role_patient.birth_date"]["present"]
    assert s.log.get(present["entry"])["payload"]["name"] == "tables-and-columns"
    # Tested is set by a test run on made-up rows, with its run id.
    run = roleshadow.test(s, DATE)
    tested = s.dimensions["bindings"]["role_patient.birth_date"]["tested"]
    assert tested["entry"] == run["id"] and tested["run"] == run["payload"]["run"] and tested["outcome"] == "passed"
    assert s.dimensions["links"][PATIENT_LINK]["tested"]["outcome"] == "passed"
    # Reconciled and validated are not set by anything on this page alone, and the readiness is derived, never stored.
    assert s.dimensions["bindings"]["role_patient.birth_date"]["reconciled"] is None
    assert s.dimensions["bindings"]["role_patient.birth_date"]["validated"] is None
    assert s.readiness()["parts"]["role_patient"]["reached"] == "runs"
    assert "readiness" not in json.loads(s.folder_files(DATE)["settings.json"])


def test_a_changed_binding_makes_its_own_evidence_and_its_link_stale_and_leaves_the_others_alone():
    s = fresh()
    s.tables_query()
    s.read_tables(tables_result())
    s.confirm("role_patient.birth_date", "yes", date=DATE)
    s.confirm("role_anaesthetic.patient_key", "yes", date=DATE)
    roleshadow.test(s, DATE)
    assert not s.stale_evidence()
    s.confirm("role_anaesthetic.patient_key", "no", "THEATRE_CASE.PERSON_KEY", date=DATE)
    stale = {(e["subject"], e["dimension"]): e["reasons"] for e in s.stale_evidence()}
    assert stale[("role_anaesthetic.patient_key", "tested")] == ["the binding changed"]
    assert stale[(PATIENT_LINK, "tested")] == ["the link changed"]
    assert not any(subject.startswith("role_patient.") for subject, _ in stale)
    # The new binding is confirmed and present again from the same result, and its part no longer runs until tested.
    held = s.dimensions_of("bindings", "role_anaesthetic.patient_key")
    assert not held["confirmed"]["stale"] and not held["present"]["stale"]
    assert s.readiness()["parts"]["role_anaesthetic"]["runs"] is None and s.readiness()["parts"]["role_patient"]["runs"] == DATE
    # The page's item shows the dimensions with the reasons.
    items = {i["about"]: i for r in s.view()["roles"] for i in r["items"]}
    assert items["role_anaesthetic.patient_key"]["dimensions"]["tested"]["stale"] == ["the binding changed"]


def test_the_actor_the_page_passes_is_recorded_and_none_passed_is_recorded_as_not_recorded():
    s = fresh()
    browser._describe = s
    reply = json.loads(browser.describe_confirm(json.dumps({"about": "role_patient.birth_date", "answer": "yes", "actor": "Dr B"})))
    assert reply["ok"] and s.log.latest("answer", about="role_patient.birth_date")["actor"] == "Dr B"
    browser.describe_confirm(json.dumps({"about": "role_patient.death_date", "answer": "yes"}))
    assert s.log.latest("answer", about="role_patient.death_date")["actor"] == "not recorded"
    s.choose_codes("role_reading.kind", {"52": "map_arterial"}, DATE)
    assert s.log.latest("codes chosen", key="role_reading.kind")["actor"] == "not recorded"
    browser._describe = None


def test_a_change_to_the_contract_marks_the_evidence_of_the_changed_part_stale(monkeypatch):
    s = fresh()
    s.confirm("role_patient.birth_date", "yes", date=DATE)
    files = roleshadow.save(s, DATE)
    changed = dict(rolemap.part_hashes(), role_patient="0" * 16)
    monkeypatch.setattr(rolemap, "part_hashes", lambda model=None: changed)
    again = opened(files)
    assert again.restored["contract_changed"] == ["role_patient"] and again.view()["schema"]["contract"]["changed"] == ["role_patient"]
    stale = {(e["subject"], e["dimension"]): e["reasons"] for e in again.stale_evidence()}
    assert stale[("role_patient.birth_date", "confirmed")] == ["the contract changed"]
    assert stale[(PATIENT_LINK, "tested")] == ["the contract changed"]
    assert not any(subject.startswith("role_reading") for subject, _ in stale)
    assert again.readiness()["parts"]["role_patient"]["runs"] is None and again.readiness()["parts"]["role_reading"]["runs"] == DATE


# A14: named normalisations.

def test_a_route_through_several_tables_and_rows_joined_into_one_text_are_saved_as_named_normalisations():
    s = fresh()
    roleshadow.correction_keep(s, PATH, date=DATE)
    roleshadow.correction_keep(s, JOINED, date=DATE)
    files = roleshadow.save(s, DATE)
    data = json.loads(files["map/map.json"])
    binding = data["roles"]["role_anaesthetic"]["columns"]["patient_key"]["binding"]
    assert binding["normalisation"] == "anaesthetic.patient_key" and "path" not in binding
    record = data["normalisations"]["anaesthetic.patient_key"]
    assert record["part"] == "role_anaesthetic" and record["grain"] == "one row for each anaesthetic"
    assert [i["table"] for i in record["inputs"]] == ["ANAES_RECORD", "THEATRE_CASE", "VISIT"]
    assert "LEFT JOIN VISIT" in record["sql"] and record["path"] == s.data["roles"]["role_anaesthetic"]["columns"]["patient_key"]["binding"]["path"]
    assert all(a.endswith(".") and "?" not in a for a in record["assumptions"]) and len(record["assumptions"]) == 3
    assert record["tests"][0]["outcome"] == "passed" and s.log.get(record["tests"][0]["entry"])["kind"] == "test run"
    joined = data["normalisations"]["device.size"]
    assert joined["joined"]["table"] == "ANAES_EVENT" and "STRING_AGG" in joined["sql"]
    # A filter stays in the binding of the part's rows, and no binding holds a time window.
    assert not any(n.endswith("anaesthetic_key") and n.startswith("reading") for n in data["normalisations"])
    # The part's SQL is written from the normalisation, and says so.
    sql = files["map/role_anaesthetic.sql"].decode()
    assert "named normalisation (anaesthetic.patient_key)" in sql and "LEFT JOIN VISIT" in sql
    # A file opened again reads each normalisation back into its route.
    again = opened(files)
    assert again.data == s.data
    # A reference to a normalisation that the map does not hold is refused.
    broken = json.loads(files["map/map.json"])
    del broken["normalisations"]["device.size"]
    with pytest.raises(rolemap.MapError, match="names a normalisation"):
        normalise.resolve(broken)


# A15: the evidence import.

@pytest.fixture(scope="module")
def saved():
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    s.set_settings("production", 2024, "Australia/Sydney", True)
    s.tables_query()
    s.read_tables(tables_result())
    for about in ("role_patient rows", "role_patient.patient_key", "role_anaesthetic rows", "role_anaesthetic.anaesthetic_key",
                  "role_anaesthetic.patient_key", "role_anaesthetic.start_time"):
        s.confirm(about, "yes", date=DATE)
    return roleshadow.save_zip(s, DATE)


def _files(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _probe_request(saved):
    schema = feasibility.Schema(_files(saved))
    found = feasibility.assess(schema, rolemap.AUDIT.read_text(encoding="utf-8"), "neonatal.sql")
    return schema, found, next(r for r in found["requests"] if r["form"] == "test query" and PATIENT_LINK in r["moves"])


def test_an_evidence_request_carries_its_format_its_version_a_stable_id_and_the_shape_it_expects(saved):
    schema, found, request = _probe_request(saved)
    assert request["format"] == describe.REQUEST_FORMAT and request["schema_id"] == schema.schema_id
    assert request["request_id"] == _probe_request(saved)[2]["request_id"]
    assert request["expects"] == [{"query": "probe-role_anaesthetic-patient_key",
                                   "columns": [{"name": c, "type": "a whole number"} for c in ("anaesthetics", "with_rows", "without_rows")]}]
    assert request["queries"][0]["sql"] == request["sql"]
    # A request that a person answers on the page carries no shape, and the import refuses it.
    answer = next(r for r in found["requests"] if r["form"] == "answer")
    assert answer["expects"] is None and answer["queries"] == []
    with pytest.raises(describe.DescribeError, match="answered on the page"):
        opened(_files(saved)).import_evidence(answer, "", "Dr C")


def test_the_import_refuses_a_result_of_the_wrong_shape_or_a_request_from_another_schema(saved):
    _, _, request = _probe_request(saved)
    s = opened(_files(saved))
    entries = s.log.entries()
    with pytest.raises(describe.DescribeError, match="does not have the columns"):
        s.import_evidence(request, "anaesthetics\twith_rows\n1200\t1100\n", "Dr C")
    with pytest.raises(describe.DescribeError, match="which is not a whole number"):
        s.import_evidence(request, "anaesthetics\twith_rows\twithout_rows\n1200\tmany\t100\n", "Dr C")
    with pytest.raises(describe.DescribeError, match="neither this version"):
        s.import_evidence({**request, "schema_id": "0" * 16}, "anaesthetics\twith_rows\twithout_rows\n1200\t1100\t100\n", "Dr C")
    with pytest.raises(describe.DescribeError, match="cannot read this as an evidence request"):
        s.import_evidence({"form": "test query"}, "", "Dr C")
    # Nothing refused reaches the journal.
    assert s.log.entries() == entries


def test_an_imported_probe_reconciles_its_link_and_saves_a_new_version_that_a_later_request_can_follow(saved, tmp_path):
    _, _, request = _probe_request(saved)
    s = opened(_files(saved))
    made_from = s.identity["schema_id"]
    found = s.import_evidence(request, "anaesthetics\twith_rows\twithout_rows\n1200\t1100\t100\n", "Dr C")
    assert found["schema_id"] != made_from and found["file"] == f"hospital-schema-{found['schema_id']}.schemalyser.zip"
    assert json.loads(found["files"]["settings.json"])["parent_id"] == made_from
    entry = found["entry"]
    assert entry["kind"] == "evidence imported" and entry["payload"]["request_id"] == request["request_id"] and entry["actor"] == "Dr C"
    reconciled = s.dimensions["links"][PATIENT_LINK]["reconciled"]
    assert reconciled["figure"] == {"anaesthetics": 1200, "with_rows": 1100, "without_rows": 100}
    assert s.log.get(reconciled["entry"])["payload"]["request"] == request["request_id"]
    # A request made from the earlier version, which the new one was made from, is still accepted.
    again = opened(found["files"])
    assert again.import_evidence(request, "anaesthetics\twith_rows\twithout_rows\n1200\t1150\t50\n", "Dr C")["schema_id"]
    # The command line imports the same result without the page, and names the new file by its version.
    schema_file = tmp_path / "hospital-schema.schemalyser.zip"
    schema_file.write_bytes(saved)
    (tmp_path / "request.json").write_text(json.dumps(request))
    (tmp_path / "result.tsv").write_text("anaesthetics\twith_rows\twithout_rows\n1200\t1100\t100\n")
    assert describe_main.main(["import-evidence", str(schema_file), str(tmp_path / "request.json"), str(tmp_path / "result.tsv"),
                          "--actor", "Dr C", "--provenance", "complete data", "--out", str(tmp_path / "out")]) == 0
    written = list((tmp_path / "out").glob("hospital-schema-*.schemalyser.zip"))
    assert len(written) == 1 and opened(_files(written[0].read_bytes())).dimensions["links"][PATIENT_LINK]["reconciled"]


def test_a_clinical_reconciliation_validates_only_the_parts_it_covers_and_a_plan_is_kept_for_the_package(saved):
    s = opened(_files(saved))
    request = {"format": describe.REQUEST_FORMAT, "request_id": "qreconcile", "schema_id": s.identity["schema_id"],
               "form": "reconciliation", "moves": ["role_patient"], "covers": s.covered(["role_patient"])}
    with pytest.raises(describe.DescribeError, match="does not have the columns"):
        s.import_evidence(request, "agreed\n10\n", "Dr C")
    s.import_evidence(request, "anaesthetics_sampled\tagreed\tdisagreed\n40\t39\t1\n", "Dr C")
    validated = s.dimensions["bindings"]["role_patient.patient_key"]["validated"]
    assert validated["by"] == "Dr C" and validated["figure"] == [{"anaesthetics_sampled": 40, "agreed": 39, "disagreed": 1}]
    assert s.dimensions["bindings"]["role_anaesthetic.start_time"]["validated"] is None
    plan = {"format": describe.REQUEST_FORMAT, "request_id": "qplan", "schema_id": s.identity["schema_id"], "form": "plan"}
    with pytest.raises(describe.DescribeError, match="plan is empty"):
        s.import_evidence(plan, "  ", "Dr D")
    s.import_evidence(plan, "<ShowPlanXML/>", "Dr D", "metadata")
    kept = s.log.latest("evidence imported", form="plan")
    assert kept["provenance"] == "metadata" and s.texts[kept["payload"]["result"]] == "<ShowPlanXML/>"
