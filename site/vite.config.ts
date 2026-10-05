import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';

const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));

export default defineConfig({
  // Relative paths, so that the site works from any folder of a static host.
  base: './',
  define: { __VERSION__: JSON.stringify(version) },
  build: { target: 'es2022', rollupOptions: { input: { index: 'index.html', sandbox: 'sandbox.html', github: 'github.html' } } },
});
