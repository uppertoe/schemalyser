# Review of goals and product: Schemalyser, 14 October 2026

Lens: whether the plan of record solves the throughput problem, for whom it adds value over handwritten Clarity SQL, and what must be true before anyone believes a result. I read `docs/roadmap.md`, `docs/product.md`, `docs/capabilities.md`, `docs/success.md`, `docs/screens.md`, `docs/ask.md`, `ask/README.md`, `docs/first-ask-note.md`, `README.md` and `core/schemalyser/rolemodel/roles.md`, and confirmed claims in `ask/internal/prompt/prompt.go`, `ask/internal/question/package.go`, `ask/internal/rolemodel/rolemodel.go`, `docs/contract.md`, `docs/audit.md` and `docs/workspace.md`. I ran no tests and changed nothing. Findings are ranked by importance.

## 1. The plan has no evidence of demand, and the cheapest evidence needs no database

The roadmap's problem statement (`docs/roadmap.md`, "The problem") divides the one clinician's time into five tasks and claims the tool removes three. Nothing in the repository records what the fifty anaesthetists have actually asked, how often, how long each answer took, or which of the five tasks consumed the time. Every later claim of value rests on that division being right, and it has never been measured.

This is the single most useful thing the owner can do this week, and it needs no production access. I recommend a question log: the last twelve to twenty-four months of requests the one clinician has received (from email and memory), each written as one sentence, with the date, who asked, the purpose (audit, research, M&M, a talk, a business case), the rough hours it took and where those hours went. Each question is then classified against the contract: answerable by a catalogue capability with parameters, answerable as a free question over contract parts, answerable only over draft parts, or not answerable from the anaesthesia record at all. The proportions decide almost everything else in this report, in particular whether the free-text service or the catalogue deserves the next month.

## 2. The value over handwriting SQL is real for the askers and for the department, and weak for the one clinician on a single simple question

The owner's doubt is well placed. For a simple question, an analyst who already knows the local build will write Clarity SQL faster than the path of conversation, package, page, compilation, review and run. The tool does not win on writing SQL.

Where the tool does win, if it works:

- **For the fifty askers**, the clarification conversation is the real product. Pinning down "neonates", "having surgery", the denominator and the treatment of repeats is the task that today needs a meeting with the one person who knows both medicine and SQL. A package with nine recorded decisions is something an asker can read and correct before any query runs. This is the first of the five tasks, and it is the one Ask can take off the one clinician entirely.
- **For the department**, the hospital schema captures where things live in the local build, with evidence, in a form that survives the departure of the one clinician. That is institutional value independent of any language model, and it is the strongest argument for the project.
- **For variants**, a parameterised capability turns the fifth task ("doing it all again for the next variant") into changing a parameter. This value is real only if most questions are variants of a few shapes, which `docs/capabilities.md` asserts and the question log in finding 1 would test.

Where the tool does not help: running the query and checking the result stay with the one clinician (`docs/roadmap.md` says so). By Amdahl's law, if checking and running dominate today, the throughput gain is small however good the rest is. The first measurable evidence of value is therefore a baseline: for the next ten real questions handled by hand, the one clinician records minutes per task under the roadmap's five headings. Then, on the stand-in, the same ten questions go through Ask and the page, and the same minutes are recorded, together with the agreement of the two answers on planted cases. Neither measure exists in `docs/success.md`, whose four measures all concern the mapping and none concern the asker or the clinician's time.

## 3. Ask ignores the capability catalogue, so free questions bypass the tested code

The system prompt (`ask/internal/prompt/prompt.go`) and the rendered contract (`ask/internal/rolemodel/rolemodel.go`, `Render`) give the model the parts, columns, kinds and vocabularies, but not the capability catalogue and not the SQL of the five tested capabilities. The model therefore writes every question's logic afresh. The day's test question, the median red cell volume per kilogram in neonates, is almost exactly the catalogue's `transfusion` and `total_volume_per_kg` capabilities, and `total_volume_per_kg` already states the hard part (running totals turned into amounts). The model rewrote that logic from scratch.

