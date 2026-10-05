"""Tunable parameters: the numbers that shape the sandbox's realistic values.

Every timing and distribution constant that the realistic values use is listed once, in PARAMETERS,
with its default, a short description and where the default came from. The site rules file may
override any of them under the key "tuning". An override is accepted only for a known key, and
only as a number, or a list of numbers, within the bounds given here; anything else is ignored.

Accepted overrides are written into the inventory as tuning.csv, in the same way as the roles, and
are checked again when the sandbox reads them. Check results of the kind "spans" can also set the
distribution of a duration, and the parameter then reports "check results" as its provenance.
"""
import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import datetime

INVENTED, PUBLIC, SITE_RULES, CHECK_RESULTS = "invented", "public source", "site rules", "check results"
# Where the fan-out of a join from a child table to its parent came from: how many child rows each parent has.
FANOUT_UNIFORM = "uniform (no check results)"
FANOUT_FROM_CHECKS = "from check results"
FANOUT_NOT_APPLIED = "uniform (the check results could not be applied)"
FANOUT_DERIVED = "from check results on other joins"
LAYOUT = ("key", "value")
LONGEST_LIST = 200
# The value that the site rules give for "still in place", from the first date among sentinelValues.
STILL_IN_PLACE_VALUE = "still_in_place_value"
SENTINEL = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$")
SENTINEL_TYPES = ("", "date", "datetime", "datetime2", "smalldatetime", "datetimeoffset")


@dataclass(frozen=True)
class Parameter:
    key: str
    default: object          # a number, or a tuple of numbers for a list
    low: float
    high: float
    description: str
    provenance: str = INVENTED
    whole: bool = False      # whether the value must be a whole number
    listed: bool = False     # whether the value is a list of numbers


# Parameters that count whole minutes, days or percentage points, because the SQL draws them with whole numbers.
WHOLE = {"youngest_age_days", "oldest_age_days", "anaesthetic_durations_minutes", "shortest_anaesthetic_minutes",
         "induction_window_minutes", "placement_window_minutes", "removal_window_minutes",
         "measurement_before_least_minutes", "measurement_before_most_minutes", "admission_before_least_minutes",
         "admission_before_most_minutes", "discharge_after_least_minutes", "discharge_after_most_minutes",
         "death_after_least_days", "death_after_most_days", "oxygen_saturation_lowest", "oxygen_saturation_highest",
         "arterial_interval_minutes", "mean_pressure_lowest", "mean_pressure_highest"}


def _p(key, default, low, high, description, provenance=INVENTED):
    return Parameter(key, default, low, high, description, provenance, key in WHOLE, isinstance(default, tuple))


