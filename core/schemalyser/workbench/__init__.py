"""The local workbench: server-rendered pages over a project folder that call the core's commands and show their reports.

    python -m schemalyser.workbench --project FOLDER [--port 8765]

The workbench owns no logic of its own. It lists what the project folder holds, starts the core's commands (the audit's
package, the plan review and the test on made-up rows) as subprocesses whose output goes into the project folder, and
renders the JSON that they write. The feasibility report, the programme, the readiness of a saved hospital schema and
its scoreboard are read by calling the core's functions directly, because they are quick and write nothing.

It binds to 127.0.0.1, refuses a request whose Host or Origin is not this computer, makes no request of its own and
never connects to a hospital database. Everything it writes, temporary files included, stays in the project folder.
docs/workbench.md describes it.
"""
