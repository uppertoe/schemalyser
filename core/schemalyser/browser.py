"""The functions the page's worker calls: the bridge of the page Describe the record. They take and return plain values
only, and each describe_ function hands its request to describe.Describe, which holds the sitting. The sitting records
the test on made-up rows and never runs it, so the functions that need one (a correction's check and keep, the check of
the map, the save and the evidence import) hand the sitting to the role shadow (roleshadow.py), which runs the test and
gives the sitting what it found."""
import io
import json
import re


def _bytes(data):
    # Under Pyodide a JavaScript Uint8Array arrives as a proxy with to_bytes().
    return data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)


# Screen 1, describing the record (index.html). The dictionary, the map and everything pasted are held by one
# describe.Describe in this worker's memory, and leave it only as the hospital folder that the page writes where the
# person chooses. A message returned here never holds a description from the dictionary, except the model that the
# page shows beside each binding, which stays in the page.

_describe = None


def _describing():
    global _describe
    if _describe is None:
        from .describe import Describe
        _describe = Describe()
    return _describe


def _reply(work):
    """Runs work and returns its JSON, or {"ok": false, "problem": sentence} where a person's input cannot be used."""
    from .describe import DescribeError
    try:
        found = work()
    except DescribeError as error:
        return json.dumps({"ok": False, "problem": str(error)})
    return json.dumps({"ok": True, **(found or {}), "model": _describing().view(invented_hospital=_hospital is not None)})


def describe_begin(version):
    global _describe
    _describe = None
    _describing().version = str(version or "")
    return "ok"


def describe_dictionary(data, tables, headings, name, tables_name, step, invented=False):
    """Loads the dictionary from the bytes of a chosen file, or of the invented dictionary that the page serves, which
    invented marks so that everything written afterwards says the folder describes no hospital."""
    d = _describing()
    return _reply(lambda: {"receipt": d.load_dictionary(
        _bytes(data), _bytes(tables) if tables is not None else None, json.loads(headings or "{}"), name,
        tables_name or "tables.csv", step, invented=bool(invented))})


def describe_dictionary_query(step):
    """The data dictionary query, which is the same for every hospital."""
    return _reply(lambda: _describing().dictionary_query(step, record=False))


def describe_dictionary_database(data, name, step):
    """Makes the data dictionary from the result of the data dictionary query: the bytes of a saved file, or the
    text pasted from the results grid."""
    d = _describing()
    return _reply(lambda: {"receipt": d.load_from_database(data if isinstance(data, str) else _bytes(data),
                                                           name or "data-dictionary.csv", step)})


def describe_dictionary_vendor(data, tables, headings, name, tables_name):
    """Adds the vendor's descriptions, from the bytes of a chosen file, to the data dictionary made from the database."""
    d = _describing()
    return _reply(lambda: {"receipt": d.add_descriptions(
        _bytes(data), _bytes(tables) if tables is not None else None, json.loads(headings or "{}"),
        name or "vendor-dictionary.csv", tables_name or "vendor-tables.csv")})


def describe_dictionary_upload(data, tables, headings, name, tables_name, step, reference=None, reference_name=None):
    """Reads a dictionary file that a person already has: a saved result of the data dictionary query, or a vendor's
    export, which adds its descriptions to a dictionary made from the database or is otherwise the dictionary itself.
    reference, when given, is the file of a reference conversion's lineage, which the proposer reads beside it."""
    d = _describing()

    def work():
        kind, receipt = d.upload(_bytes(data), _bytes(tables) if tables is not None else None, json.loads(headings or "{}"),
                                 name or "dictionary.csv", tables_name or "tables.csv", step,
                                 _bytes(reference) if reference is not None else None, reference_name or "lineage.json")
        return {"kind": kind, "receipt": receipt}
    return _reply(work)


# The paths that a saved hospital schema holds. Anything else inside the file is let go of at once.
_SCHEMA_PATH = re.compile(r"(settings\.json|journal\.json|dimensions\.json|confirmations\.csv|map/map\.json|"
                          r"codes/role_\w+\.\w+\.json|counts/judgements\.json|queries/\d{2,4}-[\w.-]+\.sql|"
                          r"results/\d{2,4}-[\w.-]+\.tsv|dictionary/[\w .()-]+\.(json|csv|tsv|txt))")


def describe_schema_open(data):
    """Opens a saved hospital schema from the bytes of its one file, and restores everything it holds."""
    import zipfile
    d = _describing()

    def work():
        from .describe import DescribeError
        try:
            with zipfile.ZipFile(io.BytesIO(_bytes(data))) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()
                         if not info.is_dir() and _SCHEMA_PATH.fullmatch(info.filename)}
        except (zipfile.BadZipFile, OSError):
            raise DescribeError("unreadable") from None
        return {"restored": d.restore(files)}
    return _reply(work)


def describe_propose(progress):
    d = _describing()
    return _reply(lambda: (d.propose(progress=lambda done, total: progress(done, total)), {})[1])


def describe_model():
    return _reply(lambda: {})


def describe_tables_query(step):
    return _reply(lambda: _describing().tables_query(step))


def describe_tables_read(text):
    return _reply(lambda: {"receipt": _describing().read_tables(text)})


def describe_confirm(request):
    """Records an answer. The name of the person who answered is passed on where the page collected one, and the core
    records "not recorded" where it did not."""
    r = json.loads(request)
    return _reply(lambda: _describing().confirm(r["about"], r["answer"], r.get("replacement") or "", r.get("note") or "",
                                                actor=r.get("actor")))


