package server

const layoutHTML = `<!doctype html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{.Brand}}: {{.Title}}</title>
<link rel="stylesheet" href="/static/ask.css">
<script src="/static/htmx.min.js" defer></script>
<script src="/static/alpine.min.js" defer></script>
</head>
<body>
<header>
  <a href="/" class="brand">{{.Brand}}</a>
  <span class="who">{{.User}}</span>
</header>
<main>
{{template "body" .}}
</main>
</body>
</html>`

const indexHTML = `{{define "body"}}
<h1>Ask a question of the anaesthesia record</h1>
<p>Write the clinical question you want answered, in your own words. The assistant will ask what it needs to pin the question down, and will then write it in the terms of the record so that the hospital can run it.</p>
<p class="notice">Do not type any patient's details. A question is about groups of patients, and what you write here leaves the hospital. Leave out names, record numbers, dates of birth and dates of admission; a message that looks as though it holds one is held back.</p>
<form method="post" action="/questions">
  <label for="first">Your question</label>
  <textarea id="first" name="message" rows="4" required placeholder="For example: what is the median red cell volume given to neonates having surgery?"></textarea>
  <button type="submit">Start</button>
</form>
{{if .Questions}}
<h2>Your questions</h2>
<table>
  <thead><tr><th>Question</th><th>State</th><th>Last changed</th>{{if .Admin}}<th>Asked by</th>{{end}}</tr></thead>
  <tbody>
  {{range .Questions}}
  <tr><td><a href="/questions/{{.ID}}">{{.Title}}</a></td><td>{{.Status}}</td><td>{{.Updated.Format "2 Jan 2006 15:04"}}</td>{{if $.Admin}}<td>{{.Owner}}</td>{{end}}</tr>
  {{end}}
  </tbody>
</table>
{{end}}
{{end}}`

const questionHTML = `{{define "body"}}
<h1>{{.Question.Title}}</h1>
<p class="state">The question is {{.Question.Status}}.</p>
<section id="conversation">
{{template "conversation" .}}
</section>
<form hx-post="/questions/{{.Question.ID}}/messages" hx-target="#conversation" hx-swap="innerHTML" hx-indicator="#waiting" method="post" action="/questions/{{.Question.ID}}/messages">
  <label for="message">Your reply</label>
  <textarea id="message" name="message" rows="3" required></textarea>
  <button type="submit">Send</button>
  <span id="waiting" class="htmx-indicator">The assistant is writing.</span>
</form>
{{end}}`

