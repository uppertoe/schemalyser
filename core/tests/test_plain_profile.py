"""The plain form of the core profile, its queries on the checklist, and its results pasted back."""
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from schemalyser import Analysis, browser, checks as checking, target
from schemalyser import profile as P

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
TARGETS = FIXTURES / "targets"
WHOLE = (FIXTURES / "profile" / "invented-core-profile.csv").read_text()
GENERAL = (FIXTURES / "profile" / "invented-general-profile.csv").read_text()
CHECKS = (FIXTURES / "invented-checks.csv").read_text()
PREFIX = {"source_prefix": "clarity_shadow.dbo."}
NOT_PLAIN = ("@", "EXEC", "SP_EXECUTESQL", "DECLARE", "SET ", "#", "CATCH", "INSERT", "INTO ", "UPDATE ",
             "DELETE ", "MERGE ", "DROP ", "CREATE ", "ALTER ", "TRUNCATE ", "BEGIN", "OPTION", "OPENQUERY", "OPENROWSET")
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402


@pytest.fixture(scope="module")
def conversion(tmp_path_factory):
    """The invented conversion with the source prefix set, as release.json would set it at a hospital."""
    folder = tmp_path_factory.mktemp("plain-profile") / "conversion"
    shutil.copytree(FIXTURES / "conversion", folder)
    (folder / "release.json").write_text(json.dumps(PREFIX))
    return folder


def _answer(sql, conversion):
    """What SQL Server gave the whole script on the invented world, restricted to what one plain query asks.

    The whole script's result in fixtures/profile/ came from SQL Server on the same database, so the rows that
    a plain query would return are taken from it: the tables and fields that tier one names, the key of a
    table, its type concepts, the versions, and the match of one join.
    """
    rows = [r for r in P.pasted_rows(WHOLE)]
    if "sys.objects" in sql:
        tables = set(re.findall(r"^\s*(?:FROM \(VALUES )?\('([a-z_]+)', '[a-z_]*'\)", sql, re.M))
        fields = set(re.findall(r"\('([a-z_]+)', '([a-z_]+)', '[a-z]+', ", sql))
        found = [r for r in rows if r[0] == "PROFILE"]
        found += [[r[0], r[1], r[2], r[3], r[4], ""] for r in rows if r[0] == "CDM_TABLE" and r[1] in tables]
        found += [r for r in rows if r[0] in ("CDM_FIELD_ABSENT", "CDM_FIELD_TYPE") and (r[1], r[2]) in fields]
        return found
    if "'SOURCE_KEY_MATCH'" in sql:
        core, source = re.findall(r"'([A-Za-z_]+\.[A-Za-z_]+)' AS VALUE_0[12]", sql)
        return [r for r in rows if r[0] == "SOURCE_KEY_MATCH" and r[1:3] == [core, source]]
    if "'TYPE_CONCEPT'" in sql:
        table = re.search(r"'TYPE_CONCEPT' AS ITEM_CATEGORY, '([a-z_]+)'", sql).group(1)
        return [r for r in rows if r[0] == "TYPE_CONCEPT" and r[1] == table]
    if "'CDM_SOURCE'" in sql:
        return [r for r in rows if r[0] == "CDM_SOURCE"]
    table = re.search(r"'CDM_TABLE' AS ITEM_CATEGORY, '([a-z_]+)'", sql).group(1)
    whole = next(r for r in rows if r[0] == "CDM_TABLE" and r[1] == table)
    return [["CDM_TABLE", table, "Y", "", "", whole[5]]]


def _tsv(rows):
    return "\n".join("\t".join(["ITEM_CATEGORY", "VALUE_01", "VALUE_02", "VALUE_03", "VALUE_04", "VALUE_05"])
                     for _ in [0]) + "\n" + "\n".join("\t".join(c or "NULL" for c in r) for r in rows) + "\n"


def _is_one_select(sql):
    statements = [s for s in sqlglot.parse(sql.replace(" WITH (NOLOCK)", ""), dialect="tsql") if s is not None]
    return len(statements) == 1 and isinstance(statements[0], (exp.Select, exp.Union))


