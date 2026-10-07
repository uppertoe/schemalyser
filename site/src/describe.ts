// The page Describe the record: screen 1, which makes the hospital folder. It shares the worker, the bridge, the style
// and the offline gate with the existing page. Everything that is loaded or pasted goes to the worker, which holds it;
// the page holds only what it shows.
import { describeStrings as d } from './describe-strings';
import * as corrections from './describe-corrections';
import { strings } from './strings';

declare const __VERSION__: string;

type State = 'loading' | 'load-failed' | 'ready' | 'locked';

interface Presence { state: 'present' | 'missing' | 'large'; missing: string[]; large: [string, number][]; rows: number | null }
interface Candidate { from: string; replacement: string; definition: string | null }
interface Item {
  about: string; attribute: string; meaning: string; type: string | null; from: string; table: string | null; column: string | null;
  bound: boolean; definition: string | null; says: string; confidence: string; candidates: Candidate[]; status: string; question: string;
  answer: string | null; date: string | null; replacement: string | null; presence: Presence | null;
  link: string | null; correction: corrections.CorrectionHeld | null;
}
interface Role { name: string; description: string; required: boolean; drafted: boolean; items: Item[] }
interface Vocabulary {
  key: string; view: string; column: string; vocabulary: string; kinds: string[]; meanings: Record<string, string>; bound: string;
  lookup: [string, string] | null; reason: string; rows: { code: string; charted: number | null; anaesthetics: number | null; name: string }[];
  chosen: Record<string, string>; year: number | null; date: string | null;
}
interface CountHeld { columns: string[] | null; rows: string[][] | null; looks_right: string | null; note: string | null; date: string | null; findings: string[] }
interface Model {
  dictionary: { tables: number; columns: number; described: number; keyed: number; skipped: number } | null;
  proposed: boolean; roles: Role[]; tally: { confirmed: number; corrected: number; not_sure: number; remaining: number; total: number };
  questions: { about: string; question: string }[]; catalogue: boolean; vocabularies: Vocabulary[]; counts: Record<string, CountHeld>;
  settings: { made: string | null; updated: string | null; database: string | null; year: number | null };
  restored: Record<string, unknown> | null;
  values: Record<string, { value: string; rows: number | null }[]>; anaesthetic_table: string | null; bases: Record<string, string>;
}
interface CountQuery { name: string; safe: boolean; sql: string; tables: [string, number | null][] }
interface CheckQuery { name: string; number: number; step: string | null; sql: string; file: string; pasted: string | null; database: string | null; columns: string[]; rows: string[][]; more: number }
type Reply = { ok: boolean; problem?: string; model?: Model; [key: string]: unknown };

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, content?: string, className?: string) => {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
};
const button = (label: string, onClick: () => void, className = '') => {
  const node = el('button', label, className);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
};

let state: State = 'loading';
let worker: Worker | null = null;
let workerHasFiles = false;
let model: Model | null = null;
let busy = false;
let nextId = 1;
const pending = new Map<number, { resolve: (value: Record<string, unknown>) => void; reject: () => void; progress?: (done: number, total: number) => void }>();
// What the page holds of the queries that the worker wrote, which it shows until the next is written.
const chartedSql = new Map<string, string>();
let countQueries: CountQuery[] = [];
let tablesSql = '';
let databaseChoice: string | null = null;
const openAnother = new Set<string>();
// The steps that a person has opened or hidden by hand; otherwise the current step is open and the rest are folded.
const opened = new Set<string>();
const hidden = new Set<string>();
let written = false;
// The sentence that says why an answer could not be recorded, beside the binding it was given for.
const problems = new Map<string, string>();

function text(id: string, value: string) {
  $(id).textContent = value;
}

function status(id: string, value: string, kind: '' | 'good' | 'problem' = '') {
  const node = $(id);
  node.hidden = !value;
  node.textContent = value;
  node.className = `status ${kind}`.trim();
}

// The worker.

function call(name: string, args: unknown[] = [], extra: Record<string, unknown> = {}, progress?: (done: number, total: number) => void) {
  return new Promise<Record<string, unknown>>((resolve, reject) => {
    if (!worker) return reject();
    const id = nextId++;
    pending.set(id, { resolve, reject, progress });
    worker.postMessage({ type: 'describe', call: name, args, id, ...extra });
  });
}

async function ask(name: string, args: unknown[] = [], extra: Record<string, unknown> = {}, progress?: (done: number, total: number) => void): Promise<Reply> {
  const reply = await call(name, args, extra, progress);
  const parsed = JSON.parse(reply.reply as string) as Reply;
  if (parsed.model) {
    model = parsed.model;
    render();
  }
  return parsed;
}

function onMessage(event: MessageEvent) {
  const message = event.data;
  if (message.type === 'ready') {
    state = 'ready';
    worker?.postMessage({ type: 'describe', call: 'describe_begin', args: [__VERSION__], id: 0 });
  } else if (message.type === 'policy-failed') {
    state = 'load-failed';
    text('t-load-failed', strings.policyFailed);
  } else if (message.type === 'load-failed') {
    state = 'load-failed';
  } else if (message.type === 'describe-progress') {
    pending.get(message.id)?.progress?.(message.done, message.total);
    return;
  } else if (message.type === 'describe-reply' || message.type === 'describe-failed') {
    const waiting = pending.get(message.id);
    pending.delete(message.id);
    if (message.type === 'describe-failed') waiting?.reject();
    else waiting?.resolve(message);
  }
  show();
}

async function startWorker() {
  try {
    const response = await fetch(new URL('worker.js', document.baseURI));
    if (!response.ok) throw new Error('worker');
    // Started from a blob so that the worker inherits this page's content security policy.
    const source = new Blob([await response.text()], { type: 'text/javascript' });
    worker = new Worker(URL.createObjectURL(source), { type: 'module' });
    workerHasFiles = false;
    worker.onmessage = onMessage;
    worker.onerror = () => {
      if (state === 'loading') state = 'load-failed';
      show();
    };
    worker.postMessage({ type: 'load', base: new URL('.', document.baseURI).href });
  } catch {
    state = 'load-failed';
    show();
  }
}

