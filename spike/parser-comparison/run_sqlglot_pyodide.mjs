// Runs run_sqlglot.py unchanged inside Pyodide, to confirm that the same
// analysis works in the browser's Python runtime and to time it.
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadPyodide } from 'pyodide';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..', '..');

const t0 = performance.now();
const chunks = [];
const pyodide = await loadPyodide({ stdout: (line) => chunks.push(line) });
const t1 = performance.now();

const wheel = readFileSync(join(here, 'wheels', 'sqlglot-30.21.0-py3-none-any.whl'));
pyodide.unpackArchive(new Uint8Array(wheel), 'wheel');
pyodide.FS.mkdirTree('/work/fixtures');
pyodide.FS.mount(pyodide.FS.filesystems.NODEFS, { root: join(repo, 'fixtures') }, '/work/fixtures');
pyodide.FS.mkdirTree('/work/spike/parser-comparison');
pyodide.FS.writeFile('/work/spike/parser-comparison/run_sqlglot.py', readFileSync(join(here, 'run_sqlglot.py')));
pyodide.runPython('import sqlglot');
const t2 = performance.now();

pyodide.runPython(`
import runpy, sys
runpy.run_path('/work/spike/parser-comparison/run_sqlglot.py', run_name='__main__')
print()
sys.stdout.flush()
`);
const t3 = performance.now();

const result = JSON.parse(chunks.join('\n'));
result.parser += ` under Pyodide ${pyodide.version}`;
result.timings = {
  loadPyodideMs: Math.round(t1 - t0),
  importSqlglotMs: Math.round(t2 - t1),
  analyseAllMs: Math.round(t3 - t2),
};
console.log(JSON.stringify(result, null, 1));
