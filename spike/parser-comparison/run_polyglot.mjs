// Parser comparison, Polyglot side.
//
// Reads the invented requests, parses each as T-SQL and writes one JSON object
// per file to stdout: parse outcome, statement kinds, base tables and
// table.column pairs resolved against the invented catalogue.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse, generate, analyzeQuery, ast, Dialect, getVersion } from '@polyglot-sql/sdk';

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'fixtures');
const GO = /^\s*GO\s*;?\s*$/gim;

function loadCatalogue() {
  const lines = readFileSync(join(FIXTURES, 'invented-catalogue.csv'), 'utf8').trim().split('\n').slice(1);
  const catalogue = {};
  for (const line of lines) {
    const [, table, column, , type] = line.split(',');
    (catalogue[table] ??= {})[column] = type;
  }
  return catalogue;
}

function walkFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walkFiles(p) : p.endsWith('.sql') ? [p] : [];
  });
}

const bare = (name) => String(name).split('.').pop().replace(/[\[\]"]/g, '').toUpperCase();

function analyse(sql, catalogue, schema) {
  const out = { ok: true, error: null, statements: 0, kinds: [], tables: new Set(), columns: new Set(), analysisErrors: 0 };
  for (const batch of sql.split(GO)) {
    if (!batch.trim()) continue;
    const parsed = parse(batch, Dialect.TSQL);
    if (!parsed.success) {
      out.ok = false;
      out.error = 'ParseError';
      continue;
    }
    for (const stmt of parsed.ast) {
      out.statements += 1;
      out.kinds.push(ast.getExprType(stmt));
      for (const name of ast.getTableNames(stmt)) {
        if (bare(name) in catalogue) out.tables.add(bare(name));
      }
      const regenerated = generate([stmt], Dialect.TSQL);
      if (!regenerated.success || !regenerated.sql?.length) {
        out.analysisErrors += 1;
        continue;
      }
      const analysed = analyzeQuery(regenerated.sql[0], { dialect: Dialect.TSQL, schema });
      if (!analysed.success) {
        out.analysisErrors += 1;
        continue;
      }
      const refs = [
        ...analysed.analysis.projections.flatMap((p) => p.upstream),
        ...(analysed.analysis.columnUses ?? []).flatMap((u) => u.references),
      ];
      for (const ref of refs) {
        if (ref.sourceKind !== 'table' || !ref.table) continue;
        const t = bare(ref.table);
        const c = bare(ref.column);
        if (catalogue[t]?.[c]) out.columns.add(`${t}.${c}`);
      }
    }
  }
  return { ...out, tables: [...out.tables].sort(), columns: [...out.columns].sort() };
}

const catalogue = loadCatalogue();
const schema = {
  tables: Object.entries(catalogue).map(([name, cols]) => ({
    name,
    columns: Object.entries(cols).map(([c, type]) => ({ name: c, type })),
  })),
};
const files = {};
for (const path of walkFiles(join(FIXTURES, 'requests')).sort()) {
  const rel = relative(join(FIXTURES, 'requests'), path);
  const start = performance.now();
  try {
    files[rel] = analyse(readFileSync(path, 'utf8'), catalogue, schema);
  } catch (e) {
    files[rel] = { ok: false, error: `Threw ${e.constructor.name}`, statements: 0, kinds: [], tables: [], columns: [] };
  }
  files[rel].ms = Math.round((performance.now() - start) * 10) / 10;
}
console.log(JSON.stringify({ parser: `polyglot ${getVersion()}`, files }, null, 1));