function lock() {
  worker?.terminate();
  worker = null;
  workerHasFiles = false;
  for (const waiting of pending.values()) waiting.reject();
  pending.clear();
  model = null;
  corrections.forget();
  chartedSql.clear();
  countQueries = [];
  tablesSql = '';
  written = false;
  for (const input of document.querySelectorAll<HTMLInputElement>('input[type=file]')) input.value = '';
  for (const area of document.querySelectorAll<HTMLTextAreaElement>('textarea')) area.value = '';
  state = 'locked';
  render();
}

window.addEventListener('online', () => {
  if (state === 'ready' && workerHasFiles) lock();
  show();
});
window.addEventListener('offline', show);

// What is shown.

function open() {
  return state === 'ready' && !navigator.onLine;
}

type StepState = 'done' | 'current' | 'available' | 'waiting' | 'problem';
const STEPS = ['1', '2', '3', '4', '5', '6', '7', '8', '9'];
const OPTIONAL = new Set(['3']);

function vocabulariesDone(m: Model) {
  return m.vocabularies.filter((v) => !v.reason).every((v) => !!v.date);
}

function countsDone(m: Model) {
  const names = countQueries.length ? countQueries.map((q) => q.name) : Object.keys(d.countHeading);
  return names.every((name) => !!m.counts[name]?.looks_right);
}

// What each step is: its state, the line that a folded step shows, and what a waiting step waits for.
function stepStates() {
  const m = model;
  const ready = open();
  const proposed = !!m?.proposed;
  const restoredMap = !!(m?.restored && (m.restored as { map?: boolean }).map);
  const done = [ready, !!m?.dictionary, restoredMap, proposed, !!m?.catalogue, proposed && m!.tally.remaining === 0,
    proposed && vocabulariesDone(m!), proposed && countsDone(m!), written];
  const waits = STEPS.map((_, i) => {
    if (i === 0) return '';
    if (!ready) return d.waitingFor.offline;
    if (i === 3) return m?.dictionary || proposed ? '' : d.waitingFor.dictionary;
    if (i >= 4) return proposed ? '' : d.waitingFor.map;
    return '';
  });
  const states: StepState[] = STEPS.map((_, i) => {
    if (i === 0) return ready ? 'done' : state === 'load-failed' || state === 'locked' ? 'problem' : 'current';
    return waits[i] ? 'waiting' : done[i] ? 'done' : 'available';
  });
  const first = states.findIndex((value, i) => value === 'available' && !OPTIONAL.has(STEPS[i]));
  if (first >= 0) states[first] = 'current';
  const statusText = (id: string) => ($(id).hidden ? '' : $(id).textContent ?? '');
  const receipts = [
    d.offlineDone,
    m?.dictionary ? d.dictionaryReceipt(m.dictionary) : '',
    statusText('t-folder-status') || d.receipt.folder,
    m ? d.receipt.proposed(m.roles.filter((r) => r.drafted).length, m.roles.length) : '',
    statusText('t-tables-status') || d.receipt.tables,
    m ? d.tally(m.tally) : '',
    d.receipt.codes,
    d.receipt.counts,
    statusText('t-write-status'),
  ];
  return { states, waits, receipts };
}

function toggleStep(n: string, open_: boolean) {
  if (open_) {
    opened.add(n);
    hidden.delete(n);
  } else {
    opened.delete(n);
    hidden.add(n);
  }
  show();
}

function show() {
  const online = navigator.onLine;
  const ready = open();
  const { states, waits, receipts } = stepStates();
  const rail = $('rail');
  rail.replaceChildren();
  STEPS.forEach((n, i) => {
    const value = states[i];
    const step = $(`step-${n}`);
    const isOpen = value === 'problem' || (value === 'current' && !hidden.has(n)) || (value !== 'waiting' && opened.has(n));
    step.dataset.state = value;
    step.dataset.open = String(isOpen);
    if (value === 'current') step.setAttribute('aria-current', 'step');
    else step.removeAttribute('aria-current');
    const word = value === 'available' && OPTIONAL.has(n) ? d.state.optional : d.state[value];
    text(`state-${n}`, word);
    const receipt = $(`receipt-${n}`);
    // A folded step shows its receipt when done, and otherwise its first instruction.
    const line = value === 'done' ? receipts[i] : value === 'available' || value === 'current' ? step.querySelector('.do')?.textContent ?? '' : '';
    receipt.textContent = line;
    receipt.hidden = isOpen || !line;
    receipt.className = value === 'done' ? 'receipt done' : 'receipt';
    const waiting = $(`waiting-${n}`);
    waiting.textContent = waits[i];
    waiting.hidden = value !== 'waiting';
    const toggle = $<HTMLButtonElement>(`toggle-${n}`);
    toggle.hidden = value === 'waiting' || value === 'problem';
    toggle.textContent = isOpen ? d.hideStep : d.showStep;
    toggle.setAttribute('aria-expanded', String(isOpen));
    toggle.onclick = () => toggleStep(n, !isOpen);

    const item = el('li', undefined, 'rail-step');
    item.dataset.state = value;
    const link = el('a');
    link.href = `#step-${n}`;
    if (value === 'current') link.setAttribute('aria-current', 'step');
    link.addEventListener('click', () => {
      if (value !== 'waiting') toggleStep(n, true);
    });
    const name = d.steps[i].replace(/^\d+\.\s*/, '');
    link.append(el('span', value === 'done' ? '✓' : value === 'problem' ? '!' : n, 'marker'), el('span', name, 'rail-name'),
      el('span', value === 'waiting' ? waits[i] : word, 'rail-state'));
    item.append(link);
    rail.append(item);
  });
  // The check of a saved folder, after the nine steps.
  const restoredMap = !!(model?.restored && (model.restored as { map?: boolean }).map);
  const checkWait = !ready ? d.waitingFor.offline : restoredMap ? '' : d.waitingFor.folder;
  const check = $('step-check');
  check.dataset.state = checkWait ? 'waiting' : 'available';
  check.dataset.open = String(!checkWait);
  text('state-check', checkWait ? d.state.waiting : d.state.optional);
  text('waiting-check', checkWait);
  $('waiting-check').hidden = !checkWait;
  const extra = el('li', undefined, 'rail-step rail-extra');
  extra.dataset.state = checkWait ? 'waiting' : 'available';
  const extraLink = el('a');
  extraLink.href = '#step-check';
  extraLink.append(el('span', '+', 'marker'), el('span', d.checkHeading, 'rail-name'), el('span', checkWait || d.state.optional, 'rail-state'));
  extra.append(extraLink);
  rail.append(extra);
  const current = states.findIndex((value) => value === 'current' || value === 'problem');
  const at = current >= 0 ? current : states.lastIndexOf('done');
  text('rail-current', d.stepOf(at + 1, STEPS.length, d.steps[at].replace(/^\d+\.\s*/, '')));

  $('t-loading').hidden = state !== 'loading';
  $('t-load-failed').hidden = state !== 'load-failed';
  $('b-loaded').hidden = !(state === 'ready' && online);
  $('t-offline-done').hidden = !ready;
  $('t-locked').hidden = state !== 'locked';
  const connection = $('t-connection');
  connection.textContent = online ? strings.connected : strings.isOffline;
  connection.dataset.online = String(online);
  for (const input of document.querySelectorAll<HTMLInputElement | HTMLButtonElement | HTMLTextAreaElement>('main input, main button, main textarea, main select')) {
    if (input.closest('#step-1') || input.classList.contains('toggle')) continue;
    input.disabled = !ready || busy || input.dataset.unusable === 'true' || (input.id === 'dictionary-load' && !$<HTMLInputElement>('dictionary').files?.length);
  }
}