def test_each_plain_profile_query_is_one_select_that_only_reads(conversion):
    tier_one = P.plain(conversion)
    assert [q["id"] for q in tier_one if q["sql"]] == ["profile:tier-one"]
    known = P.merged(None, P.accepted_rows(_answer(tier_one[0]["sql"], conversion), conversion), conversion)
    sizes = {"THEATRE_CASE": 530, "ANAES_STAFF": 500, "OBS_SHEET": 530, "DRUG_GIVEN": 500}
    queries = [q for q in P.plain(conversion, profile_text=known, source_sizes=sizes) if q["sql"]]
    assert {q["id"].split(":")[1] for q in queries} == {"tier-one", "key", "types", "cdm-source", "match"}
    for query in queries:
        sql, body = query["sql"], "\n".join(l for l in query["sql"].splitlines() if not l.startswith("--")).upper()
        assert _is_one_select(sql), query["id"]
        for word in NOT_PLAIN:
            assert word not in body, (query["id"], word)
        names = set(re.findall(r"(?:FROM|JOIN) (\S+)", body))
        if query["tier"] == 1:
            # Tier one reads only the server's own records.
            assert names <= {"(VALUES", "SYS.OBJECTS", "SYS.PARTITIONS", "SYS.COLUMNS", "INFORMATION_SCHEMA.TABLES",
                             "INFORMATION_SCHEMA.COLUMNS"}, names
        else:
            # Every table that tier two reads is read without taking locks, and every count is rounded down to ten.
            assert body.count("WITH (NOLOCK)") == sum(1 for n in names if n.startswith("[")) > 0
            if "COUNT_BIG" in body and query["id"] != "profile:cdm-source":
                assert "/ 10) * 10" in body
        if ":match:" in query["id"]:
            assert f"SELECT DISTINCT TOP ({P.PLAIN_KEY_SAMPLE})" in body and "[CLARITY_SHADOW].[DBO]." in body
    # Each result is self-describing, and read accepts it; the facts agree with the whole script's.
    rows = [r for q in queries for r in _answer(q["sql"], conversion)]
    text = P.merged(None, P.accepted_rows(P.pasted_rows(_tsv(rows)), conversion), conversion)
    plain, whole = P.read(text, conversion), P.read(WHOLE, conversion)
    for table, entry in plain["tables"].items():
        assert entry == whole["tables"][table], table
    assert plain["matches"] == whole["matches"] and plain["type_concepts"] == whole["type_concepts"]
    assert plain["cdm_source"] == whole["cdm_source"]


