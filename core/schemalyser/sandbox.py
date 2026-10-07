"""The synthetic sandbox: a DuckDB database built from a catalogue and an inventory.

Every value is invented. The inventory says which tables are in use, which columns the requests
join, and which columns they compare as earlier and later. Where the inventory also carries check
results, the generator uses them: listed values with their frequencies, the relative sizes of the
tables, which joined columns are unique, how often a joined column is empty, and the years in
which the rows of a date column fall.

Rows line up across tables. Row i of every table belongs to the same notional subject: a unique
key holds i, and a key that refers to another table holds i modulo that table's size. Table sizes
are the chosen number of rows times a power of two, so that the same subject is reached along any
path of joins. Dates that the requests compare are built from that subject, in the order the
comparisons imply, so that a reading falls inside its anaesthetic.

Where the check results hold a fanout result for a join, every parent no longer has the same number
of children: the child's rows are assigned to parents in the reported proportions, and the keys and
the realistic values follow a lineage (see Lineage) instead of i modulo a table's size. Without a
fanout result the sandbox is built exactly as described above.

Filler text is never longer than the catalogue allows its column. The first column of a table,
where the catalogue says it may not be empty, keys the table's rows and is given unique values.
"""
import csv
import io
import math
import random
import re
import zipfile
import zlib

import duckdb

from . import vocabulary as v
from . import roles as meanings
from . import tuning as tunable
from .checks import FANOUT_BANDS, Checks, ChecksError
from .realistic import Realism
from .rules import SiteRules
from .translate import SERVER_SCHEMA, Unreadable, Unsupported, to_duckdb

ROW_LIMIT = 200
LARGEST_MULTIPLE = 5        # a table is at most 2 ** 5 times the chosen number of rows
STEP_SECONDS = 1800         # the gap between one compared date and the next
NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
SET_VARIABLE = re.compile(r"^SET VARIABLE (var_\w+)")
# The most rows that a key value is given when it falls in the open-ended band of a fanout result.
MOST_CHILD_ROWS = 20


class InventoryError(ValueError):
    """The inventory file is not a zip that Schemalyser produced."""


def _table(archive, name):
    return list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8"))))


def read_inventory(data, catalogue):
    """Returns the tables in use, the joined pairs, the compared pairs, any check results, the roles and the tuning."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            tables = {row["table"] for row in _table(archive, "elements.csv")}
            if "checked.csv" in names:
                # The definition tables that the check script reads, which are built as well.
                tables |= {row["table"] for row in _table(archive, "checked.csv")}
            pairs = [((row["left_table"], row["left_column"]), (row["right_table"], row["right_column"]))
                     for row in _table(archive, "joins.csv")]
            compared = []
            if "comparisons.csv" in names:
                compared = [((row["left_table"], row["left_column"]), (row["right_table"], row["right_column"]))
                            for row in _table(archive, "comparisons.csv")]
            checks = None
            if "checks.csv" in names:
                # These results were accepted once already, under the site rules that set each column's limit.
                checks = Checks.from_csv(archive.read("checks.csv").decode("utf-8"), catalogue, SiteRules(),
                                         limit_values=False)
            roles = meanings.from_csv(archive.read("roles.csv").decode("utf-8"), catalogue) if "roles.csv" in names else []
            # The overrides were accepted once already; tuning.csv checks every row again.
            tuning = tunable.Tuning(*tunable.from_csv(archive.read("tuning.csv").decode("utf-8"))) \
                if "tuning.csv" in names else tunable.Tuning()
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, ChecksError) as error:
        raise InventoryError from error
    return tables, pairs, compared, checks, roles, tuning


def duck_type(data_type, scale=None):
    name = data_type.strip().upper()
    if name in ("NUMERIC", "DECIMAL") and scale == 0:
        return "BIGINT"
    if name.startswith(("DATETIME", "SMALLDATETIME", "TIMESTAMP")):
        return "TIMESTAMP"
    if name == "DATE":
        return "DATE"
    if name == "TIME":
        return "TIME"
    if name in ("INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BIT"):
        return "BIGINT"
    if name in ("NUMERIC", "DECIMAL", "MONEY", "SMALLMONEY"):
        return "DECIMAL(18,4)"
    if name in ("FLOAT", "REAL", "DOUBLE"):
        return "DOUBLE"
    return "VARCHAR"


def _quoted(name):
    """A name inside double quotes. The catalogue accepts only plain names; this is a second defence."""
    return '"' + name.replace('"', '""') + '"'


def _text(name):
    """A name inside single quotes."""
    return "'" + name.replace("'", "''") + "'"


# The database may not reach outside itself: no files, no network, no extensions, and no change to these settings.
LOCKED_DOWN = ("autoinstall_known_extensions=false", "autoload_known_extensions=false",
               "enable_external_access=false", "lock_configuration=true")


def _room(length):
    """The longest text a column may hold, or None where the catalogue sets no limit."""
    return length if length is not None and length > 0 else None


def _filler(kind, salt, length=None):
    spread = f"hash(i + {salt})"
    if kind == "BIGINT":
        return f"CAST(1 + {spread} % 6 AS BIGINT)"
    if kind == "DECIMAL(18,4)":
        return f"CAST(({spread} % 10000) / 100.0 AS DECIMAL(18,4))"
    if kind == "DOUBLE":
        return f"({spread} % 10000) / 100.0"
    if kind == "TIMESTAMP":
        return f"TIMESTAMP '2023-01-01' + to_seconds(CAST({spread} % 94608000 AS BIGINT))"
    if kind == "DATE":
        return f"DATE '2023-01-01' + CAST({spread} % 1095 AS INTEGER)"
    if kind == "TIME":
        return f"TIME '00:00:00' + to_seconds(CAST({spread} % 86400 AS BIGINT))"
    # Text filler is made of digits, because requests often convert a text column to a number.
    # A column that holds a single character is given a single digit.
    return f"CAST({spread} % {10 if _room(length) == 1 else 40} AS VARCHAR)"


def _row_key(kind, size, length=None):
    """Unique values for a column that keys its table's rows, or None where the column cannot hold them."""
    if kind in ("BIGINT", "DECIMAL(18,4)", "DOUBLE"):
        return f"CAST(1 + i AS {kind})"
    if kind == "VARCHAR" and (_room(length) is None or len(str(size)) <= _room(length)):
        return "CAST(1 + i AS VARCHAR)"
    if kind == "TIMESTAMP":
        return "TIMESTAMP '2023-01-01' + to_minutes(CAST(i AS BIGINT))"
    if kind == "DATE":
        return "DATE '2023-01-01' + CAST(i AS INTEGER)"
    if kind == "TIME" and size <= 86400:
        return "TIME '00:00:00' + to_seconds(CAST(i AS BIGINT))"
    return None


