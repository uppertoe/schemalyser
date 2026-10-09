# The roles of an anaesthetic record

This page describes what an anaesthetic record holds, written without reference to any hospital, vendor or database. A role is one thing that the record must be able to tell an audit. A hospital's system may hold a role in one column, across several tables, inside a text field, as a row of a table of events, or not at all. Each hospital has a map that says which of its own tables and columns play each role, written as one SQL view for each role, and an audit written against the role views then runs unchanged at any hospital.

`contract.json`, beside this page, holds the same model as data: every view, its columns, their types, the words that the proposer looks for in a data dictionary, the vocabularies, the mapping views, and the capability catalogue. When this page and `contract.json` differ, `contract.json` is the one that the code reads, and this page should be corrected to match it.

## The version 1 contract, and the drafts

Version 1 of the contract is the three views that the audits read: `role_patient`, `role_anaesthetic` and `role_reading`. Every map supplies them, and an audit may rely on their columns and their rules; a change to any of their columns is a new major version of the contract. The fifteen further views are drafts, not yet used by any audit. A map may supply them, and the owner may still change their columns, so no audit should read one until it joins the contract. In `contract.json`, each view carries the status `contract` or `draft`, and each further view below is marked as a draft under its heading.

Version 1.1 adds to version 1 without changing it. The three contract views keep their columns. Beside them it adds the mapping views, the vocabulary of source kinds and the capability catalogue, and it reshapes the drafts: each part that records events has a key of its own for each row, carries its source kind, its time of documentation and the row it amends, and carries a stay where its source has no anaesthetic; the drafts that held a hospital's own code now hold a mapping view's local key; the periods in a unit became the transfers as charted; and the technique became a part of its own. The kinds of readings grew in 1.1 as well, which is additive: every earlier kind keeps its meaning.

## The rules of the record

The role views assume the following of every hospital's record, and a map must make each of them true.

1. One row of `role_anaesthetic` is one anaesthetic, and its key identifies that anaesthetic and no other.
2. A view gives each time as the source holds it, without conversion. The hospital schema records the time zone that the source's clocks follow, and whether the source applies daylight saving, so that anyone reading a time knows what it means. An audit that needs an elapsed time across a change of daylight saving, such as the minutes of an anaesthetic that ran through the night of the change, must say so and handle it.
3. A reading, an event, a drug given, a device, a fluid or a person present belongs to an anaesthetic by its link to that anaesthetic, and never because its time falls within the anaesthetic. A ward observation taken in the same hour is not part of the anaesthetic's record.
4. A laboratory result and a diagnosis belong to the patient and the stay, and not to an anaesthetic. An audit that wants a result or a diagnosis near an anaesthetic chooses its own window around it, such as the 24 hours before the start.
5. A note belongs to an anaesthetic by its link to that anaesthetic where the hospital's system makes one, and otherwise to the stay or to the patient. A note is never linked to an anaesthetic because of when it was written.
6. A view keeps the rows that an audit leaves out, such as test patients, anaesthetics with no stop and values that were not accepted, so that the standard counts can see them.
7. A view never joins in a way that repeats a row of the thing it describes, so that the view's key identifies its row.
8. A value that the record does not hold is empty. A value that the record holds but that cannot be read as the role's type is also empty, and `role_reading` then keeps the held text in `value_text`, so that the standard counts can say why a reading's value is empty: because the record holds none, because what it holds is not a number, or because the reading was not accepted.
9. A key compares equal with itself within one hospital only, and no key is carried from one hospital to another.
10. A value is in the unit that its role or its kind fixes, and the view converts a value that the hospital holds in another unit. Only a drug's amount and a laboratory result of the kind `other` are given in the unit that the view carries beside them.
11. A part that records events records each row as the source recorded it, with a key of its own, and never collapses rows that share a time or a kind. A question decides which to keep.
12. A part that records events marks each row with the kind of record it came from, from the source kind vocabulary, so that rows that reach the part by several pathways arrive marked.
13. Where a pathway's rows carry only the stay, the part's row carries `stay_key` and an empty `anaesthetic_key`, and a question attributes it to an anaesthetic by its own logic over the roles. The map never attributes a row to an anaesthetic by a time window.
14. A column that holds a code of an open domain, such as a drug, a procedure, a diagnosis, a laboratory test or a unit, holds the opaque local key of a mapping view and never the code. The code and its description stay in the hospital schema.

## The types

| Type | Meaning |
|---|---|
| key | Any type that compares equal with itself within one hospital, such as a whole number or text. |
| date | A date without a time. |
| datetime | A date and a time of day, as the source holds it. |
| number | A number that may hold a fraction. |
| whole | A whole number. |
| flag | The whole number 1 or 0, and never empty. |
| flag_or_empty | The whole number 1 or 0, or empty where the record does not say. |
| kind | Text that is one of the kinds of the vocabulary that the column names. |
| text | Text as the hospital records it. |
| local_key | Opaque text that is the local key of a row of the mapping view that the column names, made by the hospital schema from the hospital's own code and never the code itself. |

## The two kinds of public interface

A query over the roles reads two kinds of view. The parts describe what the source recorded: one row for each patient, anaesthetic, reading, drug event and so on, at the grain at which the source recorded it. The mapping views translate a hospital's local codes into standard concepts without showing the codes.

A small domain, such as the kinds of readings or the kinds of devices, has a closed vocabulary of kinds in the contract, and the hospital schema translates each local code into one of those kinds. An open domain, such as drugs, procedures, diagnoses, laboratory tests and units, has too many values for the contract to name, so the part's column holds an opaque local key instead, and the mapping view of that domain says which standard concept each key means. A question joins the part's column to the mapping view's `local_key`, and it sees concepts and keys, never a hospital's code or description.

Every mapping view has the same four columns. The hospital schema populates it from its own translation of the codes, which holds the local code, its description, the concept, the status and the provenance, and which never leaves the hospital; the compiled view replaces each code with a stable hash made with the hospital schema's own salt.

| Column | Type | Meaning |
|---|---|---|
| local_key | local_key | An opaque key that the hospital schema makes from the hospital's own code, the same value that the part's column holds. It is never the code itself. |
| concept_id | whole | The standard concept that the local code means, and 0 where the status is unmapped. |
| status | kind | Whether the local code is `mapped`, `unmapped` or `ambiguous`. A mapped or unmapped code has one row, and an ambiguous code has a row for each concept it may mean. |
| provenance | kind | Where the mapping came from: a person, a reference conversion, the hospital's own conversion, or an inference that no person has confirmed. |

An unmapped code is an ordinary outcome that a question or a conversion step handles, and never an error. A code that the hospital schema has not yet listed at all gives the key `unlisted` in the part, and the mapping view holds no row for it, so a question that joins the view treats it as it treats an unmapped code. The invented world plants a mapped key, an unmapped key, an ambiguous key and a key that the view does not list, in `planted_concepts.json`, so that every transformation is tested on each.

The mapping views are these.

### map_drug_concept: drugs

The mapping view of drugs, whose concepts are standard concepts of OMOP's Drug domain. It translates the local keys of `role_drug.drug`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| local_key | local_key | no | An opaque key that the hospital schema makes from the hospital's own code, the same value that the part's column holds; it is never the code itself. |
| concept_id | whole | no | The concept that the local code means, which is a standard concept of OMOP's Drug domain, such as an ingredient or a clinical drug, and 0 where the status is unmapped. |
| status | kind | no | Whether the local code is mapped, unmapped or ambiguous, as one of the kinds of the mapping status vocabulary. |
| provenance | kind | no | Where the mapping came from, as one of the kinds of the mapping provenance vocabulary. |

### map_procedure_concept: procedures

The mapping view of procedures, whose concepts are standard concepts of OMOP's Procedure domain. It translates the local keys of `role_operation.procedure`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| local_key | local_key | no | An opaque key that the hospital schema makes from the hospital's own code, the same value that the part's column holds; it is never the code itself. |
| concept_id | whole | no | The concept that the local code means, which is a standard concept of OMOP's Procedure domain, and 0 where the status is unmapped. |
| status | kind | no | Whether the local code is mapped, unmapped or ambiguous, as one of the kinds of the mapping status vocabulary. |
| provenance | kind | no | Where the mapping came from, as one of the kinds of the mapping provenance vocabulary. |

### map_diagnosis_concept: diagnoses

The mapping view of diagnoses, whose concepts are standard concepts of OMOP's Condition domain. It translates the local keys of `role_diagnosis.diagnosis`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| local_key | local_key | no | An opaque key that the hospital schema makes from the hospital's own code, the same value that the part's column holds; it is never the code itself. |
| concept_id | whole | no | The concept that the local code means, which is a standard concept of OMOP's Condition domain, and 0 where the status is unmapped. |
| status | kind | no | Whether the local code is mapped, unmapped or ambiguous, as one of the kinds of the mapping status vocabulary. |
| provenance | kind | no | Where the mapping came from, as one of the kinds of the mapping provenance vocabulary. |

### map_lab_concept: laboratory tests

The mapping view of laboratory tests, whose concepts are standard concepts of OMOP's Measurement domain. It translates the local keys of `role_lab.test`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| local_key | local_key | no | An opaque key that the hospital schema makes from the hospital's own code, the same value that the part's column holds; it is never the code itself. |
| concept_id | whole | no | The concept that the local code means, which is a standard concept of OMOP's Measurement domain, and 0 where the status is unmapped. |
| status | kind | no | Whether the local code is mapped, unmapped or ambiguous, as one of the kinds of the mapping status vocabulary. |
| provenance | kind | no | Where the mapping came from, as one of the kinds of the mapping provenance vocabulary. |