def test_tier_two_waits_for_tier_one_and_is_bounded_by_construction(conversion):
    settings = P.plain_settings(conversion)
    pair = ("visit_occurrence", "visit_source_value", "THEATRE_CASE", "VISIT_KEY")
    sized = {"visit_occurrence": {"present": True, "rows": 520, "key_type": "int", "key_digits": None}}
    offer = lambda tables, sizes, chosen=settings: P.plain_match_offer(pair, "VARCHAR(50)", chosen, tables, sizes)  # noqa: E731
    assert offer({}, {"THEATRE_CASE": 530})["state"] == "sizes"
    assert offer(sized, {})["state"] == "source-sizes"
    assert offer(sized, {"THEATRE_CASE": 530}, {**settings, "source_prefix": None})["state"] == "setting"
    assert offer({"visit_occurrence": {**sized["visit_occurrence"], "present": False}}, {"THEATRE_CASE": 530})["state"] == "absent"
    # Where the server keeps no size, as for a view, the only query is a count that reads the whole of it.
    view = offer({"visit_occurrence": {**sized["visit_occurrence"], "rows": None}}, {"THEATRE_CASE": 530})
    assert view["state"] == "count" and "It reads the whole of" in view["sql"] and "keeps no record" in view["sql"]
    assert "FROM [dbo].[visit_occurrence] WITH (NOLOCK);" in view["sql"]
    # A core table over the limit is measured from the core side, on a literal sample of its pages, with each
    # value looked up as a key of the table that the source key refers to; at or under the limit, from the source side.
    large = {"visit_occurrence": {**sized["visit_occurrence"], "rows": 250_000_000}}
    core_side = P.plain_match_offer(pair, "VARCHAR(50)", settings, large, {"THEATRE_CASE": 530}, ("VISIT", "VISIT_KEY"), "number")
    assert core_side["state"] == "core-side" and core_side["id"] == offer(sized, {"THEATRE_CASE": 530})["id"]
    sql = core_side["sql"]
    body = "\n".join(line for line in sql.splitlines() if not line.startswith("--")).upper()
    assert _is_one_select(sql) and not any(word in body for word in NOT_PLAIN)
    assert "FROM [dbo].[visit_occurrence] AS c TABLESAMPLE SYSTEM (2 PERCENT) REPEATABLE (20261005) WITH (NOLOCK)" in sql
    assert f"SELECT DISTINCT TOP ({P.PLAIN_KEY_SAMPLE}) CAST(c.[visit_source_value] AS nvarchar(4000)) AS v" in sql
    assert "FROM [clarity_shadow].[dbo].[VISIT] AS s WITH (NOLOCK)" in sql and "s.[VISIT_KEY] = TRY_CAST(d.v AS bigint)" in sql
    assert "'SOURCE_KEY_MATCH_SAMPLED' AS ITEM_CATEGORY" in sql and "'THEATRE_CASE.VISIT_KEY' AS VALUE_02" in sql
    assert "/ 10) * 10" in sql and f">= {P.MINIMUM_COUNT}" in sql and "estimate from a sample" in sql.replace("\n-- ", " ")
    at_limit = {"visit_occurrence": {**sized["visit_occurrence"], "rows": checking.PLAIN_EXACT_ROWS}}
    assert offer(at_limit, {"THEATRE_CASE": 530})["state"] == "ready"
    assert P.plain_match_offer(pair, "VARCHAR(50)", settings, sized, {"THEATRE_CASE": 530}, exact_rows=100)["state"] == "core-side"
    # A text key is compared as text, and an unknown one through a cast of the key.
    assert "s.[VISIT_KEY] = d.v" in P.plain_match_core(pair, settings, 10 ** 9, ("VISIT", "VISIT_KEY"), "text")
    assert "CAST(s.[VISIT_KEY] AS nvarchar(4000)) = d.v" in P.plain_match_core(pair, settings, 10 ** 9)
    # The source table's size is still needed first.
    assert offer(large, {})["state"] == "source-sizes"
    sampled = offer(sized, {"THEATRE_CASE": 250_000_000})
    assert sampled["state"] == "sampled" and "TABLESAMPLE SYSTEM (2 PERCENT) REPEATABLE (20261005) WITH (NOLOCK)" in sampled["sql"]
    ready = offer(sized, {"THEATRE_CASE": 530})
    assert ready["state"] == "ready" and "TABLESAMPLE" not in ready["sql"]
    # Type concepts on a large table are counted from a sample and scaled up.
    sql = P.plain_type_concepts("measurement", "dbo", 3)
    assert "TABLESAMPLE SYSTEM (3 PERCENT) REPEATABLE (20261005) WITH (NOLOCK)" in sql and "(CAST(g.n * 100.0 / 3 AS bigint) / 10) * 10" in sql


def test_pasted_profile_rows_are_read_by_the_rules_of_read(conversion):
    header = "ITEM_CATEGORY\tVALUE_01\tVALUE_02\tVALUE_03\tVALUE_04\tVALUE_05"
    good = "CDM_TABLE\tvisit_occurrence\tY\t520\tint\tNULL"
    for text in (header + "\n" + good + "\n(1 rows affected)\n", good, "ITEM_CATEGORY,VALUE_01,VALUE_02,VALUE_03,VALUE_04,VALUE_05\n"
                 + good.replace("\t", ",").replace("NULL", "")):
        assert P.accepted_rows(P.pasted_rows(text), conversion) == [["CDM_TABLE", "visit_occurrence", "Y", "520", "int", ""]]
    hostile = [
        "CDM_TABLE\tvisit_occurrence\tY\t523\tint\tNULL",                                   # a count not rounded
        "CDM_TABLE\tsecret_table\tY\t520\tint\tNULL",                                       # not a CDM table
        "CDM_FIELD_ABSENT\tvisit_occurrence\tsecret_field\tNULL\tNULL\tNULL",                # not a CDM field
        "SOURCE_KEY_MATCH\tvisit_occurrence.visit_source_value\tSECRET.KEY\t530\t520\t98",   # a join the steps do not make
        "SOURCE_KEY_MATCH\tvisit_occurrence.visit_source_value\tTHEATRE_CASE.VISIT_KEY\t530\t540\t98",  # more matched than keys
        "CDM_SOURCE\t=HYPERLINK(1)\tx\tNULL\t1\tNULL",                                     # a formula
        "TYPE_CONCEPT\tmeasurement\tmeasurement_type_concept_id\t32817\t5\tNULL",          # a count under ten
    ]
    assert P.accepted_rows(P.pasted_rows("\n".join([good] + hostile)), conversion) == \
        [["CDM_TABLE", "visit_occurrence", "Y", "520", "int", ""]]
    with pytest.raises(P.ProfileError):
        P.accepted_rows(P.pasted_rows(good + "\textra"), conversion)
    with pytest.raises(P.ProfileError):
        P.accepted_rows(P.pasted_rows("DROP TABLE\tx\tNULL\tNULL\tNULL\tNULL"), conversion)


