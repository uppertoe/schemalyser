# Ask: the clinician-facing service

Ask is the public service of Schemalyser. An anaesthetist writes a clinical question in their own words, the assistant asks what it needs to pin the question down, and then writes the question in the terms of the public role contract. The service publishes the question at a secret address, and the page Describe the record in the hospital opens it from there and turns it into the hospital's own SQL through the hospital schema.

The service sees the role contract and the invented world and nothing of any hospital. It runs as one small Go binary with one SQLite file, behind the server's login wall, in the shape of the other apps of the server repository.

## What it does

- Keeps each clinician's questions, with the whole conversation, in one SQLite file.
- Reaches the model through one of three backends: `api`, the Anthropic API with a key; `cli`, the Claude Code command line on a subscription, for development on a machine that has it; and `fake`, a scripted reply for the tests and for trying the pages.
- Reads the role contract embedded at build time from `core/schemalyser/rolemodel/contract.json`, so that the service and the page always speak the same version. A test refuses an embedded copy that has fallen behind the repository's.
- Checks every question block the model writes: one SELECT over the roles, no invented part, no statement a question never makes. A block that breaks a rule is shown to the clinician as a problem and goes back to the model on the next turn.
- Publishes the latest package of a question at `/q/TOKEN`, a 48-character secret, as JSON with its hash in a header, with no login, so that the page in a hospital can fetch it. Everything else is behind the login wall.
- Records, as a proposal in the contract's own shape, anything the question needs that the contract lacks. The model never invents a role in the SQL; the owner runs the proposal through the contract checker and the try command and decides.

## The package

A question package is `schemalyser-question/1`: the title, the contract version, a specification in the clinician's terms with a cohort, an outcome and a result, the decisions made in the conversation, the SQL over the roles, a note, the parts used with the drafts marked, the proposals, the status, which model wrote it and when, and a hash of what it says. Its status is `drafted` until the examination of the roadmap exists, or `waits for the contract` while it holds a proposal.

## Who may use it, and what may be typed

Until the owner opens the service to colleagues, only the admin addresses and the addresses in `ASK_ALLOWED_EMAILS` may sign in; everyone else is refused with a sentence saying so. Every message is screened before it reaches the model: a run of six or more digits, a date written as a date of birth, or an upper-case name with an underscore holds the message back, records it as not sent, and tells the clinician why. The page says beside the text box that no patient's details are to be typed, because what is typed leaves the hospital. The development backend runs the command line with no tools, no external servers, no skills and none of the developer's settings, in an empty folder of its own.

## Running it

    cd ask
    ASK_MODEL_BACKEND=fake ASK_DEV_USER=you@example.test ASK_SQLITE=/tmp/ask.db go run .

opens the pages at `http://localhost:8080` with no model and no login wall. With `ASK_MODEL_BACKEND=cli` the assistant is the Claude Code command line on this machine's subscription; with `ASK_MODEL_BACKEND=api` and `ANTHROPIC_API_KEY` set, it is the API. `go run . -prompt` prints the system prompt the model reads.

`go test ./...` runs the tests, which use the fake backend.

## Deploying it

`Dockerfile` builds the image from the repository's root, so that it embeds the repository's contract:

    docker build -f ask/Dockerfile -t ghcr.io/uppertoe/schemalyser-ask:latest .

`deploy/` holds the app's compose file, its Caddy snippet and the settings it takes, for the `apps/ask/` folder of a server repository made from the server instance template. The snippet puts the whole site behind `import protected` except `/q/*`.

## Updating the roles

The roles live in the repository, not on the server. To change them, change `contract.json` and `roles.md`, run `python -m schemalyser.contractcheck`, try the change against a saved schema with `python -m schemalyser.describe try`, commit, and build the image again. The service then speaks the new version, and a hospital schema saved under the old one proposes the added fields from its own dictionary when it is next opened.
