# The public workspace and the import

`core/schemalyser/workspace.py` writes a workspace in which a coding agent can write a clinical question over the parts of the record and test it on invented data, and imports a finished question into a hospital's project in one direction only. `core/schemalyser/rolepolicy.py` holds the rules that a question must pass to be imported.

## The three environments

A question moves between three places, and the table shows what each of them knows.

| | The public workspace | The hospital's project | The hospital's database |
| --- | --- | --- | --- |
| Where it lives | Anywhere, such as an agent's container | The hospital's storage | The hospital's servers |
| The role contract and the invented world | Yes | Yes | No |
| The hospital schema, with its tables, codes and counts | No, only the invented one | Yes | Not as such |
| The audit's package and its reports | Only on invented data | Yes | Run once by the database analyst |
| The patients' records | No | No | Yes |

## Exporting the workspace

    python -m schemalyser.workspace export --profile public --out FOLDER [--include PATH ...]

The export builds the workspace from an explicit allowlist of public sources, and never by copying a project and taking things out of it. The allowlist holds the role contract, the compiled neonatal audit, the invented world, the OMOP field list, the module that writes the sample vocabulary, and the public modules of the core that the feasibility report, the audit's package and the testbed import. The export then writes `README.md`, `QUERIES.md`, a saved hospital schema made from the invented dictionary as the tests make one, and the example question in `queries/neonatal_low_mean_pressure/`.

`manifest.json` lists every file with its source, its checksum and the profile. The export stops, naming the path, if a file would come from outside the allowlist, from `reference/`, `etl/`, `notes/` or `fixtures/held-out/`, through a link, or as a kind of file that its folder may not hold, or if any text names a folder on someone's own computer. A path given with `--include` is refused unless the allowlist already lists it. An agent in the workspace can run the feasibility report, build the audit's package and run the testbed, all on invented data.

## What is held out

The workspace holds the development fixtures, which are the invented world and the commands that run on it, and never the held-out fixtures that judge an agent's work. The export therefore never takes anything from `fixtures/held-out/`, where the planted scenarios and their expected rows are kept, and never takes `fixtures/planted-values.txt`, the planted values that a run's output is searched for. A held-out file inside an allowlisted folder is passed over, and one that an allowlist entry or `--include` names stops the export. The planted neonates are a development fixture as rows of the role views, so the export writes `core/schemalyser/rolemodel/planted_neonates.json` with the rows and without the answers that the neonatal audit must give for them. A package built in the workspace therefore checks no planted case, and its answers are judged on the hospital's side.

`tools/workspace/compose.yaml` runs the workspace in a container with the exported folder as its only volume, and `tools/workspace/README.md` explains why a folder on its own is not a boundary.

## Importing a question

    python -m schemalyser.workspace import WORKSPACE/queries/NAME --hospital PROJECT [--schema NAME]
    python -m schemalyser.workspace check WORKSPACE/queries/NAME

The import treats the folder as untrusted. It accepts `question.sql`, `title.txt` and `note.md` and nothing else, and it refuses a link, a subfolder, a file over 64 KB, a file with the executable bit set and any declaration of dependencies. The role-level policy then reads `question.sql` in the SQL Server dialect. The question must be one SELECT, which may use common table expressions, over the three views of the contract alone. Every column must exist in the contract, every function must be one that the policy on final scripts allows, every kind named in a literal must be a word of the contract's vocabularies, and no other database, procedure, dynamic SQL or EXEC may appear. Each failed rule is reported with its fragment, and `check` applies the same rules without any hospital.

The status is decided from the package and the public contract alone, before any hospital schema is read for it:

- `malformed`, with the names of the rules it broke, where a rule of the folder or of the role policy fails;
- `requires_private_review` where the question passes every rule but lacks the form of the two-part script: a step that chooses its anaesthetics from the patients and the anaesthetics alone, every further part reached from that step by a key it carries, and a final SELECT that returns counts or other aggregates rather than rows of patients;
- `accepted` otherwise.

`check` gives the same status before the package is sent, because it applies the same function to the same files. If the question is not malformed, Schemalyser then copies it into the project's `questions/` under its title, makes the feasibility report against the named saved schema (or the only one), builds the audit's package in `audits/NAME/package/`, and writes a private validation report beside it. Whether the package could be built, and any error the build met, goes into that private report and never into the status. Into the workspace's folder Schemalyser writes only `import-result.json`, which holds the query's name, the status and one line that states it. No verdict, class, count, error or path returns, because each would tell the agent what the hospital supports. The tests import one package into two projects, one whose hospital schema links the readings to the anaesthetics and one whose schema does not, and assert that both return the same status.

## The invariant

An agent working in the workspace can write or change a clinical question over the public contracts but cannot see any hospital's mapping from those contracts to its database.

## A public interface is not shareable content

A question written over the public role views is not shareable for that reason alone. A question about an identifiable patient, or about an event that the hospital has not disclosed, is clinical content and belongs on the hospital's side, even though it uses only the roles.

## What the workspace never holds

The workspace never holds a hospital's schema, data dictionary, codes, plans, counts or results. The vendor's public specification and the reference conversion are never part of it, because both stay outside version control.
