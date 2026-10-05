// Writes dist/checksum.txt: one SHA-256 over every other file in dist, so that a release can be identified.
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const dist = fileURLToPath(new URL('../dist', import.meta.url));
const walk = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });

const overall = createHash('sha256');
for (const path of walk(dist).sort()) {
  const name = relative(dist, path).split('\\').join('/');
  if (name === 'checksum.txt') continue;
  overall.update(`${name}\0${createHash('sha256').update(readFileSync(path)).digest('hex')}\n`);
}
const checksum = overall.digest('hex');
writeFileSync(join(dist, 'checksum.txt'), checksum + '\n');
console.log('checksum', checksum);
