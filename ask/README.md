# Ask: the clinician-facing service

Ask is the public service of Schemalyser. An anaesthetist writes a clinical question in their own words, the assistant asks what it needs to pin the question down, and then writes the question in the terms of the public role contract. The service publishes the question at a secret address, and the page Describe the record in the hospital opens it from there and turns it into the hospital's own SQL through the hospital schema.

The service sees the role contract and the invented world and nothing of any hospital. It runs as one small Go binary with one SQLite file, behind the server's login wall, in the shape of the other apps of the server repository.

## What it does

- Keeps each clinician's questions, with the whole conversation, in one SQLite file.
- Reaches the model through one of three backends: `api`, the Anthropic API with a key; `cli`, the Claude Code command line on a subscription, for development on a machine that has it; and `fake`, a scripted reply for the tests and for trying the pages.
- Reads the role contract and the question package's schema, copied in from `core/schemalyser/rolemodel/` by `go generate ./...` or by the Dockerfile when the service is built, so that the service and the page always speak the same version and read the one schema. Neither copy is committed, and a test fails when a copy has fallen behind the repository's or is missing.
- Checks every question block the model writes: the package it makes from the block is validated against the schema, its SQL is one SELECT over the roles with no invented part and no statement a question never makes, read with its comments and string literals stripped, and every proposal and capability names what the contract has. A block that breaks a rule is shown to the clinician as a problem and goes back to the model on the next turn.
- Publishes each version of a question's package at `/q/TOKEN/N`, where the token is a 48-character secret and N the version, as JSON that never changes once written, with the hash of its bytes in a header; the address the asker copies carries that hash as `#sha256=`, so that the page in a hospital can check what it fetched. `/q/TOKEN` serves the latest version as a convenience. The addresses need no login; everything else is behind the login wall.
- Publishes a version of a package only once the examiner's signed report puts it at logic examined or above. The owner runs `python -m schemalyser.examination run PACKAGE --key KEY --out REPORT` on their own machine, where the private cases and the signing key live, and uploads the report on the question's page; the service verifies the signature with the examiner's public key, embedded at build from `fixtures/examiner.pub` or given as `ASK_EXAMINER_PUBLIC_KEY`, shows the level, and, where the level is below what publishes, gives the model the examiner's bounded feedback on the next turn: every rule and fragment of the static stage, whether the question ran, which metamorphic check failed, and of the planted cases and the oracle only how many disagreed. A question may have three examinations, so that the count of disagreements cannot search for the answers. The report carries the examiner's worked examples, cases at each boundary of the question in clinical words; the asker confirms each on the page, which is recorded with their name and the date as the signature of meaning, or says what is wrong with one, which returns to the assistant as the asker's own change of decision, and a version is published only once every example is confirmed.
- Records, as a proposal in the contract's own shape, anything the question needs that the contract lacks. The model never invents a role in the SQL; the owner runs the proposal through the contract checker and the try command and decides.

## The package

A question package is `schemalyser-question/1`, whose shape is `core/schemalyser/rolemodel/question.schema.json` and whose one reader in Python is `python -m schemalyser.question check`: the title; the contract version; the statement, the clinician's words for the cohort, the outcome and the result; the decisions, each typed with what it is about, the choice, a value and unit where one exists, the sentence, who made it and when; the capabilities of the catalogue it composes, each with its version and parameters, which the core inlines into the SQL as a common table expression named after the capability, so that the SQL reads the capability as a table and the question's own SQL is only the outer computation; the SQL over the roles; the parts the SQL reads, each at its version in the contract and with the contract's admission for a question, which marks a draft part as refused; any proposal for what the contract lacks; a note; and which model wrote it, when and for whom. The package carries no status and no hash of its own: the hash is of the bytes served, and what a package may claim is decided by the evidence recorded about it. The page's own state word for a question is `drafted`, or `waits for the contract` while the package holds a proposal.

## Who may use it, and what may be typed

Until the owner opens the service to colleagues, only the admin addresses and the addresses in `ASK_ALLOWED_EMAILS` may sign in; everyone else is refused with a sentence saying so. Every message is screened before it reaches the model: a run of six or more digits, a date written as a date of birth, or an upper-case name with an underscore holds the message back, records it as not sent, and tells the clinician why. The page says beside the text box that no patient's details are to be typed, because what is typed leaves the hospital. The development backend runs the command line with no tools, no external servers, no skills and none of the developer's settings, in an empty folder of its own.

## Running it

    cd ask
    ASK_MODEL_BACKEND=fake ASK_DEV_USER=you@example.test ASK_SQLITE=/tmp/ask.db go run .

opens the pages at `http://localhost:8080` with no model and no login wall. With `ASK_MODEL_BACKEND=cli` the assistant is the Claude Code command line on this machine's subscription; with `ASK_MODEL_BACKEND=api` and `ANTHROPIC_API_KEY` set, it is the API. `go run . -prompt` prints the system prompt the model reads.

`go generate ./...` copies the contract and the schema in from the repository, and `go test ./...` runs the tests, which use the fake backend. `fixtures/packages/ask-fake-backend.question.json` is the package the fake backend's settled reply produces, committed so that the core's reader is tested against what the service writes; run the tests with `ASK_WRITE_FIXTURE=1` to write it again after a change.

## Deploying it

`Dockerfile` builds the image from the repository's root, so that it embeds the repository's contract:

    docker build -f ask/Dockerfile -t ghcr.io/uppertoe/schemalyser-ask:latest .

`deploy/` holds the app's compose file, its Caddy snippet and the settings it takes, for the `apps/ask/` folder of a server repository made from the server instance template. The snippet puts the whole site behind `import protected` except `/q/*`.

## Updating the roles

The roles live in the repository, not on the server. To change them, change `contract.json` and `roles.md`, run `python -m schemalyser.contractcheck`, try the change against a saved schema with `python -m schemalyser.describe try`, commit, and build the image again. The service then speaks the new version, and a hospital schema saved under the old one proposes the added fields from its own dictionary when it is next opened.