def _within(kind, value, length):
    """A text value cut to the length that the catalogue allows its column."""
    return f"left({value}, {_room(length)})" if kind == "VARCHAR" and _room(length) is not None else value


def _weighted(listed, kind, salt):
    """An expression that picks among listed values in proportion to their counts."""
    choices = []
    for value, _, count in listed:
        if kind == "VARCHAR":
            choices.append(("'" + value.replace("'", "''") + "'", max(count, 1)))
        elif NUMBER.match(value):
            choices.append((value, max(count, 1)))
    if not choices:
        return None
    total, running, branches = sum(c for _, c in choices), 0, []
    for literal, count in choices:
        running += count
        branches.append(f"WHEN {running} > hash(i + {salt}) % {total} THEN {literal}")
    return f"CAST(CASE {' '.join(branches)} END AS {kind})"


def _moment(anchor, years):
    """A timestamp built from an anchor number: in the listed years where they are known."""
    if not years:
        return f"TIMESTAMP '2023-01-01' + to_seconds(CAST({anchor} % 94608000 AS BIGINT))"
    total, running, branches = sum(max(count, 1) for _, count in years), 0, []
    for year, count in sorted(years):
        running += max(count, 1)
        branches.append(f"WHEN {running} > {anchor} % {total} THEN TIMESTAMP '{int(year)}-01-01'")
    return f"CASE {' '.join(branches)} END + to_seconds(CAST(({anchor} // 7) % 31000000 AS BIGINT))"


def _same_code(value, code):
    """Whether a listed value is the code that a role names, comparing numbers as numbers."""
    value, code = str(value).strip(), str(code).strip()
    if value == code:
        return True
    return bool(NUMBER.match(value) and NUMBER.match(code)) and float(value) == float(code)


def _keys_rows(catalogue, table, column):
    """Whether the catalogue shows a column to key its table's rows: the first column, which may not be empty."""
    first = catalogue.table(table).first_column()
    return first is not None and first.name == column and first.nullable is False


