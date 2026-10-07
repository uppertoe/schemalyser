# The roles of an anaesthetic record

This page describes what an anaesthetic record holds, written without reference to any hospital, vendor or database. A role is one thing that the record must be able to tell an audit. A hospital's system may hold a role in one column, across several tables, inside a text field, as a row of a table of events, or not at all. Each hospital has a map that says which of its own tables and columns play each role, written as one SQL view for each role, and an audit written against the role views then runs unchanged at any hospital.

`contract.json`, beside this page, holds the same model as data: every view, its columns, their types, the words that the proposer looks for in a data dictionary, and the vocabularies. When this page and `contract.json` differ, `contract.json` is the one that the code reads, and this page should be corrected to match it.

## The rules of the record

The role views assume the following of every hospital's record, and a map must make each of them true.

1. One row of `role_anaesthetic` is one anaesthetic, and its key identifies that anaesthetic and no other.
2. Every time is in local time, as the hospital's clocks showed it. No view converts a time to another zone.
3. A reading, an event, a drug given, a device, a fluid or a person present belongs to an anaesthetic by its link to that anaesthetic, and never because its time falls within the anaesthetic. A ward observation taken in the same hour is not part of the anaesthetic's record.
4. A laboratory result and a diagnosis belong to the patient and the stay, and not to an anaesthetic. An audit that wants a result or a diagnosis near an anaesthetic chooses its own window around it, such as the 24 hours before the start.
5. A note belongs to an anaesthetic by its link to that anaesthetic where the hospital's system makes one, and otherwise to the stay or to the patient. A note is never linked to an anaesthetic because of when it was written.
6. A view keeps the rows that an audit leaves out, such as test patients, anaesthetics with no stop and values that were not accepted, so that the standard counts can see them.
7. A view never joins in a way that repeats a row of the thing it describes, so that the view's key identifies its row.
8. An empty value means that the record does not say. A value that the record holds but that cannot be read as the role's type is also empty.
9. A key compares equal with itself within one hospital only, and no key is carried from one hospital to another.
10. A value is in the unit that its role or its kind fixes, and the view converts a value that the hospital holds in another unit. Only a drug's amount and a laboratory result of the kind `other` are given in the unit that the view carries beside them.

## The types

| Type | Meaning |
|---|---|
| key | Any type that compares equal with itself within one hospital, such as a whole number or text. |
| date | A date without a time. |
| datetime | A date and a time of day, in local time. |
| number | A number that may hold a fraction. |
| whole | A whole number. |
| flag | The whole number 1 or 0, and never empty. |
| flag_or_empty | The whole number 1 or 0, or empty where the record does not say. |
| kind | Text that is one of the kinds of the vocabulary that the column names. |
| text | Text as the hospital records it. |

## What an anaesthetic record holds, and where each part sits in the roles

An anaesthetist knows the record in the order in which it is written, from the assessment before the day to the outcomes weeks later. The table below follows that order and names the role view, and where it matters the column or the kind, that holds each part. Each role view is described in full in the next section.

