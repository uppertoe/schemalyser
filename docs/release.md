# The release script and the routes of the steps

`python -m schemalyser.release CONVERSION --catalogue CATALOGUE.csv --out FOLDER` writes `release.sql`, which the operator runs with sqlcmd after the core OMOP refresh, and `source_manifest.csv`, which lists the source columns that the anaesthesia steps read. The docstring of `core/schemalyser/release.py` describes the script's stages and the checks that keep it safe. This page describes what the release requires of each step's route, as layer 3 of `docs/contract.md` asks.

## The route of a step

Every step of the core and anaesthesia layers records its route in `conversion.json`. A step on the route `"roles"` is written over the role views and the mapping views, and reads the OMOP tables that earlier steps wrote. A step on the route `"direct"` is written from the hospital's source tables, and it records three things beside the route:

- `reference`, the lineage or conversion that the step rests on, by name;
- `reason`, one sentence that says why the step takes the direct route;
- `review`, as `{"by": ..., "on": "YYYY-MM-DD"}`, who accepted the step and when, with an optional `note`.

A derived step reads only the OMOP tables, so it takes neither route and records none. An alternative given as an entry, `{"file": ..., "route": ...}`, records its own route in the same way, and one given as a bare file name takes its step's.

## What the release refuses

The release refuses the conversion when a step records no route, or when a direct step lacks its reference, its reason or its review, and the message names the step, for example: "visit_detail.sql: this step is written directly from the source tables, and conversion.json does not record the review, so the release cannot carry it." It also refuses a folder that `draft.json` marks as a draft, as a transplant from a reference is marked, until the owner has reviewed every step and removed the file.

A step over the roles reads the role views, and the release cannot yet compile them through the hospital schema into SQL over the hospital's tables. Until it can, the release refuses such a step as a step, and a step over the roles waits beside a direct step instead, named in the direct step's entry as `roles_step`. There it is checked as strictly as any step: it must read only the role views, the mapping views, its own common table expressions and the OMOP tables, and it must write the fields of its table with the table's identifier. The script carries the direct step.

## What the script states

The script's header states how many of the anaesthesia steps that it carries are written over the roles and how many directly from the source tables, and each step's comment, after the line that names its file, says which route the step takes and, for a direct step, the reference it rests on and who accepted it on what date. The command prints the same share when it writes the script.

The invented world's conversion records every step as direct, with the reference "the invented world's own conversion, written by hand against its source tables" and a review that records the commit of the invented world as its acceptance, because no separate review was made. The infusion step over the roles, `drug_exposure_infusion_roles.sql`, waits beside the direct infusion step, which stands until the invented hospital's map binds `role_drug`.
