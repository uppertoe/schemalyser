import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  // Every reaction of the page waits on the Python worker, which Firefox runs more slowly than Chromium.
  expect: { timeout: 20_000 },
  use: { baseURL: 'http://localhost:4173/', acceptDownloads: true },
  // The safeguards depend on how each browser treats a worker started from a blob, so all three are tested.
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
  webServer: { command: 'npm run preview', url: 'http://localhost:4173/', reuseExistingServer: true },
});