| Part of the record | Where it sits in the roles |
|---|---|
| Pre-anaesthetic assessment | The assessment sits across three views. Its grade, weight and height are `asa_grade`, `weight_kg` and `height_cm` of `role_anaesthetic_detail`; its facts, such as the Mallampati grade, the fasting hours, a recent upper respiratory tract infection or a family history of a problem with anaesthesia, are rows of `role_finding`; and its text is a row of `role_note` of the kind `preanaesthetic_assessment`. The date of birth is in `role_patient`, and the sex, the gestation and the birth weight are in `role_patient_detail`. A coded comorbidity is a row of `role_diagnosis`, and a preoperative result, such as a haemoglobin, is a row of `role_lab`. |
| Booking and urgency | Whether the operation was an emergency is `role_anaesthetic_detail.is_emergency`, and whether intensive care was booked beforehand is `planned_icu` of the same view. Whether the admission itself was unplanned is `role_stay.unplanned`. The indication on the theatre booking is a coded diagnosis in `role_diagnosis`. |
| The people present | Each anaesthetist, surgeon and anaesthetic assistant is a row of `role_staff`, with the role, the grade and the times at which that person was present, so that a handover shows as two rows. |
| Induction and airway | The kind of anaesthetic is `role_anaesthetic_detail.technique`. The start of anaesthesia, the induction and the moment the airway was secured are rows of `role_event`. The airway device, with its size, is a row of `role_device`, and the induction drugs are rows of `role_drug`. A difficult airway is the event `difficult_airway`. |
| Ventilation | The ventilator's settings and measurements are rows of `role_reading`, of the kinds `fio2`, `peep`, `tidal_volume` and `respiratory_rate`. The end-tidal carbon dioxide is the kind `etco2`, and the end-tidal concentration of a volatile agent is the kind `end_tidal_agent`. |
| Monitoring | The heart rate, the oxygen saturation, the temperature and the blood pressures are rows of `role_reading`. A pressure from an arterial line and a pressure from a cuff are separate kinds, each with its mean, systolic and diastolic, and the central venous pressure has its own kind. A blood glucose from a bedside meter, charted in the anaesthetic's record, is the reading `blood_glucose`. |
| Drugs and infusions | Each dose given, and each start, change of rate and stop of an infusion, is a row of `role_drug`, with its amount, unit, route and time. The drug keeps the hospital's own name or code, so an audit names the drugs that it wants in the hospital's own terms. |
| Fluids and blood | Each volume of crystalloid, colloid or blood product given, and each volume of blood or urine lost, is a row of `role_fluid`, with its kind and its volume in millilitres. Blood salvaged and returned to the patient is the kind `cell_salvage`. |
| Lines and devices | Each airway, line and catheter placed for the anaesthetic is a row of `role_device`, with its kind, size and site and the times at which it was placed and removed. |
| Events and complications | The milestones and the complications of the anaesthetic are rows of `role_event`. The complications that an anaesthetic audit usually asks about, such as laryngospasm, bronchospasm, desaturation, cardiac arrest, anaphylaxis, a drug error and awareness, each have their own kind. Where a hospital records complications in a separate incident or quality record, the map reads them from there. |
| Emergence and recovery | The extubation, the end of anaesthesia, the arrival in recovery and the discharge from recovery are rows of `role_event`. A pain score charted in the anaesthetic's record is the reading `pain_score`. Readings taken in recovery belong to the anaesthetic only where the hospital charts recovery on the anaesthetic's own record, by the third rule above. |
| The operation | Each procedure done under the anaesthetic is a row of `role_operation`, with its code, its name, its specialty and whether it was cardiac. The incision, the end of surgery and any time on bypass are rows of `role_event`. Where the anaesthetic was given, such as in theatre or in imaging, is `role_anaesthetic_detail.location`. |
| Diagnoses | Each coded diagnosis, from the coded discharge diagnoses, the problem list or the theatre booking's indication, is a row of `role_diagnosis`, with its code, its classification and whether it is the principal diagnosis of the stay. |
| The stay and unit moves | The hospital stay is a row of `role_stay`, and each period in one unit, such as intensive care, is a row of `role_unit_stay`. |
| Notes | Every piece of free text, from the assessment and the anaesthetic summary to the operation note and the discharge summary, is a row of `role_note`, and each fact that the hospital takes from a note is a row of `role_finding`. The section on notes below says how these two views are handled. |
| Outcomes | An audit works out each outcome from the roles, such as death from `role_patient.death_date`, an unplanned admission to intensive care from `role_unit_stay`, and a postoperative result from `role_lab`. The section on outcomes and covariates below lists them. |

The model leaves out three parts of the record on purpose.

- The model holds no images, such as an ultrasound picture of a block, a scanned chart or a photograph. An image cannot be counted, and most hospitals hold images outside the clinical database.
- The model records that a regional technique was used, any regional catheter, and the local anaesthetic given as a dose in `role_drug`, but not the exact shape of a regional block, such as the nerve or space, the approach, the needle and the guidance. Hospitals record blocks in very different ways, often as a form of their own or as free text, and no audit yet needs that detail.
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

