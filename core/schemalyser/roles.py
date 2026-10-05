"""Roles: what a column means, so that the sandbox can fill it with realistic values.

The site rules file may say that a column holds a date of birth, a weight, a heart rate, a
medication name and so on. A role names a catalogue column and one meaning from the fixed list
below. For a general-purpose table, where the meaning of a value depends on an identifier beside
it, the role also says which identifier it applies to.

Roles are written into the inventory as roles.csv, so that the sandbox can use them. Every
string in that file comes from the catalogue, from this module, or from the site rules file.
"""
import csv
import io
from dataclasses import dataclass

# Each role, and the units it may be given in. The first unit is the default.
ROLES = {
    "sex": ("",),
    "birth_date": ("",),
    "event_time": ("",),
    "weight": ("kg", "g", "oz", "lb"),
    "height": ("cm", "in", "m"),
    "heart_rate": ("",),
    "respiratory_rate": ("",),
    "systolic_pressure": ("",),
    "diastolic_pressure": ("",),
    "mean_pressure": ("",),
    "oxygen_saturation": ("",),
    "temperature": ("c", "f"),
    "medication_name": ("",),
    "anaesthetic_start": ("",),
    "anaesthetic_stop": ("",),
    "blood_pressure": ("",),           # held as text, systolic over diastolic, as in 110/70
    "end_tidal_co2": ("",),            # mmHg
    "end_tidal_agent": ("",),          # per cent
    "administration_time": ("",),      # when a medicine was given
    "time_during_anaesthetic": ("",),  # the time of an event that happens while the anaesthetic runs
    "placement_time": ("",),           # when an airway or a line was placed
    "removal_time": ("",),             # when it was removed
    "admission_time": ("",),           # when the hospital visit began
    "discharge_time": ("",),           # when it ended
    "procedure_name": ("",),           # the name of an operation or investigation
    "diagnosis_code": ("",),           # an ICD-10 code
    "death_date": ("",),               # empty for nearly everyone
    # Two roles that add rows rather than fill them. Each names, with "when", the code that marks its readings
    # and, with "at", the column that holds their time, and the generator writes its rows only where the check
    # results list that code. A cuff mean is written beside each charted blood pressure of the same table, and
    # an arterial mean every minute through a share of the anaesthetics, both in mmHg.
    "cuff_mean_pressure": ("",),
    "arterial_mean_pressure": ("",),
}
# The roles whose readings the generator adds as rows of their own.
ADDED_ROWS = ("cuff_mean_pressure", "arterial_mean_pressure")
LAYOUT = ("table", "column", "role", "unit", "when_column", "when_value", "at_column", "male", "female")
MAXIMUM_LENGTH = 70
FORMULA_STARTS = ("=", "+", "@", "\t", "\r")


@dataclass(frozen=True)
class Role:
    table: str
    column: str
    role: str
    unit: str = ""
    when_column: str = ""
    when_value: str = ""
    at_column: str = ""
    male: str = ""
    female: str = ""


def _plain(value):
    """A value from the site rules that is safe to write: short, on one line, and not a spreadsheet formula."""
    value = str(value).strip()
    return value if len(value) <= MAXIMUM_LENGTH and "\n" not in value and not value.startswith(FORMULA_STARTS) else None


def _make(catalogue, table, column, role, unit="", when_column="", when_value="", at_column="", male="", female=""):
    entry = catalogue.table(table or "")
    field = entry.column(column or "") if entry else None
    if field is None or role not in ROLES:
        return None
    unit = (unit or ROLES[role][0]).lower()
    if unit not in ROLES[role]:
        return None
    names = {}
    for key, name in (("when_column", when_column), ("at_column", at_column)):
        other = entry.column(name) if name else None
        if name and other is None:
            return None
        names[key] = other.name if other else ""
    values = [_plain(v) for v in (when_value, male, female)]
    if any(v is None for v in values) or bool(names["when_column"]) != bool(values[0]):
        return None
    return Role(entry.name, field.name, role, unit, names["when_column"], values[0], names["at_column"], values[1], values[2])


def from_rules(rules, catalogue):
    """The roles in the site rules that name real columns and known meanings. The rest are left out."""
    found = []
    for item in rules.roles:
        if not isinstance(item, dict):
            continue
        when = item.get("when") or {}
        values = item.get("values") or {}
        role = _make(catalogue, item.get("table"), item.get("column"), item.get("role"), item.get("unit", ""),
                     when.get("column", ""), when.get("equals", ""), item.get("at", ""),
                     values.get("male", ""), values.get("female", ""))
        if role is not None:
            found.append(role)
    return sorted(set(found), key=lambda r: (r.table, r.column, r.when_value, r.role))


def to_csv(roles):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(LAYOUT)
    for role in roles:
        writer.writerow([getattr(role, name) for name in LAYOUT])
    return out.getvalue()


def from_csv(text, catalogue):
    """Reads roles.csv from an inventory, checking every row again."""
    found = []
    for row in csv.DictReader(io.StringIO(text)):
        role = _make(catalogue, *(row.get(name) or "" for name in LAYOUT))
        if role is not None:
            found.append(role)
    return found