def test_merging_adds_tier_two_to_tier_one_and_replaces_what_is_asked_again(conversion):
    first = P.merged(None, [["CDM_TABLE", "visit_occurrence", "Y", "520", "int", ""],
                            ["CDM_FIELD_ABSENT", "visit_occurrence", "visit_source_value", "", "", ""]], conversion)
    assert P.read(first, conversion)["absent_fields"] == {"visit_occurrence": ["visit_source_value"]}
    # The highest key from tier two joins the size and the key type from tier one.
    second = P.merged(first, [["CDM_TABLE", "visit_occurrence", "Y", "", "", "3"]], conversion)
    assert P.read(second, conversion)["tables"]["visit_occurrence"] == {"present": True, "rows": 520, "key_type": "int",
                                                                         "key_digits": 3}
    assert P.read(second, conversion)["absent_fields"] == {"visit_occurrence": ["visit_source_value"]}
    # Tier one, asked again, replaces the fields of the table it reports.
    third = P.merged(second, [["CDM_TABLE", "visit_occurrence", "Y", "530", "int", ""]], conversion)
    found = P.read(third, conversion)
    assert found["absent_fields"] == {} and found["tables"]["visit_occurrence"]["rows"] == 530
    assert found["tables"]["visit_occurrence"]["key_digits"] == 3
    # A later match replaces the earlier one, and the whole script's profile can be merged into.
    match = ["SOURCE_KEY_MATCH", "visit_occurrence.visit_source_value", "THEATRE_CASE.VISIT_KEY", "0", "", ""]
    found = P.read(P.merged(WHOLE, [match], conversion), conversion)
    assert [m for m in found["matches"] if m["source"] == "THEATRE_CASE.VISIT_KEY"][0]["keys"] == 0
    assert len(found["matches"]) == len(P.read(WHOLE, conversion)["matches"])
    # An earlier general profile cannot stand beside the core profile's rows, so it is not kept.
    assert P.read(P.merged(GENERAL, [match], conversion), conversion)["format"] == "core"