Every map supplies the first three views, which the neonatal audit reads, and their columns do not change. The further views are a starter set for the outcomes and covariates below, which the owner will trim; a map supplies a further view only where the hospital records it. In each table below, "Needed" says whether the audit written so far reads the column.

### role_patient: one row for each patient

This view holds one row for each patient, test and training patients included, so that an audit can leave them out. The hospital usually holds it in the patient table of its system, which has one row for each person. The date of death is often held in a second patient table, or loaded from a registry of deaths.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| patient_key | key | yes | The hospital's own identifier of the patient, the same value that `role_anaesthetic.patient_key` holds. |
| birth_date | date | yes | The date of birth, to the day, so that an age in days counts calendar dates. |
| death_date | date | yes | The date of death where the hospital knows of it, and empty otherwise. |
| is_test | flag | yes | 1 for a test or training patient who is not a real person, and 0 for every other patient. An empty flag in the source counts as 0. |

### role_anaesthetic: one row for each anaesthetic

This view holds one row for each episode of anaesthesia or sedation given by an anaesthetist, including those whose start or stop is missing or out of order. The hospital usually holds it in an anaesthetic record or anaesthesia episode of its own, which is linked to a theatre case or to an encounter of its own. It links to `role_patient` by `patient_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | yes | The hospital's own identifier of the anaesthetic, the same value that `role_reading.anaesthetic_key` holds. |
| patient_key | key | yes | The patient who had the anaesthetic, as `role_patient.patient_key`. |
| start_time | datetime | yes | When the anaesthetic started, in local time. |
| stop_time | datetime | yes | When the anaesthetic stopped, in local time, and empty where no stop was recorded. |

### role_reading: one row for each value charted in an anaesthetic's record

This view holds one row for each value charted in an anaesthetic's own record, by a monitor or by hand, including values that were not accepted and values that are not numbers. The hospital usually holds readings in a table of measurements or observations, often the largest in its system, reached from the anaesthetic through the record or sheet on which they were charted. It links to `role_anaesthetic` by `anaesthetic_key`, and its rows are identified by the anaesthetic, the kind and the time.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | yes | The anaesthetic whose record holds the reading, as `role_anaesthetic.anaesthetic_key`. |
| kind | kind | yes | What was measured, as one of the kinds of readings below. |
| reading_time | datetime | yes | When the value was taken, in local time, and not when it was filed. |
| value | number | yes | The value as a number in the kind's own unit, and empty where the charted value is not a number. |
| accepted | flag | yes | 1 where the value counts as valid, and 0 where it was marked as an artefact, rejected or superseded. An empty flag in the source counts as 1. |

The kinds of readings are these. A map gives the hospital's own codes for each kind that it can find, and every other value charted in the record is of the kind `other`.

| Kind | Meaning |
|---|---|
| map_arterial | A mean arterial pressure from an arterial line, in mmHg. The audit needs it. |
| map_cuff | A mean arterial pressure from a non-invasive cuff, in mmHg. The audit needs it. |
| heart_rate | A heart rate, in beats a minute. |
| spo2 | An oxygen saturation by pulse oximetry, in per cent. |
| etco2 | An end-tidal carbon dioxide, in mmHg. |
| temperature | A body temperature, in degrees Celsius. |
| systolic_arterial | A systolic pressure from an arterial line, in mmHg. |
| diastolic_arterial | A diastolic pressure from an arterial line, in mmHg. |
| systolic_cuff | A systolic pressure from a non-invasive cuff, in mmHg. |
| diastolic_cuff | A diastolic pressure from a non-invasive cuff, in mmHg. |
| central_venous_pressure | A central venous pressure, in mmHg. |
| fio2 | A fraction of inspired oxygen, in per cent. |
| peep | A positive end-expiratory pressure, in cmH2O. |
| tidal_volume | An expired tidal volume, in millilitres. |
| respiratory_rate | A respiratory rate, in breaths a minute. |
| end_tidal_agent | An end-tidal concentration of a volatile agent, in per cent. The kind does not say which agent was given. |
| pain_score | A pain score, as a number on the scale that the hospital uses, usually from 0 to 10. |
| blood_glucose | A blood glucose charted in the anaesthetic's record, usually from a bedside meter, in mmol/L. A glucose that the laboratory or a blood gas analyser reports is a row of `role_lab` instead. |
| other | Any other value charted in the record, which the counts use to see whether the record is reached at all. |

### role_patient_detail: the patient's details at birth

This view holds one row for each patient whose record holds any of these details. The hospital may hold them in a second patient table, in the child's birth history, in the mother's delivery record where that is linked to the baby, or as a measurement charted on the newborn. It links to `role_patient` by `patient_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| patient_key | key | no | The patient, as `role_patient.patient_key`. |
| sex | kind | no | The sex recorded at birth, as one of the kinds of the sex vocabulary. |
| gestation_weeks | number | no | The gestational age at birth in weeks, with any days as a fraction of a week. |
| birth_weight_grams | number | no | The weight at birth in grams. |