### map_unit_concept: units

The mapping view of units, whose concepts are standard concepts of OMOP's Unit domain. It translates the local keys of `role_drug.unit`, `role_lab.unit`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| local_key | local_key | no | An opaque key that the hospital schema makes from the hospital's own code, the same value that the part's column holds; it is never the code itself. |
| concept_id | whole | no | The concept that the local code means, which is a standard concept of OMOP's Unit domain, and 0 where the status is unmapped. |
| status | kind | no | Whether the local code is mapped, unmapped or ambiguous, as one of the kinds of the mapping status vocabulary. |
| provenance | kind | no | Where the mapping came from, as one of the kinds of the mapping provenance vocabulary. |

## What an anaesthetic record holds, and where each part sits in the roles

An anaesthetist knows the record in the order in which it is written, from the assessment before the day to the outcomes weeks later. The table below follows that order and names the role view, and where it matters the column or the kind, that holds each part. Each role view is described in full in the next section.

| Part of the record | Where it sits in the roles |
|---|---|
| Pre-anaesthetic assessment | The assessment sits across three views. Its grade, weight and height are `asa_grade`, `weight_kg` and `height_cm` of `role_anaesthetic_detail`; its facts, such as the Mallampati grade, the fasting hours, a recent upper respiratory tract infection or a family history of a problem with anaesthesia, are rows of `role_finding`; and its text is a row of `role_note` of the kind `preanaesthetic_assessment`. The date of birth is in `role_patient`, and the sex, the gestation and the birth weight are in `role_patient_detail`. A coded comorbidity is a row of `role_diagnosis`, and a preoperative result, such as a haemoglobin, is a row of `role_lab`. |
| Booking and urgency | Whether the operation was an emergency is `role_anaesthetic_detail.is_emergency`, and whether intensive care was booked beforehand is `planned_icu` of the same view. Whether the admission itself was unplanned is `role_stay.unplanned`. The indication on the theatre booking is a coded diagnosis in `role_diagnosis`. |
| The people present | Each anaesthetist, surgeon and anaesthetic assistant is a row of `role_staff`, with the role, the grade and the times at which that person was present, so that a handover shows as two rows. |
| Induction and airway | Each technique charted for the anaesthetic, such as a general anaesthetic with an intravenous induction, is a row of `role_technique`. The start of anaesthesia, the induction and the moment the airway was secured are rows of `role_event`. The airway device, with its size, is a row of `role_device`, and the induction drugs are rows of `role_drug`. A difficult airway is the event `difficult_airway`. |
| Ventilation | The ventilator's settings and measurements are rows of `role_reading`, of the kinds `fio2`, `peep`, `tidal_volume` and `respiratory_rate`. The end-tidal carbon dioxide is the kind `etco2`, and the end-tidal concentration of a volatile agent is the kind `end_tidal_agent`. |
| Monitoring | The heart rate, the oxygen saturation, the temperature and the blood pressures are rows of `role_reading`. A pressure from an arterial line and a pressure from a cuff are separate kinds, each with its mean, systolic and diastolic, and the central venous pressure has its own kind. A blood glucose from a bedside meter, charted in the anaesthetic's record, is the reading `blood_glucose`. |
| Drugs and infusions | Each order, each dose given, and each start, change of rate, pause, restart and stop of an infusion, is a row of `role_drug`, with its amount, unit, route and time, and the order it carries out. The drug and the unit are local keys of `map_drug_concept` and `map_unit_concept`, so an audit names the drugs that it wants by their standard concepts. |
| Techniques and blocks | Each technique charted for the anaesthetic is a row of `role_technique`, with its side, its guidance, whether a catheter was left in place, its time and who did it. A block's drug, dose and volume are rows of `role_drug` with a perineural, epidural, caudal or intrathecal route, and a failed block, a conversion to general anaesthesia and a complication of a block are rows of `role_event`. |
| Fluids and blood | Each volume of crystalloid, colloid or blood product given, and each volume of blood or urine lost, is a row of `role_fluid`, with its kind, its volume in millilitres as charted, and whether that volume is an amount or a running total. Blood salvaged and returned to the patient is the kind `cell_salvage`. |
| Lines and devices | Each airway, line and catheter placed for the anaesthetic is a row of `role_device`, with its kind, size and site and the times at which it was placed and removed. |
| Events and complications | The milestones and the complications of the anaesthetic are rows of `role_event`. The complications that an anaesthetic audit usually asks about, such as laryngospasm, bronchospasm, desaturation, cardiac arrest, anaphylaxis, a drug error and awareness, each have their own kind. Where a hospital records complications in a separate incident or quality record, the map reads them from there. |
| Emergence and recovery | The extubation, the end of anaesthesia, the arrival in recovery and the discharge from recovery are rows of `role_event`. A pain score charted in the anaesthetic's record is the reading `pain_score`. Readings taken in recovery belong to the anaesthetic only where the hospital charts recovery on the anaesthetic's own record, by the third rule above. |
| The operation | Each procedure done under the anaesthetic is a row of `role_operation`, with the procedure as a local key of `map_procedure_concept`, its specialty and whether it was cardiac. The incision, the end of surgery and any time on bypass are rows of `role_event`. Where the anaesthetic was given, such as in theatre or in imaging, is `role_anaesthetic_detail.location`. |
| Diagnoses | Each coded diagnosis, from the coded discharge diagnoses, the problem list or the theatre booking's indication, is a row of `role_diagnosis`, with the diagnosis as a local key of `map_diagnosis_concept`, whether it is the principal diagnosis of the stay, and the source kind that says which of the three it came from. |
| The stay and unit moves | The hospital stay is a row of `role_stay`, and each transfer into or out of a unit, such as intensive care, is a row of `role_transfer`, as charted. The periods in each unit are worked out from the transfers by a question. |
| Notes | Every piece of free text, from the assessment and the anaesthetic summary to the operation note and the discharge summary, is a row of `role_note`, and each fact that the hospital takes from a note is a row of `role_finding`. The section on notes below says how these two views are handled. |
| Outcomes | An audit works out each outcome from the roles, such as death from `role_patient.death_date`, an unplanned admission to intensive care from `role_transfer`, and a postoperative result from `role_lab`. The section on the capability catalogue below lists them. |

The model leaves out three parts of the record on purpose, or holds them only in part.

- The model holds no images, such as an ultrasound picture of a block, a scanned chart or a photograph. An image cannot be counted, and most hospitals hold images outside the clinical database.
- The model records each regional technique with its side, its guidance and any catheter in `role_technique`, and the local anaesthetic given as a dose in `role_drug`, but not the finer shape of a block, such as the exact nerve or space, the approach and the needle. Hospitals record blocks in very different ways, often as a form of their own or as free text, and no audit yet needs that detail.
- The model holds no coded allergy list. The allergy list describes the patient rather than the anaesthetic, its coding differs widely between hospitals, and no audit yet reads it. An allergy written in the assessment is still held, as a finding of the kind `allergy`, and an anaphylaxis during the anaesthetic is held as the event `anaphylaxis`.

The model also does not yet hold the patient's position. The owner can add it as a column of a view when an audit needs it.

### Notes, and the facts taken from them

Much of what an anaesthetist wants from the record, such as the airway history, the fasting and the reasons for concern, is written as free text, so the model holds the notes in `role_note` and the facts taken from them in `role_finding`.

Notes are where detail that identifies a patient is densest. A note names the patient, the family, the staff and the places, and a description of a rare syndrome can identify a child on its own. A query over the roles that returns the text of a note, or a finding's value taken from one, is therefore a different class of query from one that returns counts. Such a query is an extract, and it must go through the hospital's own approval for extracts; the page will mark every query that reads `role_note.text` as one. Schemalyser itself never reads any hospital's notes. Every note it has seen is invented.

The hospital fills `role_finding` in one of four ways, and the choice of way is the hospital's.

- Many systems hold much of a pre-anaesthetic assessment as discrete answers to a form, such as a Mallampati grade or a yes or no for a recent cold. A map should find these first, and gives them the method `structured_field`.
- A rule is a pattern query written against `role_note`, such as one that looks for the hours of fasting. The rule is tested on the shadow's invented notes, then run on production, where only its counts come back to show how often it fires. A fact found in this way has the method `rule`.
- A person may read the notes and record each fact, which has the method `person`.
- The hospital may run a model over its notes inside the hospital, which has the method `model`. That decision is the hospital's and never Schemalyser's.

Any notes that the shadow holds are invented from templates, so that pattern queries can be written and tested before any real note is seen. Their phrasing is a guess until the hospital's reconciliation of real charts checks it, and a rule that passes on the shadow may still miss the way real notes are written.

## The roles

Every map supplies the first three views, which the neonatal audit reads, and their columns change only with a new version of the contract. The further views are a starter set for the outcomes and covariates below, which the owner will trim; a map supplies a further view only where the hospital records it. In each table below, "Needed" says whether the audit written so far reads the column.

### role_patient: one row for each patient

This view is part of the version 1 contract.

