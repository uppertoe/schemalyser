// The corrections of step 6 in plain forms: each kind of correction is a form, the worker writes its sentence and its
// SQL, tests the whole map with it on invented rows, keeps it, and writes the probe that tests it once against the
// database. The page holds only what the person has typed into each form until it is kept or discarded.
import { describeStrings as d } from './describe-strings';

const c = d.corrections;

export interface CorrectionHeld {
  form: string; says: string; check: string; reason: string; probe: string | null;
  probed: { kind: string; columns: string[]; rows: string[][]; date: string } | null; findings: string[];
}
export interface CorrectionItem {
  about: string; attribute: string; type: string | null; link: string | null; date: string | null; correction: CorrectionHeld | null;
  bound?: boolean; table?: string | null; column?: string | null;
  coding?: { form: string; translated: boolean; assumed: string; values: string[]; list?: boolean } | null;
}
interface Report {
  passed: boolean; sentence: string; problems: string[]; remaining: string[]; mended: string; notes: string[]; seconds: number;
  about?: Record<string, string>;
}
interface Column { name: string; type: string; key: boolean; present: boolean | null }
interface Join { from: string; table: string; to: string; repeats: boolean }
interface Step { from: string; table: string; to: string; from2: string; to2: string }
interface Draft {
  form: string; f: Record<string, string>; steps: Step[]; codes: [string, string][];
  preview: { sentence: string; sql: string } | null; problem: string; report: Report | null; reason: string; although: boolean;
  valuesSql: string; valuesName: string; valueRows: { value: string; rows: number | null }[]; ran?: boolean;
}
type Reply = { ok: boolean; problem?: string; [key: string]: unknown };

export interface Deps {
  call(name: string, args?: unknown[]): Promise<Record<string, unknown>>;
  ask(name: string, args?: unknown[]): Promise<Reply>;
  render(): void;
  setBusy(value: boolean): void;
  el<K extends keyof HTMLElementTagNameMap>(tag: K, content?: string, className?: string): HTMLElementTagNameMap[K];
  button(label: string, onClick: () => void, className?: string): HTMLButtonElement;
  grid(columns: string[], rows: (string | number | null)[][]): HTMLElement;
  pasteBox(key: string, label: string): readonly [HTMLLabelElement, HTMLTextAreaElement];
  year(): number;
  base(view: string): string | null;
  anaestheticTable(): string | null;
  kinds(about: string): string[];
  meanings(about: string): Record<string, string>;
  values(name: string): { value: string; rows: number | null }[] | null;
  close(about: string): void;
  goTo(about: string, text?: string): void;
  step: string;
  // Whether the invented hospital answers the page's queries, and the running of one there, read as a paste would be.
  invented(): boolean;
  runInvented(request: Record<string, unknown>): Promise<Reply>;
}

const drafts = new Map<string, Draft>();
const columns = new Map<string, Column[] | 'loading'>();
const joins = new Map<string, Join[] | 'loading'>();
const probeSql = new Map<string, string>();
// The test queries run on the invented hospital: '' once read, or the sentence that says why it could not be run.
const probeRan = new Map<string, string>();
let tables: string[] | 'loading' | null = null;
let deps: Deps;
let checking = '';

export function setup(given: Deps) {
  deps = given;
}

export function forget() {
  drafts.clear();
  columns.clear();
  joins.clear();
  probeSql.clear();
  probeRan.clear();
  tables = null;
}

const id = (about: string, key: string) => `c-${about.replace(/[^\w]/g, '-')}-${key}`;

async function parsed(name: string, args: unknown[] = []): Promise<Reply> {
  const reply = await deps.call(name, args);
  return JSON.parse(reply.reply as string) as Reply;
}

function ensureTables() {
  if (tables !== null) return;
  tables = 'loading';
  void parsed('describe_names').then((reply) => {
    tables = reply.ok ? (reply.tables as string[]) : [];
    const list = document.getElementById('dl-tables');
    if (list) list.replaceChildren(...(tables as string[]).map((name) => Object.assign(document.createElement('option'), { value: name })));
  }, () => { tables = null; });
}

function columnsOf(table: string): Column[] | null {
  const key = table.trim().toUpperCase();
  if (!key) return null;
  const held = columns.get(key);
  if (held === 'loading') return null;
  if (held) return held;
  columns.set(key, 'loading');
  void parsed('describe_columns', [JSON.stringify({ table })]).then((reply) => {
    columns.set(key, reply.ok ? (reply.columns as Column[]) : []);
    deps.render();
  }, () => columns.delete(key));
  return null;
}

