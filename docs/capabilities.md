# The baseline capabilities

This page is the department's list of the measures its quality work asks for, written in words before any is coded. It is the source for the catalogue in `contract.json`, and each entry becomes a capability there with its name, version, parameters, grain, unit, window, output class and requirements. The list was agreed on 9 October 2026 and is organised by the section of the anaesthesia chart each measure needs, which is also the order in which the sections are promoted (`docs/product.md`). The open questions at the end are the owner's to answer before the first five are coded.

## Principles that apply to every capability

**Shapes, not questions.** Most measures are one of six shapes with parameters: minutes beyond a threshold, the extreme in a window, the time to the first reading beyond a threshold, the count of events of a kind, the total of a kind per kilogram, and exposure intervals. A pressure below 40 and a saturation below 95 are the same capability with two settings. A capability is added only when no shape covers the measure.

**Two parameters have shapes of their own.** A threshold by age band is a table, not a number, because nearly every physiological measure needs one. A factor by agent is a table too, for the carbon dioxide equivalent of the volatile agents.

**The time a capability relies on is stated.** A charted time is a documented time, not a clinical one, and some rows are entered late. Each entry says which time it uses and how far it can be trusted, and the documented time column on the event parts is what lets a hospital measure the lag for a kind before reporting on it. Antibiotic timing is left out of the baseline for this reason: the charted time is known to be unreliable.

**Every rate carries its denominator.** Anaesthetics in the period, or anaesthetics with the relevant kind charted. The output states both, and the feasibility report's coverage claim is what makes the second honest.

**Where a fact is both charted and derivable, both are reported.** The maintenance technique is charted as a technique row and derivable from the volatile readings and the propofol events. The capability reports both and the count of disagreements, because the disagreement is itself a documentation quality measure.

**Nothing is invented for what the source does not say.** A missing stop is an interval of unknown end, a missing volume is not zero, and a gap in monitoring is not a stable patient.

## From readings alone, available with version 1

These need only the three parts of version 1 and the reading kinds vocabulary, which grows by the kinds named in each row. They are the first to run.

| Domain | Capabilities | Reading kinds |
|---|---|---|
| Physiology | Hypotension burden, hypoxaemia burden, hypocapnia and hypercapnia, bradycardia and tachycardia, each as minutes beyond a threshold by age band; the lowest pressure and saturation in a window; time to first desaturation after induction | Mean arterial and cuff pressure, saturation, end-tidal carbon dioxide, heart rate |
| Monitoring completeness | Coverage of a kind: the share of the window within the expected interval of a reading, and the longest gap | Any kind, with the expected interval as a parameter |
| Sustainability | Volatile agent consumed in millilitres of liquid per case, from fresh gas flow and expired concentration integrated over time; carbon dioxide equivalent per case from a factor table; nitrous oxide used and its minutes; desflurane used; share of maintenance at low flow with the flow threshold a parameter; total intravenous technique as a derived flag | Fresh gas flow; inspired and expired sevoflurane, desflurane and isoflurane; inspired nitrous oxide; inspired oxygen |
| Ventilation | Tidal volume per kilogram and minutes beyond a bound; peak and plateau pressure beyond a threshold; driving pressure; positive end-expiratory pressure exposure; inspired oxygen above a threshold; minutes ventilated against spontaneous, from the mode | Tidal volume, respiratory rate, peak pressure, plateau pressure, end-expiratory pressure, ventilation mode |
| Temperature | Lowest in case; minutes below 36; temperature on arrival in recovery as the first recovery reading | Temperature, with the site where charted |
| Recovery scores | Peak pain score and the share above a threshold, by scale; time to the first score below a threshold; nausea and sedation scores; emergence delirium score | Pain score by scale, nausea score, sedation score, paediatric emergence delirium score |
| Induction quality | Share of inductions at or above a threshold on the induction behaviour score, by age band, and split by premedication, induction route and parental presence where charted | Child induction behaviour score; a second induction score if the unit charts one |

Until the events part is promoted, the recovery window is anchored to the anaesthetic's stop and the entry says so. Once recovery-in and recovery-out events exist, the window is theirs.

## From the drug, technique, fluid and laboratory sections

| Domain | Capabilities | Sections |
|---|---|---|
| Premedication | Given, as a flag by drug class; dose per kilogram; time before induction, with the documented-time note | Drugs |
| Technique and blocks | Block performed by kind, and the rate where one was indicated; local anaesthetic dose per kilogram against a maximum by agent; regional use in the standard indications as a rate; block failure, conversion and complications within a period; induction route, inhalational against intravenous, with the derived check; maintenance technique, charted and derived | Techniques, drugs, events, operations |
| Blood products | Volume per kilogram by product (red cells, platelets, plasma, cryoprecipitate); haemoglobin before the first unit and after the last; massive transfusion as a flag with the threshold a parameter; cell salvage volume returned; tranexamic acid given; the ratio of plasma and platelets to red cells | Fluids, laboratory, drugs |
| Recovery analgesia and emesis | Time to first opioid in recovery; opioid exposure per kilogram across the case and the recovery window; rescue antiemetic given; prophylaxis given against the risk count | Drugs, events |
| Vasoactive drugs | Vasopressor and inotrope exposure intervals and the share of the case on each | Drugs |

## Events and outcomes

| Domain | Capabilities | Sections |
|---|---|---|
| Airway events | Laryngospasm, bronchospasm, difficult airway, failed intubation, unplanned extubation, aspiration, each as a count and a rate | Events |
| Cardiovascular and harm events | Cardiac arrest, arrhythmia requiring treatment, anaphylaxis, dental injury, awareness, drug error, positioning injury | Events |
| Recovery | Time from arrival to discharge, the median and the share beyond a limit; unplanned respiratory support or reintubation in recovery | Events |
| Intensive care | Unplanned admission within a period; planned admission as a separate flag; length of stay in the unit | Unit transfers, anaesthetic detail |
| After the anaesthetic | Return to theatre within a period; death within 24 hours, 30 days and one year as the default periods of one capability; length of stay; readmission within a period; a laboratory result in a window after the stop | Stay, operations, patient, laboratory |

## Process and documentation

Consultant presence by urgency; ASA grade, weight and height recorded; fasting times documented; preoperative assessment present. These need the anaesthetic detail and staff sections and are cheap once those are bound.

## The first five

Hypotension burden, hypoxaemia burden, hypothermia with the recovery arrival temperature, monitoring completeness, and volatile consumption with its carbon dioxide equivalent. All five need version 1 only, and the neonatal low mean pressure audit is the first of them with its parameters set.

## Open questions for the owner

- The default thresholds and the age bands for the physiological measures.
- The pain scales the recovery unit charts, and which is the default.
- What the unit charts under the second induction score, if it is a score.
- The factor table for the carbon dioxide equivalent, and whether the unit prefers a published source named in the entry.
- The massive transfusion threshold, in millilitres per kilogram.