This view holds one row for each patient, test and training patients included, so that an audit can leave them out. The hospital usually holds it in the patient table of its system, which has one row for each person. The date of death is often held in a second patient table, or loaded from a registry of deaths.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| patient_key | key | yes | The hospital's own identifier of the patient, the same value that `role_anaesthetic.patient_key` holds. |
| birth_date | date | yes | The date of birth, to the day, so that an age in days counts calendar dates. |
| death_date | date | yes | The date of death where the hospital knows of it, and empty otherwise. |
| is_test | flag | yes | 1 for a test or training patient who is not a real person, and 0 for every other patient. An empty flag in the source counts as 0. |

### role_anaesthetic: one row for each anaesthetic

This view is part of the version 1 contract.

This view holds one row for each episode of anaesthesia or sedation given by an anaesthetist, including those whose start or stop is missing or out of order. The hospital usually holds it in an anaesthetic record or anaesthesia episode of its own, which is linked to a theatre case or to an encounter of its own. It links to `role_patient` by `patient_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | yes | The hospital's own identifier of the anaesthetic, the same value that `role_reading.anaesthetic_key` holds. |
| patient_key | key | yes | The patient who had the anaesthetic, as `role_patient.patient_key`. |
| start_time | datetime | yes | When the anaesthetic started, as the source holds it. |
| stop_time | datetime | yes | When the anaesthetic stopped, as the source holds it, and empty where no stop was recorded. |

### role_reading: one row for each value charted in an anaesthetic's record

This view is part of the version 1 contract.

This view holds one row for each value charted in an anaesthetic's own record, by a monitor or by hand, including values that were not accepted and values that are not numbers. The hospital usually holds readings in a table of measurements or observations, often the largest in its system, reached from the anaesthetic through the record or sheet on which they were charted. It links to `role_anaesthetic` by `anaesthetic_key`, and a reading belongs in the view only through that link. Its rows are identified by `reading_key`.

The anaesthetic, the kind and the time do not identify one measurement, because two readings can legitimately share all three: a value charted twice at one moment, a value and its correction, or two monitors that each send a mean at the same minute. `reading_key` is therefore the hospital's own identifier of the charted value where the record has one. Where it has none, the map makes a value that is unique to each row, usually by joining the columns that together identify a row of the table, such as the sheet and the line on it, and the map must say in its evidence which of the two it gives.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | yes | The anaesthetic whose record holds the reading, as `role_anaesthetic.anaesthetic_key`. |
| kind | kind | yes | What was measured, as one of the kinds of readings below. |
| reading_time | datetime | yes | When the value was taken, as the source holds it, and not when it was filed. |
| value | number | yes | The value as a number in the kind's own unit, and empty where the charted value is not a number. |
| accepted | flag | yes | 1 where the value counts as valid, and 0 where it was marked as an artefact, rejected or superseded. An empty flag in the source counts as 1. |
| reading_key | key | no | The hospital's own identifier of the charted value, or, where the record has none, a value that the map makes unique to each row. The map says which. |
| value_text | text | no | The value as the record holds it, where it cannot be read as a number, and empty where the value is a number or the record holds none. |

The kinds of readings are these. A map gives the hospital's own codes for each kind that it can find, and every other value charted in the record is of the kind `other`.

| Kind | Unit | Meaning |
|---|---|---|
| map_arterial | mmHg | A mean arterial pressure from an arterial line, in mmHg. The audit needs it. |
| map_cuff | mmHg | A mean arterial pressure from a non-invasive cuff, in mmHg. The audit needs it. |
| heart_rate | per minute | A heart rate, in beats a minute. |
| spo2 | per cent | An oxygen saturation by pulse oximetry, in per cent. |
| etco2 | mmHg | An end-tidal carbon dioxide, in mmHg. |
| temperature | degrees Celsius | A body temperature, in degrees Celsius. |
| systolic_arterial | mmHg | A systolic pressure from an arterial line, in mmHg. |
| diastolic_arterial | mmHg | A diastolic pressure from an arterial line, in mmHg. |
| systolic_cuff | mmHg | A systolic pressure from a non-invasive cuff, in mmHg. |
| diastolic_cuff | mmHg | A diastolic pressure from a non-invasive cuff, in mmHg. |
| central_venous_pressure | mmHg | A central venous pressure, in mmHg. |
| fio2 | per cent | A fraction of inspired oxygen, in per cent. |
| peep | cmH2O | A positive end-expiratory pressure, in cmH2O. |
| tidal_volume | mL | An expired tidal volume, in millilitres. |
| respiratory_rate | per minute | A respiratory rate, in breaths a minute. |
| end_tidal_agent | per cent | An end-tidal concentration of a volatile agent, in per cent, whichever agent it is. |
| pain_score | points | A pain score, as a number on the scale that the hospital uses, usually from 0 to 10. |
| blood_glucose | mmol/L | A blood glucose charted in the anaesthetic's record, usually from a bedside meter, in mmol/L. |
| fresh_gas_flow | L/min | The total fresh gas flow, in litres a minute. |
| inspired_sevoflurane | per cent | An inspired concentration of sevoflurane, in per cent. |
| expired_sevoflurane | per cent | An expired concentration of sevoflurane, in per cent. |
| inspired_desflurane | per cent | An inspired concentration of desflurane, in per cent. |
| expired_desflurane | per cent | An expired concentration of desflurane, in per cent. |
| inspired_isoflurane | per cent | An inspired concentration of isoflurane, in per cent. |
| expired_isoflurane | per cent | An expired concentration of isoflurane, in per cent. |
| inspired_nitrous_oxide | per cent | An inspired concentration of nitrous oxide, in per cent. |
| peak_airway_pressure | cmH2O | A peak airway pressure, in cmH2O. |
| plateau_pressure | cmH2O | A plateau pressure, in cmH2O. |
| ventilation_mode | none | The mode of ventilation, which is not a number and is kept as charted in value_text. |
| pain_score_flacc | points | A pain score on the FLACC scale, from 0 to 10. |
| pain_score_numeric | points | A pain score on a numeric rating scale, from 0 to 10. |
| pain_score_faces | points | A pain score on the Wong-Baker faces scale, from 0 to 10. |
| nausea_score | points | A score of nausea and vomiting, on the scale that the chart uses. |
| sedation_score | points | A sedation score, on the scale that the chart uses. |
| ciba | points | A score of the Child Induction Behavioral Assessment. |
| paed | points | A score of the Paediatric Anaesthesia Emergence Delirium scale. |
| other | none | Any other value charted in the anaesthetic's record, which the counts use to see whether the record is reached at all. |

The kinds from `fresh_gas_flow` to `paed` were added in version 1.1, each with its unit, and no earlier kind changed its meaning. A ventilation mode is not a number, so it is kept as charted in `value_text` with an empty value. The site of a temperature is not held: `value_text` holds a value only where it is not a number, so carrying a site beside a numeric temperature would change the meaning of a column of version 1, which waits for version 2.

### role_patient_detail: the patient's details at birth

This view is a draft, not yet used by any audit.

This view holds one row for each patient whose record holds any of these details. The hospital may hold them in a second patient table, in the child's birth history, in the mother's delivery record where that is linked to the baby, or as a measurement charted on the newborn. It links to `role_patient` by `patient_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| patient_key | key | no | The patient, as `role_patient.patient_key`. |
| sex | kind | no | The sex recorded at birth, as one of the kinds of the sex vocabulary. |
| gestation_weeks | number | no | The gestational age at birth in weeks, with any days as a fraction of a week. |
| birth_weight_grams | number | no | The weight at birth in grams. |

### role_stay: one row for each hospital stay

This view is a draft, not yet used by any audit.