function joinsOf(table: string): Join[] | null {
  const key = table.trim().toUpperCase();
  if (!key) return null;
  const held = joins.get(key);
  if (held === 'loading') return null;
  if (held) return held;
  joins.set(key, 'loading');
  void parsed('describe_joins', [JSON.stringify({ table })]).then((reply) => {
    joins.set(key, reply.ok ? (reply.joins as Join[]) : []);
    deps.render();
  }, () => joins.delete(key));
  return null;
}

// Which forms suit an attribute.
export function formsFor(item: CorrectionItem): string[] {
  // A part for which the page found no table can only be given one.
  if (item.attribute === 'rows') return item.bound === false ? ['rows'] : ['rows', 'filter'];
  const forms = ['column'];
  if (item.link) {
    forms.push('path', 'pair');
    if (item.link === 'role_anaesthetic.anaesthetic_key') forms.push('window');
    return forms;
  }
  const type = item.type ?? '';
  if (type === 'flag' || type === 'flag_or_empty') forms.push('flag');
  if (type === 'number' || type === 'whole') forms.push('scale');
  if (type === 'date') forms.push('date');
  if (type === 'text') forms.push('trim', 'joined');
  if (type === 'key') forms.push('trim');
  if (type === 'kind') forms.push('codes');
  return forms;
}

function draftOf(item: CorrectionItem): Draft {
  let draft = drafts.get(item.about);
  if (!draft) {
    draft = { form: formsFor(item)[0], f: { before: '15', after: '15', separator: ' ', offset: '0' }, steps: [blankStep()], codes: [['', '']],
              preview: null, problem: '', report: null, reason: '', although: false, valuesSql: '', valuesName: '', valueRows: [] };
    drafts.set(item.about, draft);
  }
  return draft;
}

function blankStep(): Step {
  return { from: '', table: '', to: '', from2: '', to2: '' };
}

const view = (about: string) => about.split(/[ .]/)[0];

// The correction as the worker reads it, or null while the form is not yet filled.
function correctionOf(item: CorrectionItem, draft: Draft): Record<string, unknown> | null {
  const f = draft.f;
  const about = item.about;
  const values = (f.values ?? '').split(',').map((v) => v.trim()).filter(Boolean);
  switch (draft.form) {
    case 'column':
      return f.replacement ? { form: 'column', about, replacement: f.replacement } : null;
    case 'rows':
      return f.replacement ? { form: 'rows', about, table: f.replacement.split(/[.,\s]/)[0] } : null;
    case 'flag':
      return f.table && f.column && values.length ? { form: 'derived', about, table: f.table, column: f.column, derive: { form: 'flag', values } } : null;
    case 'scale':
      return f.table && f.column && f.factor ? { form: 'derived', about, table: f.table, column: f.column,
        derive: { form: 'scale', factor: Number(f.factor), offset: Number(f.offset || 0) } } : null;
    case 'date':
    case 'trim':
      return f.table && f.column ? { form: 'derived', about, table: f.table, column: f.column, derive: { form: draft.form } } : null;
    case 'filter':
      return f.table && f.column && values.length ? { form: 'filter', about, table: f.table, column: f.column, values } : null;
    case 'path':
    case 'pair': {
      const steps = draft.form === 'pair' ? draft.steps.slice(0, 1) : draft.steps;
      if (!steps.every((s) => s.from && s.table && s.to) || !f.final) return null;
      if (draft.form === 'pair' && !(steps[0].from2 && steps[0].to2)) return null;
      return { form: draft.form, about, column: f.final,
        steps: steps.map((s) => ({ from: s.from, table: s.table, to: s.to, ...(draft.form === 'pair' ? { also: [[s.from2, s.to2]] } : {}) })) };
    }
    case 'window':
      return { form: 'window', about, table: f.table ?? '', column: f.column ?? '', key: f.key ?? '', before: f.before ?? '0', after: f.after ?? '0' };
    case 'joined':
      return f.on_table && f.on_column && f.rows_table && f.link && f.text && f.order
        ? { form: 'joined', about, on_table: f.on_table, on_column: f.on_column, table: f.rows_table, link: f.link, text: f.text, order: f.order,
            separator: f.separator ?? ' ' } : null;
    case 'codes': {
      const chosen: Record<string, string> = {};
      for (const [code, kind] of draft.codes) if (code.trim() && kind) chosen[code.trim()] = kind;
      return Object.keys(chosen).length ? { form: 'codes', about, chosen } : null;
    }
    default:
      return null;
  }
}