function setBusy(value: boolean) {
  busy = value;
  show();
}

// Keeps what a person has typed into a box that is drawn again.
function kept(build: () => void) {
  const values = new Map<string, string>();
  for (const box of document.querySelectorAll<HTMLTextAreaElement | HTMLInputElement>('[data-keep]')) values.set(box.dataset.keep!, box.value);
  build();
  for (const box of document.querySelectorAll<HTMLTextAreaElement | HTMLInputElement>('[data-keep]')) {
    const value = values.get(box.dataset.keep!);
    if (value !== undefined) box.value = value;
  }
}

function render() {
  kept(() => {
    renderProposal();
    renderConfirm();
    renderVocabularies();
    renderCounts();
  });
  const proposed = !!model?.proposed;
  $('propose').hidden = !model?.dictionary || proposed;
  $('d-proposal').hidden = !proposed;
  if (model && proposed) {
    text('t-tally', d.tally(model.tally));
    renderTally(model.tally);
  }
  const questions = model?.questions ?? [];
  $('t-questions-none').hidden = questions.length > 0;
  $('questions').hidden = questions.length === 0;
  $('questions-copy').hidden = questions.length === 0;
  $('questions').textContent = [d.questionsNoteHead, '', ...questions.map((q, i) => `${i + 1}. ${q.about}: ${q.question}`)].join('\n');
  const year = $<HTMLInputElement>('year');
  if (!year.value) year.value = String(model?.settings.year ?? new Date().getFullYear() - 1);
  if (model?.settings.database && !databaseChoice) {
    databaseChoice = model.settings.database;
    renderDatabase();
  }
  // The next action of each step is the primary button; one already done becomes secondary.
  $('tables-write').classList.toggle('secondary', !!tablesSql);
  $('counts-write').classList.toggle('secondary', countQueries.length > 0);
  $('t-counts-again').hidden = countQueries.length === 0;
  show();
}

// The progress of the confirming: four figures and a bar, held in view at the top of step 6.
function renderTally(t: Model['tally']) {
  const figures = $('tally-figures');
  figures.replaceChildren();
  for (const key of ['confirmed', 'corrected', 'not_sure', 'remaining'] as const) {
    const box = el('div', undefined, `figure ${key}`);
    box.append(el('dt', d.tallyLabels[key]), el('dd', t[key].toLocaleString('en-AU')));
    figures.append(box);
  }
  const answered = t.total ? (t.total - t.remaining) / t.total : 0;
  $('tally-meter').style.width = `${Math.round(answered * 100)}%`;
}

function quote(definition: string | null, says: string) {
  const box = el('blockquote', definition ?? says, 'definition');
  return box;
}

function proposedLine(item: Item) {
  const line = el('p', undefined, 'proposed');
  line.append(el('span', `${d.proposedLabel} `, 'label'));
  line.append(el('code', item.bound ? item.from : d.nothingProposed));
  if (item.confidence) line.append(el('span', ` ${d.confidence[item.confidence] ?? item.confidence}`, `confidence ${item.confidence}`));
  return line;
}

function roleHeading(role: Role) {
  const heading = el('h3');
  heading.append(el('code', role.name, 'role-name'));
  return heading;
}

function renderProposal() {
  const box = $('proposal');
  box.replaceChildren();
  if (!model?.proposed) return;
  for (const role of model.roles) {
    const section = el('section', undefined, 'role');
    section.dataset.role = role.name;
    section.append(roleHeading(role), el('p', role.description, 'role-what'), el('p', role.required ? d.roleRequired : d.roleFurther, 'note'));
    if (!role.drafted) {
      section.append(el('p', d.roleUndrafted, 'status'));
      box.append(section);
      continue;
    }
    const list = el('ul', undefined, 'bindings');
    for (const item of role.items) {
      const entry = el('li', undefined, 'binding');
      entry.dataset.about = item.about;
      entry.append(el('p', item.attribute === 'rows' ? d.rowsAttribute : item.attribute, item.attribute === 'rows' ? 'attribute rows' : 'attribute'));
      if (item.attribute !== 'rows') entry.append(el('p', item.meaning, 'meaning'));
      entry.append(proposedLine(item));
      if (item.bound) {
        entry.append(el('p', item.definition ? d.definitionLabel : d.evidenceLabel, 'label'), quote(item.definition, item.says));
      }
      if (item.candidates.length) {
        const more = el('details');
        more.append(el('summary', d.alternativesLabel));
        const alternatives = el('ul');
        for (const candidate of item.candidates) {
          const one = el('li');
          one.append(el('code', candidate.from));
          if (candidate.definition) one.append(el('blockquote', candidate.definition, 'definition'));
          alternatives.append(one);
        }
        more.append(alternatives);
        entry.append(more);
      }
      list.append(entry);
    }
    section.append(list);
    box.append(section);
  }
}

function presenceText(presence: Presence | null, table = false) {
  if (!presence) return d.presence.unknown;
  if (presence.state === 'missing') return d.presence.missing(presence.missing);
  if (presence.state === 'large') return d.presence.large(presence.large[0][0], presence.large[0][1]);
  return table ? d.presence.presentTable : d.presence.present;
}

function answeredText(item: Item) {
  if (item.answer === 'yes') return d.answered.yes(item.date ?? '');
  if (item.answer === 'no' && item.status === 'person') return d.answered.no(item.date ?? '', item.replacement ?? '');
  if (item.answer === 'not sure') return d.answered.notSure(item.date ?? '');
  return '';
}