This view holds one row for each hospital stay, from admission to discharge, day stays included. The hospital usually holds it in its table of hospital encounters or admissions. It links to `role_patient` by `patient_key`, and `role_anaesthetic_detail`, `role_transfer`, `role_lab`, `role_diagnosis` and every part that records events link to it by `stay_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| stay_key | key | no | The hospital's own identifier of the stay. |
| patient_key | key | no | The patient whose stay it is, as `role_patient.patient_key`. |
| admit_time | datetime | no | When the patient was admitted, as the source holds it. |
| discharge_time | datetime | no | When the patient was discharged, or died in hospital, as the source holds it, and empty while the patient is still in hospital. |
| unplanned | flag_or_empty | no | 1 for an emergency or other unplanned admission, 0 for a planned one, and empty where the record does not say. The hospital usually records this as an admission type or category, whose values a person translates. |

### role_anaesthetic_detail: the anaesthetic's covariates

This view is a draft, not yet used by any audit.

This view holds one row for each anaesthetic whose record holds any of these details. The hospital holds them partly in the anaesthetic record and partly in the theatre case or surgical log, with the pre-anaesthetic assessment for the grade, the weight and the height. It links to `role_anaesthetic` by `anaesthetic_key` and to `role_stay` by `stay_key`.

The columns `height_cm` and `location` were added to this view on 7 October 2026. The column `technique` left it in version 1.1, because the record charts each technique as a row of its own, which `role_technique` now holds, and no map should choose a main one.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic, as `role_anaesthetic.anaesthetic_key`. |
| stay_key | key | no | The hospital stay in which the anaesthetic took place, as `role_stay.stay_key`, and empty where the anaesthetic belongs to no stay. |
| asa_grade | whole | no | The ASA physical status grade from 1 to 6, without any letter for an emergency. |
| is_emergency | flag_or_empty | no | 1 where the operation was done as an emergency, 0 where it was planned, and empty where the record does not say. |
| weight_kg | number | no | The patient's weight at the anaesthetic in kilograms. |
| planned_icu | flag_or_empty | no | 1 where intensive care after the operation was planned before it began, 0 where it was not, and empty where the record does not say. |
| unplanned_return | flag_or_empty | no | 1 where the operation was recorded as an unplanned return to theatre, 0 where it was not, and empty where the record does not say. |
| height_cm | number | no | The patient's height or length at the anaesthetic, in centimetres. |
| location | kind | no | Where the anaesthetic was given, as one of the kinds of the location vocabulary. |

### role_operation: one row for each procedure done under an anaesthetic

This view is a draft, not yet used by any audit.

This view holds one row for each procedure done under an anaesthetic, so that an anaesthetic for several procedures has several rows, and a procedure charted twice has two. The hospital usually holds the procedures of a theatre case or surgical log, with a lookup table of the procedures that it offers. It links to `role_anaesthetic` by `anaesthetic_key`; at many hospitals that link runs from the anaesthetic record to its theatre case, so the map must take care that a case with two anaesthetics does not repeat its procedures.

The procedure is the local key of `map_procedure_concept`. The hospital's own code and name of the procedure stay in the hospital schema.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| operation_key | key | no | The hospital's own identifier of the procedure line, or, where the record has none, a value that the map makes from the columns that identify a row, unique to each row; the map says which. |
| anaesthetic_key | key | no | The anaesthetic under which the procedure was done, as `role_anaesthetic.anaesthetic_key`. |
| procedure | local_key | no | The procedure, as the local key of `map_procedure_concept`, which gives its standard concept; the hospital's own code and name stay in the hospital schema. |
| specialty | kind | no | The surgical specialty of the procedure, as one of the kinds of the specialty vocabulary, which gives the operation's kind. |
| is_cardiac | flag_or_empty | no | 1 where the procedure was a cardiac operation or a cardiac catheter procedure, 0 where it was not, and empty where nothing says. |

### role_transfer: one row for each transfer into or out of a unit

This view is a draft, not yet used by any audit.

This view holds one row for each transfer of a patient into or out of a unit during a hospital stay, as the source records it. The hospital usually holds the movements of the encounter, with one row for each transfer and a list of units that says what kind each one is. It links to `role_stay` by `stay_key`.

It replaces the draft `role_unit_stay` of version 1.0, which asked the map to build each period in a unit from the transfers. Building a period means pairing a transfer in with the next transfer out, and deciding what a missing transfer out means; that is a decision of a question, so the period is now public logic over this part, and the map gives each transfer as charted.

Like every part that records events, it has a key of its own for each row, so that two rows that share their time and kind are both kept; a `source_kind` from the source kind vocabulary on every row, which the hospital schema gives for each pathway; the `documented_time` at which the row was entered, where the source records it apart from the time it describes; and the `amends_key` of the row it corrects, where the source records a correction as a row of its own.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| transfer_key | key | no | The hospital's own identifier of the transfer, or, where the record has none, a value that the map makes from the columns that identify a row, unique to each row; the map says which. |
| stay_key | key | no | The hospital stay during which the transfer took place, as `role_stay.stay_key`. |
| unit_kind | kind | no | The kind of unit that the patient entered or left, as one of the kinds of the unit vocabulary. |
| direction | kind | no | Whether the patient entered or left the unit, as one of the kinds of the transfer direction vocabulary. |
| transfer_time | datetime | no | When the patient entered or left the unit, as the source holds it. |
| source_kind | kind | no | The kind of record from which the transfer came, as one of the kinds of the source kind vocabulary. The hospital schema names it for each pathway to this part, and the view gives it on every row of that pathway. |
| documented_time | datetime | no | When the transfer was entered or filed, where the source records that apart from the time it describes, and empty where it does not. |
| amends_key | key | no | The key of the row of this part that this transfer amends or corrects, where the source records a correction as a row of its own, and empty otherwise. |

### role_event: one row for each milestone of an anaesthetic

This view is a draft, not yet used by any audit.

This view holds one row for each timed milestone of an anaesthetic, and for each complication that the record marks with a time. The hospital usually holds them as events of the anaesthetic record, or as the timing events of the theatre case, each with a code for its kind. A hospital may instead record a complication in a quality or incident record of the anaesthetic, which the hospital schema reads as a further pathway with the source kind `incident`. It links to `role_anaesthetic` by `anaesthetic_key` and to `role_stay` by `stay_key`.

Like every part that records events, it has a key of its own for each row, so that two rows that share their time and kind are both kept; a `source_kind` from the source kind vocabulary on every row, which the hospital schema gives for each pathway; the `documented_time` at which the row was entered, where the source records it apart from the time it describes; and the `amends_key` of the row it corrects, where the source records a correction as a row of its own.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| event_key | key | no | The hospital's own identifier of the event row, or, where the record has none, a value that the map makes from the columns that identify a row, unique to each row; the map says which. |
| anaesthetic_key | key | no | The anaesthetic to which the event belongs, as `role_anaesthetic.anaesthetic_key`, where the source links the row to it, and empty where the source carries only the stay. |
| stay_key | key | no | The hospital stay of the event, as `role_stay.stay_key`, where the source carries it. A row whose source carries only the stay has this key and an empty anaesthetic_key, and a question attributes it to an anaesthetic by its own logic over the roles. |
| kind | kind | no | The kind of event, as one of the kinds of the event vocabulary. |
| event_time | datetime | no | When the event happened, as the source holds it. |
| source_kind | kind | no | The kind of record from which the event came, as one of the kinds of the source kind vocabulary. The hospital schema names it for each pathway to this part, and the view gives it on every row of that pathway. |
| documented_time | datetime | no | When the event was entered or filed, where the source records that apart from the time it describes, and empty where it does not. |
| amends_key | key | no | The key of the row of this part that this event amends or corrects, where the source records a correction as a row of its own, and empty otherwise. |

### role_drug: one row for each drug event

This view is a draft, not yet used by any audit.

This view holds one row for each order of a drug, each dose given during an anaesthetic, and each start, change of rate, pause, restart and stop of an infusion, as the source records them. The hospital usually holds them as administrations in its medication record, linked to their orders, and sometimes as drug values charted on the anaesthetic record. It links to `role_anaesthetic` by `anaesthetic_key` and to `role_stay` by `stay_key`.

The view never turns the events into intervals. An infusion is its start, its changes of rate, its pauses and restarts and its stop, each a row, and `order_key` groups the rows of one infusion; working out the intervals over which it ran is the capability `exposure_intervals`, and an infusion whose stop the source does not record keeps an interval of unknown end there. The drug is the local key of `map_drug_concept` and the unit the local key of `map_unit_concept`, so a question names drugs by their standard concepts and never by the hospital's own names. The route is one of the kinds of the route vocabulary, which includes the perineural, epidural, caudal and intrathecal routes of a block.

Like every part that records events, it has a key of its own for each row, so that two rows that share their time and kind are both kept; a `source_kind` from the source kind vocabulary on every row, which the hospital schema gives for each pathway; the `documented_time` at which the row was entered, where the source records it apart from the time it describes; and the `amends_key` of the row it corrects, where the source records a correction as a row of its own.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| drug_event_key | key | no | The hospital's own identifier of the administration row, or, where the record has none, a value that the map makes from the columns that identify a row, unique to each row; the map says which. |
| anaesthetic_key | key | no | The anaesthetic to which the dose or infusion action belongs, as `role_anaesthetic.anaesthetic_key`, where the source links the row to it, and empty where the source carries only the stay. |
| stay_key | key | no | The hospital stay of the dose or infusion action, as `role_stay.stay_key`, where the source carries it. A row whose source carries only the stay has this key and an empty anaesthetic_key, and a question attributes it to an anaesthetic by its own logic over the roles. |
| order_key | key | no | The hospital's own identifier of the order that the dose or infusion action carries out, so that the start, rate changes, pauses and stop of one infusion can be told from those of another, and empty where the source records none. |
| drug | local_key | no | The drug, as the local key of `map_drug_concept`, which gives its standard concept; the hospital's own code and name stay in the hospital schema. |
| action | kind | no | What was done, as one of the kinds of the drug action vocabulary. |
| amount | number | no | The dose, or the rate of an infusion, as a number in the unit beside it, as the source records it. |
| unit | local_key | no | The unit of the amount, as the local key of `map_unit_concept`, which gives its standard concept. |
| route | kind | no | The route by which the drug was given, as one of the kinds of the route vocabulary. |
| given_time | datetime | no | When the dose was given or the infusion was started, changed, paused, restarted or stopped, as the source holds it. |
| source_kind | kind | no | The kind of record from which the dose or infusion action came, as one of the kinds of the source kind vocabulary. The hospital schema names it for each pathway to this part, and the view gives it on every row of that pathway. |
| documented_time | datetime | no | When the dose or infusion action was entered or filed, where the source records that apart from the time it describes, and empty where it does not. |
| amends_key | key | no | The key of the row of this part that this dose or infusion action amends or corrects, where the source records a correction as a row of its own, and empty otherwise. |

### role_technique: one row for each technique charted for an anaesthetic

This view is a draft, not yet used by any audit.

This view holds one row for each technique charted for an anaesthetic, such as a general anaesthetic with an intravenous induction and a caudal block for the same anaesthetic. The hospital usually holds the kinds of anaesthesia given as a list on the anaesthetic record, and each block as a form or a procedure note with its side, its guidance and any catheter. It links to `role_anaesthetic` by `anaesthetic_key`.

Version 1.0 held the technique as one column of `role_anaesthetic_detail` and let the map choose the main one where the record held several. The record charts each technique as an entry of its own, and has no clinical time for most of them, so a part of its own keeps closer to the chart than rows of `role_event` would, which would need a time that the record does not give. A block's drug, dose and volume are rows of `role_drug`, with a route such as perineural or caudal; a failed block, a conversion to general anaesthesia and a complication of a block are rows of `role_event`. The performer is the staff member's own key, as `role_staff.person_key` gives it, and never a name.

Like every part that records events, it has a key of its own for each row, a `source_kind`, the `documented_time` at which the row was entered, and the `amends_key` of the row it corrects. Its `recorded_time` is when the technique was begun or the block was done, where the record gives one.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic for which the technique was charted, as `role_anaesthetic.anaesthetic_key`. |
| technique_key | key | no | The hospital's own identifier of the charted technique, or, where the record has none, a value that the map makes from the columns that identify a row, unique to each row; the map says which. |
| kind | kind | no | The technique, as one of the kinds of the technique vocabulary. An anaesthetic with several techniques has a row for each, and no row is chosen as the main one. |
| laterality | kind | no | The side of the body on which a block was done, as one of the kinds of the laterality vocabulary, and empty for a technique that has no side. |
| guidance | kind | no | How a block was guided, as one of the kinds of the guidance vocabulary, and empty for a technique that is not a block. |
| catheter | flag_or_empty | no | 1 where a catheter was left in place for the block, 0 where none was, and empty where the record does not say. |
| recorded_time | datetime | no | When the technique was begun or the block was done, as the source holds it, and empty where the record gives no time. |
| documented_time | datetime | no | When the technique was entered or filed, where the source records that apart from the time of the technique, and empty where it does not. |
| performer | key | no | The hospital's own identifier of the staff member who did the technique, as `role_staff.person_key` gives it, and empty where the record does not say; never the person's name. |
| source_kind | kind | no | The kind of record from which the technique came, as one of the kinds of the source kind vocabulary. The hospital schema names it for each pathway to this part, and the view gives it on every row of that pathway. |
| amends_key | key | no | The key of the row of this part that this technique amends or corrects, where the source records a correction as a row of its own, and empty otherwise. |

### role_fluid: one row for each fluid or blood product given or lost

This view is a draft, not yet used by any audit.

This view holds one row for each volume of fluid or blood product given during an anaesthetic, and for each volume of blood or urine lost, as the source charts it. The hospital usually charts intake and output on the anaesthetic record as measurements, with one row for each kind of fluid, and records blood products in its transfusion record as well. It links to `role_anaesthetic` by `anaesthetic_key` and to `role_stay` by `stay_key`.

Some systems chart each amount, and others a running total. Version 1.0 asked the map to turn a running total into amounts; turning a total into amounts means deciding what a total that falls, restarts or is corrected means, which is a decision of a question. The view now gives the volume as charted, and `volume_form` says whether it is an amount or a running total, so that the capability that totals a volume works the amounts out in public.

Like every part that records events, it has a key of its own for each row, so that two rows that share their time and kind are both kept; a `source_kind` from the source kind vocabulary on every row, which the hospital schema gives for each pathway; the `documented_time` at which the row was entered, where the source records it apart from the time it describes; and the `amends_key` of the row it corrects, where the source records a correction as a row of its own.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| fluid_key | key | no | The hospital's own identifier of the charted volume, or, where the record has none, a value that the map makes from the columns that identify a row, unique to each row; the map says which. |
| anaesthetic_key | key | no | The anaesthetic to which the fluid given or lost belongs, as `role_anaesthetic.anaesthetic_key`, where the source links the row to it, and empty where the source carries only the stay. |
| stay_key | key | no | The hospital stay of the fluid given or lost, as `role_stay.stay_key`, where the source carries it. A row whose source carries only the stay has this key and an empty anaesthetic_key, and a question attributes it to an anaesthetic by its own logic over the roles. |
| kind | kind | no | What was given or lost, as one of the kinds of the fluid vocabulary. |
| volume_ml | number | no | The volume charted, in millilitres, as the source charts it: an amount, or a running total, as volume_form says. |
| volume_form | kind | no | Whether the volume is one amount or a running total since the start of the record, as one of the kinds of the volume form vocabulary; a question that wants amounts works them out from a running total. |
| given_time | datetime | no | When the volume was given or measured, as the source holds it. |
| source_kind | kind | no | The kind of record from which the fluid given or lost came, as one of the kinds of the source kind vocabulary. The hospital schema names it for each pathway to this part, and the view gives it on every row of that pathway. |
| documented_time | datetime | no | When the fluid given or lost was entered or filed, where the source records that apart from the time it describes, and empty where it does not. |
| amends_key | key | no | The key of the row of this part that this fluid given or lost amends or corrects, where the source records a correction as a row of its own, and empty otherwise. |

### role_device: one row for each device placed

This view is a draft, not yet used by any audit.

This view holds one row for each airway device, line or catheter placed for an anaesthetic. The hospital usually holds the lines, drains and airways of the patient's record, each with its placement and removal. A device that the source links only to the patient's stay carries the stay and an empty `anaesthetic_key`, and a question attributes it. It links to `role_anaesthetic` by `anaesthetic_key` and to `role_stay` by `stay_key`.

The columns `size` and `site` were added to this view on 7 October 2026.

Like every part that records events, it has a key of its own for each row, so that two rows that share their time and kind are both kept; a `source_kind` from the source kind vocabulary on every row, which the hospital schema gives for each pathway; the `documented_time` at which the row was entered, where the source records it apart from the time it describes; and the `amends_key` of the row it corrects, where the source records a correction as a row of its own.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| device_key | key | no | The hospital's own identifier of the device row, or, where the record has none, a value that the map makes from the columns that identify a row, unique to each row; the map says which. |
| anaesthetic_key | key | no | The anaesthetic to which the device belongs, as `role_anaesthetic.anaesthetic_key`, where the source links the row to it, and empty where the source carries only the stay. |
| stay_key | key | no | The hospital stay of the device, as `role_stay.stay_key`, where the source carries it. A row whose source carries only the stay has this key and an empty anaesthetic_key, and a question attributes it to an anaesthetic by its own logic over the roles. |
| kind | kind | no | The kind of device, as one of the kinds of the device vocabulary. |
| placed_time | datetime | no | When the device was placed, as the source holds it. |
| removed_time | datetime | no | When the device was removed, as the source holds it, and empty while it stays in place. |
| size | text | no | The size of the device as the hospital records it, such as the internal diameter of a tube or the gauge of a cannula. |
| site | text | no | Where on the body the device was placed, as the hospital records it, such as the left radial artery or the right internal jugular vein. |
| source_kind | kind | no | The kind of record from which the device came, as one of the kinds of the source kind vocabulary. The hospital schema names it for each pathway to this part, and the view gives it on every row of that pathway. |
| documented_time | datetime | no | When the device was entered or filed, where the source records that apart from the time it describes, and empty where it does not. |
| amends_key | key | no | The key of the row of this part that this device amends or corrects, where the source records a correction as a row of its own, and empty otherwise. |

### role_staff: one row for each person attending an anaesthetic

This view is a draft, not yet used by any audit.

This view holds one row for each person who attended an anaesthetic, for each period in which that person was present, so that a handover shows as two rows. The hospital usually holds the staff of the anaesthetic record or of the theatre case, each with a role and a start and end of responsibility, and a list of staff that gives each person's grade. That list usually holds the grade that the person holds today, so the map should say whether the grade on the day can be found. It links to `role_anaesthetic` by `anaesthetic_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic that the person attended, as `role_anaesthetic.anaesthetic_key`. |
| person_key | key | no | The hospital's own identifier of the staff member. The view never gives the person's name. |
| role | kind | no | What the person did at the anaesthetic, as one of the kinds of the staff role vocabulary. |
| grade | kind | no | The person's grade at the time of the anaesthetic, as one of the kinds of the grade vocabulary. |
| present_from | datetime | no | When the person took over or joined the care of the patient, as the source holds it. |
| present_to | datetime | no | When the person handed over or left the care of the patient, as the source holds it, and empty where the record does not say. |