async function preview(item: CorrectionItem) {
  const draft = draftOf(item);
  draft.report = null;
  const correction = correctionOf(item, draft);
  if (!correction) {
    draft.preview = null;
    draft.problem = '';
    deps.render();
    return;
  }
  try {
    const reply = await parsed('describe_correction_preview', [JSON.stringify(correction)]);
    if (reply.ok) {
      draft.preview = reply.preview as Draft['preview'];
      draft.problem = '';
    } else {
      draft.preview = null;
      draft.problem = reply.problem ?? d.failed;
    }
  } catch {
    draft.problem = d.failed;
  }
  deps.render();
}

// Fields.

function labelled(about: string, key: string, label: string, control: HTMLElement) {
  const box = deps.el('div', undefined, 'field');
  const caption = deps.el('label', label);
  caption.htmlFor = id(about, key);
  control.id = id(about, key);
  box.append(caption, control);
  return box;
}

function textField(item: CorrectionItem, key: string, label: string, kind = 'text') {
  const draft = draftOf(item);
  const input = deps.el('input');
  input.type = kind;
  input.spellcheck = false;
  input.autocomplete = 'off';
  input.value = draft.f[key] ?? '';
  input.addEventListener('input', () => { draft.f[key] = input.value; });
  input.addEventListener('change', () => { draft.f[key] = input.value; void preview(item); });
  return labelled(item.about, key, label, input);
}

function tableField(item: CorrectionItem, key: string, label: string, after?: () => void) {
  ensureTables();
  const draft = draftOf(item);
  const input = deps.el('input');
  input.type = 'text';
  input.spellcheck = false;
  input.autocomplete = 'off';
  input.setAttribute('list', 'dl-tables');
  input.value = draft.f[key] ?? '';
  input.addEventListener('input', () => { draft.f[key] = input.value; });
  input.addEventListener('change', () => {
    draft.f[key] = input.value.trim();
    after?.();
    void preview(item);
  });
  return labelled(item.about, key, label, input);
}

function columnSelect(table: string, value: string, onChange: (value: string) => void) {
  const select = deps.el('select');
  const known = table ? columnsOf(table) : null;
  const first = deps.el('option', known ? c.chooseColumn : c.chooseTable);
  first.value = '';
  select.append(first);
  for (const column of known ?? []) {
    const option = deps.el('option', `${column.name}${column.type ? ` (${column.type})` : ''}${column.present === false ? ', not in the database' : ''}`);
    option.value = column.name;
    select.append(option);
  }
  if (value && !(known ?? []).some((col) => col.name === value)) {
    const option = deps.el('option', value);
    option.value = value;
    select.append(option);
  }
  select.value = value;
  select.addEventListener('change', () => onChange(select.value));
  return select;
}

function columnField(item: CorrectionItem, key: string, table: string, label: string) {
  const draft = draftOf(item);
  const select = columnSelect(table, draft.f[key] ?? '', (value) => {
    draft.f[key] = value;
    void preview(item);
  });
  return labelled(item.about, key, label, select);
}

// A query to copy and run, with Run on the invented hospital beside the copy while the invented dictionary is in use,
// and its SQL behind a disclosure.
function queryBlock(sql: string, copyLabel: string, run?: () => void) {
  const box = deps.el('div', undefined, 'query-block');
  const copy = deps.el('div', undefined, 'actions');
  copy.append(deps.button(copyLabel, () => void navigator.clipboard?.writeText(sql).catch(() => undefined), 'secondary copy-query'));
  if (run && deps.invented()) copy.append(deps.button(d.invented.run, run, 'run-invented'));
  const details = deps.el('details', undefined, 'query');
  details.append(deps.el('summary', d.showQuery), deps.el('pre', sql, 'code'));
  box.append(copy, details);
  return box;
}