async function answer(about: string, value: string, replacement = '', from?: HTMLElement) {
  setBusy(true);
  try {
    const reply = await ask('describe_confirm', [JSON.stringify({ about, answer: value, replacement })]);
    if (!reply.ok) {
      problems.set(about, reply.problem ?? d.confirmFailed);
      openAnother.add(about);
    } else {
      problems.delete(about);
      openAnother.delete(about);
    }
  } catch {
    problems.set(about, d.confirmFailed);
    void from;
  }
  setBusy(false);
  render();
}

function anotherPanel(item: Item, entry: HTMLElement) {
  const panel = el('div', undefined, 'another');
  const rows = item.attribute === 'rows';
  const id = `another-${item.about.replace(/[^\w]/g, '-')}`;
  const label = el('label', rows ? d.anotherRowsLabel : d.anotherLabel);
  label.htmlFor = id;
  panel.append(label);
  let select: HTMLSelectElement | null = null;
  if (item.candidates.length) {
    select = el('select');
    select.id = id;
    for (const candidate of item.candidates) {
      const option = el('option', candidate.from);
      option.value = candidate.replacement;
      select.append(option);
    }
    const write = el('option', d.anotherNone);
    write.value = '';
    select.append(write);
    panel.append(select);
  }
  const written = el('input');
  written.type = 'text';
  written.spellcheck = false;
  written.autocomplete = 'off';
  written.dataset.keep = `${id}-written`;
  written.setAttribute('aria-label', d.anotherWritten);
  if (!select) written.id = id;
  panel.append(el('span', ` ${d.anotherWritten} `, 'label'), written);
  panel.append(button(d.anotherUse, () => {
    const chosen = written.value.trim() || select?.value || '';
    if (chosen) void answer(item.about, 'no', chosen, entry);
  }));
  const problem = el('p', problems.get(item.about) ?? '', 'status problem problem-note');
  problem.hidden = !problems.has(item.about);
  panel.append(problem);
  return panel;
}

// The three answers, in the same order and place for every column; one that does not apply is shown but cannot be used.
function answerButtons(item: Item, entry: HTMLElement, drafted: boolean) {
  const actions = el('div', undefined, 'actions answers');
  const yes = button(d.yes, () => void answer(item.about, 'yes', '', entry), 'answer-yes');
  yes.dataset.unusable = String(!(drafted && item.bound));
  yes.setAttribute('aria-pressed', String(item.answer === 'yes'));
  const another = button(d.another, () => {
    if (openAnother.has(item.about)) openAnother.delete(item.about);
    else openAnother.add(item.about);
    render();
  }, 'secondary answer-another');
  another.setAttribute('aria-expanded', String(openAnother.has(item.about)));
  another.setAttribute('aria-pressed', String(item.answer === 'no'));
  const unsure = button(d.notSure, () => void answer(item.about, 'not sure', '', entry), 'secondary answer-unsure');
  unsure.dataset.unusable = String(!drafted);
  unsure.setAttribute('aria-pressed', String(item.answer === 'not sure'));
  actions.append(yes, another, unsure);
  return actions;
}

function renderConfirm() {
  const box = $('confirm');
  box.replaceChildren();
  if (!model?.proposed) return;
  for (const role of model.roles) {
    const section = el('section', undefined, 'role');
    const items: Item[] = role.drafted ? role.items : [{
      about: `${role.name} rows`, attribute: 'rows', meaning: role.description, type: null, from: '', table: null, column: null, bound: false,
      definition: null, says: d.roleUndrafted, confidence: '', candidates: [], status: 'proposed', question: '', answer: null, date: null,
      replacement: null, presence: null, link: null, correction: null,
    }];
    const answeredCount = items.filter((item) => item.answer).length;
    const heading = roleHeading(role);
    heading.append(el('span', ` ${answeredCount} of ${items.length} answered`, 'role-count'));
    section.append(heading, el('p', role.description, 'role-what'));
    const list = el('ul', undefined, 'bindings');
    for (const item of items) {
      const entry = el('li', undefined, 'binding');
      entry.dataset.about = item.about;
      entry.dataset.answer = item.answer ?? '';
      entry.append(el('p', item.attribute === 'rows' ? d.rowsAttribute : item.attribute, item.attribute === 'rows' ? 'attribute rows' : 'attribute'));
      if (item.attribute !== 'rows' && item.meaning) entry.append(el('p', item.meaning, 'meaning'));
      entry.append(proposedLine(item));
      if (role.drafted && item.bound) {
        const more = el('details');
        more.append(el('summary', item.definition ? d.definitionLabel : d.evidenceLabel), quote(item.definition, item.says));
        entry.append(more);
      }
      if (!role.drafted) entry.append(el('p', d.roleUndrafted, 'note'));
      if (role.drafted) entry.append(el('p', presenceText(item.presence, item.attribute === 'rows'), `presence ${item.presence?.state ?? 'unknown'}`));
      const said = answeredText(item);
      if (said) entry.append(el('p', said, `answered ${item.answer === 'not sure' ? 'unsure' : item.answer}`));
      const correction = corrections.kept(item);
      if (correction) entry.append(correction);
      entry.append(answerButtons(item, entry, role.drafted));
      if (openAnother.has(item.about)) entry.append(role.drafted ? corrections.panel(item, () => anotherPanel(item, entry)) : anotherPanel(item, entry));
      list.append(entry);
    }
    section.append(list);
    box.append(section);
  }
}

// A query to copy and run: the copy button first, and the SQL itself behind a disclosure.
function queryBlock(sql: string, copyLabel: string, after?: HTMLElement) {
  const box = el('div', undefined, 'query-block');
  const actions = el('div', undefined, 'actions');
  actions.append(copyButton(copyLabel, () => sql, after));
  const details = el('details', undefined, 'query');
  details.append(el('summary', d.showQuery), el('pre', sql, 'code'));
  box.append(actions, details);
  return box;
}

function grid(columns: string[], rows: (string | number | null)[][]) {
  const frame = el('div', undefined, 'table-frame');
  const table = el('table');
  const head = el('tr');
  for (const column of columns) head.append(el('th', column));
  const body = el('tbody');
  for (const row of rows) {
    const line = el('tr');
    for (const cell of row) {
      const value = cell === null || cell === undefined ? '' : String(cell);
      line.append(el('td', value, /^-?\d+(\.\d+)?$/.test(value) ? 'number' : ''));
    }
    body.append(line);
  }
  const thead = el('thead');
  thead.append(head);
  table.append(thead, body);
  frame.append(table);
  return frame;
}