This defeats the design in `docs/product.md` ("How the questions grow"), where variety lives in parameters of a few shapes, each written once, examined and versioned. It also multiplies the examination's work, because every free package is new code to examine. I recommend that Ask be given the catalogue, that it be told to express a question as one or more capabilities with parameters wherever one fits, and that free SQL be reserved for the computation over capability outputs (a median across anaesthetics, a split by age band). A package then reads "transfusion v1, red cells, during the anaesthetic; median across neonatal anaesthetics", and only the thin outer layer is new. This is the most effective single change for safety and for throughput.

## 4. The examination of a free question has no author

Step 5 of the roadmap requires planted cases "for each question with their answers held out", and `docs/contract.md` requires expected answers "written independently". For the five capabilities the owner wrote them. For a free question from one of fifty askers, nobody is named, and writing a correct expected answer for planted cases is about as much work, and needs the same skill, as writing the SQL. The examination as specified does not scale to free questions, which is the use the service exists for.

Two remedies fit together. First, finding 3 shrinks the problem: a question composed of examined capabilities needs only its outer computation examined. Second, the planted cases for a free question should be shown to the asker as worked examples in clinical words, for example "a 3.2 kg baby, 22 days old, given 30 ml and then 15 ml of red cells during one anaesthetic, counts as 14 ml/kg; a baby with no weight recorded is left out; a baby given no blood counts as zero", and the asker confirms each. That makes the asker's sign-off of meaning concrete, it uses the asker's expertise rather than SQL, and it is the obvious place to catch a misunderstanding. The expected values for those examples can come from a second, independent implementation (a separate model call that sees the specification and the planted rows but not the SQL), with disagreement sent back for a person to resolve. The roadmap should say who writes planted answers for a free question, because at present the requirement silently falls on the one clinician again.

## 5. Clinical safety: the governance built so far covers execution, not meaning

The existing controls are good and are about the database and disclosure: the policy classes, the analyst's approval bound to hashes (`docs/audit.md`, "Approval and voiding"), rounding and suppression, a results package that stays in the hospital (`docs/contract.md`). None of them asks whether the question answers what the asker meant, and the analyst cannot judge that by reading a compiled script over hospital tables "in full". A model-written question can pass every one of those controls and be clinically wrong.

Before a result is believed, I would require the following, and the roadmap should name each signature.

- **The asker signs the meaning.** The specification and decisions in words, and the worked examples of finding 4, are confirmed by the asker before the package leaves the service. This is a record in the package, with a name and a date, not a click.
- **The package's custodian signs the logic.** Someone other than the model, at first the owner, confirms that the package passed its examination and that any draft or rehearsed parts are acceptable for the question's purpose. The status ladder of step 3 is the right vehicle, but it should record who granted each level.
- **The analyst signs the run.** This exists.
- **The result carries its attrition.** Every result returned should show the cohort count at each step (neonates, with an anaesthetic, with an operation, with a weight, with any red cells), the number of rows excluded and why, and the coverage of each recording pathway for the period. Most wrong answers in practice show themselves as an implausible denominator. The specification compiler should emit this attrition as part of every package, not as an option.
- **A triangulation check.** For each new question the result is compared with one external figure the department already trusts, such as the theatre system's monthly neonatal case count, or blood bank issue numbers for the period.
- **A chart review for any result used beyond the department.** A sample of ten to twenty cases from the result is checked against the clinical record by a clinician. `docs/roadmap.md` (standing rules) already reserves "clinically validated" for this, which is right.
- **A declared use.** Results are for audit and quality improvement under an approval reference (`docs/first-ask-note.md` already asks for one), and never for the care of an individual patient. The package should carry the approval reference.

Two specific risks. The day's question asks for a median of a small cohort: a median of fewer than about ten neonates is effectively one baby's value, and I could not confirm that the policy's rounding and suppression treat medians and other order statistics as it treats counts; it should. The prompt also asks for medians while not admitting the percentile functions, so every median is hand-built from ROW_NUMBER, which is a common source of off-by-one errors on even counts; a median is a good candidate for a tested capability of its own.

## 6. The product documents now contradict each other