function valuesHelper(item: CorrectionItem, box: HTMLElement, label: string) {
  const draft = draftOf(item);
  box.append(textField(item, 'values', label));
  box.append(deps.el('p', c.valuesNote, 'note'));
  const actions = deps.el('div', undefined, 'actions');
  actions.append(deps.button(c.valuesWrite, async () => {
    if (!draft.f.table || !draft.f.column) return;
    deps.setBusy(true);
    try {
      const reply = await parsed('describe_values_query', [JSON.stringify({ about: item.about, table: draft.f.table, column: draft.f.column,
        year: deps.year(), step: deps.step })]);
      if (reply.ok) {
        draft.valuesSql = reply.sql as string;
        draft.valuesName = reply.name as string;
        draft.valueRows = deps.values(draft.valuesName) ?? [];
        draft.problem = '';
      } else draft.problem = reply.problem ?? d.failed;
    } catch {
      draft.problem = d.failed;
    }
    deps.setBusy(false);
    deps.render();
  }, 'secondary'));
  box.append(actions);
  if (draft.valuesSql) {
    box.append(queryBlock(draft.valuesSql, c.valuesCopy, async () => {
      deps.setBusy(true);
      try {
        const reply = await deps.runInvented({ query: draft.valuesName, read: 'values' });
        if (reply.ok) {
          draft.valueRows = (reply.receipt as { values: Draft['valueRows'] }).values;
          draft.problem = '';
          draft.ran = true;
        } else draft.problem = reply.problem ?? d.invented.failed;
      } catch {
        draft.problem = d.invented.failed;
      }
      deps.setBusy(false);
      deps.render();
    }));
    const [caption, area] = deps.pasteBox(`values-${item.about}`, c.valuesPasteLabel);
    const read = deps.el('div', undefined, 'actions');
    read.append(deps.button(c.valuesRead, async () => {
      try {
        const reply = await parsed('describe_values_read', [JSON.stringify({ name: draft.valuesName, text: area.value })]);
        if (reply.ok) {
          draft.valueRows = reply.values as Draft['valueRows'];
          draft.ran = false;
          area.value = '';
          draft.problem = '';
        } else draft.problem = reply.problem ?? d.failed;
      } catch {
        draft.problem = d.failed;
      }
      deps.render();
    }));
    box.append(caption, area, read);
  }
  if (draft.valueRows.length) {
    const fieldset = deps.el('fieldset', undefined, 'values');
    if (draft.ran) fieldset.append(deps.el('p', d.invented.receipt(c.valuesRan(draft.valueRows.length)), 'status good'));
    fieldset.append(deps.el('legend', c.valuesTick));
    const chosen = new Set((draft.f.values ?? '').split(',').map((v) => v.trim()).filter(Boolean));
    for (const row of draft.valueRows) {
      const option = deps.el('label', undefined, 'option');
      const tick = deps.el('input');
      tick.type = 'checkbox';
      tick.value = row.value;
      tick.id = id(item.about, `value-${draft.valueRows.indexOf(row)}`);
      option.htmlFor = tick.id;
      tick.checked = chosen.has(row.value);
      tick.addEventListener('change', () => {
        if (tick.checked) chosen.add(row.value);
        else chosen.delete(row.value);
        draft.f.values = [...chosen].join(', ');
        void preview(item);
      });
      option.append(tick, document.createTextNode(` ${c.valueRows(row.value, row.rows)}`));
      fieldset.append(option);
    }
    box.append(fieldset);
  }
}