### role_lab: one row for each laboratory or point-of-care result

This view is a draft, not yet used by any audit.

This view holds one row for each laboratory or point-of-care result for the patient, including blood gases. The hospital usually holds the results of its laboratory orders with one row for each component of each result, and often files point-of-care and blood gas results in the same place. The view links to `role_patient` by `patient_key` and to `role_stay` by `stay_key`, and not to an anaesthetic, because a result belongs to the patient. An audit chooses its own window around the anaesthetic, such as the last haemoglobin in the 30 days before the start or the highest lactate in the 24 hours after the stop.

The kind is one of the closed vocabulary of laboratory kinds below, for the results that anaesthetic audits read most, and the test is the local key of `map_lab_concept`, so that a result of the kind `other` can still be told apart by its standard concept. The unit is the local key of `map_unit_concept`.

The units of the laboratory kinds are these: haemoglobin in g/L; lactate, glucose, potassium, sodium and base excess in mmol/L; pCO2 and pO2 in mmHg; creatinine in micromol/L; and pH without a unit.

Like every part that records events, it has a key of its own for each row, so that two rows that share their time and kind are both kept; a `source_kind` from the source kind vocabulary on every row, which the hospital schema gives for each pathway; the `documented_time` at which the row was entered, where the source records it apart from the time it describes; and the `amends_key` of the row it corrects, where the source records a correction as a row of its own.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| lab_key | key | no | The hospital's own identifier of the result component, or, where the record has none, a value that the map makes from the columns that identify a row, unique to each row; the map says which. |
| patient_key | key | no | The patient whose result it is, as `role_patient.patient_key`. |
| stay_key | key | no | The hospital stay during which the specimen was taken, as `role_stay.stay_key`, and empty where the result belongs to no stay. |
| kind | kind | no | What was measured, as one of the kinds of the laboratory vocabulary. |
| test | local_key | no | The test or component, as the local key of `map_lab_concept`, which gives its standard concept, so that a result of the kind other can still be told apart; the hospital's own code and name stay in the hospital schema. |
| value | number | no | The result as a number in the kind's own unit, which the view converts to where the hospital holds another, and empty where the result is not a number. |
| unit | local_key | no | The unit in which the hospital recorded the result, as the local key of `map_unit_concept`, so that a person can check the conversion. |
| taken_time | datetime | no | When the specimen was taken, as the source holds it, and not when the result was reported. |
| source_kind | kind | no | The kind of record from which the result came, as one of the kinds of the source kind vocabulary. The hospital schema names it for each pathway to this part, and the view gives it on every row of that pathway. |
| documented_time | datetime | no | When the result was entered or filed, where the source records that apart from the time it describes, and empty where it does not. |
| amends_key | key | no | The key of the row of this part that this result amends or corrects, where the source records a correction as a row of its own, and empty otherwise. |