function copyButton(label: string, value: () => string, after?: HTMLElement) {
  return button(label, () => {
    void navigator.clipboard?.writeText(value()).then(
      () => after && status(after.id, d.copied, 'good'),
      () => undefined,
    );
  }, 'secondary copy-query');
}

function pasteBox(key: string, label: string) {
  const id = `paste-${key.replace(/[^\w]/g, '-')}`;
  const caption = el('label', label);
  caption.htmlFor = id;
  const area = el('textarea');
  area.id = id;
  area.rows = 5;
  area.spellcheck = false;
  area.autocomplete = 'off';
  area.dataset.keep = id;
  return [caption, area] as const;
}

function renderVocabularies() {
  const box = $('vocabularies');
  box.replaceChildren();
  if (!model?.proposed) return;
  for (const vocabulary of model.vocabularies) {
    const section = el('section', undefined, 'add vocabulary');
    section.dataset.key = vocabulary.key;
    section.append(el('h3', d.vocabularyHeading(vocabulary.key)));
    if (vocabulary.reason) {
      section.append(el('p', d.vocabularyReason[vocabulary.reason] ?? vocabulary.reason, 'note'));
      box.append(section);
      continue;
    }
    section.append(el('p', d.vocabularyBound(vocabulary.bound, vocabulary.lookup ? vocabulary.lookup.join('.') : null)));
    const meanings = el('details', undefined, 'about');
    meanings.append(el('summary', d.kindsSummary(vocabulary.vocabulary)));
    const list = el('ul');
    for (const kind of vocabulary.kinds) list.append(el('li', d.kindMeaning(kind, vocabulary.meanings[kind])));
    meanings.append(list);
    section.append(meanings);
    const status_ = el('p', '', 'status');
    status_.id = `status-${vocabulary.key.replace(/[^\w]/g, '-')}`;
    status_.hidden = true;
    const sql = chartedSql.get(vocabulary.key);
    section.append(el('div', undefined, 'actions'));
    section.lastElementChild!.append(button(d.chartedWrite, async () => {
      setBusy(true);
      try {
        const reply = await ask('describe_charted_query', [JSON.stringify({ key: vocabulary.key, year: yearValue(), step: d.steps[6] })]);
        if (reply.ok) chartedSql.set(vocabulary.key, reply.sql as string);
      } catch { /* shown below */ }
      setBusy(false);
      render();
    }, sql || vocabulary.rows.length ? 'secondary' : ''));
    if (sql) {
      section.append(queryBlock(sql, d.chartedCopy, status_));
      const [caption, area] = pasteBox(`charted-${vocabulary.key}`, d.chartedPasteLabel);
      section.append(caption, area);
      const read = el('div', undefined, 'actions');
      read.append(button(d.chartedRead, async () => {
        setBusy(true);
        try {
          const reply = await ask('describe_charted_read', [JSON.stringify({ key: vocabulary.key, text: area.value, year: yearValue() })]);
          if (reply.ok) {
            area.value = '';
            const n = (reply.receipt as { rows: number }).rows;
            setBusy(false);
            render();
            status(status_.id, n ? d.chartedReceipt(n, yearValue()) : d.chartedEmpty(yearValue()), n ? 'good' : 'problem');
            return;
          }
          setBusy(false);
          status(status_.id, reply.problem ?? d.failed, 'problem');
        } catch {
          setBusy(false);
          status(status_.id, d.failed, 'problem');
        }
      }, vocabulary.rows.length ? 'secondary' : ''));
      section.append(read);
    }
    section.append(status_);
    if (vocabulary.rows.length) {
      const frame = grid(d.chartedColumns, vocabulary.rows.map((r) => [r.code, r.charted, r.anaesthetics, r.name, '']));
      const body = frame.querySelectorAll('tbody tr');
      vocabulary.rows.forEach((row, i) => {
        const select = el('select');
        select.dataset.code = row.code;
        select.setAttribute('aria-label', `${d.chartedColumns[4]} ${row.code}`);
        const none = el('option', d.notChosen);
        none.value = '';
        select.append(none);
        for (const kind of vocabulary.kinds.filter((k) => k !== 'other')) {
          const option = el('option', kind);
          option.value = kind;
          select.append(option);
        }
        select.value = vocabulary.chosen[row.code] ?? '';
        body[i].lastElementChild!.replaceChildren(select);
      });
      section.append(frame);
      const save = el('div', undefined, 'actions');
      save.append(button(d.codesSave, async () => {
        const chosen: Record<string, string> = {};
        for (const select of section.querySelectorAll<HTMLSelectElement>('select[data-code]')) if (select.value) chosen[select.dataset.code!] = select.value;
        setBusy(true);
        try {
          await ask('describe_codes', [JSON.stringify({ key: vocabulary.key, chosen })]);
        } catch { /* kept */ }
        setBusy(false);
        render();
      }));
      section.append(save);
      const count = Object.keys(vocabulary.chosen).length;
      section.append(el('p', vocabulary.date ? d.codesSaved(count, vocabulary.date) : d.codesNoneSaved, vocabulary.date ? 'status good' : 'note'));
    }
    box.append(section);
  }
}

function yearValue() {
  const value = Number($<HTMLInputElement>('year').value);
  return Number.isInteger(value) && value > 1900 ? value : new Date().getFullYear() - 1;
}