function stepsFields(item: CorrectionItem, box: HTMLElement, pair: boolean) {
  const draft = draftOf(item);
  const base = deps.base(view(item.about)) ?? '';
  const steps = pair ? draft.steps.slice(0, 1) : draft.steps;
  let at = base;
  steps.forEach((step, i) => {
    const start = at;
    const part = deps.el('fieldset', undefined, 'step');
    part.dataset.step = String(i + 1);
    part.append(deps.el('legend', c.stepHeading(i + 1)));
    const suggested = joinsOf(start);
    if (suggested && suggested.length) {
      const select = deps.el('select');
      const none = deps.el('option', c.suggestedNone);
      none.value = '';
      select.append(none);
      suggested.forEach((join, n) => {
        const option = deps.el('option', `${start}.${join.from} = ${join.table}.${join.to}${join.repeats ? `, which ${c.suggestedRepeats}` : ''}`);
        option.value = String(n);
        select.append(option);
      });
      select.addEventListener('change', () => {
        const join = suggested[Number(select.value)];
        if (!select.value || !join) return;
        Object.assign(step, { from: join.from, table: join.table, to: join.to });
        draft.steps = draft.steps.slice(0, i + 1);
        void preview(item);
      });
      part.append(labelled(item.about, `suggest-${i}`, c.suggestedLabel(start), select));
    }
    part.append(labelled(item.about, `from-${i}`, c.stepFrom(start), columnSelect(start, step.from, (v) => { step.from = v; void preview(item); })));
    const to = deps.el('input');
    to.type = 'text';
    to.spellcheck = false;
    to.autocomplete = 'off';
    to.setAttribute('list', 'dl-tables');
    to.value = step.table;
    to.addEventListener('input', () => { step.table = to.value.trim(); });
    to.addEventListener('change', () => { step.table = to.value.trim(); step.to = ''; void preview(item); });
    ensureTables();
    part.append(labelled(item.about, `to-${i}`, c.stepTo, to));
    part.append(labelled(item.about, `tocol-${i}`, c.stepToColumn, columnSelect(step.table, step.to, (v) => { step.to = v; void preview(item); })));
    if (pair) {
      part.append(labelled(item.about, `from2-${i}`, c.stepSecondFrom(start), columnSelect(start, step.from2, (v) => { step.from2 = v; void preview(item); })));
      part.append(labelled(item.about, `to2-${i}`, c.stepSecondTo, columnSelect(step.table, step.to2, (v) => { step.to2 = v; void preview(item); })));
    }
    box.append(part);
    at = step.table;
  });
  if (!pair) {
    const actions = deps.el('div', undefined, 'actions');
    if (steps.length < 3) actions.append(deps.button(c.addStep, () => { draft.steps.push(blankStep()); deps.render(); }, 'secondary'));
    if (steps.length > 1) actions.append(deps.button(c.removeStep, () => { draft.steps.pop(); void preview(item); }, 'secondary'));
    box.append(actions);
  }
  box.append(columnField(item, 'final', at, c.finalColumn(at || c.chooseTable)));
}

function codesFields(item: CorrectionItem, box: HTMLElement) {
  const draft = draftOf(item);
  const kinds = deps.kinds(item.about).filter((k) => k !== 'other');
  draft.codes.forEach((pair, i) => {
    const line = deps.el('div', undefined, 'code-line');
    const code = deps.el('input');
    code.type = 'text';
    code.spellcheck = false;
    code.autocomplete = 'off';
    code.value = pair[0];
    code.addEventListener('input', () => { pair[0] = code.value; });
    code.addEventListener('change', () => { pair[0] = code.value; void preview(item); });
    const kind = deps.el('select');
    const none = deps.el('option', d.notChosen);
    none.value = '';
    const meanings = deps.meanings(item.about);
    kind.append(none, ...kinds.map((k) => Object.assign(deps.el('option', d.kindOption(k, meanings[k])), { value: k })));
    kind.value = pair[1];
    kind.addEventListener('change', () => { pair[1] = kind.value; void preview(item); });
    line.append(labelled(item.about, `code-${i}`, c.codeLabel, code), labelled(item.about, `kind-${i}`, c.kindLabel, kind));
    box.append(line);
  });
  const actions = deps.el('div', undefined, 'actions');
  actions.append(deps.button(c.addCode, () => { draft.codes.push(['', '']); deps.render(); }, 'secondary'));
  box.append(actions);
}