### role_diagnosis: one row for each coded diagnosis

This view is a draft, not yet used by any audit.

This view holds one row for each coded diagnosis of the patient. The hospital usually holds coded diagnoses in three places: the diagnoses coded for the stay or its hospital account at discharge, the patient's problem list, and the indication or diagnosis on the theatre booking. Each is a pathway of its own, with the source kind `coded_record`, `problem_list` or `booking`, and an audit chooses among them by the source kind, `is_principal`, `stay_key` and `recorded_time`. The view links to `role_patient` by `patient_key` and to `role_stay` by `stay_key`.

The diagnosis is the local key of `map_diagnosis_concept`. The hospital's own code, its classification and its name stay in the hospital schema.

Like every part that records events, it has a key of its own for each row, so that two rows that share their time and kind are both kept; a `source_kind` from the source kind vocabulary on every row, which the hospital schema gives for each pathway; the `documented_time` at which the row was entered, where the source records it apart from the time it describes; and the `amends_key` of the row it corrects, where the source records a correction as a row of its own.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| diagnosis_key | key | no | The hospital's own identifier of the diagnosis row, or, where the record has none, a value that the map makes from the columns that identify a row, unique to each row; the map says which. |
| patient_key | key | no | The patient whose diagnosis it is, as `role_patient.patient_key`. |
| stay_key | key | no | The hospital stay for which the diagnosis was coded, as `role_stay.stay_key`, and empty for a problem-list entry that belongs to no stay. |
| diagnosis | local_key | no | The diagnosis, as the local key of `map_diagnosis_concept`, which gives its standard concept; the hospital's own code, classification and name stay in the hospital schema. |
| is_principal | flag_or_empty | no | 1 where the diagnosis is the principal diagnosis of the stay, 0 where it is another, and empty where the record does not say. |
| recorded_time | datetime | no | When the diagnosis was noted or coded, as the source holds it, and empty where the record does not say. |
| source_kind | kind | no | The kind of record from which the diagnosis came, as one of the kinds of the source kind vocabulary. The hospital schema names it for each pathway to this part, and the view gives it on every row of that pathway. |
| documented_time | datetime | no | When the diagnosis was entered or filed, where the source records that apart from the time it describes, and empty where it does not. |
| amends_key | key | no | The key of the row of this part that this diagnosis amends or corrects, where the source records a correction as a row of its own, and empty otherwise. |

### role_note: one row for each note in the record

This view is a draft, not yet used by any audit.

This view holds one row for each piece of free text in the record, with its text joined back into one value. The hospital usually holds notes in a notes table keyed by encounter, with a note type, and often splits the text of one note across several rows with a line number, which the view must join back in order. Some text sits instead in the comment fields of a structured form, which the view also reads. The view links to `role_anaesthetic` by `anaesthetic_key` where the system makes that link, to `role_stay` by `stay_key`, and to `role_patient` by `patient_key`. The text is identifying, as the section on notes above explains.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| note_key | key | no | The hospital's own identifier of the note, the same value that `role_finding.note_key` holds. |
| anaesthetic_key | key | no | The anaesthetic to which the note belongs, as `role_anaesthetic.anaesthetic_key`, and empty where the system makes no link to one. |
| patient_key | key | no | The patient whose note it is, as `role_patient.patient_key`. |
| stay_key | key | no | The hospital stay to which the note belongs, as `role_stay.stay_key`, and empty where it belongs to no stay, such as a note of a clinic visit. |
| kind | kind | no | What kind of note it is, as one of the kinds of the note vocabulary. |
| written_time | datetime | no | When the note was written or signed, as the source holds it, and not when it was last edited. |
| author_role | kind | no | The grade of the person who wrote the note, as one of the kinds of the grade vocabulary. |
| text | text | no | The whole text of the note, with its lines joined back in order. |

### role_finding: one row for each fact found in the record

This view is a draft, not yet used by any audit.

This view holds one row for each fact that the hospital's own process takes from a note or from a structured field, with the method by which it was found. A fact from a structured field has no note, and its `note_key` is empty. The view links to `role_note` by `note_key`, to `role_anaesthetic` by `anaesthetic_key` and to `role_patient` by `patient_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| note_key | key | no | The note from which the fact was taken, as `role_note.note_key`, and empty for a fact taken from a structured field. |
| anaesthetic_key | key | no | The anaesthetic to which the fact belongs, as `role_anaesthetic.anaesthetic_key`, and empty where nothing links it to one. |
| patient_key | key | no | The patient of whom the fact is true, as `role_patient.patient_key`. |
| kind | kind | no | What the fact is about, as one of the kinds of the finding vocabulary. |
| value | text | no | The fact as text, such as 3 for a Mallampati grade, 6 for the hours of fasting, or yes or no for a history. |
| method | kind | no | How the fact was found, as one of the kinds of the finding method vocabulary. |
| confidence | number | no | The confidence that the hospital's process gives the fact, from 0 to 1, and empty where it gives none, as for a structured field or a person's reading. |
| found_time | datetime | no | When the fact was recorded in a structured field, or taken from the note, as the source holds it. |

## The kinds of source record, and several pathways to one part

A part that records events can be reached by more than one pathway. A drug, for example, may be ordered in one table, given in another, and charted as a value on the anaesthetic record in a third, and a correction may be recorded as a row of its own. The hospital schema binds each pathway on its own, with the kind of record it reads, and the compiled part is the union of the pathways, each row carrying its pathway's kind as `source_kind`. The hospital schema keeps the evidence of each pathway apart, so that one pathway can be confirmed and checked while another is still proposed. Which pathway a question trusts, and how it reconciles an order with an administration, is the question's decision, made in public over the rows.

The kinds are these, and no kind names a vendor's table.

| Kind | Meaning |
|---|---|
| order | An order or a prescription, made before anything is done. |
| administration | A record that a drug or a fluid was given, made by the person who gave it. |
| charted_value | A value charted on the anaesthetic record or a flowsheet, by hand or from a device. |
| procedure_log | A log of the case, such as the timing events or the procedures of the theatre case. |
| device_record | A record of a line, an airway or another device, with its placement and removal. |
| transfer | A movement of the patient into or out of a unit. |
| result | A result reported by the laboratory or by a point-of-care analyser. |
| coded_record | A code assigned after the event, as at discharge. |
| problem_list | An entry of the patient's problem list. |
| booking | A booking or request made before the event, such as the indication on a theatre booking. |
| incident | An incident or quality record made about the event. |
| correction | A row that the source records to amend or correct an earlier row. |

`role_reading` carries no source kind in version 1.1. Its columns are those of version 1, which change only with version 2, and whether a reading was charted by a monitor, by hand or retrospectively is recorded here as a question for that version.

## The vocabularies

A map translates the hospital's own codes into these kinds. Most vocabularies end with `other`, which holds any value that the map has not translated; the drug actions, the sides, the guidance, the directions of a transfer, the forms of a volume, the source kinds and the two vocabularies of the mapping views name every value they allow.

