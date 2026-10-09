"""The dependency rules of docs/contract.md, section 6, checked against every import in the core.

schemalyser/LAYERS.toml gives the layer of every module. The test reads each module's imports with ast, including those
inside functions and those made by importlib.import_module, resolves each import of the package to a module, and fails
on any edge that breaks a rule. An import of a module that LAYERS.toml does not declare fails as well.

A superseded module is exempt as an importer until it is retired, but it is not a way round the rules. Every edge from
an active module to a superseded one is listed in SUPERSEDED_DEBTS with the fix that will remove it. The test also
follows the imports onward through superseded modules, so that an active module that reaches a forbidden layer by way of
one or more superseded modules breaks the rule as though it imported that layer itself; each such path is listed in
DEBTS_THROUGH_SUPERSEDED with its fix.

Edges that broke the rules directly when the test was first written are listed in KNOWN_DEBTS, each with the rule it
breaks and the planned fix. For all three lists, the test passes while every breaking edge is listed, fails on any edge
that is not, and fails when a listed debt no longer exists. A further test pins each list to its entries of 9 October
2026, so that a list can only shrink as the debts are paid.
"""
import ast
from collections import deque
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python before 3.11
    import tomli as tomllib

PACKAGE = Path(__file__).resolve().parents[1] / "schemalyser"
RANK = {"shared": 0, "1": 1, "2": 2, "3": 3, "4": 4, "orchestration": 4.5, "5": 5}
NAMES = {"shared": "shared infrastructure", "1": "layer 1", "2": "layer 2", "3": "layer 3", "4": "layer 4",
         "orchestration": "orchestration", "5": "layer 5", "adapter": "a reference adapter", "superseded": "superseded"}
WORKBENCH = "schemalyser.workbench"
# The core's public commands and loaders, which are all that the workbench may import.
WORKBENCH_MAY_IMPORT = {"schemalyser.audit", "schemalyser.feasibility", "schemalyser.rolemap", "schemalyser.testbed",
                        "schemalyser.describe", "schemalyser.workspace", "schemalyser.project"}
# The folders of the repository from which no module in the core may load code.
OUTSIDE = ("tools", "site")

# The edges that broke the rules when this test was first written, as (importer, imported, the rule, the planned fix).
# An edge is one module importing another, wherever in the module the import sits. Remove an entry once its fix lands;
# the test fails while a listed edge no longer exists, so that the list only shrinks.
KNOWN_DEBTS = [
    ("schemalyser.release", "schemalyser.convert", "layer 3 may not import from layer 4",
     "Put the conversion folder's format (its fields, layers, counts and mapping rows) in a layer 3 module that both "
     "release.py and convert.py import."),
    ("schemalyser.rolemap", "schemalyser.release", "layer 2 may not import from layer 3",
     "Move _single_select, which both modules use to check a single SELECT, into the shared tier."),
    ("schemalyser.rolemap", "schemalyser.convert", "layer 2 may not import from layer 4",
     "Move rolemap's world shadow (hospital_run) to a layer 4 module."),
    ("schemalyser.rolemap", "schemalyser.harness", "layer 2 may not import from layer 4",
     "Move rolemap's command line, whose shadow command builds a world, to layer 4."),
    ("schemalyser.describe", "schemalyser.hospital", "layer 2 may not import from layer 4",
     "Have browser.py pass the invented hospital's runner into describe.py, so that describe.py imports nothing below it."),
    ("schemalyser.compare", "schemalyser.datadict", "reference adapters import from layers 1 and 3 only",
     "Have compare.py read the data dictionary that datadict.py writes as a file."),
    ("schemalyser.transplant", "schemalyser.convert", "reference adapters import from layers 1 and 3 only",
     "Have transplant.py read the conversion folder that convert.py's format describes as files, through the layer 3 "
     "module that will hold that format."),
]

# The edges from an active module to a superseded one, as (importer, superseded module, the planned fix). Remove an
# entry once its fix lands or the superseded module is retired; an edge that is not listed fails the test.
SUPERSEDED_DEBTS = [
    # Empty since the superseded modules were retired on 9 October 2026 (B8 and C4).
]

# The paths by which an active module reaches a module of a layer it may not import, through one or more superseded
# modules, as (importer, (the superseded modules on the way, in order), the module reached, the rule, the planned fix).
# The path is the shortest one, found by visiting imports in alphabetical order.
DEBTS_THROUGH_SUPERSEDED = [
    # Empty since the superseded modules were retired on 9 October 2026 (B8 and C4).
]