function formFields(item: CorrectionItem, box: HTMLElement) {
  const draft = draftOf(item);
  const f = draft.f;
  const base = deps.base(view(item.about)) ?? '';
  const anaesthetic = deps.anaestheticTable() ?? '';
  switch (draft.form) {
    case 'flag':
      if (!f.table && (item.table || base)) {
        f.table = item.table || base;
        if (item.table && item.column) f.column = item.column;
      }
      box.append(tableField(item, 'table', c.tableLabel, () => { f.column = ''; }), columnField(item, 'column', f.table ?? '', c.columnLabel));
      valuesHelper(item, box, c.flagValuesLabel);
      break;
    case 'filter':
      if (!f.table && base) f.table = base;
      box.append(tableField(item, 'table', c.filterTable, () => { f.column = ''; }), columnField(item, 'column', f.table ?? '', c.filterColumn));
      valuesHelper(item, box, c.valuesLabel);
      break;
    case 'scale':
      box.append(tableField(item, 'table', c.tableLabel, () => { f.column = ''; }), columnField(item, 'column', f.table ?? '', c.columnLabel),
        textField(item, 'factor', c.factorLabel), textField(item, 'offset', c.offsetLabel));
      break;
    case 'date':
    case 'trim':
      box.append(tableField(item, 'table', c.tableLabel, () => { f.column = ''; }), columnField(item, 'column', f.table ?? '', c.columnLabel));
      break;
    case 'path':
      stepsFields(item, box, false);
      break;
    case 'pair':
      stepsFields(item, box, true);
      break;
    case 'window':
      box.append(deps.el('p', c.windowRule, 'note window-rule'));
      if (!f.table && base) f.table = base;
      box.append(tableField(item, 'table', c.sharedTable, () => { f.column = ''; }), columnField(item, 'column', f.table ?? '', c.sharedColumn),
        columnField(item, 'key', anaesthetic, c.anaestheticKey(anaesthetic)),
        textField(item, 'before', c.beforeLabel, 'number'), textField(item, 'after', c.afterLabel, 'number'));
      break;
    case 'joined':
      if (!f.on_table && base) f.on_table = base;
      box.append(tableField(item, 'on_table', c.onTable, () => { f.on_column = ''; }), columnField(item, 'on_column', f.on_table ?? '', c.onColumn),
        tableField(item, 'rows_table', c.rowsTable, () => { f.link = ''; f.text = ''; f.order = ''; }),
        columnField(item, 'link', f.rows_table ?? '', c.linkColumn), columnField(item, 'text', f.rows_table ?? '', c.textColumn),
        columnField(item, 'order', f.rows_table ?? '', c.orderColumn), textField(item, 'separator', c.separatorLabel));
      break;
    case 'codes':
      codesFields(item, box);
      break;
    default:
      break;
  }
}

// The panel under Choose another. The translation of a column's codes after Yes has one form only, so it offers no
// choice of the kind of change.
export function panel(item: CorrectionItem, plain: () => HTMLElement, translate = false): HTMLElement {
  const draft = draftOf(item);
  const box = deps.el('div', undefined, 'correction');
  box.dataset.form = draft.form;
  const forms = formsFor(item);
  if (!translate) box.append(...kindOfChange(item, draft, forms));
  if (!document.getElementById('dl-tables')) {

    const list = deps.el('datalist');
    list.id = 'dl-tables';
    document.body.append(list);
  }
  const fields = deps.el('div', undefined, 'fields');
  if (draft.form === 'column' || draft.form === 'rows') fields.append(plain());
  else formFields(item, fields);
  box.append(fields);
  if (draft.problem) box.append(deps.el('p', `${c.problemLabel} ${draft.problem}`, 'status problem problem-note'));
  if (!draft.preview) {
    box.append(deps.el('p', c.incomplete, 'note'));
    return box;
  }
  box.append(deps.el('p', c.sentenceLabel, 'label sentence-label'), deps.el('p', draft.preview.sentence, 'correction-sentence'));
  const sql = deps.el('details');
  sql.append(deps.el('summary', c.sqlLabel), deps.el('pre', draft.preview.sql, 'code correction-sql'));
  box.append(sql);
  box.append(deps.el('p', c.checkWhat, 'note'));
  const actions = deps.el('div', undefined, 'actions');
  const correction = correctionOf(item, draft)!;
  actions.append(deps.button(c.checkButton, async () => {
    checking = item.about;
    deps.setBusy(true);
    deps.render();
    try {
      const reply = await parsed('describe_correction_check', [JSON.stringify(correction)]);
      if (reply.ok) draft.report = reply.report as Report;
      else draft.problem = reply.problem ?? d.failed;
    } catch {
      draft.problem = d.failed;
    }
    checking = '';
    deps.setBusy(false);
    deps.render();
  }, draft.report ? 'secondary' : ''));
  box.append(actions);
  if (checking === item.about) box.append(deps.el('p', c.checking, 'status working-note'));
  if (draft.report) box.append(reportBox(draft.report, item, correction));
  return box;
}

// The choice of the kind of change, with a sentence on when each suits.
function kindOfChange(item: CorrectionItem, draft: Draft, forms: string[]): HTMLElement[] {
  const select = deps.el('select', undefined, 'correction-form');
  for (const form of forms) select.append(Object.assign(deps.el('option', c.forms[form]), { value: form }));
  select.value = draft.form;
  select.addEventListener('change', () => {
    draft.form = select.value;
    draft.preview = null;
    draft.report = null;
    draft.problem = '';
    void preview(item);
  });
  return [deps.el('p', c.intro, 'do'), labelled(item.about, 'form', c.formLabel, select), deps.el('p', c.formWhat[draft.form] ?? '', 'note')];
}