PARAMETERS = (
    # Ages.
    _p("age_curve_exponent", 1.7, 0.2, 5, "How strongly ages lean young. A uniform number is raised to this power, "
       "so a larger number gives younger children; 1.7 puts half of them under about five and a half years."),
    _p("youngest_age_days", 2, 0, 3650, "The youngest age, in days, at a subject's first event."),
    _p("oldest_age_days", 6570, 1, 43800, "The oldest age, in days, at a subject's first event."),
    # The anaesthetic.
    _p("anaesthetic_durations_minutes", (60, 70, 80, 90, 105, 120, 140, 165, 195, 240, 320), 1, 10080,
       "The durations of an anaesthetic, in minutes, chosen among with equal chance."),
    _p("shortest_anaesthetic_minutes", 60, 1, 1440, "The shortest anaesthetic, in minutes, so that events placed "
       "after the start still fall before the stop."),
    _p("induction_dose_share", 0.5, 0, 1, "The share of doses given at induction rather than later in the anaesthetic."),
    _p("induction_window_minutes", 15, 1, 240, "How long induction lasts, in minutes from the start of the anaesthetic."),
    _p("placement_window_minutes", 15, 1, 240, "How long after the start of the anaesthetic an airway or a line is placed, "
       "at most, in minutes."),
    _p("removal_window_minutes", 10, 1, 240, "How long before the end of the anaesthetic an airway or a line is removed, "
       "at most, in minutes."),
    _p("still_in_place_share", 0, 0, 1, "The share of airways and lines that are recorded as still in place, with the "
       "first date among the site rules' sentinel values as their removal time."),
    _p("measurement_before_least_minutes", 30, 0, 10080, "The least time, in minutes, by which a weight or a height "
       "is taken before the anaesthetic starts."),
    _p("measurement_before_most_minutes", 1410, 1, 10080, "The most time, in minutes, by which a weight or a height "
       "is taken before the anaesthetic starts."),
    # The hospital visit.
    _p("admission_before_least_minutes", 1440, 0, 43200, "The least time, in minutes, from admission to the start "
       "of the anaesthetic."),
    _p("admission_before_most_minutes", 2880, 1, 43200, "The most time, in minutes, from admission to the start "
       "of the anaesthetic."),
    _p("discharge_after_least_minutes", 120, 0, 43200, "The least time, in minutes, from the end of the anaesthetic "
       "to discharge."),
    _p("discharge_after_most_minutes", 4320, 1, 43200, "The most time, in minutes, from the end of the anaesthetic "
       "to discharge."),
    # Death.
    _p("death_share", 0.02, 0, 1, "The share of subjects who have a date of death."),
    _p("death_after_least_days", 4, 0, 3650, "The least time, in days, from a subject's last event to their death."),
    _p("death_after_most_days", 404, 1, 3650, "The most time, in days, from a subject's last event to their death."),
    # Observations during the anaesthetic.
    _p("end_tidal_co2_mean", 38, 10, 80, "The usual end-tidal carbon dioxide under anaesthesia, in mmHg."),
    _p("end_tidal_co2_spread", 4, 0, 20, "The spread of end-tidal carbon dioxide, in mmHg. Values fall about three "
       "spreads either side of the usual value."),
    _p("end_tidal_co2_lowest", 25, 0, 80, "The lowest end-tidal carbon dioxide, in mmHg."),
    _p("end_tidal_co2_highest", 55, 10, 120, "The highest end-tidal carbon dioxide, in mmHg."),
    _p("end_tidal_agent_mean", 2.2, 0, 10, "The usual end-tidal volatile agent concentration, in per cent."),
    _p("end_tidal_agent_spread", 0.6, 0, 5, "The spread of the end-tidal agent concentration, in per cent."),
    _p("end_tidal_agent_lowest", 0.4, 0, 10, "The lowest end-tidal agent concentration, in per cent."),
    _p("end_tidal_agent_highest", 4.0, 0, 10, "The highest end-tidal agent concentration, in per cent."),
    _p("oxygen_saturation_lowest", 96, 50, 100, "The lowest oxygen saturation, in per cent."),
    _p("oxygen_saturation_highest", 100, 50, 100, "The highest oxygen saturation, in per cent."),
    _p("temperature_lowest_c", 36.2, 30, 42, "The lowest temperature, in degrees Celsius."),
    _p("temperature_highest_c", 37.5, 30, 42, "The highest temperature, in degrees Celsius."),
    # Blood pressure where the reference file is missing or lacks a spread.
    _p("systolic_sd_fallback", 8, 0, 40, "The spread of systolic pressure, in mmHg, where the reference file gives none."),
    _p("diastolic_sd_fallback", 6, 0, 40, "The spread of diastolic pressure, in mmHg, where the reference file gives none."),
    _p("systolic_base", 90, 40, 160, "Without the reference file: systolic pressure at birth, in mmHg."),
    _p("systolic_per_year", 2, 0, 10, "Without the reference file: the rise in systolic pressure each year, in mmHg."),
    _p("systolic_highest", 120, 60, 200, "Without the reference file: the highest median systolic pressure, in mmHg."),
    _p("diastolic_base", 55, 20, 120, "Without the reference file: diastolic pressure at birth, in mmHg."),
    _p("diastolic_per_year", 1, 0, 10, "Without the reference file: the rise in diastolic pressure each year, in mmHg."),
    _p("diastolic_highest", 80, 30, 140, "Without the reference file: the highest median diastolic pressure, in mmHg."),
    _p("pressure_fallback_spread", 7, 0, 40, "Without the reference file: the spread of blood pressure, in mmHg."),
    # Mean pressure, which the monitor charts as a reading of its own beside a cuff pressure or from an arterial line.
    _p("arterial_line_share", 0.15, 0, 1, "The share of anaesthetics with an arterial line, whose mean pressure the "
       "monitor charts throughout the anaesthetic."),
    _p("arterial_interval_minutes", 1, 1, 60, "How often, in minutes, the mean pressure from an arterial line is charted."),
    _p("mean_pressure_lowest", 20, 0, 200, "The lowest mean pressure that the sandbox writes, in mmHg. A drawn value "
       "below it is written as this value."),
    _p("mean_pressure_highest", 120, 10, 250, "The highest mean pressure that the sandbox writes, in mmHg. A drawn "
       "value above it is written as this value."),
)
BY_KEY = {p.key: p for p in PARAMETERS}
# Pairs that must stay in order. Where overrides would reverse a pair, the overrides of both are ignored.
ORDERED = (("youngest_age_days", "oldest_age_days"),
           ("measurement_before_least_minutes", "measurement_before_most_minutes"),
           ("admission_before_least_minutes", "admission_before_most_minutes"),
           ("discharge_after_least_minutes", "discharge_after_most_minutes"),
           ("death_after_least_days", "death_after_most_days"),
           ("end_tidal_co2_lowest", "end_tidal_co2_highest"),
           ("end_tidal_agent_lowest", "end_tidal_agent_highest"),
           ("oxygen_saturation_lowest", "oxygen_saturation_highest"),
           ("temperature_lowest_c", "temperature_highest_c"),
           ("mean_pressure_lowest", "mean_pressure_highest"))


