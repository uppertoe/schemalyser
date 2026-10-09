# The release script and the routes of the steps

`python -m schemalyser.release CONVERSION --catalogue CATALOGUE.csv --out FOLDER [--schema MAP]` writes `release.sql`, which the operator runs with sqlcmd after the core OMOP refresh, `source_manifest.csv`, which lists the source columns that the anaesthesia steps read, and `step_classes.json`, which holds the static policy's report on every step and gate. The docstring of `core/schemalyser/release.py` describes the script's stages and the checks that keep it safe. This page describes what the release requires of each step's route, as layer 3 of `docs/contract.md` asks.

## The route of a step

Every step of the core and anaesthesia layers records its route in `conversion.json`. A step on the route `"roles"` is written over the role views and the mapping views, and reads the OMOP tables that earlier steps wrote. A step on the route `"direct"` is written from the hospital's source tables, and it records three things beside the route:

- `reference`, the lineage or conversion that the step rests on, by name;
- `reason`, one sentence that says why the step takes the direct route;
- `review`, as `{"by": ..., "on": "YYYY-MM-DD"}`, who accepted the step and when, with an optional `note`.

A derived step reads only the OMOP tables, so it takes neither route and records none. An alternative given as an entry, `{"file": ..., "route": ...}`, records its own route in the same way, and one given as a bare file name takes its step's.

## What the release refuses

The release refuses the conversion when a step records no route, or when a direct step lacks its reference, its reason or its review, and the message names the step, for example: "visit_detail.sql: this step is written directly from the source tables, and conversion.json does not record the review, so the release cannot carry it." It also refuses a folder that `draft.json` marks as a draft, as a transplant from a reference is marked, until the owner has reviewed every step and removed the file.

## A step over the roles

A step over the roles reads the role views and the mapping views, and the release compiles it through the hospital schema, as the audit path compiles a question. Each role view and mapping view that the step reads becomes a common table expression placed ahead of the step's own: a role view is the schema's SELECT over the hospital's tables, and a mapping view is the schema's translation of its local codes, with every code replaced by its opaque key. The OMOP tables that the step reads stay as they are. The script carries the result as the step's text, so that the testbed and the SQL Server harness run the same artefact, and the source manifest lists what the compiled step reads.

The hospital schema is the map folder given with `--schema`, or otherwise the map folder beside the conversion folder, as `fixtures/map` sits beside `fixtures/conversion`. Without one, the release refuses the step by name. It also refuses a step that reads anything but the role views, the mapping views, its own common table expressions and the OMOP tables; a step that reads a view the schema does not supply; and a step whose own common table expression bears the name of a table that a view reads, which the compiled step could not tell apart. A step over the roles may still wait beside a direct step, named in that step's entry as `roles_step`, where it is checked as strictly as any step and the script carries the direct step.

## The class of each step

Before the script is written, the read side of every step, alternative and gate goes to the static policy under the conversion purpose, as `docs/policy.md` describes, a step over the roles as compiled. The release refuses a step or gate that it carries and that the policy places in class D. A step that it does not carry, such as a core step, may be of class D where `conversion.json` records why, as `"policy_class": {"class": "D", "reason": one sentence}`; the header carries that reason, and the release refuses a recorded class that the policy no longer derives. The header states the classes of the steps and gates that the script carries and the class of the script as a whole, which is C at best, and each step's comment names its class.

## What the script states

The script's header states how many of the anaesthesia steps that it carries are written over the roles and how many directly from the source tables, and each step's comment, after the line that names its file, says which route the step takes and, for a direct step, the reference it rests on and who accepted it on what date. The command prints the same share when it writes the script.

The invented world's conversion records nineteen of its twenty steps that read the record as direct, with the reference "the invented world's own conversion, written by hand against its source tables" and a review that records the commit of the invented world as its acceptance, because no separate review was made. The infusions are written over the roles by `drug_exposure_infusion_roles.sql`, compiled through the invented hospital's map, which binds `role_drug` by three pathways: the administrations, the corrections and the orders. The direct infusion step that it replaced, `drug_exposure_infusion.sql`, is kept as a recorded alternative on the direct route with its reference, reason and review.

Every step and gate that the script carries is of class C. Seven steps were class D before they were rewritten. Five attributed a reading or an event to an anaesthetic within a margin that they joined on a constant; each now reads the margin as a value beside each anaesthetic, in a bounded join on the anaesthetic's own visit detail. The visit detail's concept, the general concept of an anaesthetic and the switch of the calculated mean pressure are read from their one mapping row as values in the same way. The core step `cdm_source.sql` remains class D, because it states the date on which the CDM was built through GETDATE, and `conversion.json` records that reason.