### role_stay: one row for each hospital stay

This view holds one row for each hospital stay, from admission to discharge, day stays included. The hospital usually holds it in its table of hospital encounters or admissions. It links to `role_patient` by `patient_key`, and `role_anaesthetic_detail`, `role_unit_stay`, `role_lab` and `role_diagnosis` link to it by `stay_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| stay_key | key | no | The hospital's own identifier of the stay. |
| patient_key | key | no | The patient whose stay it is, as `role_patient.patient_key`. |
| admit_time | datetime | no | When the patient was admitted, in local time. |
| discharge_time | datetime | no | When the patient was discharged, or died in hospital, in local time, and empty while the patient is still in hospital. |
| unplanned | flag_or_empty | no | 1 for an emergency or other unplanned admission, 0 for a planned one, and empty where the record does not say. The hospital usually records this as an admission type or category, whose values a person translates. |

### role_anaesthetic_detail: the anaesthetic's covariates

This view holds one row for each anaesthetic whose record holds any of these details. The hospital holds them partly in the anaesthetic record and partly in the theatre case or surgical log, with the pre-anaesthetic assessment for the grade, the weight and the height. It links to `role_anaesthetic` by `anaesthetic_key` and to `role_stay` by `stay_key`.

The columns `height_cm` and `location` were added to this view on 7 October 2026. No audit read the view before then, so no audit had to change, but a map written before that date needs the two columns added, even if only as empty values.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic, as `role_anaesthetic.anaesthetic_key`. |
| stay_key | key | no | The hospital stay in which the anaesthetic took place, as `role_stay.stay_key`, and empty where it belongs to no stay. |
| technique | kind | no | The kind of anaesthetic, as one of the kinds of the technique vocabulary. A record may hold several, and the map chooses the main one. |
| asa_grade | whole | no | The ASA physical status grade from 1 to 6, without any letter for an emergency. |
| is_emergency | flag_or_empty | no | 1 where the operation was done as an emergency, 0 where it was planned, and empty where the record does not say. The hospital may record it on the theatre case or booking, or as an E after the ASA grade. |
| weight_kg | number | no | The patient's weight at the anaesthetic, in kilograms. The hospital may hold it on the pre-anaesthetic assessment, as a measurement charted on the day, or on the theatre case. |
| planned_icu | flag_or_empty | no | 1 where intensive care after the operation was planned before it began, 0 where it was not, and empty where the record does not say. The hospital usually records it on the booking as the planned destination or as a request for a bed. |
| unplanned_return | flag_or_empty | no | 1 where the operation was recorded as an unplanned return to theatre, 0 where it was not, and empty where the record does not say. |
| height_cm | number | no | The patient's height or length at the anaesthetic, in centimetres. The hospital may hold it on the pre-anaesthetic assessment or as a measurement charted on the day, as it holds the weight. |
| location | kind | no | Where the anaesthetic was given, as one of the kinds of the location vocabulary. The hospital usually records the room or department of the theatre case, and its list of rooms says what kind each one is. |

### role_operation: one row for each procedure done under an anaesthetic

This view holds one row for each procedure done under an anaesthetic, so that an anaesthetic for several procedures has several rows. The hospital usually holds the procedures of a theatre case or surgical log, with a lookup table of the procedures that it offers. It links to `role_anaesthetic` by `anaesthetic_key`; at many hospitals that link runs from the anaesthetic record to its theatre case, so the map must take care that a case with two anaesthetics does not repeat its procedures.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic under which the procedure was done, as `role_anaesthetic.anaesthetic_key`. |
| procedure_code | text | no | The hospital's own code of the procedure, or a national code where the hospital uses one. |
| procedure_name | text | no | The name of the procedure as the hospital's list gives it. |
| specialty | kind | no | The surgical specialty of the procedure, as one of the kinds of the specialty vocabulary, which gives the operation's kind. |
| is_cardiac | flag_or_empty | no | 1 where the procedure was a cardiac operation or a cardiac catheter procedure, 0 where it was not, and empty where nothing says. The hospital may record it through the specialty, the procedure's code, or a diagnosis of the stay. |

### role_unit_stay: one row for each period in one unit

This view holds one row for each period that a patient spent in one unit during a hospital stay. The hospital usually holds it in the movements of the encounter, with one row for each transfer into or out of a unit and a list of units that says what kind each one is. It links to `role_stay` by `stay_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| stay_key | key | no | The hospital stay, as `role_stay.stay_key`. |
| unit_kind | kind | no | The kind of unit, as one of the kinds of the unit vocabulary. |
| entered_time | datetime | no | When the patient entered the unit, in local time. |
| left_time | datetime | no | When the patient left the unit, in local time, and empty while the patient is still there. |