// A list of findings, each linked to the row of the column that it concerns where the check names one.
function list(items: string[], className = 'findings', about: Record<string, string> = {}) {
  const node = deps.el('ul', undefined, className);
  for (const text of items) {
    const entry = deps.el('li');
    const target = about[text];
    if (target) {
      const link = deps.el('a', text, 'finding-link');
      link.href = '#step-6';
      link.dataset.about = target;
      link.addEventListener('click', (event) => {
        event.preventDefault();
        deps.goTo(target, text);
      });
      // Each finding says what to do about it at its row.
      entry.append(link);
      if (className === 'findings') entry.append(deps.el('span', ` ${c.findingDo}`, 'finding-do'));
    } else entry.textContent = text;
    node.append(entry);
  }
  return node;
}

// A column or table chosen in place of the proposal: its sentence is shown, and the person then chooses Check this
// change, as in every other form, before it can be kept or discarded.
export async function useAlternative(item: CorrectionItem, chosen: string) {
  const draft = draftOf(item);
  draft.form = item.attribute === 'rows' ? 'rows' : 'column';
  draft.f.replacement = chosen;
  draft.report = null;
  draft.although = false;
  await preview(item);
}

// Starts the translation of a column whose source holds codes, after Yes: the 1-or-0 form, or the translation of
// codes, filled in with the column and any values that the proposer guessed.
export function startTranslation(item: CorrectionItem) {
  if (drafts.has(item.about) || !item.coding) return;
  const draft = draftOf(item);
  draft.form = item.coding.form === 'flag' ? 'flag' : 'codes';
  if (draft.form === 'flag') {
    draft.f.table = item.table ?? '';
    draft.f.column = item.column ?? '';
    draft.f.values = item.coding.values.join(', ');
    void preview(item);
  }
}

function reportBox(report: Report, item: CorrectionItem, correction: Record<string, unknown>) {
  const draft = draftOf(item);
  const box = deps.el('div', undefined, `check-report ${report.passed ? 'passed' : 'failed'}`);
  box.append(deps.el('p', report.sentence, `status ${report.passed ? 'good' : 'problem'}`));
  const about = report.about ?? {};
  if (report.problems.length) box.append(list(report.problems, 'findings', about));
  if (report.remaining.length && report.problems.length) box.append(deps.el('p', c.remainingLabel), list(report.remaining, 'findings', about));
  else if (report.remaining.length) box.append(list(report.remaining, 'findings', about));
  if (report.mended) box.append(deps.el('p', report.mended, 'status good'));
  if (report.notes.length) box.append(deps.el('p', c.notesLabel), list(report.notes, 'notes', about));
  box.append(deps.el('p', c.checkSeconds(report.seconds), 'note'));
  if (report.passed) box.append(deps.el('p', c.passedMeans, 'note passed-means'));
  if (!report.passed) {
    const option = deps.el('label', undefined, 'option');
    const tick = deps.el('input');
    tick.type = 'checkbox';
    tick.className = 'although';
    tick.checked = draft.although;
    tick.addEventListener('change', () => { draft.although = tick.checked; deps.render(); });
    option.append(tick, document.createTextNode(` ${c.although}`));
    box.append(option);
    if (draft.although) {
      const reason = deps.el('input');
      reason.type = 'text';
      reason.value = draft.reason;
      reason.addEventListener('input', () => { draft.reason = reason.value; });
      box.append(labelled(item.about, 'reason', c.reasonLabel, reason));
    }
  }
  const actions = deps.el('div', undefined, 'actions');
  // After a failed check, only Discard is offered until the box is ticked, and then the keep with its reason.
  if (report.passed || draft.although) actions.append(deps.button(report.passed ? c.keep : c.keepAlthough, async () => {
    deps.setBusy(true);
    try {
      const reply = await deps.ask('describe_correction_keep', [JSON.stringify({ correction, although: draft.although, reason: draft.reason })]);
      if (reply.ok) {
        drafts.delete(item.about);
        deps.close(item.about);
      } else draft.problem = reply.problem ?? d.failed;
    } catch {
      draft.problem = d.failed;
    }
    deps.setBusy(false);
    deps.render();
  }, 'answer-keep'));
  actions.append(deps.button(c.discard, () => {
    drafts.delete(item.about);
    deps.close(item.about);
    deps.render();
  }, 'secondary'));
  box.append(actions);
  return box;
}