def _one(parameter, value):
    """A single number for a parameter, or None where it is not acceptable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if not parameter.low <= value <= parameter.high:
        return None
    if parameter.whole:
        return int(value) if float(value).is_integer() else None
    return float(value)


def _value(parameter, value):
    if parameter.listed:
        if not isinstance(value, (list, tuple)) or not 0 < len(value) <= LONGEST_LIST:
            return None
        numbers = [_one(parameter, item) for item in value]
        return None if any(n is None for n in numbers) else tuple(numbers)
    return _one(parameter, value)


def accept(overrides):
    """The overrides that name a known parameter and give it an acceptable value. The rest are ignored."""
    if not isinstance(overrides, dict):
        return {}
    accepted = {}
    for key, value in overrides.items():
        parameter = BY_KEY.get(key) if isinstance(key, str) else None
        number = _value(parameter, value) if parameter else None
        if number is not None:
            accepted[key] = number
    for low, high in ORDERED:
        if (low in accepted or high in accepted) and \
                accepted.get(low, BY_KEY[low].default) > accepted.get(high, BY_KEY[high].default):
            accepted.pop(low, None)
            accepted.pop(high, None)
    return accepted


def sentinel(rules):
    """The first date among the site rules' sentinel values, or None where there is none."""
    for item in rules.sentinel_values if isinstance(rules.sentinel_values, list) else ():
        if not isinstance(item, dict):
            continue
        value, data_type = str(item.get("value", "")).strip(), str(item.get("dataType", "")).strip().lower()
        if data_type in SENTINEL_TYPES and _date(value):
            return value
    return None


def _date(text):
    if not SENTINEL.match(text):
        return False
    for form in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            datetime.strptime(text, form)
            return True
        except ValueError:
            continue
    return False


def _number_text(value):
    return repr(value) if isinstance(value, float) else str(value)