### role_event: one row for each milestone of an anaesthetic

This view holds one row for each timed milestone of an anaesthetic, and for each complication that the record marks with a time. The hospital usually holds them as events of the anaesthetic record, or as the timing events of the theatre case, each with a code for its kind. A hospital may instead record a complication in a quality or incident record of the anaesthetic, which the map then reads as well. It links to `role_anaesthetic` by `anaesthetic_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic, as `role_anaesthetic.anaesthetic_key`. |
| kind | kind | no | The kind of event, as one of the kinds of the event vocabulary. |
| event_time | datetime | no | When the event happened, in local time. |

### role_drug: one row for each dose or infusion action

This view holds one row for each dose given during an anaesthetic, and for each start, change of rate and stop of an infusion. The hospital usually holds them as administrations in its medication record, linked to their orders, and sometimes as drug values charted on the anaesthetic record. It links to `role_anaesthetic` by `anaesthetic_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic during which the drug was given, as `role_anaesthetic.anaesthetic_key`. |
| drug | text | no | The drug, as the hospital's own name or code for it. |
| action | kind | no | What was done, as one of the kinds of the drug action vocabulary. |
| amount | number | no | The dose, or the rate of an infusion, as a number in the unit beside it. |
| unit | text | no | The unit of the amount, as the hospital records it. |
| route | text | no | The route by which the drug was given, as the hospital records it. |
| given_time | datetime | no | When the dose was given or the infusion changed, in local time. |

### role_device: one row for each device placed

This view holds one row for each airway device, line or catheter placed for an anaesthetic. The hospital usually holds the lines, drains and airways of the patient's record, each with its placement and removal; where the device belongs to the patient rather than to the anaesthetic, the map must find the link. It links to `role_anaesthetic` by `anaesthetic_key`.

The columns `size` and `site` were added to this view on 7 October 2026, a few days after the view itself. No audit read the view before then, so no audit had to change, but a map written before that date needs the two columns added, even if only as empty values.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic for which the device was placed, as `role_anaesthetic.anaesthetic_key`. |
| kind | kind | no | The kind of device, as one of the kinds of the device vocabulary. |
| placed_time | datetime | no | When the device was placed, in local time. |
| removed_time | datetime | no | When the device was removed, in local time, and empty while it stays in place. |
| size | text | no | The size of the device as the hospital records it, such as the internal diameter of a tube or the gauge of a cannula. The hospital may hold it on the device row or as a property charted when the device was placed. |
| site | text | no | Where on the body the device was placed, as the hospital records it, such as the left radial artery or the right internal jugular vein. |