def skewed(bands, children, parents, rng):
    """Assigns each of the child rows to a parent, so that the numbers of parents with one child, two,
    three to five and so on follow the bands of a fanout result. Returns a parent for each child row.

    Each parent with children falls in a band in proportion to the counts, and draws its number of
    children evenly within that band. The total is then made to equal the number of child rows,
    keeping each parent within its band where that is possible. A parent may be left without
    children, as one is where the real table has fewer child rows than parents with children.
    """
    usable = [(low, (high - 1) if high is not None else MOST_CHILD_ROWS, count)
              for label, low, high in FANOUT_BANDS for name, count in bands if name == label and count > 0]
    if not usable or children < 1 or parents < 1:
        return None
    weight = sum(c for _, _, c in usable)
    mean = sum((low + high) / 2 * c for low, high, c in usable) / weight

    def quotas(m):
        exact = [m * c / weight for _, _, c in usable]
        whole = [int(x) for x in exact]
        for k in sorted(range(len(usable)), key=lambda k: whole[k] - exact[k])[:m - sum(whole)]:
            whole[k] += 1
        return whole

    m = min(parents, children, max(1, round(children / mean)))
    while m > 1 and sum(q * low for q, (low, _, _) in zip(quotas(m), usable)) > children:
        m -= 1
    bounds = [(low, high) for q, (low, high, _) in zip(quotas(m), usable) for _ in range(q)]
    rng.shuffle(bounds)
    counts = [rng.randint(low, high) for low, high in bounds]
    extra = children - sum(counts)
    if extra < 0:
        # Too many: take rows away from parents above the least of their band, in proportion to their room.
        room = [c - low for c, (low, _) in zip(counts, bounds)]
        _spread_change(counts, room, -extra, -1, rng)
    elif extra > 0:
        # Too few: add rows to parents below the most of their band, and the rest to the largest parents.
        room = [high - c for c, (_, high) in zip(counts, bounds)]
        left = _spread_change(counts, room, extra, 1, rng)
        largest = sorted(range(len(counts)), key=lambda k: -counts[k])
        for step in range(left):
            counts[largest[step % len(largest)]] += 1
    chosen = rng.sample(range(parents), len(counts))
    assigned = [parent for parent, count in zip(chosen, counts) for _ in range(count)]
    rng.shuffle(assigned)
    return assigned


