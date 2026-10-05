"""Regenerate the CDC growth reference files in this directory.

Run with ``python3 build_data.py``. The script downloads the four CDC LMS
data files, interpolates L, M and S to every whole month from 0 to 240 for
each sex, and rewrites growth_weight.csv and growth_stature.csv beside this
script. It uses only the Python standard library. See SOURCES.md for the
provenance of every file in this directory.
"""

import csv
import io
import os
import urllib.request

CDC_BASE = "https://www.cdc.gov/growthcharts/data/zscore/"
HERE = os.path.dirname(os.path.abspath(__file__))

# Output file -> (infant file for months 0-35, 2-20 year file for months 36-240)
GROWTH = {
    "growth_weight.csv": ("wtageinf.csv", "wtage.csv"),
    "growth_stature.csv": ("lenageinf.csv", "statage.csv"),
}


def fetch(url):
    # The CDC server refuses some custom user agents, so keep urllib's default.
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def read_lms(name):
    """Return {sex: [(age_months, L, M, S), ...]} sorted by age."""
    text = fetch(CDC_BASE + name).decode("utf-8-sig")
    table = {}
    for row in csv.DictReader(io.StringIO(text)):
        if not row.get("Sex") or not row["Sex"].strip().isdigit():
            continue  # skip any repeated header lines
        sex = int(row["Sex"])
        point = tuple(float(row[k]) for k in ("Agemos", "L", "M", "S"))
        table.setdefault(sex, []).append(point)
    for points in table.values():
        points.sort()
    return table


def interpolate(points, month):
    """Linearly interpolate (L, M, S) at a whole month between neighbouring CDC ages."""
    for (a0, *v0), (a1, *v1) in zip(points, points[1:]):
        if a0 <= month <= a1:
            if month == a0:
                return v0
            if month == a1:
                return v1
            t = (month - a0) / (a1 - a0)
            return [x0 + t * (x1 - x0) for x0, x1 in zip(v0, v1)]
    raise ValueError(f"month {month} lies outside the CDC table")


def build_growth(out_name, infant_name, child_name):
    infant, child = read_lms(infant_name), read_lms(child_name)
    lines = ["sex,age_months,l,m,s"]
    for sex in (1, 2):
        for month in range(0, 241):
            points = infant[sex] if month <= 35 else child[sex]
            l, m, s = interpolate(points, month)
            lines.append(f"{sex},{month},{l:.6f},{m:.6f},{s:.6f}")
    with open(os.path.join(HERE, out_name), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"wrote {out_name} ({len(lines) - 1} rows)")


def main():
    for out_name, (infant_name, child_name) in GROWTH.items():
        build_growth(out_name, infant_name, child_name)


if __name__ == "__main__":
    main()