def describe_settings(request):
    """Records the database, the year, or the time zone of the database's clocks. timeZoneFrom says whether the zone is
    the one the page proposed from this computer, which the core then records as proposed rather than as given."""
    r = json.loads(request)
    return _reply(lambda: _describing().set_settings(r.get("database"), r.get("year"), r.get("timeZone"), r.get("daylightSaving"),
                                                     r.get("timeZoneFrom")))


def describe_charted_query(request):
    r = json.loads(request)
    return _reply(lambda: _describing().charted_query(r["key"], r["year"], r.get("step") or ""))


def describe_charted_read(request):
    r = json.loads(request)
    return _reply(lambda: {"receipt": _describing().read_charted(r["key"], r["text"], r["year"])})


def describe_codes(request):
    r = json.loads(request)
    return _reply(lambda: _describing().choose_codes(r["key"], r["chosen"], actor=r.get("actor")))


def describe_counts(request):
    r = json.loads(request)
    return _reply(lambda: {"queries": _describing().count_queries(r.get("year"), r.get("step") or "")})


def describe_count_read(request):
    r = json.loads(request)
    return _reply(lambda: {"receipt": _describing().read_count(r["name"], r["text"])})


def describe_count_judge(request):
    r = json.loads(request)
    return _reply(lambda: _describing().judge_count(r["name"], r["looksRight"], r.get("note") or "", actor=r.get("actor")))


def describe_schema_files():
    """The paths inside the saved hospital schema, which the page counts once it has saved the file."""
    return json.dumps(sorted(_describing().folder_files()))


def describe_schema_zip():
    """Saves a new version of the hospital schema and gives the bytes of its one file, whose name, which carries the
    version's schema_id, the model then gives as schema.file. The test on made-up rows that the schema owes runs first."""
    from . import roleshadow
    return roleshadow.save_zip(_describing())


def describe_check():
    return _reply(lambda: {"check": _describing().check()})


def describe_compare(request):
    r = json.loads(request)
    return _reply(lambda: {"compared": _describing().compare(r["name"], r["text"])})


# The corrections of screen 1: a form previewed, checked on invented rows, kept, and probed against the database.

def describe_correction_preview(request):
    return _reply(lambda: {"preview": _describing().correction_preview(json.loads(request))})


def describe_correction_check(request):
    from . import roleshadow
    return _reply(lambda: {"report": roleshadow.correction_check(_describing(), json.loads(request))})


def describe_model_check():
    from . import roleshadow
    return _reply(lambda: {"report": roleshadow.check_model(_describing())})


def describe_correction_keep(request):
    from . import roleshadow
    r = json.loads(request)
    return _reply(lambda: roleshadow.correction_keep(_describing(), r["correction"], bool(r.get("although")),
                                                     r.get("reason") or "", actor=r.get("actor")))


def describe_import_evidence(request):
    """Imports the result of one of the feasibility report's evidence requests: request is {"request": the request as
    the report wrote it, "result": the text of its result or {query: text}, "actor", "provenance"}. The new version is
    saved in the worker, and the model gives its schema_id and file name."""
    from . import roleshadow
    r = json.loads(request)

    def work():
        # The import saves a new version, so the test on made-up rows that the schema owes, if any, is run first.
        roleshadow.run_owed_test(_describing())
        found = _describing().import_evidence(r.get("request"), r.get("result") or "", r.get("actor"), r.get("provenance"))
        return {"imported": {"schema_id": found["schema_id"], "file": found["file"], "entry": found["entry"]["id"]}}
    return _reply(work)


def describe_names():
    return _reply(lambda: _describing().names())


def describe_columns(request):
    return _reply(lambda: _describing().columns_of(json.loads(request)["table"]))


def describe_joins(request):
    return _reply(lambda: _describing().joins_from(json.loads(request)["table"]))


def describe_values_query(request):
    r = json.loads(request)
    return _reply(lambda: _describing().values_query(r["about"], r["table"], r["column"], r.get("year"), r.get("step") or ""))


def describe_values_read(request):
    r = json.loads(request)
    return _reply(lambda: _describing().read_values(r["name"], r["text"]))


def describe_probe_query(request):
    r = json.loads(request)
    return _reply(lambda: _describing().probe_query(r["about"], r.get("year"), r.get("step") or ""))


def describe_probe_read(request):
    r = json.loads(request)
    return _reply(lambda: {"receipt": _describing().read_probe(r["about"], r["text"])})


# The invented hospital, on which the page runs its own queries when the invented dictionary is in use. It is built once
# for each sitting, from the files that the page fetched beside the invented dictionary, and kept in this worker.

_hospital = None


def describe_hospital_build(names, *data):
    """Builds the invented hospital from its published files: names is a JSON list of their paths, and data their bytes
    in the same order."""
    global _hospital
    from .hospital import HospitalError, InventedHospital
    if _hospital is not None:
        return json.dumps({"ok": True, "tables": _hospital.tables})
    try:
        _hospital = InventedHospital({path: _bytes(d) for path, d in zip(json.loads(names), data)})
    except (HospitalError, ValueError, KeyError):
        return json.dumps({"ok": False, "problem": "hospital"})
    return json.dumps({"ok": True, "tables": _hospital.tables})


def describe_hospital_run(request):
    """Runs one query that the page has offered on the invented hospital, and reads its result as a paste would be read:
    request is {"query": its name, "read": the reading, and the reading's own key, year, name or about}."""
    r = json.loads(request)
    given = {k: r[k] for k in ("key", "year", "name", "about") if k in r}
    return _reply(lambda: {"receipt": _describing().run_invented(_hospital, r["query"], r["read"], **given)})