def _spread_change(counts, room, amount, sign, rng):
    """Moves counts by up to their room, in proportion to it, until the amount is used. Returns what is left."""
    total = sum(room)
    if total <= 0:
        return amount
    used = 0
    for k, r in enumerate(room):
        step = min(r, amount * r // total)
        counts[k] += sign * step
        room[k] -= step
        used += step
    order = [k for k in range(len(counts)) if room[k] > 0]
    rng.shuffle(order)
    for k in order:
        if used == amount:
            break
        step = min(room[k], amount - used)
        counts[k] += sign * step
        used += step
    return amount - used


class Lineage:
    """Which row of every other table each row of a table belongs to, following the keys.

    Tables that are unique in one key are the same rows: row i of each is entity i. Such tables form a
    group. A key that is not unique refers from its table's group to the group of the tables in which
    the key is unique, and gives each row a position there: by default row i refers to i modulo the
    number of positions, as the positional scheme does, and where a fanout result is in hand the
    positions follow it. A group's rows then reach, through each position, everything that the group
    they refer to reaches. Where two keys of one group reach the same group, the second is chosen so
    that both agree, so that a case's patient is always its hospital visit's patient.

    This is used only where a fanout result applies, so that a sandbox built without one is unchanged.
    """

    def __init__(self, tables, sizes, keys, unique, pools, edges, seed):
        self.sizes = sizes
        homes = {}
        for key, domain in sorted(keys.items()):
            if unique(key):
                homes.setdefault(domain, []).append(key[0])
        parent = {t: t for t in tables}

        def find(t):
            while parent[t] != t:
                parent[t] = parent[parent[t]]
                t = parent[t]
            return t

        for members in homes.values():
            for t in members[1:]:
                parent[find(t)] = find(members[0])
        self.group = {t: find(t) for t in tables}
        self.group_size = {}
        for t in tables:
            g = self.group[t]
            self.group_size[g] = max(self.group_size.get(g, 0), sizes[t])
        self.home = {d: self.group[members[0]] for d, members in homes.items()}
        # Each reference from a group to a key: the fanout edge that skews it, if any.
        refs = {}
        for key, domain in sorted(keys.items()):
            g = self.group[key[0]]
            if unique(key) or domain not in self.home or self.home[domain] == g:
                continue
            refs.setdefault((g, domain), None)
            if key in edges:
                refs[(g, domain)] = (key, edges[key])
        self.applied = set()     # the child columns whose positions a fanout result drew
        self.shaped = set()      # the (group, key domain) whose positions differ from i modulo the positions
        self.conflicts = 0
        self.positions = {}      # (group, key domain) -> the position of each row of the group
        self.reach = {}          # group -> {group reached: the row of that group for each row of this one}
        # The references are accepted one at a time, those that a fanout result skews first, and one that
        # would close a loop of keys is left loose: it keeps its positions and reaches nothing through them.
        accepted, loose = {}, set()

        def reaches(start, goal):
            seen, todo = set(), [start]
            while todo:
                g = todo.pop()
                if g == goal:
                    return True
                if g not in seen:
                    seen.add(g)
                    todo.extend(accepted.get(g, ()))
            return False

        for (g, d), edge in sorted(refs.items(), key=lambda item: (item[1] is None, item[0][0], item[0][1])):
            target = self.home[d]
            if reaches(target, g):
                loose.add((g, d))
            else:
                accepted.setdefault(g, set()).add(target)
        order, done = [], set()

        def visit(g):
            done.add(g)
            for target in sorted(accepted.get(g, ())):
                if target not in done:
                    visit(target)
            order.append(g)

        for g in sorted(set(self.group.values())):
            if g not in done:
                visit(g)
        for g in order:
            size = self.group_size[g]
            reach = {g: list(range(size))}
            mine = sorted(((d, edge) for (h, d), edge in refs.items() if h == g),
                          key=lambda item: (item[1] is None, -len(self.reach.get(self.home[item[0]], ())), item[0]))
            for d, edge in mine:
                target = self.home[d]
                positions = [i % pools[d] for i in range(size)]
                if edge is not None:
                    (child, column), bands = edge
                    rng = random.Random(zlib.crc32(f"{child}.{column}".encode()) + seed)
                    drawn = skewed(bands, sizes[child], pools[d], rng)
                    if drawn is not None:
                        positions[:len(drawn)] = drawn
                        self.applied.add((child, column))
                if (g, d) not in loose and target in self.reach:
                    positions = self._agree(reach, self.reach[target], positions, pools[d])
                if any(p != i % pools[d] for i, p in enumerate(positions)):
                    self.shaped.add((g, d))
                if (g, d) not in loose and target in self.reach:
                    for k, rows in self.reach[target].items():
                        if k not in reach:
                            reach[k] = [rows[p] if 0 <= p < len(rows) else -1 for p in positions]
                self.positions[(g, d)] = positions
            self.reach[g] = reach

    def _agree(self, reach, theirs, positions, pool):
        """Positions that reach, through the group referred to, the same rows as this group already does."""
        shared = [k for k in theirs if k in reach]
        if not shared:
            return positions
        index = {}
        for p in range(min(pool, len(theirs[shared[0]]))):
            index.setdefault(tuple(theirs[k][p] for k in shared), []).append(p)
        agreed = list(positions)
        for i, p in enumerate(positions):
            wanted = tuple(reach[k][i] for k in shared)
            if 0 <= p < pool and tuple(theirs[k][p] for k in shared) == wanted:
                continue
            candidates = index.get(wanted)
            if candidates:
                agreed[i] = candidates[i % len(candidates)]
            else:
                self.conflicts += 1
        return agreed

    def shaped_by_others(self, table, domain):
        """Whether a table's key follows fanout results on other joins, through the keys, rather than i modulo."""
        return (self.group[table], domain) in self.shaped

    def position(self, table, domain):
        """The position of each row of a table in a key that it does not hold uniquely, or None."""
        found = self.positions.get((self.group[table], domain))
        return found[:self.sizes[table]] if found is not None else None

    def up(self, table, target):
        """For each row of a table, the row of the target table that it belongs to, or None where it reaches none.

        None in place of the list means that the table does not reach the target table at all.
        """
        rows = self.reach[self.group[table]].get(self.group[target])
        if rows is None:
            return None
        return [p if 0 <= p < self.sizes[target] else None for p in rows[:self.sizes[table]]]

    def down(self, table, target):
        """For each row of a table, the rows of the target table that belong to it, or None where the target
        table does not reach this one."""
        g, k = self.group[table], self.group[target]
        if g == k or g not in self.reach[k]:
            return None
        found = [[] for _ in range(self.sizes[table])]
        for row, p in enumerate(self.reach[k][g][:self.sizes[target]]):
            if 0 <= p < len(found):
                found[p].append(row)
        return found


class Sandbox:
    def __init__(self, catalogue, inventory_zip, fanout=None, also=()):
        """fanout gives designed fanout results, in the form of Checks.fanout, which the stand-in database of
        the harness uses in place of check results. also names further tables to build, such as those that the
        audit's steps read, where the catalogue holds them."""
        self.catalogue = catalogue
        tables, self.pairs, self.compared, self.checks, self.roles, self.tuning = read_inventory(inventory_zip, catalogue)
        tables = set(tables) | set(also)
        self.tables = sorted(t.name for t in map(catalogue.table, tables) if t is not None)
        self.has_values = bool(self.checks and self.checks.values)
        self.designed_fanout = dict(fanout or {})
        self.con = None
        self.lineage = None
        self.fanout = []      # each parent-child join, and where its fan-out came from, once built

    def _key(self, table, column):
        entry = self.catalogue.table(table)
        field = entry.column(column) if entry is not None else None
        return (entry.name, field.name) if field is not None and entry.name in self.tables else None

    def _groups(self, pairs):
        """Groups columns that are linked by the given pairs. Returns each column's group number."""
        parent = {}

        def find(key):
            parent.setdefault(key, key)
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        for left, right in pairs:
            a, b = self._key(*left), self._key(*right)
            if a is not None and b is not None:
                parent[find(a)] = find(b)
        roots = sorted({find(key) for key in parent})
        return {key: roots.index(find(key)) for key in parent}

    def _sizes(self, rows):
        """Each table's number of rows: the chosen number times a power of two, by its real size."""
        known = {t: n for t, n in (self.checks.rows.items() if self.checks else ()) if n > 0 and t in self.tables}
        smallest = min(known.values()) if known else 0
        sizes = {}
        for table in self.tables:
            power = min(LARGEST_MULTIPLE, max(0, round(math.log2(known[table] / smallest)))) if table in known else 0
            sizes[table] = rows * 2 ** power
        return sizes

    def _timeline(self, typed, sizes, seed):
        """For each date column that the requests compare: its anchor, its place in the order, and its years."""
        dates = {(t, c.name) for t in self.tables for c in self.catalogue.table(t).columns.values()
                 if typed(c) in ("TIMESTAMP", "DATE")}
        edges = set()
        for left, right in self.compared:
            a, b = self._key(*left), self._key(*right)
            if a in dates and b in dates and a != b:
                edges.add((a, b))
        groups = self._groups(edges)
        years = self.checks.years if self.checks else {}
        plan = {}
        for group in sorted(set(groups.values())):
            nodes = sorted(key for key, g in groups.items() if g == group)
            order = {node: 0 for node in nodes}
            for _ in range(len(nodes)):
                moved = False
                for a, b in edges:
                    if a in order and order[b] < order[a] + 1:
                        order[b], moved = order[a] + 1, True
                if not moved:
                    break
            else:
                continue    # the comparisons contradict each other, so these columns keep their filler
            span = min(sizes[table] for table, _ in nodes)
            salt = zlib.crc32(repr(nodes).encode()) + seed
            listed = next((years[node] for node in nodes if node in years), None)
            # Where any of the columns holds a date without a time, the steps are whole days.
            step = 86400 if any(typed(self.catalogue.table(t).column(c)) == "DATE" for t, c in nodes) else STEP_SECONDS
            for node in nodes:
                plan[node] = (span, order[node], salt, listed, step)
        return plan

    def build(self, rows, seed=1):
        rows = max(1, int(rows))
        self.con = duckdb.connect()
        for setting in LOCKED_DOWN:
            self.con.execute(f"SET {setting}")
        typed = lambda column: duck_type(column.data_type, column.scale)  # noqa: E731
        stats = self.checks.columns if self.checks else {}
        listed = self.checks.values if self.checks else {}
        # A code whose readings a role adds as rows of its own is not drawn among the listed values, because the
        # generator places those rows itself. The role adds them only where the check results list its code.
        added, kept = set(), dict(listed)
        for role in self.roles:
            if role.role in meanings.ADDED_ROWS and role.when_column:
                values = listed.get((role.table, role.when_column)) or []
                if any(_same_code(value, role.when_value) for value, _, _ in values):
                    added.add(role)
                    kept[(role.table, role.when_column)] = [entry for entry in kept[(role.table, role.when_column)]
                                                            if not _same_code(entry[0], role.when_value)]
        listed = kept
        years = self.checks.years if self.checks else {}
        # Joined date columns are left out of the key groups, so that they stay dates.
        domains = {key: group for key, group in self._groups(self.pairs).items()
                   if typed(self.catalogue.table(key[0]).column(key[1])) not in ("TIMESTAMP", "DATE", "TIME")}
        sizes = self._sizes(rows)
        self.date_columns = frozenset(c.name.upper() for t in self.tables for c in self.catalogue.table(t).columns.values()
                                      if typed(c) in ("TIMESTAMP", "DATE"))
        # The columns that SQL Server holds as whole numbers in every table that has them, so that a query that divides
        # them gives a whole number here as it does there.
        whole, other = set(), set()
        for t in self.tables:
            for c in self.catalogue.table(t).columns.values():
                (whole if c.data_type.strip().upper() in ("INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT") else other).add(c.name.upper())
        # The count of rows in SQL Server's own records of its tables (see _server_records) is a whole number as well.
        self.whole_columns = frozenset((whole | {"ROWS"}) - other)
        text_domains = {d for (table, column), d in domains.items()
                        if typed(self.catalogue.table(table).column(column)) == "VARCHAR"}

        # Fanout results for a join from a key that is not unique to one that is, between tables in use.
        results = {**(self.checks.fanout if self.checks else {}), **self.designed_fanout}
        edges = {}
        for (child, column, parent, key), bands in sorted(results.items()):
            a, b = self._key(child, column), self._key(parent, key)
            # A result in which every key value has one row says only that the column is unique, and changes nothing.
            repeats = any(band != FANOUT_BANDS[0][0] and count > 0 for band, count in bands)
            if a in domains and b in domains and domains[a] == domains[b] and a not in listed and a[0] != b[0] and repeats:
                edges[a] = (b, bands)
        children = set(edges)

        def unique(key):
            if key in children:
                return False    # a fanout result says that the column's values repeat
            if edges and not stats:
                # With fanout results but no others, a column is unique where the catalogue shows it to key its table.
                return _keys_rows(self.catalogue, *key)
            # Without check results every joined column is taken to be unique.
            return stats.get(key, {}).get("unique", not stats) if key in stats or not stats else False

        edges = {a: (b, bands) for a, (b, bands) in edges.items() if unique(b)}

        # Where the check results list the values of any column in a group, the group uses those
        # values, so that a category column and its lookup table agree.
        domain_values = {}
        for key in sorted(domains):
            if key in listed:
                domain_values.setdefault(domains[key], listed[key])
        for key, domain in domains.items():
            if domain in domain_values and key not in listed and stats.get(key, {}).get("unique"):
                sizes[key[0]] = len(domain_values[domain])    # the lookup table holds each listed value once
        # A key that refers to another table counts no further than the largest table it is unique in.
        pools = {}
        for key, domain in domains.items():
            if unique(key):
                pools[domain] = max(pools.get(domain, 0), sizes[key[0]])
        timeline = self._timeline(typed, sizes, seed)
        # Where fanout results apply, each key follows the lineage of its rows rather than i modulo the
        # number of positions. Without them nothing below changes.
        self.lineage = None
        entity = {key: d for key, d in domains.items() if d not in domain_values and d in pools}
        if any(domains[a] not in domain_values and domains[a] in pools for a in edges):
            self.lineage = Lineage(self.tables, sizes, entity, unique, pools,
                                   {a: bands for a, (_, bands) in edges.items()}, seed)
        self.con.execute("CREATE TEMP TABLE sandbox_lineage (t VARCHAR, d BIGINT, r BIGINT, p BIGINT)")

        total, kinds = 0, {}
        for name in self.tables:
            size = sizes[name]
            definitions, values = [], []
            first = self.catalogue.table(name).first_column()
            for column in self.catalogue.table(name).columns.values():
                key = (name, column.name)
                salt = zlib.crc32(f"{name}.{column.name}".encode()) + seed
                kind = typed(column)
                domain = domains.get(key)
                group_values = listed.get(key) or domain_values.get(domain)
                value = None
                if domain is not None:
                    kind = "VARCHAR" if domain in text_domains else "BIGINT"
                    if group_values and stats.get(key, {}).get("unique"):
                        literals = [("'" + val.replace("'", "''") + "'") if kind == "VARCHAR" else val
                                    for val, _, _ in group_values if kind == "VARCHAR" or NUMBER.match(val)]
                        if literals:
                            value = f"CAST(([{', '.join(literals)}])[1 + i % {len(literals)}] AS {kind})"
                    elif group_values:
                        value = _weighted(group_values, kind, salt)
                    if value is None:
                        subject = "i" if unique(key) or domain not in pools else f"(i % {pools[domain]})"
                        followed = self.lineage.position(name, domain) if self.lineage and not unique(key) else None
                        if followed is not None:
                            # The positions go to the database as text, which is much quicker than a list of numbers.
                            self.con.execute("INSERT INTO sandbox_lineage SELECT $1, $2, r.n - 1, CAST(x.l[r.n] AS BIGINT) "
                                             "FROM (SELECT string_split($3, ',') AS l) AS x, range(1, len(x.l) + 1) AS r(n)",
                                             [name, domain, ",".join(map(str, followed))])
                            subject = (f"(SELECT m.p FROM sandbox_lineage m WHERE m.t = {_text(name)} "
                                       f"AND m.d = {domain} AND m.r = i)")
                        value = f"CAST({(domain + 1) * 1000000} + {subject} AS {kind})"
                        info = stats.get(key)
                        if info and info["rows"] and info["nulls"]:
                            per_mille = min(999, round(1000 * info["nulls"] / info["rows"]))
                            value = f"CASE WHEN hash(i + {salt + 1}) % 1000 < {per_mille} THEN NULL ELSE {value} END"
                elif key in timeline:
                    span, place, group_salt, listed_years, step = timeline[key]
                    value = (f"{_moment(f'hash((i % {span}) + {group_salt})', listed_years)} + to_seconds("
                             f"{place * step} + CAST(hash(i + {salt}) % {step - step // 6} AS BIGINT))")
                    value = f"CAST({value} AS {kind})"
                elif key in listed:
                    value = _weighted(listed[key], kind, salt)
                elif key in years and kind in ("TIMESTAMP", "DATE"):
                    value = f"CAST({_moment(f'hash(i + {salt})', years[key])} AS {kind})"
                if value is None and column.data_type.strip().upper() == "BIT":
                    value = f"CAST(hash(i + {salt}) % 2 AS BIGINT)"
                elif value is None and column is first and column.nullable is False:
                    # The first column of a table, where it may not be empty, keys the table's rows,
                    # so that ordering by it numbers the rows in one way only.
                    value = _row_key(kind, size, column.max_length)
                kinds[key] = kind
                definitions.append(f"{_quoted(column.name)} {kind}")
                value = value or _filler(kind, salt, column.max_length)
                # A key shared across a join is left whole, so that the join still finds its rows.
                values.append(value if domain is not None else _within(kind, value, column.max_length))
            self.con.execute(f"CREATE TABLE {_quoted(name)} ({', '.join(definitions)})")
            self.con.execute(f"INSERT INTO {_quoted(name)} SELECT {', '.join(values)} FROM range({size}) AS r(i)")
            total += size
        self.con.execute("DROP TABLE sandbox_lineage")
        self.fanout = self._fanout_provenance(domains, unique, results, edges)
        # Columns that the site rules give a meaning to are then filled from public reference data.
        # A role that cannot be applied leaves the filler in place, and is listed in the result.
        spans = self.checks.spans if self.checks else {}
        try:
            realism = Realism(self.con, self.catalogue, self.roles, sizes, kinds, self.tuning, spans, self.lineage, added)
            failures = realism.apply()
            total += realism.added
        except duckdb.Error:
            failures = [{"table": r.table, "column": r.column, "role": r.role, "reason": "database"}
                        for r in self.roles if r.table in sizes]
        self._server_records()
        return {"tables": len(self.tables), "rows": total, "hasValues": self.has_values,
                "sentence": v.built_sentence(len(self.tables), total), "rolesNotApplied": failures}

    def _fanout_provenance(self, domains, unique, results, edges):
        """Each join from a key that is not unique to one that is, with where its fan-out came from."""
        joins = set()
        for left, right in self.pairs:
            a, b = self._key(*left), self._key(*right)
            if a in domains and b in domains and a[0] != b[0]:
                for child, parent in ((a, b), (b, a)):
                    if unique(parent) and not unique(child):
                        joins.add((child, parent))
        applied = self.lineage.applied if self.lineage else set()
        derived = {child for child, _ in joins if self.lineage and child not in applied
                   and self.lineage.shaped_by_others(child[0], domains[child])}
        given = {(c, k) for (c, k, _, _) in results}
        return tunable.fanout_provenance(sorted(joins), applied, given, derived)

    def _server_records(self):
        """Keeps a copy of the records that SQL Server holds of its own tables, so that the page's table sizes query
        and its first query return here what SQL Server would: sys.tables and sys.partitions, which give each
        table's number of rows, and INFORMATION_SCHEMA.COLUMNS and INFORMATION_SCHEMA.TABLES, which list the tables
        and their columns under the catalogue's own types, all in the schema dbo. The number of rows is counted
        afresh at each read, and OBJECT_ID, SCHEMA_ID and QUOTENAME work as they do in SQL Server on these names."""
        schema = SERVER_SCHEMA
        objects = {name: 1000 + i for i, name in enumerate(self.tables, start=1)}
        self.con.execute(f"CREATE SCHEMA {schema}")
        listed = " UNION ALL ".join(f"SELECT {_text(name)}, {number}" for name, number in objects.items()) \
            or "SELECT NULL, NULL WHERE false"
        self.con.execute(f"CREATE TABLE {schema}.sys_tables AS SELECT CAST(n AS VARCHAR) AS name, "
                         f"CAST(o AS BIGINT) AS object_id, CAST(1 AS BIGINT) AS schema_id FROM ({listed}) AS x(n, o)")
        counted = " UNION ALL ".join(f"SELECT {number}, (SELECT COUNT(*) FROM {_quoted(name)})"
                                     for name, number in objects.items()) or "SELECT NULL, NULL WHERE false"
        self.con.execute(f"CREATE VIEW {schema}.sys_partitions AS SELECT CAST(o AS BIGINT) AS object_id, "
                         f"CAST(0 AS BIGINT) AS index_id, CAST(n AS BIGINT) AS rows FROM ({counted}) AS x(o, n)")
        precision = {"BIGINT": 19, "INT": 10, "INTEGER": 10, "SMALLINT": 5, "TINYINT": 3, "BIT": 1, "FLOAT": 53, "REAL": 24}
        rows = []
        for name in self.tables:
            for place, column in enumerate(self.catalogue.table(name).columns.values(), start=1):
                data_type = column.data_type.strip().lower() or "nvarchar"
                text = data_type.endswith("char") or data_type in ("text", "ntext")
                rows.append(", ".join([
                    _text(name), _text(column.name), str(column.position or place), _text(data_type),
                    str(column.max_length) if text and column.max_length is not None else "NULL",
                    str(precision.get(data_type.upper(), 18 if data_type in ("numeric", "decimal") else 0) or "NULL")
                    if not text else "NULL",
                    str(column.scale) if column.scale is not None and not text else "NULL",
                    "'NO'" if column.nullable is False else "'YES'"]))
        values = ", ".join(f"({row})" for row in rows) or "(NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
        self.con.execute(
            f"CREATE TABLE {schema}.information_schema_columns AS SELECT 'practice' AS TABLE_CATALOG, "
            "'dbo' AS TABLE_SCHEMA, CAST(t AS VARCHAR) AS TABLE_NAME, CAST(c AS VARCHAR) AS COLUMN_NAME, "
            "CAST(p AS BIGINT) AS ORDINAL_POSITION, CAST(d AS VARCHAR) AS DATA_TYPE, "
            "CAST(l AS BIGINT) AS CHARACTER_MAXIMUM_LENGTH, CAST(np AS BIGINT) AS NUMERIC_PRECISION, "
            "CAST(ns AS BIGINT) AS NUMERIC_SCALE, CAST(n AS VARCHAR) AS IS_NULLABLE "
            f"FROM (VALUES {values}) AS x(t, c, p, d, l, np, ns, n) WHERE t IS NOT NULL")
        self.con.execute(f"CREATE TABLE {schema}.information_schema_tables AS SELECT 'practice' AS TABLE_CATALOG, "
                         "'dbo' AS TABLE_SCHEMA, name AS TABLE_NAME, 'BASE TABLE' AS TABLE_TYPE "
                         f"FROM {schema}.sys_tables")
        # OBJECT_ID reads the last part of a name such as [dbo].[VISIT] or dbo.VISIT; any schema other than dbo has
        # no tables here.
        self.con.execute("CREATE MACRO quotename(n) AS '[' || replace(n, ']', ']]') || ']'")
        self.con.execute("CREATE MACRO schema_id(n) AS CASE WHEN lower(n) = 'dbo' THEN CAST(1 AS BIGINT) END")
        self.con.execute(
            "CREATE MACRO object_id(n) AS (SELECT t.object_id FROM " + schema + ".sys_tables AS t "
            "WHERE lower(t.name) = lower(regexp_extract(n, '\\[?([^.\\[\\]]+)\\]?$', 1)) "
            "AND lower(coalesce(nullif(regexp_extract(n, '^\\[?([^.\\[\\]]+)\\]?\\.', 1), ''), 'dbo')) = 'dbo')")

    def _clear_temporary_tables(self):
        for (name,) in self.con.execute("SELECT table_name FROM duckdb_tables() WHERE temporary").fetchall():
            self.con.execute(f"DROP TABLE IF EXISTS {_quoted(name)}")

    def run(self, sql, keep=True):
        """Translates and runs a piece of T-SQL. Returns a plain dictionary describing what happened.

        With keep turned off, everything the statements did is undone afterwards, so that nothing
        from the text that was run stays in the database.
        """
        try:
            # The practice database answers as SQL Server would, so a division of whole numbers gives a whole number.
            statements = to_duckdb(sql, self.date_columns, getattr(self, "whole_columns", frozenset()), server_records=True)
        except Unreadable:
            return {"status": "unreadable"}
        except Unsupported:
            return {"status": "unsupported"}
        except Exception:
            return {"status": "unreadable"}
        translated = ";\n".join(statements)
        variables = sorted({match.group(1) for s in statements if (match := SET_VARIABLE.match(s))})
        self._clear_temporary_tables()
        columns, shown, count, any_rows = [], [], None, False
        if not keep:
            self.con.execute("BEGIN TRANSACTION")
        try:
            for statement in statements:
                cursor = self.con.execute(statement)
                if statement.lstrip().upper().startswith(("SELECT", "WITH", "FROM", "(")) and cursor.description:
                    fetched = cursor.fetchall()
                    columns = [d[0] for d in cursor.description]
                    count, shown = len(fetched), fetched[:ROW_LIMIT]
                    any_rows = any_rows or count > 0
            result = {"status": "ok", "columns": columns, "count": count, "anyRows": any_rows, "translated": translated,
                      "rows": [[None if value is None else str(value) for value in row] for row in shown]}
        except duckdb.Error as error:
            result = {"status": "database-error", "message": str(error).splitlines()[0], "translated": translated}
        finally:
            if not keep:
                self.con.execute("ROLLBACK")
            # Variables and temporary tables do not outlive the run, as in a new session.
            for name in variables:
                try:
                    self.con.execute(f"RESET VARIABLE {name}")
                except duckdb.Error:
                    pass
            self._clear_temporary_tables()
        return result

    def outcome(self, sql):
        """How a past request fared: one of the three outcomes in the fixed vocabulary."""
        result = self.run(sql, keep=False)
        if result["status"] != "ok":
            return v.OUTCOME_NOT_RUN
        return v.OUTCOME_ROWS if result["anyRows"] else v.OUTCOME_NO_ROWS