function renderCounts() {
  const box = $('counts');
  box.replaceChildren();
  if (!model?.proposed) return;
  for (const query of countQueries) {
    const held = model.counts[query.name];
    const section = el('section', undefined, 'add count-block');
    section.dataset.count = query.name;
    section.append(el('h3', d.countHeading[query.name] ?? query.name), el('p', d.countWhat[query.name] ?? ''));
    const about = el('details', undefined, 'about');
    about.append(el('summary', d.countTablesSummary), el('p', query.safe ? d.countSafe : d.countScript((5000).toLocaleString('en-AU')), 'note'),
      el('p', d.countTables, 'note'));
    const tables = el('ul', undefined, 'note');
    for (const [table, size] of query.tables) tables.append(el('li', `${table} (${size === null ? d.sizeUnknown : d.sizeRows(size)})`));
    about.append(tables);
    section.append(about);
    const status_ = el('p', '', 'status');
    status_.id = `status-count-${query.name}`;
    status_.hidden = true;
    section.append(queryBlock(query.sql, d.countCopy, status_));
    const [caption, area] = pasteBox(`count-${query.name}`, d.countPasteLabel);
    section.append(caption, area);
    const read = el('div', undefined, 'actions');
    read.append(button(d.countRead, async () => {
      setBusy(true);
      try {
        const reply = await ask('describe_count_read', [JSON.stringify({ name: query.name, text: area.value })]);
        setBusy(false);
        if (reply.ok) {
          area.value = '';
          render();
          status(status_.id, d.countReceipt((reply.receipt as { rows: number }).rows), 'good');
        } else status(status_.id, reply.problem ?? d.failed, 'problem');
      } catch {
        setBusy(false);
        status(status_.id, d.failed, 'problem');
      }
    }, held?.rows ? 'secondary' : ''));
    section.append(read, status_);
    if (held?.rows && held.columns) {
      section.append(grid(held.columns, held.rows));
      const findings = el('ul', undefined, 'findings');
      for (const finding of held.findings) findings.append(el('li', finding));
      section.append(held.findings.length ? findings : el('p', d.countNoFindings, 'note'));
      const fieldset = el('fieldset', undefined, 'judgement');
      fieldset.append(el('legend', d.lookRightLegend));
      for (const [value, label] of d.lookRight) {
        const option = el('label', undefined, 'option');
        const radio = el('input');
        radio.type = 'radio';
        radio.name = `right-${query.name}`;
        radio.value = value;
        radio.checked = held.looks_right === value;
        option.append(radio, document.createTextNode(` ${label}`));
        fieldset.append(option);
      }
      const noteId = `note-${query.name}`;
      const noteLabel = el('label', d.lookRightNote);
      noteLabel.htmlFor = noteId;
      const note = el('input');
      note.type = 'text';
      note.id = noteId;
      note.value = held.note ?? '';
      fieldset.append(noteLabel, note);
      const save = el('div', undefined, 'actions');
      save.append(button(d.lookRightSave, async () => {
        const chosen = fieldset.querySelector<HTMLInputElement>('input[type=radio]:checked');
        if (!chosen) return;
        setBusy(true);
        try {
          await ask('describe_count_judge', [JSON.stringify({ name: query.name, looksRight: chosen.value, note: note.value })]);
        } catch { /* kept */ }
        setBusy(false);
        render();
      }, held.looks_right ? 'secondary' : ''));
      fieldset.append(save);
      if (held.looks_right) fieldset.append(el('p', d.lookRightSaved(held.looks_right, held.date ?? ''), 'status good'));
      section.append(fieldset);
    }
    box.append(section);
  }
}

function renderDatabase() {
  const box = $('database-options');
  box.replaceChildren();
  for (const [value, label] of d.databaseOptions) {
    const option = el('label', undefined, 'option');
    const radio = el('input');
    radio.type = 'radio';
    radio.name = 'database';
    radio.value = value;
    radio.checked = databaseChoice === value;
    radio.addEventListener('change', async () => {
      databaseChoice = value;
      $('t-database-unsure').hidden = value !== 'unsure';
      try {
        await ask('describe_settings', [JSON.stringify({ database: value })]);
      } catch { /* kept */ }
    });
    option.append(radio, document.createTextNode(` ${label}`));
    box.append(option);
  }
  show();
}

// The inputs.

$('dictionary').addEventListener('change', show);
$('dictionary-load').addEventListener('click', async () => {
  const file = $<HTMLInputElement>('dictionary').files?.[0];
  const tables = $<HTMLInputElement>('dictionary-tables').files?.[0];
  if (!file || !open()) return;
  const headings: Record<string, string> = {};
  for (const input of document.querySelectorAll<HTMLInputElement>('#headings input')) if (input.value.trim()) headings[input.dataset.field!] = input.value.trim();
  setBusy(true);
  status('t-dictionary-status', d.dictionaryReading);
  workerHasFiles = true;
  try {
    const reply = await ask('describe_dictionary', [file, tables ?? undefined, JSON.stringify(headings), file.name, tables?.name ?? undefined, d.steps[1]]);
    if (reply.ok) status('t-dictionary-status', d.dictionaryReceipt(reply.receipt as Parameters<typeof d.dictionaryReceipt>[0]), 'good');
    else {
      status('t-dictionary-status', reply.problem ?? d.dictionaryFailed, 'problem');
      if (/heading/.test(reply.problem ?? '')) $<HTMLDetailsElement>('d-headings').open = true;
    }
  } catch {
    status('t-dictionary-status', d.dictionaryFailed, 'problem');
  }
  setBusy(false);
});

// The paths inside the chosen folder, without the folder's own name, which the browser puts first.
function folderFiles(input: HTMLInputElement) {
  const files = [...(input.files ?? [])];
  return files.map((file) => ({ path: (file.webkitRelativePath || file.name).split('/').slice(1).join('/'), file })).filter((f) => f.path);
}

$('hospital-folder').addEventListener('change', async () => {
  const input = $<HTMLInputElement>('hospital-folder');
  if (!input.files?.length || !open()) return;
  setBusy(true);
  status('t-folder-status', d.folderReading);
  workerHasFiles = true;
  try {
    const reply = await ask('describe_folder_end', [], { files: folderFiles(input) });
    const restored = reply.restored as Parameters<typeof d.folderReceipt>[0] & { problem: string };
    status('t-folder-status', [restored.problem, d.folderReceipt(restored)].filter(Boolean).join(' '), restored.map ? 'good' : '');
    $('t-folder-no-dictionary').hidden = !(restored.map && !model?.dictionary);
  } catch {
    status('t-folder-status', d.folderFailed, 'problem');
  }
  input.value = '';
  setBusy(false);
});

$('propose').addEventListener('click', async () => {
  if (!open()) return;
  setBusy(true);
  const progress = $<HTMLProgressElement>('propose-progress');
  progress.hidden = false;
  progress.removeAttribute('value');
  status('t-propose-status', d.proposeProgress(0, 0));
  try {
    const reply = await ask('describe_propose', [], {}, (done, total) => {
      progress.max = total;
      progress.value = done;
      status('t-propose-status', d.proposeProgress(done, total));
    });
    if (reply.ok && model) status('t-propose-status', d.proposeDone(model.roles.length, model.roles.filter((r) => r.drafted).length), 'good');
    else status('t-propose-status', reply.problem ?? d.failed, 'problem');
  } catch {
    status('t-propose-status', d.failed, 'problem');
  }
  progress.hidden = true;
  setBusy(false);
});

