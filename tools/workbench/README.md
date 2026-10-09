# The workbench in a container

This folder builds one image with the core, the workbench, DuckDB, the invented hospital, htmx and Alpine.js, and a built copy of the page Describe the record when `site/dist` exists. `docs/workbench.md` describes the workbench itself.

## Building the image

Build from the root of the repository. If you want the page Describe the record inside the image, run `npm run build` in `site/` first.

```sh
docker build -f tools/workbench/Dockerfile -t schemalyser-workbench:dev .
```

The build fetches htmx and Alpine.js from the npm registry once and checks each against its checksum, then installs the pinned Python packages from `requirements.txt` with their checksums and removes pip. `Dockerfile.dockerignore` sends only the core, the invented world, the SQL Server harness and `site/dist`, so nothing under `reference/` or `etl/` reaches the image. The image is about 360 MB, and a first build takes under a minute.

## Running it with a project folder

Name the project folder in `PROJECT`. The workbench makes `schemas/`, `questions/`, `audits/` and `runs/` in it if they are missing.

```sh
PROJECT=/path/to/project docker compose -f tools/workbench/compose.yaml up workbench
```

Then open http://127.0.0.1:8765/ in a browser on the same computer. Without compose, the same is:

```sh
docker run --rm -p 127.0.0.1:8765:8765 --read-only --cap-drop ALL -v /path/to/project:/project schemalyser-workbench:dev
```

The container's file system is read-only, so the project folder is the only place the workbench can write. On Linux, set `WORKBENCH_UID` and `WORKBENCH_GID` to your own user and group, so that the files it writes belong to you.

## The sqlserver profile

A run with the engine SQL Server needs the SQL Server harness, which reaches the container `schemalyser-mssql` with `docker exec` and `docker cp`. The profile `sqlserver` adds that container, as `tools/sqlserver/README.md` defines it, and a second workbench, built with the Docker command line and given the Docker socket, on port 8766:

```sh
PROJECT=/path/to/project MSSQL_SA_PASSWORD="$(cat reference/mssql-password.txt)" \
  docker compose -f tools/workbench/compose.yaml --profile sqlserver up mssql workbench-sqlserver
```

If `schemalyser-mssql` already runs from the harness's README, leave `mssql` out of that command. The second workbench reads the password from `reference/mssql-password.txt`, or from the file that `MSSQL_PASSWORD_FILE` names. The Docker socket gives that container control of Docker on this computer, so use the profile on a developer's machine only, never on one that holds hospital data. The full profile also needs PostgreSQL, the Data Quality Dashboard's image and the Athena download, which the image does not hold, so it runs from the command line rather than in the container.

## What the workbench never does

- It makes no request to any other site. Its port is published on 127.0.0.1 only, it refuses a request whose Host is not this computer and a form from another site, and its pages may connect only to the workbench itself.
- It never connects to a hospital database. The database analyst runs an audit's package under the hospital's own controls, and the clinician brings back only the estimated plan for review.
- It has no logic of its own. Every verdict, class, state and outcome it shows comes from a report that the core's commands wrote.
- It writes nothing outside the project folder, and keeps its temporary files in the folder's `.tmp/`.
