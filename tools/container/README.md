# The boundary image

This image runs the part of Schemalyser that reads confidential input: the analyser, the check script, the register of open questions and the checklist for each target query. It reads a state folder and a folder of requests, and writes one output folder for a person to read. It never builds the sandbox, never runs a conversion and never opens a network connection. `docs/architecture.md`, under "Running at the boundary", describes the command and the state folder.

## What the image holds

The image is built on the official Python 3.13 slim image, pinned by the digest of its multi-platform index. It adds two packages, `sqlglot==30.21.0` and `duckdb==1.5.1`, each installed from a wheel whose checksum is given in `requirements.txt`, and then removes pip. It copies `core/schemalyser` and nothing else from the project. It runs as the user `boundary` (uid 10001), who does not own the code, and its entrypoint is the boundary command itself, in exec form, so no shell is involved when it runs.

The image needs duckdb only because `harness.py` and `convert.py`, which the register and the checklists use, import it at the top of their modules. The boundary never calls it.

The image holds no `curl`, `wget`, `git`, `ssh`, `nc` or `pip`. The base image's `apt-get` and shell remain, and the entrypoint uses neither.

## Building it

Build the image from the root of the project, so that it can copy the package:

    docker build -f tools/container/Dockerfile -t schemalyser-boundary:dev .

BuildKit reads `Dockerfile.dockerignore` beside the Dockerfile, which keeps everything out of the build context except the package and `requirements.txt`. If a package is upgraded, replace its version and its checksums in `requirements.txt` with those that PyPI publishes for the new wheels.

## Running it

Run the image with no network, a read-only file system, and both inputs mounted read-only:

    docker run --rm --network none --read-only --tmpfs /tmp \
        --cap-drop ALL --security-opt no-new-privileges \
        -v /path/to/state:/state:ro -v /path/to/requests:/requests:ro -v /path/to/out:/out \
        schemalyser-boundary:dev --state-commit STATE_SHA --requests-commit REQUESTS_SHA

The two commit options are optional. When they are given, `summary.md` and `provenance.json` name the commits that were read. The output folder must be empty. On a Linux host, the output folder must be writable by the user that runs the container. If the folder belongs to you, add `--user "$(id -u):$(id -g)"` to the command.

The command exits with 0 when it has written every output, with 1 when it could not read a target query (it still writes everything else, and `summary.md` names the query), and with 2 when the state folder or one of the other folders cannot be used, in which case it writes nothing. A target query that is not yet ready is not an error. Any other error ends the run with 3 and one fixed sentence, which carries no text from any input.

## What a run guarantees

- The container has no network interface apart from loopback, so nothing it reads can leave it except through the output folder.
- The inputs are mounted read-only, and the image's own files cannot be changed while it runs.
- Every name in the outputs comes from the catalogue, the site rules, the OMOP field list, the conversion's own files, the names of the target queries or Schemalyser's fixed wording. No output holds text from a request or a value from the check results. The tests in `core/tests/test_boundary.py` check this against the invented world, the planted values and a hostile request.
- Two runs over the same inputs give identical files, and `provenance.json` gives a checksum of every output and of the Schemalyser code that wrote it.

## What a run does not guarantee

- The image does not decide what may be committed. A person must read `summary.md` and the other outputs before committing any of them to the state repository.
- The check script and the checklists name tables and columns from the hospital's catalogue. They are confidential to the hospital and belong only in its private repositories.
- The image is only as trustworthy as its build. The hospital should build it from a reviewed commit, or check the digest of an image that someone else built, and pull it by that digest.

## The example workflow

`example-workflow.yml` is an example of a GitHub Actions workflow for the owner's state repository. It checks out the state repository at the triggering commit and the requests repository at a pinned commit, runs the image with no network and read-only inputs, and keeps the output folder as an artefact. It commits nothing, pushes nothing and prints no file's contents. The workflow has not been run. Before it is used, every placeholder in capitals must be filled in, including the commit SHA of each action, the registry and digest of the image, the commit of the requests repository and the runner's label.

## How the image was checked

On Apple Silicon with Docker Desktop, the image was built as `schemalyser-boundary:dev`, and the invented world in `fixtures/` was copied into the layout of a state folder. The container was run with the command above, with `--network none`, `--read-only` and read-only inputs. Its 22 output files, with the four target queries now in `fixtures/targets/`, were identical, byte for byte, to those of the command run directly from the same code. Inside the container, a connection to an outside address failed because the network was unreachable, and a write to the image's own files was refused.