### role_staff: one row for each person attending an anaesthetic

This view holds one row for each person who attended an anaesthetic, for each period in which that person was present, so that a handover shows as two rows. The hospital usually holds the staff of the anaesthetic record or of the theatre case, each with a role and a start and end of responsibility, and a list of staff that gives each person's grade. That list usually holds the grade that the person holds today, so the map should say whether the grade on the day can be found. It links to `role_anaesthetic` by `anaesthetic_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic that the person attended, as `role_anaesthetic.anaesthetic_key`. |
| person_key | key | no | The hospital's own identifier of the staff member. The view never gives the person's name. |
| role | kind | no | What the person did at the anaesthetic, as one of the kinds of the staff role vocabulary. |
| grade | kind | no | The person's grade at the time of the anaesthetic, as one of the kinds of the grade vocabulary. |
| present_from | datetime | no | When the person took over or joined the care of the patient, in local time. |
| present_to | datetime | no | When the person handed over or left the care of the patient, in local time, and empty where the record does not say. |

### role_fluid: one row for each fluid or blood product given or lost

This view holds one row for each volume of fluid or blood product given during an anaesthetic, and for each volume of blood or urine lost. The hospital usually charts intake and output on the anaesthetic record as measurements, with one row for each kind of fluid, and records blood products in its transfusion record as well. Some systems chart a running total rather than each amount, and the map must then give each amount once. It links to `role_anaesthetic` by `anaesthetic_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic during which the fluid was given or lost, as `role_anaesthetic.anaesthetic_key`. |
| kind | kind | no | What was given or lost, as one of the kinds of the fluid vocabulary. |
| volume_ml | number | no | The volume given or lost, in millilitres, as one amount and not as a running total. |
| given_time | datetime | no | When the volume was given or measured, in local time. |

### role_lab: one row for each laboratory or point-of-care result

This view holds one row for each laboratory or point-of-care result for the patient, including blood gases. The hospital usually holds the results of its laboratory orders with one row for each component of each result, and often files point-of-care and blood gas results in the same place. The view links to `role_patient` by `patient_key` and to `role_stay` by `stay_key`, and not to an anaesthetic, because a result belongs to the patient. An audit chooses its own window around the anaesthetic, such as the last haemoglobin in the 30 days before the start or the highest lactate in the 24 hours after the stop.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| patient_key | key | no | The patient whose result it is, as `role_patient.patient_key`. |
| stay_key | key | no | The hospital stay during which the specimen was taken, as `role_stay.stay_key`, and empty where the result belongs to no stay. |
| kind | kind | no | What was measured, as one of the kinds of the laboratory vocabulary. |
| value | number | no | The result as a number in the kind's own unit, which the view converts to where the hospital holds another, and empty where the result is not a number. |
| unit | text | no | The unit in which the hospital recorded the result, as it records it, so that a person can check the conversion. |
| taken_time | datetime | no | When the specimen was taken, in local time, and not when the result was reported. |

The units of the laboratory kinds are these: haemoglobin in g/L; lactate, glucose, potassium, sodium and base excess in mmol/L; pCO2 and pO2 in mmHg; creatinine in micromol/L; and pH without a unit.

### role_diagnosis: one row for each coded diagnosis

This view holds one row for each coded diagnosis of the patient. The hospital usually holds coded diagnoses in three places: the diagnoses coded for the stay or its hospital account at discharge, the patient's problem list, and the indication or diagnosis on the theatre booking. Each points at a list of diagnoses that gives the code and the name. A map may supply all three, and an audit chooses among them by `is_principal`, `stay_key` and `recorded_time`. The view links to `role_patient` by `patient_key` and to `role_stay` by `stay_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| patient_key | key | no | The patient whose diagnosis it is, as `role_patient.patient_key`. |
| stay_key | key | no | The hospital stay for which the diagnosis was coded, as `role_stay.stay_key`, and empty for a problem-list entry that belongs to no stay. |
| code | text | no | The diagnosis code in the classification that `code_system` names. |
| code_system | text | no | The classification of the code, such as ICD-10-AM or SNOMED CT, or the hospital's own list. A hospital that uses one classification only may give it as a fixed value. |
| name | text | no | The name of the diagnosis as the hospital's list gives it. |
| is_principal | flag_or_empty | no | 1 where the diagnosis is the principal diagnosis of the stay, 0 where it is another, and empty where the record does not say. |
| recorded_time | datetime | no | When the diagnosis was noted or coded, in local time, and empty where the record does not say. |

