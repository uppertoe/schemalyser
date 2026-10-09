"""The workbench's pages. Every page is rendered on the server from what the project folder holds and the reports that
the core writes; every action is a link or a form on the page; htmx swaps one section after a form or while a command
runs, and Alpine.js folds sections and copies SQL. Nothing here judges, counts or decides: the core does that."""
import datetime as dt
import json
import tempfile
from pathlib import Path

import markdown as markdown_lib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import audit, feasibility, rolemap, testbed
from . import jobs
from .project import Project, ProjectError, read_json, safe_name

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HOSTS = ["127.0.0.1", "localhost"]
CSP = ("default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
STATE_WORDS = {
    "running": "The command is running.",
    "finished": "The command has finished.",
    "failed": "The command stopped with an error, and its log below says why.",
    "interrupted": "The command stopped before it finished, most likely because the workbench was closed while it ran.",
    "not started": "The command has not started.",
}


def _markdown(text):
    md = markdown_lib.Markdown(extensions=["tables", "fenced_code"])
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    return Markup(md.convert(text or ""))


def _number(value):
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else value


def _day(iso):
    try:
        day = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return iso or ""
    return f"{day.day} {day:%B %Y}"


def worlds(project):
    """The worlds a run may use: the invented hospital, and any world folder that the project keeps in worlds/."""
    found = [("fixtures", "The invented hospital")]
    extra = project.root / "worlds"
    if extra.is_dir():
        found += [(str(p), p.name) for p in sorted(extra.iterdir()) if (p / "conversion").is_dir()]
    return found


def create_app(project_folder, hosts=None, describe_folder=None):
    project = Project(project_folder)
    # The core's own temporary files, such as those that reading a saved hospital schema makes, go inside the project.
    tempfile.tempdir = str(project.tmp)
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    env.filters.update(number=_number, day=_day, md=_markdown, tojson_pretty=lambda v: json.dumps(v, indent=2, ensure_ascii=False))
    describe_folder = Path(describe_folder) if describe_folder else ROOT / "site" / "dist"
    has_describe = (describe_folder / "index.html").is_file()

    def page(request, template, status=200, **context):
        context.update(request=request, project=project, has_describe=has_describe,
                       fragment=request.headers.get("HX-Request") == "true")
        html = env.get_template(template).render(**context)
        return HTMLResponse(html, status_code=status, headers={"Content-Security-Policy": CSP})

    def problem(request, error, back="/"):
        return page(request, "problem.html", status=400, message=str(error), back=back)

    def go(url):
        return RedirectResponse(url, status_code=303)

    async def same_origin(request):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return False
        return True

    # The overview.

    def overview(request: Request):
        schemas = []
        for name in project.schemas():
            try:
                schema = feasibility.Schema.load(project.schema_path(name))
                board = rolemap.scoreboard(rolemap.read_saved_map(project.schema_path(name)))
                parts = [{"title": v["title"], "state": (schema.readiness.get("parts") or {}).get(v["name"], {}).get("reached")}
                         for v in rolemap.contract()["views"] if v.get("required")]
                schemas.append({"name": name, "updated": schema.updated(), "parts": parts, "overall": board["lines"][2]
                                if len(board["lines"]) > 2 else "", "problem": None})
            except (feasibility.FeasibilityError, rolemap.MapError, KeyError, ValueError) as error:
                schemas.append({"name": name, "problem": str(error), "parts": []})
        return page(request, "overview.html", schemas=schemas, questions=project.questions(),
                    audits=[dict(a, state=jobs.state(project.folder("audits") / a["name"], a)) for a in project.audits()],
                    runs=[_run_summary(project, r) for r in project.runs()], worlds=worlds(project))

    async def add_schema(request: Request):
        if not await same_origin(request):
            return PlainTextResponse("The workbench accepts a form only from its own pages.", status_code=403)
        form = await request.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            return problem(request, "Choose a saved hospital schema to add.")
        try:
            name = project.add_schema(upload.filename, await upload.read())
        except ProjectError as error:
            return problem(request, error)
        return go(f"/schemas/{name}")

    def schema_page(request: Request):
        name = request.path_params["name"]
        try:
            path = project.schema_path(name)
            schema = feasibility.Schema.load(path)
            board = rolemap.scoreboard(rolemap.read_saved_map(path))
        except (ProjectError, feasibility.FeasibilityError, rolemap.MapError) as error:
            return problem(request, error)
        titles = {v["name"]: v["title"] for v in rolemap.contract()["views"]}
        parts = [dict(held, title=titles.get(view, view), contract=held.get("status") == "contract")
                 for view, held in (schema.readiness.get("parts") or {}).items()]
        return page(request, "schema.html", name=name, schema=schema, parts=parts, board=board,
                    states=schema.readiness.get("states") or {}, measured=schema.readiness.get("measured") or {},
                    questions=project.questions())

    # Questions.

    async def add_question(request: Request):
        if not await same_origin(request):
            return PlainTextResponse("The workbench accepts a form only from its own pages.", status_code=403)
        form = await request.form()
        try:
            name = project.add_question(form.get("title"), form.get("sql"))
        except ProjectError as error:
            return problem(request, error)
        return go(f"/questions/{name}")

    def question_page(request: Request):
        try:
            path = project.question_path(request.path_params["name"])
        except ProjectError as error:
            return problem(request, error)
        text = path.read_text(encoding="utf-8")
        return page(request, "question.html", name=path.name, title=feasibility.question_text(text), sql=text,
                    schemas=project.schemas())

    # Can this be answered.

    def answerable(request: Request):
        chosen_schema = request.query_params.get("schema") or (project.schemas()[:1] or [""])[0]
        chosen_question = request.query_params.get("question", "")
        report = programme = error = None
        if chosen_schema:
            try:
                schema = feasibility.Schema.load(project.schema_path(chosen_schema))
                if chosen_question:
                    path = project.question_path(chosen_question)
                    report = feasibility.assess(schema, path.read_text(encoding="utf-8"), path.name)
                else:
                    queries = [(q["name"], project.question_path(q["name"]).read_text(encoding="utf-8"))
                               for q in project.questions()]
                    programme = feasibility.programme(schema, queries) if queries else None
            except (ProjectError, feasibility.FeasibilityError, rolemap.MapError) as found:
                error = str(found)
        return page(request, "answerable.html", schemas=project.schemas(), questions=project.questions(),
                    chosen_schema=chosen_schema, chosen_question=chosen_question, report=report,
                    programme=programme, error=error)

    # Audits.

    def audits_page(request: Request):
        listed = [dict(a, state=jobs.state(project.folder("audits") / a["name"], a)) for a in project.audits()]
        return page(request, "audits.html", audits=listed, schemas=project.schemas(), questions=project.questions(),
                    chosen_schema=request.query_params.get("schema", ""),
                    chosen_question=request.query_params.get("question", ""))

    async def start_audit(request: Request):
        if not await same_origin(request):
            return PlainTextResponse("The workbench accepts a form only from its own pages.", status_code=403)
        form = await request.form()
        try:
            schema = project.schema_path(form.get("schema", ""))
            question = project.question_path(form.get("question", ""))
        except ProjectError as error:
            return problem(request, error, "/audits")
        start, end = (form.get("from") or "").strip(), (form.get("to") or "").strip()
        if bool(start) != bool(end):
            return problem(request, "A period needs both its first and its last date, or neither.", "/audits")
        name = project.unique("audits", f"{question.stem}_{dt.date.today().isoformat()}")
        folder = project.folder("audits") / name
        folder.mkdir()
        decisions = [line.strip() for line in (form.get("decisions") or "").splitlines() if line.strip()]
        command = jobs.module("audit", "build", schema, question, "--out", folder / "package")
        if start:
            command += ["--period", start, end]
        if decisions or form.get("exact"):
            (folder / "decisions.json").write_text(json.dumps(
                {"decisions": [{"about": "", "decision": d, "by": "the clinician", "date": dt.date.today().isoformat()}
                               for d in decisions], "exact_small_numbers": bool(form.get("exact"))}, indent=2) + "\n",
                encoding="utf-8")
            command += ["--decisions", folder / "decisions.json"]
        (folder / "request.json").write_text(json.dumps(
            {"schema": schema.name, "question": question.name, "period": [start, end] if start else None,
             "decisions": decisions, "exact_small_numbers": bool(form.get("exact"))}, indent=2) + "\n", encoding="utf-8")
        jobs.start(project, folder, "request.json", command)
        return go(f"/audits/{name}")

    def _audit(name):
        folder = project.record_folder("audits", name)
        record = read_json(folder / "request.json") or {}
        package = folder / "package"
        found = {"name": name, "folder": folder, "record": record, "state": jobs.state(folder, record),
                 "log": jobs.log_tail(folder)}
        if (package / "manifest.json").is_file() and found["state"] != "running":
            found.update(
                manifest=read_json(package / "manifest.json"), safety=read_json(package / "safety-report.json"),
                expected=read_json(package / "expected-output.json"), feasibility=read_json(package / "feasibility.json"),
                review=read_json(package / "plan-review.json"), status=audit.status(package),
                readme=(package / "README.md").read_text(encoding="utf-8"),
                specification=(package / "specification.md").read_text(encoding="utf-8"),
                script=(package / "query.sql").read_text(encoding="utf-8"),
                needs=audit.README_WORDING.get(read_json(package / "manifest.json")["execution_class"], ""))
        elif (package / "feasibility.json").is_file() and found["state"] != "running":
            found["feasibility"] = read_json(package / "feasibility.json")
        return found

    def audit_page(request: Request):
        try:
            found = _audit(request.path_params["name"])
        except ProjectError as error:
            return problem(request, error, "/audits")
        return page(request, "audit.html", a=found, state_words=STATE_WORDS, plan_message=request.query_params.get("plan"))

    def audit_progress(request: Request):
        try:
            found = _audit(request.path_params["name"])
        except ProjectError as error:
            return problem(request, error, "/audits")
        if found["state"] != "running":
            return Response(status_code=200, headers={"HX-Refresh": "true"})
        return page(request, "progress.html", job=found, url=f"/audits/{found['name']}/progress", state_words=STATE_WORDS)

    async def review_plan(request: Request):
        if not await same_origin(request):
            return PlainTextResponse("The workbench accepts a form only from its own pages.", status_code=403)
        name = request.path_params["name"]
        try:
            folder = project.record_folder("audits", name)
        except ProjectError as error:
            return problem(request, error, "/audits")
        form = await request.form()
        upload, pasted = form.get("file"), (form.get("plan") or "").strip()
        if upload is not None and getattr(upload, "filename", ""):
            data, filename = await upload.read(), Path(upload.filename).name
        elif pasted:
            data, filename = pasted.encode("utf-8"), "pasted.sqlplan"
        else:
            return problem(request, "Upload or paste the estimated plan that the database analyst saved.", f"/audits/{name}")
        plans = folder / "plans"
        plans.mkdir(exist_ok=True)
        stem = Path(filename).stem if safe_ok(Path(filename).stem) else "plan"
        target = plans / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{stem}.sqlplan"
        target.write_bytes(data)
        code, said = jobs.run_now(project, jobs.module("audit", "plan", folder / "package", target))
        (folder / "plan-log.txt").write_text(said + "\n", encoding="utf-8")
        if code:
            return page(request, "problem.html", status=400, message=said.splitlines()[-1] if said else
                        "The plan review did not finish.", back=f"/audits/{name}")
        return go(f"/audits/{name}#plan")

    # The test on made-up rows.

    def runs_page(request: Request):
        return page(request, "runs.html", runs=[_run_summary(project, r) for r in project.runs()], worlds=worlds(project))

    async def start_run(request: Request):
        if not await same_origin(request):
            return PlainTextResponse("The workbench accepts a form only from its own pages.", status_code=403)
        form = await request.form()
        world = form.get("world") or "fixtures"
        if world not in {w for w, _ in worlds(project)}:
            return problem(request, "Choose one of the worlds that the form offers.", "/runs")
        try:
            rows = int(form.get("rows") or 200)
        except ValueError:
            rows = 0
        if not 10 <= rows <= 100_000:
            return problem(request, "The number of rows is a whole number from 10 to 100,000.", "/runs")
        profile = form.get("profile") if form.get("profile") in ("fast", "full") else "fast"
        engine = form.get("engine") if form.get("engine") in ("duckdb", "sqlserver") else "duckdb"
        vocabulary = form.get("vocabulary") if form.get("vocabulary") in testbed.VOCABULARIES else None
        name = project.unique("runs", f"{dt.datetime.now():%Y-%m-%d_%H%M%S}_{profile}")
        folder = project.folder("runs") / name
        folder.mkdir()
        (folder / "run.json").write_text(json.dumps({"world": world, "rows": rows, "profile": profile, "engine": engine,
                                                     "vocabulary": vocabulary}, indent=2) + "\n", encoding="utf-8")
        command = jobs.module("testbed", "run", "--world", world, "--out", folder / "out", "--rows", rows,
                              "--profile", profile, "--engine", engine)
        if vocabulary:
            command += ["--vocabulary", vocabulary]
        jobs.start(project, folder, "run.json", command)
        return go(f"/runs/{name}")

    def _run(name):
        folder = project.record_folder("runs", name)
        record = read_json(folder / "run.json") or {}
        report = read_json(folder / "out" / "report.json")
        return {"name": name, "folder": folder, "record": record, "state": jobs.state(folder, record),
                "log": jobs.log_tail(folder), "report": report}

    def run_page(request: Request):
        try:
            found = _run(request.path_params["name"])
        except ProjectError as error:
            return problem(request, error, "/runs")
        report = found["report"]
        checks = {c["check"]: c for c in (report or {}).get("checks", [])}
        return page(request, "run.html", r=found, report=report, checks=checks, state_words=STATE_WORDS)

    def run_progress(request: Request):
        try:
            found = _run(request.path_params["name"])
        except ProjectError as error:
            return problem(request, error, "/runs")
        if found["state"] != "running":
            return Response(status_code=200, headers={"HX-Refresh": "true"})
        return page(request, "progress.html", job=found, url=f"/runs/{found['name']}/progress", state_words=STATE_WORDS)

    def step_page(request: Request):
        try:
            found = _run(request.path_params["name"])
        except ProjectError as error:
            return problem(request, error, "/runs")
        report, file = found["report"] or {}, request.path_params["file"]
        step = next((s for s in report.get("steps", []) if s["file"] == file), None)
        if step is None:
            return problem(request, f"The run's report names no step {file}.", f"/runs/{found['name']}")
        traced = next((s for s in report["reconciliation"]["steps"] if s["step"] == file), None)
        scenarios = [s for s in report.get("scenarios", []) if file in (s.get("steps") or [])]
        unexplained = [u for u in report["reconciliation"]["coverage"]["unexplained"]["items"]
                       if u.get("step") == file or file in (u.get("steps") or [])]
        _, _, conversion = testbed.resolve_world(found["record"].get("world", "fixtures"))
        source = conversion / file
        sql = source.read_text(encoding="utf-8") if source.is_file() else None
        return page(request, "step.html", r=found, step=step, traced=traced, scenarios=scenarios,
                    unexplained=unexplained, sql=sql)

    routes = [
        Route("/", overview),
        Route("/schemas", add_schema, methods=["POST"]),
        Route("/schemas/{name}", schema_page),
        Route("/questions", add_question, methods=["POST"]),
        Route("/questions/{name}", question_page),
        Route("/answerable", answerable),
        Route("/audits", audits_page),
        Route("/audits", start_audit, methods=["POST"]),
        Route("/audits/{name}", audit_page),
        Route("/audits/{name}/progress", audit_progress),
        Route("/audits/{name}/plan", review_plan, methods=["POST"]),
        Route("/runs", runs_page),
        Route("/runs", start_run, methods=["POST"]),
        Route("/runs/{name}", run_page),
        Route("/runs/{name}/progress", run_progress),
        Route("/runs/{name}/steps/{file}", step_page),
        Mount("/static", StaticFiles(directory=HERE / "static"), name="static"),
    ]
    if has_describe:
        routes.append(Mount("/describe", StaticFiles(directory=describe_folder, html=True), name="describe"))
    app = Starlette(routes=routes, middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=hosts or HOSTS)])
    app.state.project = project
    return app


def safe_ok(name):
    try:
        safe_name(name)
        return True
    except ProjectError:
        return False


def _run_summary(project, record):
    folder = project.folder("runs") / record["name"]
    report = read_json(folder / "out" / "report.json") or {}
    summary = report.get("summary") or {}
    return dict(record, state=jobs.state(folder, record), outcome=summary.get("outcome"),
                scenarios=summary.get("scenarios"), scenarios_passed=summary.get("scenarios_passed"),
                seconds=summary.get("seconds"))