// A kept correction, beside its binding, with its probe.
export function kept(item: CorrectionItem): HTMLElement | null {
  const held = item.correction;
  if (!held) return null;
  const box = deps.el('div', undefined, 'kept-correction');
  box.append(deps.el('p', held.says, 'correction-sentence'));
  box.append(deps.el('p', c.kept(d.day(item.date ?? ''), held.check), held.check.startsWith('passed') ? 'status good' : 'status problem'));
  if (held.reason) box.append(deps.el('p', c.keptReason(held.reason), 'note'));
  if (!held.probe) {
    box.append(deps.el('p', c.probeNone, 'note'));
    return box;
  }
  const probe = deps.el('div', undefined, 'probe');
  probe.dataset.probe = held.probe;
  const what = held.probe === 'flag' && item.type === 'flag' ? c.probeWhat.flag_two : c.probeWhat[held.probe] ?? '';
  probe.append(deps.el('p', what.replace('{year}', String(deps.year())), 'note'));
  const actions = deps.el('div', undefined, 'actions');
  actions.append(deps.button(c.probeWrite, async () => {
    try {
      const reply = await parsed('describe_probe_query', [JSON.stringify({ about: item.about, year: deps.year(), step: deps.step })]);
      if (reply.ok) probeSql.set(item.about, reply.sql as string);
    } catch { /* shown below */ }
    deps.render();
  }, 'secondary'));
  probe.append(actions);
  const sql = probeSql.get(item.about);
  if (sql) {
    const status = deps.el('p', '', 'status problem');
    status.hidden = true;
    const copy = queryBlock(sql, c.probeCopy, async () => {
      deps.setBusy(true);
      try {
        const reply = await deps.runInvented({ query: `probe-${item.about.replace(/[^\w]+/g, '-').replace(/^-+|-+$/g, '')}`, read: 'probe', about: item.about });
        probeRan.set(item.about, reply.ok ? '' : reply.problem ?? d.invented.failed);
      } catch {
        probeRan.set(item.about, d.invented.failed);
      }
      deps.setBusy(false);
      deps.render();
    });
    const [caption, area] = deps.pasteBox(`probe-${item.about}`, c.probePasteLabel);
    const read = deps.el('div', undefined, 'actions');
    const ran = probeRan.get(item.about);
    if (ran) {
      status.hidden = false;
      status.textContent = ran;
    }
    read.append(deps.button(c.probeRead, async () => {
      deps.setBusy(true);
      try {
        const reply = await deps.ask('describe_probe_read', [JSON.stringify({ about: item.about, text: area.value })]);
        probeRan.delete(item.about);
        if (!reply.ok) {
          status.hidden = false;
          status.textContent = reply.problem ?? d.failed;
        }
      } catch {
        status.hidden = false;
        status.textContent = d.failed;
      }
      deps.setBusy(false);
    }));
    probe.append(copy, caption, area, read, status);
  }
  if (held.probed) {
    if (probeRan.get(item.about) === '') probe.append(deps.el('p', d.invented.receipt(c.probeRan), 'status good'));
    probe.append(deps.grid(held.probed.columns, held.probed.rows));
    if (held.findings.length) probe.append(list(held.findings));
  }
  box.append(probe);
  return box;
}

// The check of the map as it stands, at the head of step 6.
export async function checkModel(out: HTMLElement) {
  out.replaceChildren(deps.el('p', c.checking, 'status working-note'));
  try {
    const reply = await parsed('describe_model_check');
    out.replaceChildren();
    if (!reply.ok) {
      out.append(deps.el('p', reply.problem ?? d.failed, 'status problem'));
      return;
    }
    const report = reply.report as Report;
    out.append(deps.el('p', report.sentence, `status ${report.passed ? 'good' : 'problem'}`));
    if (report.problems.length) out.append(list(report.problems, 'findings', report.about ?? {}));
    if (report.notes.length) out.append(deps.el('p', c.notesLabel), list(report.notes, 'notes', report.about ?? {}));
    out.append(deps.el('p', c.checkSeconds(report.seconds), 'note'));
  } catch {
    out.replaceChildren(deps.el('p', d.failed, 'status problem'));
  }
}