def _layers():
    return tomllib.loads((PACKAGE / "LAYERS.toml").read_text(encoding="utf-8"))["modules"]


def _modules():
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = ("schemalyser",) + path.relative_to(PACKAGE).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found[".".join(parts)] = path
    return found


def _relative(package, level, module):
    base = package.split(".")
    base = base[:len(base) - (level - 1)]
    return ".".join(base + ([module] if module else []))


def _callee(node):
    """The name of the function that a call calls, without its module: import_module for importlib.import_module."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _imports(name, path, known):
    """[(imported module, line, names taken)] for every import of the package in the module. A module that the import
    names but that does not exist in the package is returned by the name the import gives it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
    edges = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "schemalyser":
                    edges.append((alias.name, node.lineno, []))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                module = _relative(package, node.level, node.module)
            elif node.module and node.module.split(".")[0] == "schemalyser":
                module = node.module
            else:
                continue
            for alias in node.names:
                full = f"{module}.{alias.name}"
                edges.append((full, node.lineno, []) if full in known else (module, node.lineno, [alias.name]))
        elif isinstance(node, ast.Call) and _callee(node) in ("import_module", "__import__") and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            target = node.args[0].value
            if target.startswith("."):
                level = len(target) - len(target.lstrip("."))
                edges.append((_relative(package, level, target.lstrip(".") or None), node.lineno, []))
            elif target.split(".")[0] == "schemalyser":
                edges.append((target, node.lineno, []))
    return tree, edges


def _surface(module):
    return module == WORKBENCH or module.startswith(WORKBENCH + ".")


def _broken(importer, layer, imported, held):
    """The rule that the edge breaks, in a sentence, or None."""
    if held == "superseded":
        return None
    if _surface(imported) and not _surface(importer):
        return "no module in the core imports from the workbench"
    if held == "5" and not (_surface(importer) and _surface(imported)):
        return "layer 5 is imported by nothing"
    if held == "adapter" and layer != "adapter":
        return "reference adapters are imported by nothing; the core reads the files they write"
    if layer == "adapter":
        if held not in ("shared", "1", "3", "adapter"):
            return "reference adapters import from layers 1 and 3 only"
        return None
    if layer == "shared":
        return None if held == "shared" else "shared infrastructure imports only shared infrastructure"
    if RANK[held] > RANK[layer]:
        return f"{NAMES[layer]} may not import from {NAMES[held]}, which is below it"
    return None


def _names_outside(node):
    """Whether an expression names tools/ or site/ in a string, such as ROOT / "tools" / "harness.py"."""
    for part in ast.walk(node):
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            if any(piece in OUTSIDE for piece in part.value.replace("\\", "/").split("/")):
                return True
    return False