| Vocabulary | Kinds |
|---|---|
| sex | female, male, indeterminate, other |
| technique | general_inhalational, general_intravenous, sedation, spinal, epidural, combined_spinal_epidural, caudal, upper_limb_block, lower_limb_block, trunk_block, groin_block, head_neck_block, local_infiltration, other |
| specialty | cardiac, general, neurosurgery, orthopaedic, ear_nose_throat, dental, ophthalmic, plastic, urology, imaging, other |
| unit | intensive_care, high_dependency, theatre, recovery, emergency, ward, other |
| location | theatre, imaging, catheter_laboratory, intensive_care, emergency_department, ward, other |
| event | anaesthesia_start, induction, airway_secured, incision, bypass_on, bypass_off, surgery_end, extubation, anaesthesia_stop, recovery_in, recovery_out, laryngospasm, bronchospasm, desaturation, difficult_airway, unplanned_reintubation, cardiac_arrest, anaphylaxis, drug_error, awareness, block_failure, conversion_to_general, block_complication, other |
| device | endotracheal_tube, supraglottic_airway, arterial_line, central_line, peripheral_line, regional_catheter, other |
| drug_action | ordered, dose, infusion_start, rate_change, infusion_pause, infusion_restart, infusion_stop, not_given |
| note | preanaesthetic_assessment, anaesthetic_summary, airway_note, procedure_note, operation_note, recovery_note, consent, discharge_summary, progress_note, other |
| finding | airway_difficulty, mallampati, fasting_hours, previous_anaesthetic_problem, family_anaesthetic_history, syndrome, cardiac_history, respiratory_history, reflux, snoring_or_apnoea, recent_urti, allergy, medication, other |
| finding_method | structured_field, rule, person, model, other |
| staff_role | anaesthetist, surgeon, anaesthetic_assistant, other |
| grade | consultant, fellow, registrar, resident, nurse, other |
| fluid | crystalloid, colloid, red_cells, platelets, plasma, cryoprecipitate, cell_salvage, blood_loss, urine_output, other |
| lab | haemoglobin, lactate, glucose, potassium, sodium, ph, pco2, po2, base_excess, creatinine, other |
| laterality | left, right, bilateral, midline |
| guidance | ultrasound, nerve_stimulator, landmark, none |
| route | intravenous, intramuscular, subcutaneous, oral, rectal, intranasal, inhaled, topical, infiltration, perineural, epidural, caudal, intrathecal, other |
| transfer_direction | in, out |
| volume_form | amount, running_total |
| source_kind | order, administration, charted_value, procedure_log, device_record, transfer, result, coded_record, problem_list, booking, incident, correction |
| mapping_status | mapped, unmapped, ambiguous |
| mapping_provenance | a person, a reference conversion, the hospital's own conversion, an inference |

The event vocabulary holds the milestones in the order in which they usually happen, then the complications. A complication is kept as an event only where the record marks it; an audit that also wants a desaturation found from the readings works it out from `role_reading` itself. An anaesthetic assistant is the anaesthetic nurse or technician who assisted the anaesthetist, and the other people in the room, such as the scrub nurse or a perfusionist, are of the role `other`.

## The capability catalogue

An audit works out each outcome and covariate from the roles, so that no map has to. Each is a capability of the catalogue in `contract.json`: a named measure, versioned, written only in the roles' terms, with what it measures, its parameters, its grain, its unit, its window relative to the anaesthetic where one applies, its output class, and what it requires: the parts, columns, kinds, links and mapping views it reads. A capability may be declared before any hospital supports it. The feasibility report resolves a question through the capabilities that it names, by a line `-- capability: NAME` among its leading comments, and states the evidence of each requirement; an unsupported requirement is an ordinary outcome that produces an evidence request.

The output class is one of three: a row for each anaesthetic with the value measured (`row_per_anaesthetic`), a flag of 1 or 0 for each anaesthetic (`flag`), or an aggregate over many anaesthetics (`aggregate`). The outcomes and covariates below are version 1 of each, carried over from version 1.0 with what they read as their requirements, and the six generic shapes after them are declared with their parameters, with their SQL still to be written over the roles.

The last five are the first of the department's measures (`docs/capabilities.md`), and each carries its SQL. The catalogue entry names a file under `capabilities/`, beside this file, which is public, begins with the capability's name and version, and holds each parameter as a placeholder of one fixed form, the parameter's name between double braces. A parameter is a number, a whole number, a whole number or empty, a choice from a fixed list, or a table: a threshold by age band, a list of kinds with their preference, and a factor by agent are tables, because one number cannot hold them. Hypotension burden and hypoxaemia burden are the shape `minutes_beyond_threshold` with a threshold by age band, and the neonatal low mean pressure audit is the first instance of hypotension burden. Hypothermia anchors its recovery window to the anaesthetic's stop until the events part is promoted. Each was tested on planted cases in the role shadow against expected answers written by hand and held out, in `fixtures/held-out/capabilities/`.

| Capability | Version | Output | Requires | Meaning |
|---|---|---|---|---|
| death_within_days | 1 | flag | `role_patient.death_date`, `role_anaesthetic.start_time` | Whether the patient died within a given number of days of the anaesthetic's start, counted by calendar date. |
| unplanned_intensive_care | 1 | flag | `role_transfer.unit_kind`, `role_transfer.direction`, `role_transfer.transfer_time`, `role_anaesthetic_detail.planned_icu`, `role_anaesthetic.stop_time`, `role_transfer.unit_kind = intensive_care` | Whether the patient entered intensive care within 24 hours after the anaesthetic's stop, when intensive care had not been planned and the patient was not already there at the start. |
| return_to_theatre_30_days | 1 | flag | `role_anaesthetic.start_time`, `role_anaesthetic.stop_time`, `role_anaesthetic.patient_key`, `role_anaesthetic_detail.unplanned_return` | Whether the same patient had another anaesthetic with an operation within 30 days after this one's stop, and whether the hospital recorded that return as unplanned. |
| length_of_stay | 1 | row_per_anaesthetic | `role_stay.admit_time`, `role_stay.discharge_time`, `role_anaesthetic_detail.stay_key`, `role_anaesthetic.stop_time` | The time from admission to discharge of the stay in which the anaesthetic took place, and the time from the anaesthetic's stop to that discharge. |
| readmission_30_days | 1 | flag | `role_stay.admit_time`, `role_stay.discharge_time`, `role_stay.unplanned`, `role_anaesthetic_detail.stay_key` | Whether the patient began another unplanned stay within 30 days after the discharge of the stay in which the anaesthetic took place. |
| anaesthetic_complication | 1 | flag | `role_event.kind` | Whether the anaesthetic's record holds any of the complications of the event vocabulary, such as laryngospasm, desaturation or cardiac arrest. |
| transfusion | 1 | row_per_anaesthetic | `role_fluid.kind`, `role_fluid.volume_ml`, `role_fluid.volume_form`, `role_anaesthetic_detail.weight_kg`, `role_fluid.kind = red_cells`, `role_fluid.kind = platelets`, `role_fluid.kind = plasma`, `role_fluid.kind = cryoprecipitate` | Whether the patient was given red cells, platelets, plasma or cryoprecipitate during the anaesthetic, and how much in millilitres for each kilogram. |
| recovery_time | 1 | row_per_anaesthetic | `role_event.kind`, `role_event.event_time`, `role_event.kind = recovery_in`, `role_event.kind = recovery_out` | The time from arrival in recovery to discharge from recovery. |
| postoperative_result | 1 | row_per_anaesthetic | `role_lab.kind`, `role_lab.value`, `role_lab.taken_time`, `role_anaesthetic.stop_time` | A laboratory result, such as a lactate or a creatinine, in a window after the anaesthetic's stop that the audit chooses. |
| gestational_age | 1 | row_per_anaesthetic | `role_patient_detail.gestation_weeks` | The gestational age at birth, as recorded. |
| weight_at_anaesthetic | 1 | row_per_anaesthetic | `role_anaesthetic_detail.weight_kg` | The patient's weight at the anaesthetic, as recorded. |
| asa_grade | 1 | row_per_anaesthetic | `role_anaesthetic_detail.asa_grade` | The ASA physical status grade, as recorded. |
| emergency | 1 | flag | `role_anaesthetic_detail.is_emergency` | Whether the operation was done as an emergency, as recorded. |
| cardiac | 1 | flag | `role_operation.is_cardiac` | Whether any procedure under the anaesthetic was cardiac. |
| operation_kind | 1 | row_per_anaesthetic | `role_operation.specialty` | The specialty of the main procedure, which the audit chooses by its own rule. |
| height_at_anaesthetic | 1 | row_per_anaesthetic | `role_anaesthetic_detail.height_cm` | The patient's height or length at the anaesthetic, as recorded. |
| location | 1 | row_per_anaesthetic | `role_anaesthetic_detail.location` | Where the anaesthetic was given, as recorded. |
| consultant_present | 1 | flag | `role_staff.role`, `role_staff.grade`, `role_staff.role = anaesthetist`, `role_staff.grade = consultant` | Whether an anaesthetist of the grade consultant was present at any time during the anaesthetic. |
| principal_diagnosis | 1 | row_per_anaesthetic | `role_diagnosis.diagnosis`, `role_diagnosis.is_principal`, `role_anaesthetic_detail.stay_key`, `map_diagnosis_concept` | The principal diagnosis of the stay in which the anaesthetic took place, as a standard concept. |
| preoperative_result | 1 | row_per_anaesthetic | `role_lab.kind`, `role_lab.value`, `role_lab.taken_time`, `role_anaesthetic.start_time` | A laboratory result, such as a haemoglobin, in a window before the anaesthetic's start that the audit chooses. |
| assessment_finding | 1 | row_per_anaesthetic | `role_finding.kind`, `role_finding.value`, `role_finding.method` | A fact from the assessment, such as a recent upper respiratory tract infection or snoring, which the audit may restrict to the methods that it trusts. |
| minutes_beyond_threshold | 1 | row_per_anaesthetic | `role_reading.kind`, `role_reading.reading_time`, `role_reading.value`, `role_reading.accepted`, `role_anaesthetic.start_time`, `role_anaesthetic.stop_time` | The minutes of an anaesthetic during which accepted readings of a kind lay beyond a threshold, counting each reading until the next. |
| extreme_in_window | 1 | row_per_anaesthetic | `role_reading.kind`, `role_reading.reading_time`, `role_reading.value`, `role_reading.accepted`, `role_anaesthetic.start_time`, `role_anaesthetic.stop_time` | The lowest or highest accepted reading of a kind within a window relative to the anaesthetic. |
| time_to_first_beyond_threshold | 1 | row_per_anaesthetic | `role_reading.kind`, `role_reading.reading_time`, `role_reading.value`, `role_reading.accepted`, `role_anaesthetic.start_time` | The minutes from the anaesthetic's start to the first accepted reading of a kind beyond a threshold, and empty where none lies beyond it. |
| count_of_events | 1 | row_per_anaesthetic | `role_event.kind`, `role_event.event_time` | How many events of the chosen kinds the anaesthetic's record holds within a window. |
| total_volume_per_kg | 1 | row_per_anaesthetic | `role_fluid.kind`, `role_fluid.volume_ml`, `role_fluid.volume_form`, `role_fluid.given_time`, `role_anaesthetic_detail.weight_kg` | The total volume of the chosen kinds of fluid given during the anaesthetic, for each kilogram of the patient's weight, with running totals turned into amounts. |
| exposure_intervals | 1 | row_per_anaesthetic | `role_drug.drug`, `role_drug.order_key`, `role_drug.action`, `role_drug.amount`, `role_drug.unit`, `role_drug.given_time`, `role_drug.source_kind`, `role_drug.amends_key`, `map_drug_concept`, `map_unit_concept` | The intervals during which an infusion of a drug ran, from its start, rate changes, pauses, restarts and stop, with an interval whose stop the source does not record kept as one of unknown end. |
| hypotension_burden | 1 | row_per_anaesthetic | `role_reading.kind`, `role_reading.reading_time`, `role_reading.value`, `role_reading.accepted`, `role_anaesthetic.start_time`, `role_anaesthetic.stop_time`, `role_anaesthetic.patient_key`, `role_patient.is_test`, `role_patient.birth_date` | The minutes of an anaesthetic, within a window, during which accepted readings of the chosen pressure kinds lay beyond a threshold set by the patient's age, counting each reading until the next and for no longer than a set time. |
| hypoxaemia_burden | 1 | row_per_anaesthetic | `role_reading.kind`, `role_reading.reading_time`, `role_reading.value`, `role_reading.accepted`, `role_anaesthetic.start_time`, `role_anaesthetic.stop_time`, `role_anaesthetic.patient_key`, `role_patient.is_test`, `role_patient.birth_date` | The minutes of an anaesthetic, within a window, during which accepted readings of saturation lay below a threshold set by the patient's age, counting each reading until the next and for no longer than a set time. |
| hypothermia | 1 | row_per_anaesthetic | `role_reading.kind`, `role_reading.reading_time`, `role_reading.value`, `role_reading.accepted`, `role_anaesthetic.start_time`, `role_anaesthetic.stop_time`, `role_anaesthetic.patient_key`, `role_patient.is_test`, `role_reading.kind = temperature` | The minutes of an anaesthetic, within a window, during which accepted temperatures lay below a threshold, the lowest temperature in the window, and the first temperature after the anaesthetic's stop, within a recovery window anchored to the stop, as the temperature on arrival in recovery. |
| monitoring_completeness | 1 | row_per_anaesthetic | `role_reading.kind`, `role_reading.reading_time`, `role_reading.value`, `role_reading.accepted`, `role_anaesthetic.start_time`, `role_anaesthetic.stop_time`, `role_anaesthetic.patient_key`, `role_patient.is_test` | The share of a window that lies within an expected interval after an accepted reading of the chosen kinds, and the longest gap in the window without one. |
| volatile_consumption | 1 | row_per_anaesthetic | `role_reading.kind`, `role_reading.reading_time`, `role_reading.value`, `role_reading.accepted`, `role_anaesthetic.start_time`, `role_anaesthetic.stop_time`, `role_anaesthetic.patient_key`, `role_patient.is_test`, `role_reading.kind = fresh_gas_flow` | The millilitres of liquid volatile agent consumed in an anaesthetic, for each agent, from the fresh gas flow and the expired concentration integrated over time, with the carbon dioxide equivalent from a factor for each agent. |