def to_csv(accepted, still_in_place=None):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(LAYOUT)
    for key in sorted(accepted):
        value = accepted[key]
        writer.writerow([key, " ".join(map(_number_text, value)) if isinstance(value, tuple) else _number_text(value)])
    if still_in_place:
        writer.writerow([STILL_IN_PLACE_VALUE, still_in_place])
    return out.getvalue()


def _parsed(text):
    try:
        numbers = [float(part) for part in text.split()]
    except ValueError:
        return None
    return numbers


def from_csv(text):
    """Reads tuning.csv from an inventory, checking every row again. Returns the overrides and the sentinel."""
    overrides, still_in_place = {}, None
    for row in csv.DictReader(io.StringIO(text)):
        key, value = (row.get("key") or "").strip(), (row.get("value") or "").strip()
        if key == STILL_IN_PLACE_VALUE:
            still_in_place = value if _date(value) else None
            continue
        parameter, numbers = BY_KEY.get(key), _parsed(value)
        if parameter is None or not numbers:
            continue
        overrides[key] = numbers if parameter.listed else (numbers[0] if len(numbers) == 1 else None)
    return accept({k: v for k, v in overrides.items() if v is not None}), still_in_place


class Tuning:
    """The value of every parameter, with the defaults, the accepted overrides and anything set by check results."""

    def __init__(self, overrides=None, still_in_place=None, defaults=None):
        self.overrides = accept(overrides or {})
        self.still_in_place = still_in_place if still_in_place and _date(still_in_place) else None
        # Defaults that public reference data replaces, such as durations from a reference file.
        self.defaults = dict(defaults or {})
        self.derived = {}     # key -> a description of what the check results set

    def __getitem__(self, key):
        if key in self.overrides:
            return self.overrides[key]
        if key in self.defaults:
            return self.defaults[key][0]
        return BY_KEY[key].default

    def set_from_checks(self, key, description):
        """Records that check results set a parameter, in place of its value here."""
        self.derived[key] = description

    def provenance(self, key):
        if key in self.derived:
            return CHECK_RESULTS
        if key in self.overrides:
            return SITE_RULES
        if key in self.defaults:
            return self.defaults[key][1]
        return BY_KEY[key].provenance

    def report(self):
        """Every parameter with its current value, its default, its description and its provenance."""
        rows = []
        for p in PARAMETERS:
            value = self.derived.get(p.key, self[p.key])
            default = self.defaults[p.key][0] if p.key in self.defaults else p.default
            rows.append({"key": p.key, "value": value, "default": default, "description": p.description,
                         "provenance": self.provenance(p.key)})
        if self.still_in_place:
            rows.append({"key": STILL_IN_PLACE_VALUE, "value": self.still_in_place, "default": None,
                         "description": "The removal time that records an airway or a line as still in place.",
                         "provenance": SITE_RULES})
        return rows


def table(tuning=None):
    """The full table of parameters, with current values and provenance, for another module to report."""
    return (tuning or Tuning()).report()


def fanout_provenance(joins, applied, given, derived=()):
    """Each parent-child join with where its fan-out came from, for another module to report.

    joins holds ((child table, child column), (parent table, parent column)) pairs. applied holds the
    child columns whose rows follow a fanout result of their own, derived those whose rows follow
    fanout results on other joins through the keys, such as a case's patient through its hospital
    visit, and given those for which the check results hold a fanout result at all. Without any of
    these the fan-out is uniform: every parent has the same number of children.
    """
    rows = []
    for (child, column), (parent, key) in joins:
        if (child, column) in applied:
            provenance = FANOUT_FROM_CHECKS
        elif (child, column) in derived:
            provenance = FANOUT_DERIVED
        else:
            provenance = FANOUT_NOT_APPLIED if (child, column) in given else FANOUT_UNIFORM
        rows.append({"child_table": child, "child_column": column, "parent_table": parent, "parent_column": key,
                     "provenance": provenance})
    return rows
