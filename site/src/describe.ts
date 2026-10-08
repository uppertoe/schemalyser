// The page Describe the record: screen 1, which makes the saved hospital schema. It shares the worker, the bridge, the style
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
  link: string | null; correction: corrections.CorrectionHeld | null; title: string;
  coding: { form: string; translated: boolean; assumed: string; values: string[]; list?: boolean } | null;
}
interface Role { name: string; title: string; description: string; required: boolean; drafted: boolean; items: Item[] }
interface Vocabulary {
  key: string; title: string; view: string; column: string; vocabulary: string; kinds: string[]; meanings: Record<string, string>; bound: string;
  lookup: [string, string] | null; reason: string; rows: { code: string; charted: number | null; anaesthetics: number | null; name: string }[];
  chosen: Record<string, string>; year: number | null; date: string | null;
}
interface CountHeld {
  columns: string[] | null; rows: string[][] | null; looks_right: string | null; note: string | null; date: string | null; findings: string[];
  database: string | null;
}
interface Tally {
  confirmed: number; corrected: number; not_sure: number; remaining: number; untranslated: number; total: number;
  tables: number; tables_remaining: number;
}
interface Model {
  dictionary: {
    tables: number; columns: number; described: number; keyed: number; skipped: number; invented: boolean; source: string | null;
    saved: boolean; vendor: { file: string; matched: number; gained: number } | null;
  } | null;
  proposed: boolean; roles: Role[]; tally: Tally;
  untranslated: { about: string; title: string; from: string }[]; unfinished: string; counts_offered: string[];
  questions: { about: string; title: string; question: string; meaning: string }[]; catalogue: boolean;
  catalogue_source: 'database' | 'query' | null; vocabularies: Vocabulary[]; counts: Record<string, CountHeld>;
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
// The data dictionary query, which is the same for every hospital and is fetched from the worker once it is ready.
let dictionarySql = '';
let databaseChoice: string | null = null;
const openAnother = new Set<string>();
// The columns whose answer a person has asked to change, which show their choices again.
const changing = new Set<string>();
// The steps that a person has opened or hidden by hand; otherwise the current step is open and the rest are folded.
const opened = new Set<string>();
const hidden = new Set<string>();
let written = false;
// The name of the one file that holds the saved hospital schema.
const SCHEMA_FILE = 'hospital-schema.schemalyser.zip';
// Whether the hospital schema last saved was a draft, which leaves step 9 to be done again.
let writtenDraft = false;
// The text of a finding of a check, shown at the row that its link leads to.
const landed = new Map<string, string>();
// Whether something has changed since the hospital schema was saved, which makes step 9 to be done again.
let changedSinceWritten = false;
// The calls that change nothing that the saved hospital schema holds.
const READING = new Set(['describe_model', 'describe_check', 'describe_compare', 'describe_dictionary_query']);
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
  if (parsed.ok && !READING.has(name) && written) {
    written = false;
    changedSinceWritten = true;
  }
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
    void ask('describe_dictionary_query', [d.steps[1]]).then((reply) => {
      dictionarySql = (reply.sql as string) ?? '';
      $('database-query').textContent = dictionarySql;
      show();
    }, () => undefined);
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
  changing.clear();
  openAnother.clear();
  chartedSql.clear();
  countQueries = [];
  tablesSql = '';
  written = false;
  for (const input of document.querySelectorAll<HTMLInputElement>('input[type=file]')) input.value = '';
  for (const area of document.querySelectorAll<HTMLTextAreaElement>('textarea')) area.value = '';
  state = 'locked';
  render();
}

// The page lets go of everything when the tab comes back online, so that nothing loaded or pasted while it was offline can
// leave. Before anything is loaded there is nothing to let go of, so the tab may go back online to fetch the invented
// dictionary, which is public; once that or anything else is loaded, going online again locks the page.
function holdsAnything() {
  return workerHasFiles || !!model?.dictionary || !!model?.restored;
}