`docs/product.md` says the screen "does not express questions, and there is no general query builder", and that the product is an export of chart sections with capabilities as the only route for computation inside the database. `docs/ask.md` and the roadmap's layer 6 introduce exactly a general question writer. `README.md` still describes a clinician and a colleague sitting together once and audits run "once for real". Readers, including a first colleague or a governance committee, will not know which product is intended. I recommend that `docs/product.md` be rewritten to say how the three modes relate: the catalogue for recurring measures, the export for row-level work done outside the database, and free questions as the clarification route that should usually end as a composition of capabilities.

## 7. "Rehearsed" and "clinically validated" without production access

Without production access, nothing can honestly be called clinically validated, and the roadmap is right to reserve the term. The proposed rehearsed status (step 2) needs an honest caveat. The stand-in is built from the vendor's published specification and the reference map is hand-written by the same author, so agreement between the naive schema and the reference shows that the proposer and the author read the specification alike. It does not show that either matches a real hospital. The gold score in `docs/success.md` makes the same point quietly: 26 of 33 documented bindings exact, but only 5 corroborated and no hospital-only bindings, and it is the hospital-only bindings, chiefly the local configuration of what is charted where and under which local codes, where real sites differ from the specification. Fluids and blood products are a case in point, since `roles.md` itself notes that blood products are often recorded in a transfusion record as well as the anaesthesia record.

I recommend that the ladder name what each level shows, in words a governance reader can follow: "well formed"; "logic verified" (examination passed on invented data); "rehearsed on the published specification" (the parts it reads agree on the stand-in); "mapping confirmed at this hospital" (counts and probes judged); "clinically validated" (records reconciled). Step 1's comparison "with a query written by hand the way an analyst would write it" should be written by someone other than the author of the reference map if at all possible, or else described as a consistency check rather than an independent one.

## 8. What the fifty anaesthetists need is mostly not free questions

From the catalogue in `docs/capabilities.md` and the nature of departmental work, the likely demand is dominated by recurring measures (hypotension, hypothermia, transfusion, PONV, recovery times, airway events) asked again with a different period, age band or specialty, plus a smaller number of genuinely new questions for a talk or a research proposal. If the question log confirms this, the highest throughput product is not a conversation per question but a standing monthly run: the catalogue capabilities computed for every anaesthetic in the period, held inside the hospital as one rounded, governed table, which most askers can then filter themselves or have filtered for them. Turnaround for a catalogue question becomes days rather than a queue. Free questions remain for the rest, and the conversation is still valuable there.

What the asker sees is unspecified. The roadmap ends at the analyst's run. Nothing says how a result returns to the asker, in what form, with what caveats, or how long it should take. I recommend a results page or a one-page result note in the asker's language: the question as specified, the decisions, the attrition, the result, what was rounded or suppressed, the package's status, and the date of the data. The asker should also be able to see where their question stands in the queue. Without this, the fifty experience the service as a form that disappears into an inbox.

## 9. Building the service before the examination

The owner chose to build Ask out of order, and the first version is small and careful (a prompt that forbids invented roles, a checker of the package, a status that stays at "drafted"). The risks are social and procedural rather than technical.

- A credible package with nine decisions looks finished. Colleagues will treat "drafted" as "nearly done", and the queue of drafted packages will fall on the one clinician, which is the throughput problem in a new form. I recommend that Ask not be opened to colleagues until the examination runs on its output, as step 7's own definition of done says, and that until then it be used by the owner alone to build the question log and the pass-rate measure.
- Free text typed by clinicians goes to the model provider. A clinician will, sooner or later, type a patient's name, record number or a recognisable case ("the 26-weeker from last Tuesday"). The service has no warning on the page and no screening of the text. I recommend a plain warning beside the text box and a simple check for record-number-like strings before anything is sent, with a sentence in `docs/ask.md`.
- Contract proposals from many askers will arrive at one owner. The proposal mechanism is sound, but the roadmap should say how proposals are triaged, or the contract will either stall or grow without discipline.
- The fetch by address (step 4) will need the hospital's approval for a hospital machine to fetch from an internet address, and `docs/contract.md` already says a package "moves through a mechanism the hospital approves". The file route may be all that is ever permitted. The address is a convenience, not a requirement, and should not hold up anything.

