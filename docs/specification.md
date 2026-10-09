# The specification of an export

`core/schemalyser/specification.py` is the first stage of compilation. It takes a specification of an export of the anaesthesia record and compiles it into SQL over the roles, one statement for each section. The second stage, which `audit.py` runs, takes each statement through the role policy, the feasibility report and the hospital schema to a package that the database analyst runs.

    python -m schemalyser.specification check SPECIFICATION.json
    python -m schemalyser.specification compile SPECIFICATION.json --out FOLDER [--episodes EPISODES.csv]
    python -m schemalyser.audit export hospital-schema.schemalyser.zip SPECIFICATION.json --episodes EPISODES.csv --out FOLDER

## The two files

The specification is a small public file. It names the clinician's choices and holds no SQL and no hospital material, so it can be saved, reused for another list of episodes, shared with another hospital and recorded by its hash in every package made from it.

The episode list is a separate private file, and it never goes into the specification. It is a CSV file with one episode on each line, under the header `anaesthetic_key` for a list of anaesthetics, or `patient_key,date` for a list of patients with the date of each one's anaesthetic, written as YYYY-MM-DD. Every package records the list by its form, its count and its hash. A list may hold at most 5,000 episodes, because that is the most one package's cohort may hold, and a longer list is divided first. When Schemalyser cannot read a line, it names the file and the line but never the value.

## The format

The format is `schemalyser-specification/1`. `fixtures/export/neonatal-pressures.specification.json` is an example, and `fixtures/export/` holds an invented episode list in each form.

```json
{
  "format": "schemalyser-specification/1",
  "contract_version": "1.1",
  "title": "Mean pressures of the planted neonates",
  "episodes": {"form": "anaesthetic_keys"},
  "sections": [
    {"name": "anaesthetics", "part": "role_anaesthetic"},
    {"name": "mean_pressures", "part": "role_reading", "kinds": ["map_arterial", "map_cuff"],
     "window": {"from": "start", "from_minutes": -15, "to": "stop", "to_minutes": 15},
     "flags": {"accepted": 1}}
  ],
  "derived": [
    {"name": "minutes_below_40", "capability": "minutes_beyond_threshold", "version": 1,
     "parameters": {"kind": "map_arterial", "direction": "below", "threshold": 40, "window": "anaesthetic"}}
  ],
  "output": {"class": "rows", "keys": "pseudonymised", "leaving": ["anaesthetics", "mean_pressures", "minutes_below_40"]}
}
```

- `contract_version` is the version of the role contract that the specification was written against, and it must be the version that Schemalyser holds.
- `episodes` gives the form of the list. For patient and date pairs, it also gives `window_hours`, from 0 to 168, and `several`, the rule for a pair with more than one anaesthetic in its window. An anaesthetic belongs to a pair when its patient is the pair's patient and it started from `window_hours` before the start of the date to `window_hours` after its end. With `"all, marked"`, every such anaesthetic is kept and marked as ambiguous; with `"none"`, only the pairs that resolve to exactly one anaesthetic are kept.
- Each section names a part of the role contract. `kinds` selects on the part's column of a kind, and each kind must be a word of that column's vocabulary. `window` runs from the anaesthetic's start or stop, with an offset in whole minutes, to its start or stop, and applies to the time at which the part's event happened. `flags` sets any of the part's flags to 0 or 1. A part must be linked to an anaesthetic or a patient, and the anaesthetic and the patient themselves take no window.
- Each derived section names a capability of the catalogue in `contract.json` at its version, with a value of the right type for each of its parameters and no others.
- `output` says whether the sections return rows or aggregate counts, whether the keys are pseudonymised or kept as recorded, and which sections may leave the hospital. A section that is not named there does not leave, which keeps notes in unless they are named.

`check` and `compile` refuse a specification that breaks any of these rules, and name each rule with where it broke: `format`, `contract_version`, `episodes`, `sections`, `kinds`, `window`, `flags`, `link`, `derived` and `output`.

## What it compiles to

Each section becomes one SELECT over the roles. Its first step, `episode`, chooses the episodes from the anaesthetics. For a list of anaesthetics, the step keeps the listed keys. For patient and date pairs, the steps `candidate`, `pairs`, `matched` and `per_pair` find every anaesthetic of the listed patients within each pair's window, and `episode` keeps one row for each anaesthetic, with the number of its pair and whether that pair was ambiguous.

A section of a part then joins the part to the episodes by its link, and keeps the rows of the chosen kinds and flags within the window. With rows as the output class, it returns the episode with the part's columns. With aggregate, it returns the number of rows and of anaesthetics for each kind. A derived section is the capability's SQL, from `core/schemalyser/rolemodel/capabilities/NAME.sql`, with each placeholder `{{parameter}}` filled and joined to the episodes on the anaesthetic's key. Where the catalogue declares a capability that has no SQL yet, the section is reported as declared without SQL, and nothing is compiled for it.

With the keys pseudonymised, every section numbers the episodes in the order of their starts and returns the number in place of the anaesthetic's key, and leaves out the part's own keys. A further section, `episode_key`, holds the link from each number to its anaesthetic and never leaves. For patient and date pairs, a further section, `episode_resolution`, counts the pairs, the pairs resolved to exactly one anaesthetic, the ambiguous pairs and the pairs resolved to none, so that the share resolved to exactly one can be read before any section is used.

Without an episode list, every statement chooses no episode, so that the public side can check the statements against the role policy and run them on the invented world. With a list, its keys are written into the statements, which are then as private as the list. `compile` writes `FOLDER/compiled.json`, with the specification's hash, the episode list's hash, and each section's output class, its outcome under the role policy and the hash of its file, and one file of SQL for each section in `FOLDER/sections/`.

## The second stage

`python -m schemalyser.audit export` compiles the specification with the episode list, then takes each section through the role policy, the feasibility report and the compilation through the hospital schema to a package of its own, as `docs/audit.md` describes. Each package's manifest carries the specification's hash and the output class of every section, and `export.json` records what became of each section. A section of rows read from a large table, such as the readings, is a large clinical extraction, of class C, and needs the database team's approval before it runs.