const conversationHTML = `{{define "conversation"}}
{{range .Messages}}
<div class="turn {{.Role}}">
  <div class="speaker">{{if eq .Role "user"}}You{{else if eq .Role "examiner"}}The examiner{{else}}Assistant{{end}}</div>
  <div class="text">{{.Content}}</div>
</div>
{{end}}
{{if .Error}}<p class="problem">{{.Error}}</p>{{end}}
{{if .Package}}
<div class="package" x-data="{open: false}">
  <h2>The question, written for the hospital</h2>
  <p>This is version {{.Package.Version}} of the question package. Its examination stands at <strong>{{.Level}}</strong>. {{.LevelSays}}</p>
  {{if .Published}}<p>The hospital opens it in Schemalyser at this address, which is private to whoever holds it and names this version, so that what the hospital opens is what you see here:</p>
  <p><code>{{.Address}}</code></p>
  <p class="state">The latest version is always at <code>{{.Latest}}</code>.</p>{{else}}
  <p class="state">The package is published at its address once the examiner's report puts it at logic examined or above.</p>{{end}}
  {{if .Examples}}<h3>What the question means, in examples</h3>
  <p>The examiner worked these cases out from the decisions. Confirm each that is what you mean, or say what is wrong with one and the assistant will change the decision it rests on.</p>
  <ul class="examples">{{range .Examples}}
    <li><span class="example">{{.Says}}</span>
      {{if eq .Verdict "confirmed"}}<span class="state">Confirmed by {{.By}} on {{.Date}}.</span>
      {{else if eq .Verdict "rejected"}}<span class="state">Rejected by {{.By}} on {{.Date}}: {{.Note}}</span>
      {{else}}<form hx-post="/questions/{{$.Question.ID}}/examples" hx-target="#conversation" hx-swap="innerHTML" method="post" action="/questions/{{$.Question.ID}}/examples" class="verdict" x-data="{rejecting: false}">
        <input type="hidden" name="example" value="{{.ID}}">
        <button type="submit" name="verdict" value="confirmed">That is what I mean</button>
        <button type="button" @click="rejecting = !rejecting">Something is wrong</button>
        <span x-show="rejecting" x-cloak><input type="text" name="note" placeholder="What is wrong with it"> <button type="submit" name="verdict" value="rejected">Send</button></span>
      </form>{{end}}
    </li>{{end}}
  </ul>{{end}}
  {{if .Pkg.Approval}}<p>The question serves the {{.Pkg.Approval.Use}} with the reference {{.Pkg.Approval.Reference}}.</p>{{else}}<p class="state">The package carries no approval reference yet; the assistant asks for the audit or research the question serves.</p>{{end}}
  {{if .Signoff}}<p class="state">The custodian, {{.Signoff.By}}, signed the logic for {{.Signoff.Use}} on {{.Signoff.Created.Format "2 January 2006"}}{{if .Signoff.Accepts}}, accepting {{range $i, $p := .Signoff.Accepts}}{{if $i}}, {{end}}{{$p}}{{end}}{{end}}.</p>
  {{else if and .Admin .Published .Pkg.Approval}}<form hx-post="/questions/{{.Question.ID}}/signoff" hx-target="#conversation" hx-swap="innerHTML" method="post" action="/questions/{{.Question.ID}}/signoff" class="signoff">
    <input type="hidden" name="use" value="{{.Pkg.Approval.Use}}">
    {{if .Ceiling}}<label><input type="checkbox" name="accept" value="yes"> I accept {{range $i, $p := .Ceiling}}{{if $i}}, {{end}}{{$p}}{{end}}, rehearsed on the stand-in, for this {{.Pkg.Approval.Use}}.</label>{{end}}
    <button type="submit">Sign the logic as custodian</button>
  </form>{{end}}
  {{if .Admin}}<form hx-post="/questions/{{.Question.ID}}/report" hx-target="#conversation" hx-swap="innerHTML" hx-encoding="multipart/form-data" method="post" action="/questions/{{.Question.ID}}/report" enctype="multipart/form-data" class="report">
    <label for="report">The examiner's report of this version, signed ({{.Examinations}} of {{.ExaminationsAllowed}} examinations used)</label>
    <input id="report" type="file" name="report" accept="application/json" required>
    <button type="submit">Upload the report</button>
  </form>{{end}}
  <p>Its parts of the record: {{range $i, $p := .Pkg.Parts}}{{if $i}}, {{end}}{{$p.Name}}{{end}}.{{if .Pkg.Drafts}} Not yet in the contract: {{range $i, $p := .Pkg.Drafts}}{{if $i}}, {{end}}{{$p}}{{end}}.{{end}}</p>
  {{if .Pkg.Decisions}}<h3>Decisions</h3><ul>{{range .Pkg.Decisions}}<li>{{.Decision}}</li>{{end}}</ul>{{end}}
  {{if .Pkg.Proposals}}<h3>What the record does not yet describe</h3><ul>{{range .Pkg.Proposals}}<li>A {{.Kind}} for {{.Part}}: {{.Because}}</li>{{end}}</ul><p>The owner of the record's description decides whether to add these. Until then the question waits.</p>{{end}}
  <p><a href="/questions/{{.Question.ID}}/package.json" download>Download the package</a> <button type="button" @click="open = !open" x-text="open ? 'Hide the SQL' : 'Show the SQL'"></button></p>
  <pre x-show="open" x-cloak>{{.Pkg.QuestionSQL}}</pre>
</div>
{{end}}
{{end}}`

const styleCSS = `:root { --ink: #1d2733; --paper: #fbfaf7; --line: #d9d4c7; --soft: #f0ede4; --accent: #2d5f7a; }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.5 Georgia, "Times New Roman", serif; color: var(--ink); background: var(--paper); }
header { display: flex; justify-content: space-between; align-items: baseline; padding: 12px 24px; border-bottom: 1px solid var(--line); }
.brand { font-weight: bold; color: var(--ink); text-decoration: none; }
.who { color: #666; font-size: 14px; }
main { max-width: 820px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 12px; } h2 { font-size: 19px; margin: 24px 0 8px; } h3 { font-size: 16px; margin: 16px 0 4px; }
label { display: block; margin: 12px 0 4px; } textarea { width: 100%; font: inherit; padding: 8px; border: 1px solid var(--line); border-radius: 4px; }
button { font: inherit; padding: 8px 16px; background: var(--accent); color: white; border: 0; border-radius: 4px; margin-top: 8px; cursor: pointer; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; } th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }
.turn { padding: 10px 12px; margin: 8px 0; border-radius: 6px; background: var(--soft); } .turn.user { background: white; border: 1px solid var(--line); }
.speaker { font-size: 13px; color: #666; margin-bottom: 2px; } .text { white-space: pre-wrap; }
.state { color: #666; } .problem { color: #8a2f2f; } .notice { color: #5a4a1a; background: #fbf3dc; padding: 8px 12px; border-radius: 4px; }
.package { margin-top: 24px; padding: 16px; border: 1px solid var(--line); border-radius: 6px; background: white; }
code { word-break: break-all; } pre { white-space: pre-wrap; background: var(--soft); padding: 12px; border-radius: 4px; font-size: 14px; }
.htmx-indicator { display: none; margin-left: 12px; color: #666; } .htmx-request .htmx-indicator, .htmx-request.htmx-indicator { display: inline; }
[x-cloak] { display: none; }`