def test_the_checklist_carries_the_profile_queries_and_ticks_when_their_results_are_given(conversion):
    world = make_checks.WORLD
    sql = (TARGETS / "neonatal_low_mean_pressure.sql").read_text()
    rows, traced = target.checklist(world, conversion, sql, CHECKS)
    core = {r["question_id"]: r for r in rows if r["kind"] == "core"}
    assert core and all(r["status"] != "answered" for r in core.values())
    profile = traced["queries"]["profile"]
    assert [q["id"] for q in profile] == ["profile:tier-one"]
    assert all(r["_queries"] == ["profile:tier-one"] and r["query_state"] == "ready" for r in core.values())
    # Only what this target's items need is asked: their tables and fields.
    tier_one = profile[0]["sql"]
    assert "('death', 'death_date', 'datetime'" not in tier_one or "core-death.death_date" in core
    assert "('measurement'," not in tier_one
    # Tier one's result is pasted, and the matches are offered.
    known = P.merged(None, P.accepted_rows(_answer(tier_one, conversion), conversion), conversion)
    rows, traced = target.checklist(world, conversion, sql, CHECKS, known)
    ids = [q["id"] for q in traced["queries"]["profile"]]
    assert ids and all(i.startswith("profile:match:") for i in ids)
    left = {r["question_id"]: r for r in rows if r["kind"] == "core" and r["status"] != "answered"}
    assert set(left) == {k for k in core if k.startswith("C-source-value-")}
    # The matches are pasted, and every core item ticks.
    answers = [r for q in traced["queries"]["profile"] for r in _answer(q["sql"], conversion)]
    whole = P.merged(known, P.accepted_rows(answers, conversion), conversion)
    rows, traced = target.checklist(world, conversion, sql, CHECKS, whole)
    assert all(r["status"] == "answered" for r in rows if r["kind"] == "core") and not traced["queries"]["profile"]
    # Without a source prefix the match cannot be asked, and the item says so.
    rows, traced = target.checklist(world, FIXTURES / "conversion", sql, CHECKS, known)
    item = next(r for r in rows if r["question_id"].startswith("C-source-value-"))
    assert item["query_state"] == "waiting" and "until source_prefix is set in release.json" in item["query_reason"]
    # A match that has run and found no key is recorded, and not asked again.
    empty = [r if r[0] != "SOURCE_KEY_MATCH" else [*r[:3], "0", "", ""] for r in answers]
    rows, traced = target.checklist(world, conversion, sql, CHECKS, P.merged(known, P.accepted_rows(empty, conversion), conversion))
    assert not traced["queries"]["profile"]
    item = next(r for r in rows if r["question_id"] == "C-source-value-visit_occurrence.visit_source_value")
    assert item["query_state"] == "ran" and "has run and found no key in the source table" in item["query_reason"]