def _file_loads(tree):
    """[(line, how)] for every way in which a module loads code from outside the package, for example from tools/:
    a module loaded from a file by its path, a script run by runpy, any change to sys.path, an import of a top-level
    package named tools or site, and a dynamic import whose module is not a constant name in the package."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "path" and isinstance(node.value, ast.Name) \
                and node.value.id == "sys":
            found.append((node.lineno, "it reads or changes sys.path"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in OUTSIDE:
                    found.append((node.lineno, f"it imports {alias.name}"))
                elif top == "runpy":
                    found.append((node.lineno, "it imports runpy"))
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            top = node.module.split(".")[0]
            if top in OUTSIDE:
                found.append((node.lineno, f"it imports from {node.module}"))
            elif node.module == "sys" and any(alias.name == "path" for alias in node.names):
                found.append((node.lineno, "it takes sys.path, which it can then change"))
            elif top == "runpy":
                found.append((node.lineno, "it imports from runpy"))
        elif isinstance(node, ast.Call):
            callee = _callee(node)
            if callee in ("spec_from_file_location", "SourceFileLoader", "SourcelessFileLoader", "spec_from_loader"):
                found.append((node.lineno, f"it loads a module from a file by its path with {callee}"))
            elif callee in ("run_path", "run_module"):
                found.append((node.lineno, f"it runs code with runpy's {callee}"))
            elif callee in ("import_module", "__import__"):
                first = node.args[0] if node.args else None
                constant = isinstance(first, ast.Constant) and isinstance(first.value, str)
                if not constant or not (first.value.startswith(".") or first.value.split(".")[0] == "schemalyser"):
                    found.append((node.lineno, f"it imports a module with {callee} that is not a constant name in "
                                               "the package"))
            elif callee in ("exec", "compile") and any(_names_outside(arg) for arg in node.args):
                found.append((node.lineno, f"it passes a path in tools/ or site/ to {callee}"))
    return sorted(set(found))


def _private_uses(tree, edges):
    """Names starting with an underscore that a module takes from the core, by import or by attribute."""
    aliases = {}
    for node in ast.walk(tree):
        # Inside the workbench, a relative import of two levels or more reaches the core.
        if isinstance(node, ast.ImportFrom) and node.level >= 2:
            for alias in node.names:
                aliases[alias.asname or alias.name] = node.lineno
    found = [(module, line, n) for module, line, names in edges for n in names
             if n.startswith("_") and not _surface(module)]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases \
                and node.attr.startswith("_"):
            found.append((node.value.id, node.lineno, node.attr))
    return found


def test_every_module_declares_its_layer():
    layers, modules = _layers(), _modules()
    missing = sorted(set(modules) - set(layers))
    gone = sorted(set(layers) - set(modules))
    unknown = sorted(m for m, v in layers.items() if v["layer"] not in NAMES)
    assert not missing, "These modules declare no layer in schemalyser/LAYERS.toml: " + ", ".join(missing)
    assert not gone, "LAYERS.toml names modules that do not exist: " + ", ".join(gone)
    assert not unknown, "These modules have a layer that the contract does not name: " + ", ".join(unknown)


def _graph():
    """{module: [(imported module, line, names taken)]} for every module of the package, superseded ones included."""
    modules = _modules()
    graph, trees = {}, {}
    for name, path in modules.items():
        trees[name], edges = _imports(name, path, modules)
        graph[name] = [edge for edge in edges if edge[0] != name]
    return modules, graph, trees


def _through_superseded(name, graph, layers):
    """{(the superseded modules on the way, the active module reached): line of the first edge} for every active module
    that name reaches through one or more superseded modules, by the shortest path in alphabetical order."""
    reached, seen = {}, set()
    queue = deque()
    for imported, line, _ in sorted(graph[name]):
        if layers.get(imported, {}).get("layer") == "superseded" and imported not in seen:
            seen.add(imported)
            queue.append(((imported,), line))
    while queue:
        via, line = queue.popleft()
        for imported, _, _ in sorted(graph.get(via[-1], [])):
            held = layers.get(imported, {}).get("layer")
            if held is None or imported == name:
                continue
            if held == "superseded":
                if imported not in seen:
                    seen.add(imported)
                    queue.append((via + (imported,), line))
            elif imported not in {reached_module for _, reached_module in reached}:
                reached[(via, imported)] = line
    return reached


def _breaches():
    """(breaches, superseded, through): every breaking edge in a sentence, keyed by (importer, imported) or by
    (importer, None) for a load from outside the package; every edge from an active module to a superseded one, keyed
    by (importer, imported); and every breaking path through superseded modules, keyed by (importer, via, reached)."""
    layers = _layers()
    modules, graph, trees = _graph()
    breaches, superseded, through = {}, {}, {}
    for name, path in modules.items():
        layer = layers.get(name, {}).get("layer")
        where = str(path.relative_to(PACKAGE.parent))
        for imported, line, _ in graph[name]:
            if imported not in layers:
                breaches.setdefault((name, imported), []).append(
                    f"{where}:{line}: {name} imports {imported}, which declares no layer in schemalyser/LAYERS.toml, "
                    "and every module that the core imports must declare one.")
        if layer is None or layer == "superseded":
            continue
        for imported, line, _ in graph[name]:
            held = layers.get(imported, {}).get("layer")
            if held is None:
                continue
            if held == "superseded":
                superseded.setdefault((name, imported), []).append(
                    f"{where}:{line}: {name} ({NAMES[layer]}) imports {imported}, which is superseded.")
                continue
            rule = _broken(name, layer, imported, held)
            if rule:
                breaches.setdefault((name, imported), []).append(
                    f"{where}:{line}: {name} ({NAMES[layer]}) imports {imported} ({NAMES[held]}), and {rule}.")
        for (via, reached), line in _through_superseded(name, graph, layers).items():
            held = layers[reached]["layer"]
            rule = _broken(name, layer, reached, held)
            if rule:
                through.setdefault((name, via, reached), []).append(
                    f"{where}:{line}: {name} ({NAMES[layer]}) reaches {reached} ({NAMES[held]}) through "
                    f"{' and then '.join(via)}, which {'is' if len(via) == 1 else 'are'} superseded, and {rule}.")
        for line, how in _file_loads(trees[name]):
            breaches.setdefault((name, None), []).append(
                f"{where}:{line}: {name} loads code from outside the package, because {how}, "
                "and no module in the core imports from site/ or tools/.")
        if _surface(name):
            for imported, line, _ in graph[name]:
                if not _surface(imported) and imported not in WORKBENCH_MAY_IMPORT:
                    breaches.setdefault((name, imported), []).append(
                        f"{where}:{line}: the workbench imports {imported}, "
                        "and the workbench imports only the core's public commands and loaders.")
            for module, line, attr in _private_uses(trees[name], graph[name]):
                breaches.setdefault((name, f"{module}.{attr}"), []).append(
                    f"{where}:{line}: the workbench uses {module}.{attr}, "
                    "and the workbench uses only the core's public commands and loaders.")
    return breaches, superseded, through


def _keys():
    return ({(importer, imported) for importer, imported, _, _ in KNOWN_DEBTS},
            {(importer, imported) for importer, imported, _ in SUPERSEDED_DEBTS},
            {(importer, tuple(via), reached) for importer, via, reached, _, _ in DEBTS_THROUGH_SUPERSEDED})


def test_imports_keep_to_the_dependency_rules():
    breaches, _, _ = _breaches()
    listed, _, _ = _keys()
    new = sorted(line for edge, lines in breaches.items() if edge not in listed for line in lines)
    assert not new, "These imports break the dependency rules of docs/contract.md, section 6:\n" + "\n".join(sorted(set(new)))


def test_every_import_of_a_superseded_module_by_an_active_one_is_a_listed_debt_with_its_fix():
    _, superseded, _ = _breaches()
    _, listed, _ = _keys()
    new = sorted(line for edge, lines in superseded.items() if edge not in listed for line in lines)
    assert not new, ("These active modules import superseded ones, and each such edge must be listed in "
                     "SUPERSEDED_DEBTS in tests/test_contract.py with its planned fix:\n" + "\n".join(new))


def test_an_active_module_does_not_reach_a_forbidden_layer_through_superseded_modules_unless_the_path_is_listed():
    _, _, through = _breaches()
    _, _, listed = _keys()
    new = sorted(line for path, lines in through.items() if path not in listed for line in lines)
    assert not new, ("These active modules reach a layer they may not import through superseded modules, and each "
                     "such path must be listed in DEBTS_THROUGH_SUPERSEDED in tests/test_contract.py with its planned "
                     "fix:\n" + "\n".join(new))


def test_the_known_debts_only_shrink():
    breaches, superseded, through = _breaches()
    known, listed_superseded, listed_through = _keys()
    paid = [f"KNOWN_DEBTS: {importer} no longer imports {imported} ({fix})"
            for importer, imported, _, fix in KNOWN_DEBTS if (importer, imported) not in breaches]
    paid += [f"SUPERSEDED_DEBTS: {importer} no longer imports {imported} ({fix})"
             for importer, imported, fix in SUPERSEDED_DEBTS if (importer, imported) not in superseded]
    paid += [f"DEBTS_THROUGH_SUPERSEDED: {importer} no longer reaches {reached} through {', '.join(via)} ({fix})"
             for importer, via, reached, _, fix in DEBTS_THROUGH_SUPERSEDED
             if (importer, tuple(via), reached) not in through]
    assert not paid, "These debts are paid, so remove them from tests/test_contract.py:\n" + "\n".join(paid)
    assert len(known) == len(KNOWN_DEBTS), "KNOWN_DEBTS lists an edge twice."
    assert len(listed_superseded) == len(SUPERSEDED_DEBTS), "SUPERSEDED_DEBTS lists an edge twice."
    assert len(listed_through) == len(DEBTS_THROUGH_SUPERSEDED), "DEBTS_THROUGH_SUPERSEDED lists a path twice."


# The entries of each list on 9 October 2026. A debt that is paid is removed from its list and from here; nothing is
# ever added here, so that a list that has grown fails the test below.
PINNED_KNOWN_DEBTS = {
    ("schemalyser.release", "schemalyser.convert"),
    ("schemalyser.rolemap", "schemalyser.release"),
    ("schemalyser.rolemap", "schemalyser.convert"),
    ("schemalyser.rolemap", "schemalyser.harness"),
    ("schemalyser.describe", "schemalyser.hospital"),
    ("schemalyser.compare", "schemalyser.datadict"),
    ("schemalyser.transplant", "schemalyser.convert"),
}
PINNED_SUPERSEDED_DEBTS = set()
PINNED_DEBTS_THROUGH_SUPERSEDED = set()


def test_the_debt_lists_hold_exactly_their_pinned_entries_so_that_no_list_can_grow():
    for title, listed, pinned in zip(("KNOWN_DEBTS", "SUPERSEDED_DEBTS", "DEBTS_THROUGH_SUPERSEDED"), _keys(),
                                     (PINNED_KNOWN_DEBTS, PINNED_SUPERSEDED_DEBTS, PINNED_DEBTS_THROUGH_SUPERSEDED)):
        grown = sorted(map(str, listed - pinned))
        assert not grown, (f"{title} has grown beyond its entries of 9 October 2026. A new breach is fixed, not "
                           "listed:\n" + "\n".join(grown))
        paid = sorted(map(str, pinned - listed))
        assert not paid, (f"These entries have left {title}, so remove them from its pinned copy as well:\n"
                          + "\n".join(paid))


def test_the_rules_allow_the_shared_tier_and_imports_within_a_layer():
    # Any layer may import a shared module, and a shared module only another shared one.
    for layer in ("1", "2", "3", "4", "orchestration", "5", "adapter"):
        assert _broken("schemalyser.a", layer, "schemalyser.b", "shared") is None
    assert _broken("schemalyser.a", "shared", "schemalyser.b", "2") is not None
    # Modules within one layer may import one another, and one adapter another.
    for layer in ("1", "2", "3", "4", "orchestration", "adapter"):
        assert _broken("schemalyser.a", layer, "schemalyser.b", layer) is None
    # Layer 5 is imported by nothing in the core, and an adapter by nothing but another adapter.
    assert _broken("schemalyser.audit", "orchestration", "schemalyser.browser", "5") is not None
    assert _broken("schemalyser.audit", "orchestration", "schemalyser.compare", "adapter") is not None


def test_every_way_of_loading_code_from_tools_or_site_is_caught():
    loads = [
        'import importlib.util\nspec = importlib.util.spec_from_file_location("h", ROOT / "tools" / "h.py")',
        'import sys\nsys.path.insert(0, str(ROOT / "tools"))',
        'import sys\nsys.path.append("site")',
        'import sys\nsys.path += ["tools"]',
        'from sys import path\npath.insert(0, "tools")',
        'import importlib\nharness = importlib.import_module("harness")',
        'import importlib\nharness = importlib.import_module(name)',
        'harness = __import__("tools.sqlserver.harness")',
        'import runpy\nrunpy.run_path(str(ROOT / "tools" / "h.py"))',
        'from runpy import run_module\nrun_module("harness")',
        'import tools.sqlserver.harness',
        'from site.build import pages',
        'exec(compile(open(ROOT / "tools" / "h.py").read(), "h", "exec"))',
    ]
    for text in loads:
        assert _file_loads(ast.parse(text)), f"This load from outside the package was not caught:\n{text}"
    # An import within the package, made dynamically, is an edge like any other and is not a load from outside.
    assert not _file_loads(ast.parse('import importlib\nmodule = importlib.import_module("schemalyser.audit")'))
    assert not _file_loads(ast.parse('import importlib\nmodule = importlib.import_module(".audit", "schemalyser")'))


def test_a_dynamic_import_within_the_package_is_followed_as_an_edge(tmp_path):
    path = tmp_path / "example.py"
    path.write_text('import importlib\nmodule = importlib.import_module("schemalyser.target")\n'
                    'other = importlib.import_module(".checks", __package__)\n', encoding="utf-8")
    _, edges = _imports("schemalyser.example", path, {"schemalyser.target", "schemalyser.checks"})
    assert {edge[0] for edge in edges} == {"schemalyser.target", "schemalyser.checks"}


def test_an_import_of_a_module_that_does_not_exist_is_kept_by_its_own_name_so_that_it_fails(tmp_path):
    path = tmp_path / "example.py"
    path.write_text("from . import nowhere\nfrom .elsewhere import thing\nimport schemalyser.absent\n",
                    encoding="utf-8")
    _, edges = _imports("schemalyser.example", path, {"schemalyser"})
    names = {edge[0] for edge in edges}
    assert {"schemalyser.elsewhere", "schemalyser.absent"} <= names
    assert names <= {"schemalyser", "schemalyser.elsewhere", "schemalyser.absent"}