## 10. The capability catalogue and its open questions

The catalogue is well conceived: shapes over questions, tables for age-band thresholds, the documented-time caveat, denominators stated, charted and derived both reported. Three comments.

- The first five are all readings-only and all intraoperative physiology. They are the cheapest to build, which is a good reason, but they may not be what colleagues ask most. The question log should decide whether one of the first five is replaced, for instance by transfusion per kilogram, which the day's conversation shows people ask.
- The open questions (thresholds and age bands, pain scales, the second induction score, the CO2-equivalent factors, the massive transfusion threshold) are clinical decisions that the owner should not make alone. I recommend that the owner draft defaults with a published source for each and circulate them to two or three colleagues for agreement, which also starts the social work of making the catalogue the department's rather than the owner's. The age-band thresholds for hypotension in particular have no consensus, and the entry should say whose bands it uses.
- Neonatal age by days since birth (as the prompt fixes) ignores prematurity. For a paediatric department, postmenstrual age is often the clinically relevant measure. The catalogue should say which age each capability uses, and offer postmenstrual age where gestation is recorded.

## 11. OMOP is not on the critical path for the fifty

Step 8 (OMOP as a source, and the anaesthesia layer promoted) serves other hospitals and the hospital's research platform. It does nothing for the throughput of the fifty at this hospital, and it can wait until a second site or a research need appears. The existing conversion steps and testbed are a sound proof that the roles project to OMOP, and that is enough for now.

## 12. The real critical path is access, and it is not in the plan

Every level above "rehearsed" depends on the hospital: the dictionary-only query of `docs/first-ask-note.md` step 1, the probes, and the reconciliation. The dictionary-only query reads only SQL Server's own catalogue and no patient data, so it may be approvable much sooner than production access. The roadmap treats access as something that will happen ("When the database becomes available"). I recommend that obtaining the system catalogue query on the training or reporting copy, and the approval it needs, be an explicit parallel item with an owner and a next action, because one real dictionary would replace more speculation than any amount of further stand-in work.

## What I would change in the roadmap

1. **Add a step 0, the question log and the baseline**, needing no database: the last one to two years of real requests, classified against the contract and the catalogue, and minutes per task on the next ten questions done by hand. This is the first measurable evidence of value and it decides the order below.
2. **Keep step 1**, using the transfusion question, but have the hand-written comparator written independently where possible, and add the attrition table to what the package must hold.
3. **Merge steps 2 and 3** into one step that fixes the package and its status ladder, with each level named for what it shows and recording who granted it, the rehearsed level included with its caveat.
4. **Move the examination (step 5) ahead of the page's import (step 4)**, and redefine it for free questions: capabilities examined once, the outer computation examined by worked examples confirmed by the asker, with expected values from an independent second implementation.
5. **Change Ask now so that it composes catalogue capabilities** and writes free SQL only for what lies outside them; give it the catalogue and the capability SQL.
6. **Fold the portable dialect (step 6) into the examination**, since it is the precondition of running the same SQL in DuckDB and SQL Server, and keep it small.
7. **Shrink step 4 to the file route.** Fetch by address follows only once the hospital has approved it.
8. **Add a step for the return path**: the result note the asker receives, its caveats and status, and the turnaround the department can expect.
9. **Add a governance step**: who signs meaning, logic and run; the approval reference on every package; the use declared; the treatment of medians and small cells in the policy; and the warning against patient details in Ask.
10. **Hold Ask to the owner's own use** until the examination passes its output, and use it to measure the pass rate of step 7 on the question log's real questions rather than on ten invented ones.
11. **Defer step 8 (OMOP as a source)** until a second site or a research need asks for it.
12. **Add a parallel track for access**: the dictionary-only query on a non-production copy, with the approval it needs, owned and dated.
13. **Rewrite `docs/product.md` and `README.md`** so that the export, the catalogue and free questions are described as one product, with the catalogue first.
