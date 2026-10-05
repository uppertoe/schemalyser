import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

// The invented world arranged in the layout of the boundary's state folder, and the boundary
// command's own outputs for it, so that the page's checklist can be compared with the command's.

export const fixtures = fileURLToPath(new URL('../../fixtures/', import.meta.url));
const core = fileURLToPath(new URL('../../core/', import.meta.url));

// A request from the data team that joins an airway or line to its anaesthetic, which no invented
// request does. Adding it should answer the open join of AIRWAY_DEVICE.ANAES_KEY to ANAES_RECORD.ANAES_KEY.
export const EXTRA_REQUEST = {
  name: 'airways_by_anaesthetic.sql',
  text: `-- Airways and lines with the anaesthetic during which each was placed.
SELECT a.ANAES_KEY, a.ANAES_START_TS, d.DEVICE_KIND_KEY, d.PLACED_TS
FROM AIRWAY_DEVICE AS d
JOIN ANAES_RECORD AS a ON a.ANAES_KEY = d.ANAES_KEY;
`,
};

export const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });

export function makeState(folder: string, options: { profile?: boolean; sourcePrefix?: string; catalogue?: boolean; checks?: boolean } = {}) {
  mkdirSync(folder, { recursive: true });
  if (options.catalogue !== false) cpSync(fixtures + 'invented-catalogue.csv', join(folder, 'catalogue.csv'));
  cpSync(fixtures + 'invented-site-rules.json', join(folder, 'site-rules.json'));
  if (options.checks !== false) cpSync(fixtures + 'invented-checks.csv', join(folder, 'checks.csv'));
  cpSync(fixtures + 'conversion', join(folder, 'conversion'), { recursive: true });
  cpSync(fixtures + 'targets', join(folder, 'targets'), { recursive: true });
  if (options.profile !== false) cpSync(fixtures + 'profile/invented-core-profile.csv', join(folder, 'core-profile.csv'));
  // A source prefix, as release.json sets it at a hospital, so that the core profile can measure the joins.
  if (options.sourcePrefix) writeFileSync(join(folder, 'conversion', 'release.json'), JSON.stringify({ source_prefix: options.sourcePrefix }));
  return folder;
}

// Every file under a folder, by its path relative to the folder.
export function filesUnder(folder: string) {
  const files: Record<string, Buffer> = {};
  for (const path of walk(folder)) files[relative(folder, path).split('\\').join('/')] = readFileSync(path);
  return files;
}

function runBoundary(state: string, requests: string, out: string) {
  execFileSync(
    'uv',
    ['run', '--quiet', '--with', 'sqlglot==30.21.0', '--with', 'duckdb==1.5.1', 'python', '-m', 'schemalyser.boundary',
      '--state', state, '--requests', requests, '--out', out],
    { cwd: core, stdio: 'pipe' },
  );
  const outputs: Record<string, string> = {};
  for (const [name, bytes] of Object.entries(filesUnder(out))) outputs[name] = bytes.toString('utf8');
  return outputs;
}

// Reads CSV as the core writes it.
export function parseCsv(csv: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < csv.length; i++) {
    const ch = csv[i];
    if (quoted) {
      if (ch === '"' && csv[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') {
      row.push(field);
      field = '';
    } else if (ch === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else field += ch;
  }
  if (field || row.length) rows.push([...row, field]);
  const [header, ...body] = rows;
  return body.map((cells) => Object.fromEntries(header.map((name, i) => [name, cells[i] ?? ''])));
}

export interface Expected {
  state: string;
  targets: string[];
  before: Record<string, string>;
  after: Record<string, string>;
}

// The command's outputs for the invented world, and again with the extra request added.
export function expected(): Expected {
  const base = mkdtempSync(join(tmpdir(), 'schemalyser-boundary-'));
  const state = makeState(join(base, 'state'));
  const requests = join(base, 'requests');
  cpSync(fixtures + 'requests', requests, { recursive: true });
  const before = runBoundary(state, requests, join(base, 'before'));
  writeFileSync(join(requests, EXTRA_REQUEST.name), EXTRA_REQUEST.text);
  const after = runBoundary(state, requests, join(base, 'after'));
  const targets = Object.keys(before)
    .filter((name) => name.startsWith('targets/') && name.endsWith('/checklist.csv'))
    .map((name) => name.split('/')[1])
    .sort();
  return { state, targets, before, after };
}

// The answers to plain queries from the invented stand-in database, as the results grid of SQL Server
// Management Studio would copy them, with a header row.
export function answer(queries: string[]) {
  return execFileSync(
    'uv',
    ['run', '--quiet', '--with', 'sqlglot==30.21.0', '--with', 'duckdb==1.5.1', 'python', '../fixtures/answer_queries.py'],
    { cwd: core, input: JSON.stringify(queries), encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] },
  );
}

export function counts(outputs: Record<string, string>, target: string) {
  const rows = parseCsv(outputs[`targets/${target}/checklist.csv`]);
  // The tally counts the first phase: the blocking items that the answer from the source database rests on.
  const blocking = rows.filter((row) => row.blocking === 'yes' && row.phase === 'source');
  const by = (list: Record<string, string>[], status: string) => list.filter((row) => row.status === status).length;
  return {
    rows,
    total: blocking.length,
    answered: by(blocking, 'answered'),
    statuses: { answered: by(rows, 'answered'), partly: by(rows, 'partly'), open: by(rows, 'open') },
  };
}