$('tables-write').addEventListener('click', async () => {
  setBusy(true);
  try {
    const reply = await ask('describe_tables_query', [d.steps[4]]);
    tablesSql = reply.sql as string;
    $('tables-query').textContent = tablesSql;
    text('t-tables-names', d.tablesNames(reply.tables as number));
    $('b-tables-query').hidden = !tablesSql;
  } catch {
    status('t-tables-status', d.failed, 'problem');
  }
  setBusy(false);
  render();
});
$('tables-copy').addEventListener('click', () => void navigator.clipboard?.writeText(tablesSql).catch(() => undefined));
$('tables-read').addEventListener('click', async () => {
  const area = $<HTMLTextAreaElement>('tables-paste');
  if (!area.value.trim()) return;
  setBusy(true);
  try {
    const reply = await ask('describe_tables_read', [area.value]);
    if (reply.ok) {
      const receipt = reply.receipt as Parameters<typeof d.tablesReceipt>[0] & { doubt: string };
      status('t-tables-status', [d.tablesReceipt(receipt), receipt.doubt === 'most' ? d.tablesDoubt : ''].filter(Boolean).join(' '), receipt.doubt ? 'problem' : 'good');
      area.value = '';
    } else status('t-tables-status', d.tablesUnreadable, 'problem');
  } catch {
    status('t-tables-status', d.tablesUnreadable, 'problem');
  }
  setBusy(false);
});

$('model-check').addEventListener('click', async () => {
  setBusy(true);
  await corrections.checkModel($('model-check-result'));
  setBusy(false);
});
$('questions-copy').addEventListener('click', () => void navigator.clipboard?.writeText($('questions').textContent ?? '').catch(() => undefined));

$('year').addEventListener('change', async () => {
  try {
    await ask('describe_settings', [JSON.stringify({ year: yearValue() })]);
  } catch { /* kept */ }
});

$('counts-write').addEventListener('click', async () => {
  setBusy(true);
  try {
    const reply = await ask('describe_counts', [JSON.stringify({ year: yearValue(), step: d.steps[7] })]);
    if (reply.ok) countQueries = reply.queries as CountQuery[];
  } catch { /* kept */ }
  setBusy(false);
  render();
});