window.addEventListener('online', () => {
  if (state === 'ready' && holdsAnything()) lock();
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

// Step 7 is done once every list that a bound column of a kind offers is saved.
function listsSaved(m: Model) {
  return m.vocabularies.filter((v) => !v.reason && v.date).length;
}

function listsOffered(m: Model) {
  return m.vocabularies.filter((v) => !v.reason).length;
}

function vocabulariesDone(m: Model) {
  return listsSaved(m) === listsOffered(m);
}

// Step 8 is done once every count offered has a saved judgement.
function judged(m: Model) {
  return m.counts_offered.filter((name) => !!m.counts[name]?.looks_right).length;
}

function countsDone(m: Model) {
  return m.counts_offered.length > 0 && judged(m) === m.counts_offered.length;
}

// Step 6 is done once every column and table has an answer and every code is translated.
function confirmDone(t: Tally) {
  return t.remaining === 0 && t.tables_remaining === 0 && t.untranslated === 0;
}

// What each step is: its state, the line that a folded step shows, and what a waiting step waits for.
function stepStates() {
  const m = model;
  const ready = open();
  const proposed = !!m?.proposed;
  const restoredMap = !!(m?.restored && (m.restored as { map?: boolean }).map);
  const done = [ready, !!m?.dictionary, restoredMap, proposed, !!m?.catalogue, proposed && confirmDone(m!.tally),
    proposed && vocabulariesDone(m!), proposed && countsDone(m!), written && !writtenDraft];
  const waits = STEPS.map((_, i) => {
    if (i === 0) return '';
    // Step 2 is open while the tab is still online, for the invented dictionary; its file controls wait for offline.
    if (i === 1 && state === 'ready') return '';
    if (!ready) return d.waitingFor.offline;
    if (i === 3) return m?.dictionary || proposed ? '' : d.waitingFor.dictionary;
    // Step 5 is answered by the data dictionary made from the database, which needs no proposal first.
    if (i === 4 && m?.catalogue_source === 'database') return '';
    if (i >= 4) return proposed ? '' : d.waitingFor.map;
    return '';
  });
  const states: StepState[] = STEPS.map((_, i) => {
    if (i === 0) return ready ? 'done' : state === 'load-failed' || state === 'locked' ? 'problem' : 'current';
    return waits[i] ? 'waiting' : done[i] ? 'done' : 'available';
  });
  const first = states.findIndex((value, i) => value === 'available' && !OPTIONAL.has(STEPS[i]));
  if (first >= 0 && !states.includes('current')) states[first] = 'current';
  const statusText = (id: string) => ($(id).hidden ? '' : $(id).textContent ?? '');
  const receipts = [
    d.offlineDone,
    m?.dictionary ? d.dictionaryReceipt(m.dictionary) : '',
    statusText('t-folder-status') || d.receipt.folder,
    m ? d.receipt.proposed(m.roles.filter((r) => r.drafted).length, m.roles.length) : '',
    m?.catalogue_source === 'database' ? d.tablesAnswered : statusText('t-tables-status') || d.receipt.tables,
    m ? d.receipt.confirmed(m.tally.total, m.roles.filter((r) => !r.drafted).length) : '',
    m ? d.receipt.codes(listsSaved(m), listsOffered(m)) : '',
    m ? d.receipt.counts(judged(m), m.counts_offered.length) : '',
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
    // While the tab is online with nothing loaded, step 2 stays open beside step 1, so that the invented dictionary
    // can be loaded; once it is loaded, step 2 stays open to say what to do next until the tab goes offline.
    const online2 = n === '2' && state === 'ready' && online && !hidden.has(n) && (!model?.dictionary || model.dictionary.source === 'invented');
    const isOpen = value === 'problem' || (value === 'current' && !hidden.has(n)) || (value !== 'waiting' && opened.has(n)) || online2;
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
    // Steps 6 to 9 say how far each has gone until it is done.
    let progress = '';
    if (model?.proposed && value !== 'done' && value !== 'waiting') {
      const t = model.tally;
      if (n === '6') progress = d.stillToAnswer(t.remaining, t.tables_remaining, t.untranslated);
      if (n === '7' && listsOffered(model)) progress = d.receipt.codes(listsSaved(model), listsOffered(model)).replace(/\.$/, '');
      if (n === '8' && model.counts_offered.length) progress = d.receipt.counts(judged(model), model.counts_offered.length).replace(/\.$/, '');
      if (n === '9' && written && writtenDraft) progress = d.savedDraft;
    }
    const railWord = value === 'waiting' ? waits[i] : progress ? `${word}: ${progress}` : word;
    link.append(el('span', value === 'done' ? '✓' : value === 'problem' ? '!' : n, 'marker'), el('span', name, 'rail-name'),
      el('span', railWord, 'rail-state'));
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
  // Once the invented dictionary has loaded while the tab is online, step 1 and step 2 say what to do next.
  const inventedOnline = state === 'ready' && online && model?.dictionary?.source === 'invented';
  if (inventedOnline) {
    text('t-offline-invented', d.inventedLoaded);
    status('t-invented-status', d.inventedLoaded, 'good');
  } else if ($('t-offline-invented').textContent === d.inventedLoaded) {
    linked('t-offline-invented', d.offlineInvented, '2');
    if ($('t-invented-status').textContent === d.inventedLoaded) status('t-invented-status', '');
  }
  // The choice of database stands in step 2 until a dictionary that needs step 5's own query is loaded.
  const fromDatabase = model?.catalogue_source === 'database';
  const slot = $(model?.dictionary && !fromDatabase && model.dictionary.source !== 'database' ? 'database-slot-5' : 'database-slot-2');
  if ($('f-database').parentElement !== slot) slot.append($('f-database'));
  $('b-tables').hidden = fromDatabase;
  $('t-tables-what').hidden = fromDatabase;
  $('t-tables-answered').hidden = !fromDatabase;
  text('t-tables-answered', d.tablesAnswered);
  // The vendor's file adds descriptions to a dictionary made from the database, and is otherwise the dictionary itself.
  text('dictionary-load', model?.dictionary?.source === 'database' ? d.vendorLoad : d.dictionaryLoad);
  const connection = $('t-connection');
  connection.textContent = online ? strings.connected : strings.isOffline;
  connection.dataset.online = String(online);
  for (const input of document.querySelectorAll<HTMLInputElement | HTMLButtonElement | HTMLTextAreaElement>('main input, main button, main textarea, main select')) {
    if (input.closest('#step-1') || input.classList.contains('toggle')) continue;
    // The invented dictionary is loaded while the tab is online, so its button needs only the page to be ready.
    if (input.id === 'invented-load') {
      input.disabled = state !== 'ready' || busy;
      continue;
    }
    // The data dictionary query names nothing of the hospital's, so it may be copied while the tab is online.
    if (input.id === 'database-copy') {
      input.disabled = state !== 'ready' || !dictionarySql;
      continue;
    }
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
  for (const box of document.querySelectorAll<HTMLTextAreaElement | HTMLInputElement | HTMLSelectElement>('[data-keep]')) values.set(box.dataset.keep!, box.value);
  build();
  for (const box of document.querySelectorAll<HTMLTextAreaElement | HTMLInputElement | HTMLSelectElement>('[data-keep]')) {
    const value = values.get(box.dataset.keep!);
    if (value !== undefined) box.value = value;
    box.dispatchEvent(new Event('kept'));
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
  $('questions').textContent = [d.questionsNoteHead, '',
    ...questions.map((q, i) => `${i + 1}. ${q.title}: ${q.question} ${d.questionsMeaning(q.meaning)}`)].join('\n\n');
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
  if (changedSinceWritten && !written) status('t-write-status', d.writtenStale);
  // The check of the whole map says, once something is answered, that it checks the map as it now stands.
  const answeredAny = !!model?.proposed && (model.tally.total - model.tally.remaining + model.tally.tables - model.tally.tables_remaining) > 0;
  text('t-model-check-what', answeredAny ? d.corrections.modelCheckWhatAfter : d.corrections.modelCheckWhat);
  text('model-check', answeredAny ? d.corrections.modelCheckAgain : d.corrections.modelCheck);
  renderDraft();
  show();
}

// Step 9 says what keeps the folder a draft, and lists each column whose codes are not yet translated.
function renderDraft() {
  const note = $('t-write-draft');
  const list = $('write-draft-list');
  list.replaceChildren();
  const unfinished = model?.proposed ? model.unfinished : '';
  note.hidden = !unfinished;
  note.textContent = unfinished ? d.draftNote(unfinished) : '';
  const untranslated = model?.untranslated ?? [];
  $('t-write-draft-codes').hidden = !untranslated.length;
  text('t-write-draft-codes', d.draftCodes);
  for (const column of untranslated) {
    const item = el('li');
    const link = el('a', column.title);
    link.href = '#step-6';
    link.addEventListener('click', (event) => {
      event.preventDefault();
      goTo(column.about);
    });
    item.append(link, document.createTextNode(` (${column.from})`));
    list.append(item);
  }
}

// The progress of the confirming: four figures and a bar, held in view at the top of step 6.
function renderTally(t: Model['tally']) {
  const figures = $('tally-figures');
  figures.replaceChildren();
  for (const key of ['confirmed', 'corrected', 'not_sure', 'untranslated', 'remaining'] as const) {
    const box = el('div', undefined, `figure ${key}`);
    box.append(el('dt', d.tallyLabels[key]), el('dd', t[key].toLocaleString('en-AU')));
    figures.append(box);
  }
  const answered = t.total ? (t.total - t.remaining - t.untranslated) / t.total : 0;
  $('tally-meter').style.width = `${Math.round(answered * 100)}%`;
}

function quote(definition: string | null, says: string) {
  const box = el('blockquote', definition ?? says, 'definition');
  return box;
}

// Where a binding comes from, as the map writes it ("T.C, by A.a = B.b, then B.c = C.d"), with each link in words:
// the column in code type, then "linked by matching A.a to B.b, then B.c to C.d".
function fromNode(from: string) {
  const box = el('span', undefined, 'from');
  const [head, ...rest] = from.split(', by ');
  box.append(el('code', head));
  if (rest.length) {
    const links = rest.join(', by ').split(', then ').map((step) => step.split(' and ').map((pair) => pair.split(' = ')));
    box.append(document.createTextNode(`, ${d.linkedBy} `));
    links.forEach((pairs, i) => {
      if (i) box.append(document.createTextNode(`, ${d.linkedThen} `));
      pairs.forEach((pair, j) => {
        if (j) box.append(document.createTextNode(' and '));
        box.append(el('code', pair[0]));
        if (pair[1]) box.append(document.createTextNode(` ${d.linkedTo} `), el('code', pair[1]));
      });
    });
  }
  return box;
}

function capital(text: string) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function attributeName(item: Item) {
  return item.attribute === 'rows' ? d.rowsAttribute : capital(item.title || item.attribute);
}

// What the page proposes, or what a person has settled, for one column.
function proposedLine(item: Item) {
  const line = el('p', undefined, 'proposed');
  const rows = item.attribute === 'rows';
  if (!item.bound) {
    line.append(el('span', rows ? d.nothingProposedRows : d.nothingProposed, 'label nothing'));
    return line;
  }
  const settled = item.answer === 'yes' ? d.confirmedLabel : item.answer === 'no' && item.status === 'person' ? d.correctedLabel : d.proposedLabel;
  line.append(el('span', `${settled} `, 'label'), fromNode(rows ? item.table ?? item.from : item.from));
  if (settled === d.proposedLabel && item.confidence) line.append(el('span', ` ${d.confidence[item.confidence] ?? item.confidence}`, `confidence ${item.confidence}`));
  return line;
}

function roleHeading(role: Role) {
  const heading = el('h3');
  heading.append(el('span', role.title || role.name, 'role-name'));
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
      entry.append(el('p', attributeName(item), item.attribute === 'rows' ? 'attribute rows' : 'attribute'));
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
          one.append(fromNode(candidate.from));
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

// The answer that a column holds, as a state: confirmed, corrected or not sure, with its date.
function untranslated(item: Item) {
  return !!item.coding && !item.coding.translated && (item.answer === 'yes' || (item.answer === 'no' && item.status === 'person'));
}

function answeredNode(item: Item) {
  const day = d.day(item.date ?? '');
  if (untranslated(item) && item.answer === 'yes') return el('p', d.answered.untranslated(day), 'answered untranslated');
  if (untranslated(item)) {
    const line = el('p', undefined, 'answered untranslated');
    const [before, after] = d.answered.untranslatedNo(day, '\u0000').split('\u0000');
    line.append(document.createTextNode(before), fromNode(item.from), document.createTextNode(after));
    return line;
  }
  if (item.answer === 'yes') return el('p', d.answered.yes(day), 'answered yes');
  if (item.answer === 'not sure') return el('p', d.answered.notSure(day), 'answered unsure');
  if (item.answer === 'no' && item.status === 'person') {
    const line = el('p', undefined, 'answered no');
    const [before, after] = d.answered.no(day, '\u0000').split('\u0000');
    line.append(document.createTextNode(before), fromNode(item.attribute === 'rows' ? item.table ?? item.from : item.from), document.createTextNode(after));
    return line;
  }
  return null;
}

async function answer(about: string, value: string, replacement = '', from?: HTMLElement) {
  setBusy(true);
  try {
    const reply = await ask('describe_confirm', [JSON.stringify({ about, answer: value, replacement })]);
    if (!reply.ok) {
      problems.set(about, reply.problem ?? d.confirmFailed);
    } else {
      problems.delete(about);
      landed.delete(about);
      openAnother.delete(about);
      changing.delete(about);
    }
  } catch {
    problems.set(about, d.confirmFailed);
    void from;
  }
  setBusy(false);
  render();
}

// The choice of another column or table: one that the page found, or one written by hand. Whichever is chosen, the
// page checks it on invented rows and shows the result before it can be kept.
function anotherPanel(item: Item) {
  const panel = el('div', undefined, 'another');
  const rows = item.attribute === 'rows';
  const id = `another-${item.about.replace(/[^\w]/g, '-')}`;
  let select: HTMLSelectElement | null = null;
  if (item.candidates.length) {
    const label = el('label', rows ? d.anotherRowsChoose : d.anotherLabel);
    label.htmlFor = id;
    select = el('select');
    select.id = id;
    for (const candidate of item.candidates) {
      const option = el('option', candidate.from.replace(/, by /, `, ${d.linkedBy} `).replace(/ = /g, ` ${d.linkedTo} `).replace(/, then /g, `, ${d.linkedThen} `));
      option.value = candidate.replacement;
      select.append(option);
    }
    const write = el('option', d.anotherNone);
    write.value = '';
    select.append(write);
    select.value = '';
    select.dataset.keep = `${id}-chosen`;
    panel.append(label, select);
  } else if (!rows) {
    panel.append(el('p', d.anotherNoneFound, 'note no-alternative'));
  }
  const written = el('input');
  written.type = 'text';
  written.spellcheck = false;
  written.autocomplete = 'off';
  written.dataset.keep = `${id}-written`;
  const writtenLabel = rows ? (select ? d.anotherRowsWrittenOr : d.anotherRowsLabel) : select ? d.anotherWrittenOr : d.anotherWritten;
  written.setAttribute('aria-label', writtenLabel);
  if (!select) written.id = id;
  panel.append(el('span', ` ${writtenLabel} `, 'label'), written);
  const inForce = el('p', '', 'note in-force');
  const sayInForce = () => {
    const option = select?.selectedOptions[0];
    inForce.textContent = written.value.trim() ? d.inForceWritten(written.value.trim())
      : select?.value && option ? d.inForceChosen(option.textContent ?? '') : '';
    inForce.hidden = !inForce.textContent;
  };
  written.addEventListener('input', () => {
    if (written.value.trim() && select) select.value = '';
    sayInForce();
  });
  select?.addEventListener('change', () => {
    if (select!.value) written.value = '';
    sayInForce();
  });
  written.addEventListener('kept', sayInForce);
  select?.addEventListener('kept', sayInForce);
  sayInForce();
  panel.append(inForce);
  panel.append(button(d.anotherUse, () => {
    const chosen = written.value.trim() || select?.value || '';
    if (chosen) void corrections.useAlternative(item, chosen);
  }));
  return panel;
}

// The answers for one column, in the same order and place for every column. A column for which the page found
// nothing offers no Yes. A column that has an answer shows that answer, with Change the answer to bring the choices back.
function answerButtons(item: Item, drafted: boolean) {
  const actions = el('div', undefined, 'actions answers');
  const rows = item.attribute === 'rows';
  if (item.answer && !changing.has(item.about) && !openAnother.has(item.about)) {
    actions.classList.add('settled');
    actions.append(button(d.change, () => {
      changing.add(item.about);
      render();
    }, 'link answer-change'));
    return actions;
  }
  const found = drafted && item.bound;
  if (found) {
    const yes = button(d.yes, () => void answer(item.about, 'yes', ''), 'answer-yes');
    yes.setAttribute('aria-pressed', String(item.answer === 'yes'));
    actions.append(yes);
  }
  const label = found ? (rows ? d.anotherRows : d.another) : rows ? d.chooseTable : d.chooseColumn;
  const another = button(label, () => {
    if (openAnother.has(item.about)) openAnother.delete(item.about);
    else openAnother.add(item.about);
    render();
  }, `${found ? 'secondary ' : ''}answer-another`);
  another.setAttribute('aria-expanded', String(openAnother.has(item.about)));
  another.setAttribute('aria-pressed', String(item.answer === 'no'));
  actions.append(another);
  const unsure = button(d.notSure, () => void answer(item.about, 'not sure', ''), 'secondary answer-unsure');
  unsure.dataset.unusable = String(!drafted);
  unsure.setAttribute('aria-pressed', String(item.answer === 'not sure'));
  actions.append(unsure);
  return actions;
}

function renderConfirm() {
  const box = $('confirm');
  box.replaceChildren();
  if (!model?.proposed) return;
  for (const role of model.roles) {
    const section = el('section', undefined, 'role');
    section.dataset.role = role.name;
    const items: Item[] = role.drafted ? role.items : [{
      about: `${role.name} rows`, attribute: 'rows', meaning: role.description, type: null, from: '', table: null, column: null, bound: false,
      definition: null, says: d.roleUndrafted, confidence: '', candidates: [], status: 'proposed', question: '', answer: null, date: null,
      replacement: null, presence: null, link: null, correction: null, title: '', coding: null,
    }];
    const answeredCount = items.filter((item) => item.answer).length;
    const heading = roleHeading(role);
    heading.append(el('span', d.partCount(answeredCount, items.length), 'role-count'));
    const who = d.teamParts.includes(role.name) ? 'team' : 'colleague';
    section.append(heading, el('p', role.description, 'role-what'), el('p', d.whoAnswers[who], `who ${who}`));
    const list = el('ul', undefined, 'bindings');
    for (const item of items) {
      const entry = el('li', undefined, 'binding');
      entry.dataset.about = item.about;
      entry.dataset.answer = item.answer ?? '';
      entry.append(el('p', attributeName(item), item.attribute === 'rows' ? 'attribute rows' : 'attribute'));
      if (item.attribute !== 'rows' && item.meaning) entry.append(el('p', item.meaning, 'meaning'));
      const said = answeredNode(item);
      // A corrected column shows what it was corrected to, and no longer the proposal with its confidence.
      if (!(said && item.answer === 'no')) entry.append(proposedLine(item));
      if (said) entry.append(said);
      // The reason for a proposal is the dictionary's own words that matched, shown under it.
      if (role.drafted && item.bound && !item.correction && item.status === 'proposed') {
        const reason = el('p', undefined, 'reason');
        reason.append(el('span', `${d.reasonLabel} `, 'label'), document.createTextNode(item.says));
        entry.append(reason);
      }
      if (role.drafted && item.bound && item.definition) {
        const more = el('details');
        more.append(el('summary', d.definitionLabel), quote(item.definition, item.says));
        entry.append(more);
      }
      // A column whose source holds codes says so before Yes, with any translation that the page has assumed.
      if (item.coding && !item.coding.translated && !item.answer) {
        const coded = el('div', undefined, 'coded');
        if (item.coding.assumed) coded.append(el('p', item.coding.assumed, 'assumed'));
        coded.append(el('p', item.coding.form === 'flag' ? d.coded.flag : item.coding.list ? d.coded.kind : d.coded.kindForm, 'note'));
        entry.append(coded);
      }
      const found = landed.get(item.about);
      if (found) entry.append(el('p', d.landed(found), 'status problem landed'));
      if (!role.drafted) entry.append(el('p', d.roleUndrafted, 'note'));
      if (role.drafted && item.bound) entry.append(el('p', presenceText(item.presence, item.attribute === 'rows'), `presence ${item.presence?.state ?? 'unknown'}`));
      const correction = corrections.kept(item);
      if (correction) entry.append(correction);
      entry.append(answerButtons(item, role.drafted));
      const problem = problems.get(item.about);
      if (problem) entry.append(el('p', problem, 'status problem problem-note'));
      if (openAnother.has(item.about)) entry.append(corrections.panel(item, () => anotherPanel(item)));
      else if (untranslated(item) && !changing.has(item.about)) entry.append(translation(item));
      list.append(entry);
    }
    section.append(list);
    box.append(section);
  }
}

// The translation of a column answered whose codes are not yet translated: the 1-or-0 form for a flag, and for a kind
// the list of its codes in step 7, or the form of codes where step 7 cannot list them.
function translation(item: Item) {
  const box = el('div', undefined, 'translation');
  if (item.coding!.form === 'kind' && item.coding!.list) {
    box.append(el('p', d.coded.kindNext, 'do'));
    const actions = el('div', undefined, 'actions');
    actions.append(button(d.codesFromRow, () => {
      opened.add('7');
      hidden.delete('7');
      show();
      const target = document.querySelector<HTMLElement>(`#vocabularies [data-key="${CSS.escape(item.about)}"]`);
      target?.scrollIntoView({ block: 'start' });
      target?.querySelector<HTMLElement>('button')?.focus({ preventScroll: true });
    }, 'translate-codes'));
    box.append(actions);
    return box;
  }
  corrections.startTranslation(item);
  box.append(el('p', item.coding!.form === 'flag' ? d.coded.flagNext : d.coded.kindNextForm, 'do'));
  box.append(corrections.panel(item, () => anotherPanel(item)));
  return box;
}

// Takes the person to the row of one column in step 6, from a finding of a check, and repeats the finding there.
function goTo(about: string, finding?: string) {
  const target = document.querySelector<HTMLElement>(`#confirm [data-about="${CSS.escape(about)}"]`)
    ?? document.querySelector<HTMLElement>(`#confirm [data-about="${CSS.escape(about.split(/[ .]/)[0])} rows"]`);
  if (!target) return;
  if (finding) {
    landed.set(target.dataset.about!, finding);
    render();
    return goTo(about);
  }
  opened.add('6');
  hidden.delete('6');
  show();
  target.classList.add('sought');
  target.scrollIntoView({ block: 'start' });
  target.querySelector<HTMLElement>('button')?.focus({ preventScroll: true });
  window.setTimeout(() => target.classList.remove('sought'), 2500);
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

function grid(columns: string[], rows: (string | number | null)[][], counted: number[] = []) {
  const frame = el('div', undefined, 'table-frame');
  const table = el('table');
  const head = el('tr');
  for (const column of columns) head.append(el('th', column));
  const body = el('tbody');
  for (const row of rows) {
    const line = el('tr');
    row.forEach((cell, at) => {
      let value = cell === null || cell === undefined ? '' : String(cell);
      if (/^-?\d+$/.test(value)) value = Number(value).toLocaleString('en-AU');
      if (!value && counted.includes(at)) value = d.underTen;
      line.append(el('td', value, /^-?[\d,]+(\.\d+)?$/.test(value) || value === d.underTen ? 'number' : ''));
    });
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
    section.append(el('h3', d.vocabularyHeading(vocabulary.title || vocabulary.key)));
    if (vocabulary.reason) {
      section.append(el('p', d.vocabularyReason[vocabulary.reason] ?? vocabulary.reason, 'note'));
      box.append(section);
      continue;
    }
    section.append(el('p', d.vocabularyBound(vocabulary.bound, vocabulary.lookup ? vocabulary.lookup.join('.') : null)));
    const meanings = el('details', undefined, 'about');
    meanings.append(el('summary', d.kindsSummary));
    const list = el('ul');
    for (const kind of vocabulary.kinds) list.append(el('li', d.kindOption(kind, vocabulary.meanings[kind])));
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
      const frame = grid(d.chartedColumns, vocabulary.rows.map((r) => [r.code, r.charted, r.anaesthetics, r.name, '']), [1, 2]);
      const body = frame.querySelectorAll('tbody tr');
      vocabulary.rows.forEach((row, i) => {
        const select = el('select');
        select.dataset.code = row.code;
        select.setAttribute('aria-label', `${d.chartedColumns[4]} ${row.code}`);
        const none = el('option', d.notChosen);
        none.value = '';
        select.append(none);
        for (const kind of vocabulary.kinds) {
          const option = el('option', d.kindOption(kind, vocabulary.meanings[kind]));
          option.value = kind;
          select.append(option);
        }
        select.value = vocabulary.chosen[row.code] ?? '';
        body[i].lastElementChild!.replaceChildren(select);
      });
      section.append(frame, el('p', d.codesOther, 'note'));
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
    const training = model.settings.database === 'training';
    section.append(el('h3', d.countHeading[query.name] ?? query.name), el('p', (d.countWhat[query.name] ?? '').replace('{year}', String(yearValue()))));
    if (training) section.append(el('p', d.countTraining, 'status training'));
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
      section.append(grid(held.columns, held.rows, held.columns.map((_, at) => at).filter((at) => at > 0)));
      const findings = el('ul', undefined, 'findings');
      for (const finding of held.findings) findings.append(el('li', finding));
      section.append(held.findings.length ? findings : el('p', d.countNoFindings, 'note'));
      if (training) section.append(el('p', d.countTrainingNote, 'note'));
      const fieldset = el('fieldset', undefined, 'judgement');
      fieldset.append(el('legend', d.lookRightLegend));
      if (d.lookRightCompare[query.name]) fieldset.append(el('p', d.lookRightCompare[query.name], 'note compare'));
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
      if (held.looks_right) fieldset.append(el('p', d.lookRightSaved(held.looks_right, d.day(held.date ?? '')), 'status good'));
      if (held.looks_right && held.database === 'training') fieldset.append(el('p', d.lookRightTraining, 'note'));
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

// The data dictionary made from the database: its query copied, and its result pasted or chosen as a saved file.
$('database-copy').addEventListener('click', () => {
  void navigator.clipboard?.writeText(dictionarySql).then(() => status('t-database-copied', d.copied, 'good'), () => undefined);
});

async function fromDatabase(data: string | File, name: string) {
  setBusy(true);
  status('t-database-status', d.databaseReading);
  workerHasFiles = true;
  try {
    const reply = await ask('describe_dictionary_database', [data, name, d.steps[1]]);
    if (reply.ok) {
      status('t-database-status', d.databaseReceipt(reply.receipt as Parameters<typeof d.databaseReceipt>[0]), 'good');
      status('t-dictionary-status', '');
      status('t-vendor-status', '');
      $<HTMLTextAreaElement>('database-paste').value = '';
    } else status('t-database-status', reply.problem ?? d.databaseUnreadable, 'problem');
  } catch {
    status('t-database-status', d.databaseUnreadable, 'problem');
  }
  $<HTMLInputElement>('database-file').value = '';
  setBusy(false);
}

$('database-read').addEventListener('click', () => {
  const pasted = $<HTMLTextAreaElement>('database-paste').value;
  if (pasted.trim() && open()) void fromDatabase(pasted, 'data-dictionary.csv');
});
$('database-file').addEventListener('change', () => {
  const file = $<HTMLInputElement>('database-file').files?.[0];
  if (file && open()) void fromDatabase(file, 'data-dictionary.csv');
});

$('dictionary').addEventListener('change', show);
$('dictionary-load').addEventListener('click', async () => {
  const file = $<HTMLInputElement>('dictionary').files?.[0];
  const tables = $<HTMLInputElement>('dictionary-tables').files?.[0];
  if (!file || !open()) return;
  const headings: Record<string, string> = {};
  for (const input of document.querySelectorAll<HTMLInputElement>('#headings input')) if (input.value.trim()) headings[input.dataset.field!] = input.value.trim();
  setBusy(true);
  workerHasFiles = true;
  // Beside a dictionary made from the database, the vendor's file adds its descriptions.
  if (model?.dictionary?.source === 'database') {
    status('t-vendor-status', d.dictionaryReading);
    try {
      const reply = await ask('describe_dictionary_vendor', [file, tables ?? undefined, JSON.stringify(headings), file.name, tables?.name ?? undefined]);
      if (reply.ok) {
        const receipt = reply.receipt as Parameters<typeof d.databaseReceipt>[0];
        status('t-vendor-status', receipt.vendor ? d.vendorReceipt(receipt.vendor) : '', 'good');
        status('t-database-status', d.databaseReceipt(receipt), 'good');
      } else {
        status('t-vendor-status', reply.problem ?? d.dictionaryFailed, 'problem');
        if (/heading/.test(reply.problem ?? '')) $<HTMLDetailsElement>('d-headings').open = true;
      }
    } catch {
      status('t-vendor-status', d.dictionaryFailed, 'problem');
    }
    setBusy(false);
    return;
  }
  status('t-dictionary-status', d.dictionaryReading);
  try {
    const reply = await ask('describe_dictionary', [file, tables ?? undefined, JSON.stringify(headings), file.name, tables?.name ?? undefined, d.steps[1]]);
    status('t-invented-status', '');
    if (reply.ok) status('t-database-status', '');
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

// The invented dictionary is served with the page, beside the invented example, and fetched while the tab is online.
// It is then loaded exactly as a chosen file would be, and marked as invented so that everything saved says so.
$('invented-load').addEventListener('click', async () => {
  if (busy || state !== 'ready') return;
  if (!navigator.onLine) {
    status('t-invented-status', d.inventedOnlineOnly, 'problem');
    return;
  }
  setBusy(true);
  status('t-invented-status', d.inventedLoading);
  try {
    const base = new URL('./example/dictionary/', location.href);
    const take = async (name: string) => {
      const response = await fetch(new URL(name, base));
      if (!response.ok) throw new Error(name);
      return new File([await response.blob()], name);
    };
    const [file, tables] = await Promise.all([take('invented-dictionary.csv'), take('invented-tables.csv')]);
    // The invented dictionary is public and was fetched online, so loading it does not by itself lock the page; once it
    // is loaded, the page holds a dictionary, and going online again after the tab has been offline locks the page.
    const reply = await ask('describe_dictionary', [file, tables, '{}', file.name, tables.name, d.steps[1], true]);
    if (reply.ok) {
      status('t-invented-status', navigator.onLine ? d.inventedLoaded : '', 'good');
      status('t-database-status', '');
      status('t-dictionary-status', d.dictionaryReceipt(reply.receipt as Parameters<typeof d.dictionaryReceipt>[0]), 'good');
    } else status('t-invented-status', reply.problem ?? d.inventedFailed, 'problem');
  } catch {
    status('t-invented-status', d.inventedFailed, 'problem');
  }
  setBusy(false);
});

// A saved hospital schema is one file, which the worker opens and restores. A file that holds no dictionary is opened
// only once a dictionary is loaded at step 2.
$('schema-file').addEventListener('change', async () => {
  const input = $<HTMLInputElement>('schema-file');
  const file = input.files?.[0];
  if (!file || !open()) return;
  setBusy(true);
  status('t-folder-status', d.folderReading);
  workerHasFiles = true;
  try {
    const reply = await ask('describe_schema_open', [file]);
    if (!reply.ok) status('t-folder-status', d.folderFailed, 'problem');
    else {
      const restored = reply.restored as Parameters<typeof d.folderReceipt>[0] & { problem: string; needs_dictionary: boolean };
      if (restored.needs_dictionary) status('t-folder-status', d.folderNeedsDictionary, 'problem');
      else {
        status('t-folder-status', [restored.problem, d.folderReceipt(restored)].filter(Boolean).join(' '), restored.map ? 'good' : '');
        if (restored.dictionary && model?.dictionary) {
          const fromDb = model.dictionary.source === 'database';
          status(fromDb ? 't-database-status' : 't-dictionary-status', d.dictionaryReceipt(model.dictionary), 'good');
          status(fromDb ? 't-dictionary-status' : 't-database-status', '');
        }
      }
    }
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

// The saved hospital schema, as one file that the browser downloads.
$('write-save').addEventListener('click', async () => {
  setBusy(true);
  try {
    const reply = await call('describe_schema_zip');
    const zip = reply.zip as Uint8Array<ArrayBuffer>;
    const link = el('a');
    link.href = URL.createObjectURL(new Blob([zip], { type: 'application/zip' }));
    link.download = SCHEMA_FILE;
    link.click();
    URL.revokeObjectURL(link.href);
    written = true;
    writtenDraft = !!model?.unfinished;
    status('t-write-status', d.saved(model?.unfinished ?? ''), 'good');
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
  'h-choice-database': d.choiceDatabase,
  't-database-origin': d.databaseOrigin,
  'database-copy': d.databaseCopy,
  's-database-query': d.showQuery,
  's-database-safe': d.databaseSafeSummary,
  't-database-safe': d.querySafe,
  't-database-returns': d.databaseReturns,
  't-database-back': d.databaseBack,
  't-database-small': d.databaseSmall,
  't-database-small-how': d.databaseSmallHow,
  'l-database-paste': d.databasePasteLabel,
  'database-read': d.databaseRead,
  't-database-large': d.databaseLarge,
  's-database-large-how': d.databaseLargeSummary,
  'l-database-file': d.databaseFileLabel,
  't-choice-real-alone': d.choiceRealAlone,
  't-choice-invented': d.choiceInventedWhat,
  'h-choice-real': d.choiceReal,
  't-choice-real': d.choiceRealWhat,
  'h-choice-invented': d.choiceInvented,
  'invented-load': d.inventedLoad,
  'h-choice-saved': d.choiceSaved,
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
  'l-schema': d.folderLabel,
  'b-schema-file': d.folderChoose,
  's-folder-about': d.aboutFile,
  't-folder-about': d.folderAbout,
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
  't-confirm-intro': d.confirmIntro,
  't-confirm-what': d.confirmWhat,
  't-confirm-legend': d.confirmLegend,
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
  'write-save': d.writeSave,
  't-write-save-note': d.writeSaveNote,
};
for (const [id, value] of Object.entries(fixed)) text(id, value);
d.steps.forEach((heading, i) => text(`h-step-${i + 1}`, heading));
$('t-offline-how').replaceChildren(...d.offlineHow.map((sentence) => el('li', sentence)));
$('t-tables-how').replaceChildren(...d.tablesHow.map((sentence) => el('li', sentence)));
$('t-database-large-how').replaceChildren(...d.databaseLargeHow.map((sentence) => el('li', sentence)));
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
// A sentence with a link to a step, which opens that step.
function linked(id: string, [before, label, after]: string[], n: string) {
  const link = el('a', label);
  link.href = `#step-${n}`;
  link.addEventListener('click', () => toggleStep(n, true));
  $(id).replaceChildren(document.createTextNode(before), link, document.createTextNode(after));
}
linked('t-offline-invented', d.offlineInvented, '2');
linked('t-choice-saved', d.choiceSavedWhat, '3');
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
  meanings: (about) => model?.vocabularies.find((v) => v.key === about)?.meanings ?? {},
  values: (name) => model?.values?.[name] ?? null,
  close: (about) => {
    openAnother.delete(about);
    changing.delete(about);
  },
  goTo,
  step: d.steps[5],
});

show();
render();
void startWorker();
