# What Schemalyser is for

Schemalyser is a reusable export of the anaesthesia record. A clinician gives it a list of anaesthetic episodes and chooses the sections of the anaesthesia chart they want, and it produces an approved package of SQL that the hospital's database analyst runs to return those sections, one file for each, at the grain the record was charted. The same mapping that serves the export also serves two other consumers: an audit that must be computed inside the database, and the anaesthesia layer of the hospital's OMOP database. This page says how those three fit together and how the tool grows. `docs/contract.md` is the standard the code is held to, and `docs/roadmap.md` is the order in which the work is done.

## One mapping, three consumers

The hospital schema is the one private thing. It is built on the screen Describe the record, and it records where the hospital's database keeps each part of the anaesthesia record, with the evidence for every fact. It is the only part of the system that knows the vendor. Everything else reads the roles, which are the vendor-free description of the record that is the same at every hospital.

Three consumers read the roles.

| Consumer | What goes in | What comes out | Who writes the logic |
|---|---|---|---|
| The anaesthesia record export | A list of episodes and a specification of sections | One file for each section, for those episodes | The clinician, by choosing on the screen |
| An audit computed inside the database | SQL over the roles, where rows cannot leave and an aggregate must be made inside | A result the policy has classed, rounded where it leaves | A person, or a coding agent on the public side, as a reusable capability |
| The OMOP anaesthesia layer | The validated roles | The release script that fills the anaesthesia tables beside the hospital's core | A coding agent on the public side, tested on the invented world |

The export and the OMOP layer are the same mapping with different targets. A section validated for the export is a part validated for OMOP, because the conversion step for that part reads the part and nothing else. The OMOP layer is therefore never a separate clinical effort; it is a release built from sections the department already trusts.

## The export

The clinician's question resolves into three choices: which episodes, which sections of the chart, and what will be computed. The screen takes the first two. The episodes come as a list of anaesthetic keys, or as patient and date pairs with a declared rule for resolving each pair to an anaesthetic, and the list stays on the private side as a file named by its hash. The sections are the parts of the role model, shown as the chart a clinician knows: the anaesthetic itself, readings of chosen kinds, drugs, fluids and blood products, devices, events, staff, operations, and notes. Each section takes a window relative to the anaesthetic and the flags its part allows, and nothing finer, so that the rows come out whole and the computation is done afterwards.

The screen writes a specification: a small public file that names the choices and holds no SQL and no hospital material, so that it can be saved, reused for another cohort, shared with another hospital, and hashed into the package. The compiler turns the specification into SQL over the roles, which the role policy checks and the invented world can run, and then resolves that SQL through the hospital schema into the script over the hospital's tables, arranged as a series the analyst can stop after at each step. The feasibility report says, for a specification, which sections this hospital supports and to what state of evidence, and names the smallest investigation that would move each one that falls short.

An export is row-level data for named episodes. The specification declares its output class, how the keys are pseudonymised, and which sections may leave, with notes excluded unless named, and each section's approval is the hospital's under its own rules. An aggregate audit, which returns rounded counts, is the smaller first thing to run on production.

## How the questions grow

The variety of clinical questions is variety in what is computed, not in what is retrieved. Most computation is done by the clinician over the exported files. Where rows cannot leave, the computation is a capability: a measure written once over the roles, with parameters, that then appears on the screen as a derived section. Minutes beyond a threshold, parameterised by kind, direction, threshold and window, answers a pressure below 40 and a saturation below 95 alike. The catalogue of capabilities is public and the same at every hospital, each capability is versioned and names the parts and kinds it requires, and a hospital that lacks a part sees the capability as not currently supported, with the evidence request that would change that.

A capability is added by the public route. A clinician states the measure in words; on the public side, with a coding agent, it is written as SQL over the roles against the invented world, with planted cases and expected answers written independently and held out; the question package is imported privately and checked against the role policy; and the catalogue entry is written with its name, version, meaning, parameters, grain, unit and requirements. The department writes the list of measures its audits use, in words, before any is coded, and most turn out to be parameters of a few shapes.

## The order of the sections

A section is promoted from draft when the export needs it, in the order the department needs, and never before it has been validated at the hospital. The order is readings, which are version 1 of the contract and the first section on production; then drugs, whose event grain of start, rate change and stop is the contract's boundary test; then fluids and blood products, devices and events; then operations and staff, whose tables the hospital's core OMOP also reads; and notes last, because free text is the most identifiable section and needs its own approval class.

## What this does not do

The screen does not express questions, and there is no general query builder. A question needing a distinction the roles do not carry, such as whether a reading was charted retrospectively, cannot be answered through any selection, and the catalogue is how that limit is made explicit rather than approximated. A computation no capability covers, when rows cannot leave, needs a new capability before it reaches the screen. A section the hospital has not mapped is reported as not currently mapped, never as unavailable. In each case the screen says so.