function base64Bytes(value: string) {
  const raw = atob(value);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

type DirectoryHandle = {
  getDirectoryHandle(name: string, options?: { create?: boolean }): Promise<DirectoryHandle>;
  getFileHandle(name: string, options?: { create?: boolean }): Promise<{ createWritable(): Promise<{ write(data: BlobPart): Promise<void>; close(): Promise<void> }> }>;
  removeEntry(name: string, options?: { recursive?: boolean }): Promise<void>;
};
const picker = (window as unknown as { showDirectoryPicker?: (options?: { mode?: string }) => Promise<DirectoryHandle> }).showDirectoryPicker;

$('write-folder').addEventListener('click', async () => {
  if (!picker) return;
  const keep = $<HTMLInputElement>('keep-dictionary').checked;
  try {
    const root = await picker({ mode: 'readwrite' });
    setBusy(true);
    const reply = await call('describe_folder_files', [keep]);
    const files = JSON.parse(reply.reply as string) as [string, string][];
    for (const [path, data] of files) {
      const parts = path.split('/');
      let folder = root;
      for (const part of parts.slice(0, -1)) folder = await folder.getDirectoryHandle(part, { create: true });
      const handle = await folder.getFileHandle(parts[parts.length - 1], { create: true });
      const writable = await handle.createWritable();
      await writable.write(base64Bytes(data));
      await writable.close();
    }
    if (!keep) await root.removeEntry('dictionary', { recursive: true }).catch(() => undefined);
    written = true;
    status('t-write-status', d.written(files.length), 'good');
  } catch {
    status('t-write-status', d.writeFailed, 'problem');
  }
  setBusy(false);
});

$('write-zip').addEventListener('click', async () => {
  const keep = $<HTMLInputElement>('keep-dictionary').checked;
  setBusy(true);
  try {
    const reply = await call('describe_folder_zip', [keep]);
    const zip = reply.zip as Uint8Array<ArrayBuffer>;
    const link = el('a');
    link.href = URL.createObjectURL(new Blob([zip], { type: 'application/zip' }));
    link.download = 'hospital-folder.zip';
    link.click();
    URL.revokeObjectURL(link.href);
    const listed = JSON.parse((await call('describe_folder_files', [keep])).reply as string) as unknown[];
    written = true;
    status('t-write-status', d.zipped(listed.length), 'good');
  } catch {
    status('t-write-status', d.writeFailed, 'problem');
  }
  setBusy(false);
});

$('check').addEventListener('click', async () => {
  setBusy(true);
  const box = $('check-result');
  box.replaceChildren();
  try {
    const reply = await ask('describe_check');
    const found = reply.check as { rebuilt: boolean; same: boolean | null; differences: string[]; queries: CheckQuery[] };
    if (!found.rebuilt) box.append(el('p', d.checkNoDictionary, 'status'));
    else if (found.same) box.append(el('p', d.checkSame, 'status good'));
    else {
      box.append(el('p', d.checkDiffers(found.differences.length), 'status problem'));
      const list = el('ul', undefined, 'findings');
      for (const difference of found.differences) list.append(el('li', difference));
      box.append(list);
    }
    if (!found.queries.length) box.append(el('p', d.checkNoQueries, 'note'));
    for (const query of found.queries) {
      const section = el('section', undefined, 'check-query');
      section.dataset.query = query.name;
      section.append(el('h4', d.checkQuery(query.number, query.file, query.step ?? '')));
      section.append(queryBlock(query.sql, d.tablesCopy), el('p', d.checkPrevious(query.pasted, query.database), 'note'));
      if (query.columns.length) section.append(grid(query.columns, query.rows));
      const [caption, area] = pasteBox(`check-${query.name}`, d.checkPasteLabel);
      const out = el('div');
      const compare = el('div', undefined, 'actions');
      compare.append(button(d.checkCompare, async () => {
        out.replaceChildren();
        try {
          const answer = JSON.parse((await call('describe_compare', [JSON.stringify({ name: query.name, text: area.value })])).reply as string) as Reply;
          if (!answer.ok) {
            out.append(el('p', answer.problem === 'unreadable' ? d.tablesUnreadable : answer.problem ?? d.failed, 'status problem'));
            return;
          }
          const differences = (answer.compared as { differences: string[] }).differences;
          out.append(el('p', differences.length ? d.checkChanges : d.checkNoChange, differences.length ? 'status problem' : 'status good'));
          const list = el('ul', undefined, 'findings');
          for (const difference of differences) list.append(el('li', difference));
          if (differences.length) out.append(list);
        } catch {
          out.append(el('p', d.failed, 'status problem'));
        }
      }));
      section.append(caption, area, compare, out);
      box.append(section);
    }
  } catch {
    box.append(el('p', d.failed, 'status problem'));
  }
  setBusy(false);
});

// The fixed wording.

const fixed: Record<string, string> = {
  title: d.title,
  intro: d.intro,
  't-private': d.privateNote,
  't-back': d.back,
  'a-back': d.backLink,
  'h-rail': d.railLabel,
  't-offline-what': d.offlineWhat,
  't-loading': d.loading,
  't-load-failed': d.loadFailed,
  't-loaded': d.loaded,
  's-offline-how': d.offlineHowSummary,
  't-no-files': d.noFiles,
  't-offline-done': d.offlineDone,
  't-locked': d.locked,
  's-policy': d.policySummary,
  't-policy-held': strings.policyHeld,
  't-dictionary-what': d.dictionaryWhat,
  'l-dictionary': d.dictionaryLabel,
  's-dictionary-about': d.aboutFile,
  't-dictionary-about': d.dictionaryAbout,
  'l-dictionary-tables': d.tablesLabel,
  's-tables-about': d.aboutFile,
  't-tables-about': d.tablesAbout,
  's-headings': d.headingsSummary,
  't-headings-what': d.headingsWhat,
  'dictionary-load': d.dictionaryLoad,
  't-folder-what': d.folderWhat,
  'l-hospital-folder': d.folderLabel,
  's-folder-about': d.folderAboutSummary,
  't-folder-about': d.folderAbout,
  't-folder-no-dictionary': d.folderNoDictionary,
  'h-check': d.checkHeading,
  't-check-what': d.checkWhat,
  check: d.checkButton,
  't-propose-what': d.proposeWhat,
  's-propose-about': d.proposeAboutSummary,
  't-propose-about': d.proposeAbout,
  propose: d.proposeButton,
  's-proposal': d.proposalSummary,
  't-tables-what': d.tablesWhat,
  'l-database': d.databaseLegend,
  's-database-about': d.databaseAboutSummary,
  't-database-about': d.databaseAbout,
  't-database-unsure': d.databaseUnsure,
  'tables-write': d.tablesWrite,
  'tables-copy': d.tablesCopy,
  's-tables-query': d.showQuery,
  's-query-safe': d.querySafeSummary,
  't-query-safe': d.querySafe,
  'l-tables-paste': d.tablesPasteLabel,
  'tables-read': d.tablesRead,
  't-confirm-what': d.confirmWhat,
  's-confirm-about': d.confirmAboutSummary,
  't-confirm-about': d.confirmAbout,
  't-model-check-what': d.corrections.modelCheckWhat,
  'model-check': d.corrections.modelCheck,
  'h-questions': d.questionsHeading,
  't-questions-what': d.questionsWhat,
  't-questions-none': d.questionsNone,
  'questions-copy': d.questionsCopy,
  't-codes-what': d.codesWhat,
  'l-year': d.yearLabel,
  't-year-note': d.yearNote,
  's-codes-safe': d.codesAboutSummary,
  't-codes-safe': d.codesSafe((5000).toLocaleString('en-AU')),
  't-counts-what': d.countsWhat,
  's-counts-about': d.countsAboutSummary,
  't-counts-about': d.countsAbout,
  'counts-write': d.countsWrite,
  't-counts-again': d.countsAgain,
  't-write-what': d.writeWhat,
  's-write-about': d.writeAboutSummary,
  't-write-about': d.writeAbout,
  'l-keep': d.keepLabel,
  't-keep-note': d.keepNote,
  'write-folder': d.writeFolder,
  't-write-folder-note': d.writeFolderNote,
  'write-zip': d.writeZip,
  't-write-zip-note': d.writeZipNote,
};
for (const [id, value] of Object.entries(fixed)) text(id, value);
d.steps.forEach((heading, i) => text(`h-step-${i + 1}`, heading));
$('t-offline-how').replaceChildren(...d.offlineHow.map((sentence) => el('li', sentence)));
$('t-tables-how').replaceChildren(...d.tablesHow.map((sentence) => el('li', sentence)));
$('headings').replaceChildren(...d.headingFields.map(([field, label]) => {
  const box = el('div', undefined, 'field');
  const caption = el('label', label);
  caption.htmlFor = `heading-${field}`;
  const input = el('input');
  input.type = 'text';
  input.id = `heading-${field}`;
  input.dataset.field = field;
  input.autocomplete = 'off';
  input.spellcheck = false;
  box.append(caption, input);
  return box;
}));
$('write-folder').hidden = !picker;
$('t-write-folder-note').hidden = !picker;
// Where the browser can write into a folder, that is the primary action and the zip the other; elsewhere the zip is.
$('write-zip').classList.toggle('secondary', !!picker);
text('t-version', d.version(__VERSION__));
renderDatabase();

// The boxes are emptied when the page is left, so that the browser does not keep their contents.
window.addEventListener('pagehide', () => {
  for (const area of document.querySelectorAll<HTMLTextAreaElement>('textarea')) area.value = '';
});

corrections.setup({
  call: (name, args = []) => call(name, args),
  ask: (name, args = []) => ask(name, args),
  render,
  setBusy,
  el,
  button,
  grid,
  pasteBox,
  year: yearValue,
  base: (view) => model?.bases?.[view] ?? null,
  anaestheticTable: () => model?.anaesthetic_table ?? null,
  kinds: (about) => model?.vocabularies.find((v) => v.key === about)?.kinds ?? [],
  values: (name) => model?.values?.[name] ?? null,
  close: (about) => { openAnother.delete(about); },
  step: d.steps[5],
});

show();
render();
void startWorker();