### role_note: one row for each note in the record

This view holds one row for each piece of free text in the record, with its text joined back into one value. The hospital usually holds notes in a notes table keyed by encounter, with a note type, and often splits the text of one note across several rows with a line number, which the view must join back in order. Some text sits instead in the comment fields of a structured form, which the view also reads. The view links to `role_anaesthetic` by `anaesthetic_key` where the system makes that link, to `role_stay` by `stay_key`, and to `role_patient` by `patient_key`. The text is identifying, as the section on notes above explains.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| note_key | key | no | The hospital's own identifier of the note, the same value that `role_finding.note_key` holds. |
| anaesthetic_key | key | no | The anaesthetic to which the note belongs, as `role_anaesthetic.anaesthetic_key`, and empty where the system makes no link to one. |
| patient_key | key | no | The patient whose note it is, as `role_patient.patient_key`. |
| stay_key | key | no | The hospital stay to which the note belongs, as `role_stay.stay_key`, and empty where it belongs to no stay, such as a note of a clinic visit. |
| kind | kind | no | What kind of note it is, as one of the kinds of the note vocabulary. |
| written_time | datetime | no | When the note was written or signed, in local time, and not when it was last edited. |
| author_role | kind | no | The grade of the person who wrote the note, as one of the kinds of the grade vocabulary. |
| text | text | no | The whole text of the note, with its lines joined back in order. |

### role_finding: one row for each fact found in the record

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
| found_time | datetime | no | When the fact was recorded in a structured field, or taken from the note, in local time. |

## The vocabularies

A map translates the hospital's own codes into these kinds. Every vocabulary except the drug actions ends with `other`, which holds any value that the map has not translated.

| Vocabulary | Kinds |
|---|---|
| sex | female, male, indeterminate, other |
| technique | general, regional, sedation, local, other |
| specialty | cardiac, general, neurosurgery, orthopaedic, ear_nose_throat, dental, ophthalmic, plastic, urology, imaging, other |
| unit | intensive_care, high_dependency, theatre, recovery, emergency, ward, other |
| location | theatre, imaging, catheter_laboratory, intensive_care, emergency_department, ward, other |
| event | anaesthesia_start, induction, airway_secured, incision, bypass_on, bypass_off, surgery_end, extubation, anaesthesia_stop, recovery_in, recovery_out, laryngospasm, bronchospasm, desaturation, difficult_airway, unplanned_reintubation, cardiac_arrest, anaphylaxis, drug_error, awareness, other |
| device | endotracheal_tube, supraglottic_airway, arterial_line, central_line, peripheral_line, regional_catheter, other |
| drug_action | dose, infusion_start, rate_change, infusion_stop, not_given |
| note | preanaesthetic_assessment, anaesthetic_summary, airway_note, procedure_note, operation_note, recovery_note, consent, discharge_summary, progress_note, other |
| finding | airway_difficulty, mallampati, fasting_hours, previous_anaesthetic_problem, family_anaesthetic_history, syndrome, cardiac_history, respiratory_history, reflux, snoring_or_apnoea, recent_urti, allergy, medication, other |
| finding_method | structured_field, rule, person, model, other |
| staff_role | anaesthetist, surgeon, anaesthetic_assistant, other |
| grade | consultant, fellow, registrar, resident, nurse, other |
| fluid | crystalloid, colloid, red_cells, platelets, plasma, cryoprecipitate, cell_salvage, blood_loss, urine_output, other |
| lab | haemoglobin, lactate, glucose, potassium, sodium, ph, pco2, po2, base_excess, creatinine, other |