A capability is added by the public route. A clinician states the measure in words. A person or a coding agent writes it as SQL over the roles against the invented world, with planted cases and expected answers written independently and held out; the question package is imported and checked against the role policy; and the catalogue entry is written with its name, version, meaning, parameters, grain, unit, window, output class and requirements. A change to what a capability measures is a new version of it, and the earlier version stays in the catalogue for the audits that name it.

## How each role relates to OMOP

The roles are written for an anaesthetic audit, and the OMOP Common Data Model, version 5.4, is written for observational research across every kind of care. The rule is that every role is either a straightforward projection of OMOP, which a query over an OMOP database could give with no new meaning, or says here exactly what it adds that OMOP does not express. The table below gives, for each view, the OMOP table onto which it projects and what it adds.

| View | OMOP table | A projection, or what the role adds |
|---|---|---|
| role_patient | PERSON, with the date of death from DEATH | A projection, except `is_test`. An OMOP conversion leaves test patients out, whereas the role keeps them and marks them, so that the counts can see them. |
| role_anaesthetic | VISIT_DETAIL, PROCEDURE_OCCURRENCE and EPISODE | The role adds an anaesthetic as one episode with its own identity, its own start and its own stop. OMOP spreads the same thing across a VISIT_DETAIL for the time in theatre, a PROCEDURE_OCCURRENCE for the anaesthetic procedure and, where a conversion builds one, an EPISODE, and none of the three is by itself the anaesthetic that the record holds. |
| role_reading | MEASUREMENT | The role adds the link from each reading to its anaesthetic by the record on which it was charted, rather than by its time. OMOP links a measurement to a visit or a visit detail, and a query that wants the readings of one anaesthetic must otherwise choose them by time, which rule 3 forbids. The role also keeps whether a value was accepted, which OMOP does not hold; `value_text` is OMOP's `value_source_value`. |
| role_patient_detail | PERSON, MEASUREMENT and OBSERVATION | A projection: the sex is PERSON's gender, and the gestation and the birth weight are a measurement or an observation of the newborn. |
| role_stay | VISIT_OCCURRENCE | A projection. Whether the admission was unplanned is the visit's admission source or type. |
| role_anaesthetic_detail | OBSERVATION, MEASUREMENT and PROCEDURE_OCCURRENCE | The role adds one row for each anaesthetic that gathers its covariates. OMOP holds the ASA grade, the urgency, the weight and the planned destination as separate observations and measurements, tied to the anaesthetic only through EPISODE_EVENT, where a conversion fills it, or by time. |
| role_operation | PROCEDURE_OCCURRENCE | The role adds the link from each procedure to the anaesthetic under which it was done. OMOP links a procedure to a visit, and two operations of one admission are told apart only by time. |
| role_transfer | VISIT_DETAIL | A projection of the movements from which VISIT_DETAIL is built. OMOP's VISIT_DETAIL gives a period in a unit, which a conversion works out from the transfers. |
| role_event | PROCEDURE_OCCURRENCE, OBSERVATION and CONDITION_OCCURRENCE | The role adds the timed milestones of an anaesthetic, such as induction, incision and extubation, as one vocabulary linked to the anaesthetic. OMOP has no standard home for these milestones, and a complication becomes a condition or an observation with no link to the anaesthetic in which it happened. |
| role_technique | PROCEDURE_OCCURRENCE | The role adds each technique charted for an anaesthetic, with its side, its guidance and its catheter, linked to the anaesthetic. OMOP holds an anaesthetic technique as a procedure with no link to the anaesthetic beyond time. |
| role_drug | DRUG_EXPOSURE | The role adds the link to the anaesthetic by the record, and each start, change of rate and stop of an infusion as its own row. OMOP records an exposure with a start and an end, and a change of rate within it is usually lost. |
| role_device | DEVICE_EXPOSURE | A projection, with the link to the anaesthetic by the record rather than by time. |
| role_staff | PROVIDER | The role adds each person's periods of presence at an anaesthetic, so that a handover shows. OMOP's PROVIDER describes the person, and a clinical row carries at most one provider, with no times. |
| role_fluid | DRUG_EXPOSURE, MEASUREMENT and OBSERVATION | A projection: a fluid or blood product given is a drug exposure, and a loss is a measurement or an observation, each with the link to the anaesthetic by the record. |
| role_lab | MEASUREMENT | A projection. |
| role_diagnosis | CONDITION_OCCURRENCE | A projection. Whether the diagnosis is principal is the condition's status. |
| role_note | NOTE | A projection. NOTE's visit detail gives the link to the anaesthetic where the hospital's system makes one. |
| role_finding | OBSERVATION, with NOTE_NLP for a fact taken from text | The role adds a fact taken from a note or a structured field together with the method by which it was found, and the confidence that the hospital's process gives it. OMOP's NOTE_NLP holds terms found in a note, and an observation holds a fact, but neither says whether a person, a rule or a model found it. |

## The question that the first three views answer

The neonatal audit asks: among neonates, children under 28 days old at the start, who had an anaesthetic, how many minutes of each anaesthetic were spent with a mean arterial pressure below 40, and how many of those children died within 90 days? To answer it, the audit needs each anaesthetic with its patient, its start and its stop; the patient's date of birth and date of death; and every accepted mean arterial pressure reading during the anaesthetic, with its time, its value, and whether it came from a cuff or an arterial line.