def test_the_page_reads_pasted_profile_results_and_works_the_checklist_out_again(conversion, monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    for name, source in (("catalogue.csv", "invented-catalogue.csv"), ("site-rules.json", "invented-site-rules.json"),
                         ("checks.csv", "invented-checks.csv")):
        (state / name).write_bytes((FIXTURES / source).read_bytes())
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    browser._analysis = Analysis((FIXTURES / "invented-catalogue.csv").read_text(),
                                 (FIXTURES / "invented-site-rules.json").read_text())
    browser.boundary_begin()
    for kind, folder, prefix in (("state", state, ""), ("state", conversion, "conversion/"), ("state", TARGETS, "targets/"),
                                 ("requests", FIXTURES / "requests", "")):
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            assert browser.boundary_put(kind, prefix + path.relative_to(folder).as_posix(), path.read_bytes())
    first = json.loads(browser.boundary_run())
    neonatal = next(t for t in first["targets"] if t["name"] == "neonatal_low_mean_pressure")
    assert [q["id"] for q in neonatal["profile"]] == ["profile:tier-one"] and first["profile"] == ""
    assert json.loads(browser.profile_paste("not a result")) == {"ok": False}
    result = json.loads(browser.profile_paste(_tsv(_answer(neonatal["profile"][0]["sql"], conversion))))
    assert result["ok"] and result["pasted"]["accepted"] == result["pasted"]["read"] > 0
    after = next(t for t in result["boundary"]["targets"] if t["name"] == "neonatal_low_mean_pressure")
    assert after["profile"] and all(q["id"].startswith("profile:match:") for q in after["profile"])
    ticked = [r for r in after["rows"] if r["id"].startswith("core-")]
    assert ticked and all(r["status"] == "answered" for r in ticked)
    saved = result["boundary"]["profile"]
    assert P.read(saved, browser.BOUNDARY_ROOT + "/state/conversion")["tables"]["person"]["present"]
    assert "profile:match:" in browser._boundary["files"]["targets/neonatal_low_mean_pressure/queries.sql"] or \
        "SOURCE_KEY_MATCH" in browser._boundary["files"]["targets/neonatal_low_mean_pressure/queries.sql"]
    browser.clear()
    assert browser._pasted_profile is None and browser._profile is None


def test_a_match_from_the_core_side_is_validated_and_settles_the_item_as_an_estimate(conversion):
    good = ["SOURCE_KEY_MATCH_SAMPLED", "visit_occurrence.visit_source_value", "THEATRE_CASE.VISIT_KEY", "520", "510", "98"]
    hostile = [["SOURCE_KEY_MATCH_SAMPLED", "visit_occurrence.visit_source_value", "SECRET.KEY", "520", "510", "98"],
               ["SOURCE_KEY_MATCH_SAMPLED", "visit_occurrence.visit_source_value", "THEATRE_CASE.VISIT_KEY", "520", "530", "98"],
               ["SOURCE_KEY_MATCH_SAMPLED", "visit_occurrence.visit_source_value", "THEATRE_CASE.VISIT_KEY", "523", "510", "98"],
               ["SOURCE_KEY_MATCH_SAMPLED", "visit_occurrence.visit_source_value", "THEATRE_CASE.VISIT_KEY", "520", "510", "180"],
               ["SOURCE_KEY_MATCH_SAMPLED", "person.person_source_value", "THEATRE_CASE.VISIT_KEY", "520", "510", "98"]]
    assert P.accepted_rows([good] + hostile, conversion) == [good]
    header = "\t".join(P.PLAIN_CATEGORIES["SOURCE_KEY_MATCH_SAMPLED"][1])
    assert P.pasted_rows(f"SOURCE_KEY_MATCH_SAMPLED\t{header}\n" + "\t".join(good)) == [good]
    found = P.read(P.merged(None, [good], conversion), conversion)["matches"]
    assert found == [{"core": good[1], "source": good[2], "keys": 520, "matched": 510, "percent": 98, "side": "core",
                      "steps": ["visit_detail_through_case.sql"]}]
    # A match of the same join from the other side replaces it, and the reverse.
    source = ["SOURCE_KEY_MATCH", *good[1:3], "530", "520", "97"]
    both = P.merged(P.merged(None, [good], conversion), [source], conversion)
    assert [m["side"] for m in P.read(both, conversion)["matches"]] == ["source"]
    assert [m["side"] for m in P.read(P.merged(both, [good], conversion), conversion)["matches"]] == ["core"]
    # The whole script never writes the new category.
    assert "SOURCE_KEY_MATCH_SAMPLED" not in P.script(conversion)

    # On the checklist, a large core table gets the core-side query, and the item says so.
    world = make_checks.WORLD
    sql = (TARGETS / "neonatal_low_mean_pressure.sql").read_text()
    rows = [r for r in P.pasted_rows(WHOLE) if r[0] in ("PROFILE", "CDM_TABLE")]
    rows = [r if r[:2] != ["CDM_TABLE", "visit_occurrence"] else [*r[:3], "250000000", *r[4:]] for r in rows]
    known = P.merged(None, P.accepted_rows(rows, conversion), conversion)
    checklist, traced = target.checklist(world, conversion, sql, CHECKS, known)
    item = next(r for r in checklist if r["question_id"] == "C-source-value-visit_occurrence.visit_source_value")
    assert item["query_state"] == "ready" and "measures the join from the core side" in item["query_reason"]
    assert "which gives an estimate" in item["query_reason"]
    offered = {q["id"]: q["sql"] for q in traced["queries"]["profile"]}
    assert all("SOURCE_KEY_MATCH_SAMPLED" in offered[i] and "[clarity_shadow].[dbo].[VISIT]" in offered[i]
               for i in item["_queries"]) and len(item["_queries"]) == 3
    # A high match from the core side settles the item, and says that it is an estimate from a sample.
    def item_with(percent):
        matches = [["SOURCE_KEY_MATCH_SAMPLED", "visit_occurrence.visit_source_value", source_key, "520",
                    str(520 * percent // 1000 * 10), str(percent)]
                   for source_key in ("THEATRE_CASE.VISIT_KEY", "OBS_SHEET.VISIT_KEY", "DRUG_GIVEN.VISIT_KEY")]
        text = P.merged(known, P.accepted_rows(matches, conversion), conversion)
        found, _ = target.checklist(world, conversion, sql, CHECKS, text)
        return next(r for r in found if r["question_id"] == "C-source-value-visit_occurrence.visit_source_value")
    high = item_with(99)
    assert high["status"] == "answered" and not high["_queries"]
    assert "the figure was measured from the core side" in high["evidence_in_hand"]
    assert "99 per cent are keys in the source system" in high["evidence_in_hand"] and "estimate from a sample" in high["evidence_in_hand"]
    assert "below" not in high["evidence_in_hand"]
    # A low one also settles the question, as a finding that the guess does not hold.
    low = item_with(40)
    assert low["status"] == "answered" and "the share is below 95 per cent, so the guess does not hold there" in low["evidence_in_hand"]
