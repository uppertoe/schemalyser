# The public workspace in a container

This folder runs a coding agent inside an exported public workspace, with that workspace as the only folder of this computer that the agent can see. `docs/workspace.md` describes the workspace itself and the import that takes a finished question back into a hospital's project.

## Exporting the workspace

Run the export from `core/`, and choose a folder outside the repository:

```sh
python -m schemalyser.workspace export --profile public --out /path/to/workspace
```

## Running the container

The service uses the workbench's image, which holds Python with the pinned packages, so build that image first as `tools/workbench/README.md` describes. Then name the exported folder in `WORKSPACE`:

```sh
WORKSPACE=/path/to/workspace docker compose -f tools/workspace/compose.yaml run --rm workspace
```

This opens a shell in `/workspace`, with `core` on the Python path, and the commands in the workspace's `QUERIES.md` then run as written there. On Linux, set `WORKSPACE_UID` and `WORKSPACE_GID` to your own user and group, so that the files written in the container belong to you.

The exported folder is the only volume. The service publishes no port, runs as a user who is not root, drops every capability and keeps the container's own file system read-only, with a small area of memory for temporary files that disappears when the container stops.

## Running a coding agent inside it

The workbench's image holds no coding agent. To run one, build a small image of your own from `schemalyser-workbench:dev` that adds the agent's command line, then name it in `WORKSPACE_IMAGE`. Pass the agent's own key for its model provider when you start the container, rather than writing it into the image or the workspace:

```sh
WORKSPACE=/path/to/workspace WORKSPACE_IMAGE=my-agent:latest \
  docker compose -f tools/workspace/compose.yaml run --rm -e AGENT_API_KEY workspace
```

Inside the shell, start the agent in `/workspace` and give it the task, such as writing a question in `queries/NAME/`. Once it has finished, run `python -m schemalyser.workspace check queries/NAME` yourself before the folder goes to the hospital's side.

The container keeps the default network, because the agent needs to reach its model provider. Run it on a computer that holds no hospital data and that cannot reach a hospital's network, because the container can still reach anything that the computer's network can.

## Why a folder alone is not a boundary

An agent started in a folder on this computer runs as you. It can read every file that you can read, including the repository's private folders, any hospital's project kept on the same disk, and your credentials. It can follow a link out of the folder, and any command that it runs has the same reach. Telling the agent to stay in a folder is an instruction, not a control.

The container is what makes the workspace a boundary. Inside it, the exported folder is the only part of this computer that exists, so the agent cannot read what is not mounted, whatever it is told or decides to do.