The event vocabulary holds the milestones in the order in which they usually happen, then the complications. A complication is kept as an event only where the record marks it; an audit that also wants a desaturation found from the readings works it out from `role_reading` itself. An anaesthetic assistant is the anaesthetic nurse or technician who assisted the anaesthetist, and the other people in the room, such as the scrub nurse or a perfusionist, are of the role `other`.

## The outcomes and covariates

An audit works out each outcome from the roles, so that no map has to. The starter set is below.

| Outcome or covariate | Worked out from | How |
|---|---|---|
| Death within a number of days | `role_patient.death_date`, `role_anaesthetic.start_time` | The patient died on a date from the day of the anaesthetic's start to that many days after it. |
| Unplanned admission to intensive care | `role_unit_stay`, `role_anaesthetic_detail.planned_icu`, `role_anaesthetic.stop_time` | The patient entered an intensive care unit within 24 hours after the anaesthetic's stop, intensive care had not been planned, and the patient was not already there at the start. |
| Return to theatre within 30 days | `role_anaesthetic`, `role_operation`, `role_anaesthetic_detail.unplanned_return` | The same patient had another anaesthetic with an operation within 30 days after this one's stop. Where the hospital records a return as unplanned, the audit can count only those. |
| Length of stay | `role_stay`, `role_anaesthetic_detail.stay_key` | The time from admission to discharge of the stay in which the anaesthetic took place, and the time from the anaesthetic's stop to that discharge. |
| Readmission within 30 days | `role_stay` | The patient began another unplanned stay within 30 days after the discharge of the stay in which the anaesthetic took place. |
| Gestational age at birth | `role_patient_detail.gestation_weeks` | As recorded. |
| Weight at the anaesthetic | `role_anaesthetic_detail.weight_kg` | As recorded. |
| ASA grade | `role_anaesthetic_detail.asa_grade` | As recorded. |
| Emergency status | `role_anaesthetic_detail.is_emergency` | As recorded. |
| Cardiac operation | `role_operation.is_cardiac` | Any procedure under the anaesthetic that was cardiac. |
| The operation's kind | `role_operation.specialty` | The specialty of the main procedure, which the audit chooses by its own rule. |
| A complication of the anaesthetic | `role_event.kind` | The anaesthetic's record holds any of the complications of the event vocabulary, such as laryngospasm, desaturation or cardiac arrest. |
| Transfusion | `role_fluid.kind`, `role_fluid.volume_ml` | The patient was given red cells, platelets, plasma or cryoprecipitate during the anaesthetic, and the audit can work out the volume for each kilogram from `role_anaesthetic_detail.weight_kg`. |
| Time in recovery | `role_event` | The time from the event `recovery_in` to the event `recovery_out`. |
| A result before or after the anaesthetic | `role_lab`, `role_anaesthetic.start_time`, `role_anaesthetic.stop_time` | A result, such as a haemoglobin before the start or a lactate after the stop, in a window that the audit chooses. |
| Height at the anaesthetic | `role_anaesthetic_detail.height_cm` | As recorded. |
| Where the anaesthetic was given | `role_anaesthetic_detail.location` | As recorded. |
| A consultant present | `role_staff.role`, `role_staff.grade` | An anaesthetist of the grade consultant was present at any time during the anaesthetic. |
| The principal diagnosis | `role_diagnosis.code`, `role_diagnosis.is_principal` | The principal diagnosis of the stay in which the anaesthetic took place. |
| A fact from the assessment | `role_finding.kind`, `role_finding.value`, `role_finding.method` | A fact such as a recent upper respiratory tract infection or snoring, which the audit may restrict to the methods that it trusts. |

## The question that the first three views answer

The neonatal audit asks: among neonates, children under 28 days old at the start, who had an anaesthetic, how many minutes of each anaesthetic were spent with a mean arterial pressure below 40, and how many of those children died within 90 days? To answer it, the audit needs each anaesthetic with its patient, its start and its stop; the patient's date of birth and date of death; and every accepted mean arterial pressure reading during the anaesthetic, with its time, its value, and whether it came from a cuff or an arterial line.
