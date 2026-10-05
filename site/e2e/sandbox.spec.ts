import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { setOnline } from './network';
import { sandboxStrings as s } from '../src/sandbox-strings';
import { strings } from '../src/strings';

const fixtures = fileURLToPath(new URL('../../fixtures/', import.meta.url));
const shots = process.env.SCREENSHOTS;

// WebKit does not report the content security policy to a worker started from a blob, so the page
// cannot confirm that the policy holds there and refuses to start. See webkit.spec.ts.
test.skip(({ browserName }) => browserName === 'webkit', 'The page refuses to start in WebKit.');

test('the inventory from the first page builds a sandbox in which SQL and the past requests run', async ({ page, context, browserName }) => {
  // Make the inventory with the first page, exactly as a user would.
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 90_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#t-files-sentence')).toBeVisible({ timeout: 60_000 });
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
  const inventory = await download.path();
  await setOnline(page, context, browserName, true);

  // The sandbox.
  const violations: string[] = [];
  page.on('console', (message) => {
    if (message.text().includes('Content Security Policy')) violations.push(message.text());
  });
  await page.goto('./sandbox.html');
  await expect(page.getByText(s.intro)).toBeVisible();
  await expect(page.locator('#build')).toBeDisabled();
  await expect(page.locator('#t-loading')).toBeHidden({ timeout: 120_000 });
  await expect(page.locator('#folder')).toBeDisabled();

  // A file that is not an inventory is refused.
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await page.locator('#inventory').setInputFiles(fixtures + 'planted-values.txt');
  await page.locator('#build').click();
  await expect(page.getByText(s.inventoryError)).toBeVisible({ timeout: 30_000 });

  // The real inventory builds the database.
  await page.locator('#inventory').setInputFiles(inventory);
  await page.locator('#build').click();
  await expect(page.locator('#t-built')).toHaveText('Schemalyser has built 20 tables containing 10,000 rows.', {
    timeout: 60_000,
  });
  await expect(page.getByText(s.invented)).toBeVisible();

  // A query in T-SQL, with a variable, a temp table and a join, runs and returns rows.
  await page.locator('#sql').fill(`DECLARE @n int = 3;
SELECT TOP 7 tc.CASE_KEY, ar.ANAES_KEY INTO #c
FROM dbo.THEATRE_CASE tc WITH (NOLOCK) JOIN dbo.ANAES_RECORD ar ON ar.CASE_KEY = tc.CASE_KEY;
SELECT c.CASE_KEY, c.ANAES_KEY, @n AS n FROM #c c;`);
  await page.locator('#run').click();
  await expect(page.locator('#t-query-message')).toHaveText(s.returned(7));
  await expect(page.locator('#query-table tbody tr')).toHaveCount(7);
  await expect(page.locator('#query-table th')).toHaveText(['CASE_KEY', 'ANAES_KEY', 'n']);
  if (shots) await page.screenshot({ path: `${shots}/5-sandbox-query.png`, fullPage: true });

  // More rows than the page shows.
  await page.locator('#sql').fill('SELECT * FROM THEATRE_CASE');
  await page.locator('#run').click();
  await expect(page.locator('#t-query-message')).toHaveText(s.returnedFirst(500, 200));

  // What the sandbox cannot do is said plainly.
  await page.locator('#sql').fill('CREATE PROCEDURE p AS BEGIN SELECT 1 END');
  await page.locator('#run').click();
  await expect(page.locator('#t-query-message')).toHaveText(s.unsupported);
  await page.locator('#sql').fill('SELECT FROM WHERE');
  await page.locator('#run').click();
  await expect(page.locator('#t-query-message')).toHaveText(s.unreadable);
  await page.locator('#sql').fill('SELECT NO_SUCH_COLUMN FROM THEATRE_CASE');
  await page.locator('#run').click();
  await expect(page.locator('#t-query-message')).toHaveText(s.databaseError);
  await expect(page.locator('#t-database-message')).toContainText('NO_SUCH_COLUMN');

  // The past requests are accepted only offline.
  await expect(page.locator('#run-requests')).toBeDisabled();
  await setOnline(page, context, browserName, false);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#run-requests').click();
  await expect(page.locator('#t-requests-sentence')).toHaveText(
    'Of 15 requests, 4 ran and returned rows, 8 ran and returned no rows, and 3 could not be run in the sandbox.',
    { timeout: 60_000 },
  );
  await expect(page.locator('#requests-table tbody tr')).toHaveCount(15);
  if (shots) await page.screenshot({ path: `${shots}/6-sandbox-requests.png`, fullPage: true });

  // When the network returns, the page lets go of the file names.
  await setOnline(page, context, browserName, true);
  await expect(page.locator('#requests-result')).toBeHidden();
  await expect(page.locator('#requests-table tbody tr')).toHaveCount(0);
  expect(violations).toEqual([]);
});
