"""Results of pure work, kept by the content of their inputs, so that the page does not work out again what an answer cannot change.

The page works the whole checklist out again after every answer. Most of that work depends only on the SQL files, the
catalogue, the conversion and the target query, which an answer seldom changes, so each such result is kept here under
a key made from the content of its inputs and is given back, as a copy, when the same inputs come again. A result is
never kept under anything but its inputs' content, so a kept result is always the one that the work would give.
"""
import copy
import hashlib
import os
from collections import OrderedDict

# The most results kept of each kind; the oldest is dropped first.
LIMIT = 4096
_kept = {}


def digest(*parts):
    """A short fingerprint of some text or bytes."""
    found = hashlib.sha256()
    for part in parts:
        found.update(part if isinstance(part, bytes) else repr(part).encode("utf-8", "surrogatepass"))
        found.update(b"\0")
    return found.hexdigest()


def folder(path):
    """A fingerprint of every file under a folder, by relative path and content."""
    found = hashlib.sha256()
    for root, dirs, names in os.walk(str(path)):
        dirs.sort()
        for name in sorted(names):
            if name.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(root, name)
            found.update(os.path.relpath(full, str(path)).encode("utf-8", "surrogatepass") + b"\0")
            with open(full, "rb") as f:
                found.update(f.read())
            found.update(b"\0")
    return found.hexdigest()


def catalogue(entries):
    """A fingerprint of a catalogue's content, kept on the catalogue once worked out."""
    if entries is None:
        return None
    found = getattr(entries, "_fingerprint", None)
    if found is None:
        found = digest(sorted((t.name, t.schema, sorted((c.name, c.data_type, c.max_length, c.scale, c.position, c.nullable)
                                                     for c in t.columns.values())) for t in entries.tables()))
        try:
            entries._fingerprint = found
        except AttributeError:
            pass
    return found


def remembered(kind, key, work, copied=True):
    """The result of work() for this key, worked out once. A copy is given back unless copied is False."""
    kept = _kept.setdefault(kind, OrderedDict())
    if key in kept:
        kept.move_to_end(key)
        result = kept[key]
    else:
        result = work()
        kept[key] = result
        if len(kept) > LIMIT:
            kept.popitem(last=False)
    return copy.deepcopy(result) if copied else result


def clear():
    _kept.clear()
