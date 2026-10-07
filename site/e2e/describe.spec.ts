import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { setOnline } from './network';
import { describeStrings as d } from '../src/describe-strings';
import { strings } from '../src/strings';

// The page Describe the record, walked through with the invented dictionary and the invented world: loaded, taken
// offline, the map proposed, the tables and columns query answered from the invented catalogue, some bindings
// confirmed, a list of what is charted and a count pasted, the folder saved as a zip, and the folder chosen again.
const fixtures = fileURLToPath(new URL('../../fixtures/', import.meta.url));
const pass = process.env.DESCRIBE_PASS;

test.skip(({ browserName }) => browserName === 'webkit', 'The page refuses to start in WebKit.');

// The result of the tables and columns query for the invented world, in the ten columns that SQL Server returns, as
// SQL Server Management Studio copies it with its headers. The sizes are invented, and the readings are large.
function tablesResult() {
  const [head, ...lines] = readFileSync(fixtures + 'invented-catalogue.csv', 'utf8').trim().split('\n');
  const rows = lines.map((line) => {
    const cells = line.split(',').map((cell) => cell || 'NULL');
    return [...cells, cells[1] === 'OBS_READING' ? '25000000' : '1200'].join('\t');
  });
  return [[...head.split(','), 'TABLE_ROWS'].join('\t'), ...rows].join('\n');
}

const charted = [
  'code\tcharted\tanaesthetics\tname',
  '52\t4210\t380\tMean arterial pressure, arterial line',
  '51\t2950\t610\tMean arterial pressure, cuff',
  '10\t8800\t640\tHeart rate',
  '77\tNULL\tNULL\tLocal observation',
].join('\n');

const coverage = [
  'start_year\tanaesthetics\twith_patient\twith_birth_date\twith_death_date\ttest_patients\twith_stop\tstop_before_start',
  '2023\t400\t400\t400\t20\t0\t390\t0',
  '2024\t420\t420\t410\t10\t0\t410\t0',
].join('\n');

async function stage(page: Page, name: string) {
  if (!pass) return;
  const { writeFileSync, mkdirSync } = await import('node:fs');
  mkdirSync(pass, { recursive: true });
  writeFileSync(join(pass, `${name}.txt`), await page.locator('body').innerText());
}

async function loadAndGoOffline(page: Page, context: import('@playwright/test').BrowserContext, browserName: string) {
  await page.goto('./describe.html');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 90_000 });
  await expect(page.locator('#dictionary')).toBeDisabled();
  await setOnline(page, context, browserName, false);
  await expect(page.getByText(d.offlineDone)).toBeVisible();
}

async function loadDictionary(page: Page) {
  await page.locator('#dictionary').setInputFiles(fixtures + 'dictionary/invented-dictionary.csv');
  await page.locator('#dictionary-tables').setInputFiles(fixtures + 'dictionary/invented-tables.csv');
  await page.locator('#dictionary-load').click();
  await expect(page.locator('#t-dictionary-status')).toHaveText(
    d.dictionaryReceipt({ tables: 25, columns: 98, described: 98, keyed: 25, skipped: 0 }),
  );
}

test('the record is described, written as a hospital folder and restored from it', async ({ page, context, browserName }) => {
  test.setTimeout(240_000);
  const requestsWhileOffline: string[] = [];
  let offline = false;
  page.on('request', (request) => {
    if (offline && !request.url().startsWith('blob:') && !request.url().startsWith('data:')) requestsWhileOffline.push(request.url());
  });

  // The existing page leads here as its new first step.
  await page.goto('./');
  await expect(page.locator('#a-describe')).toHaveAttribute('href', './describe.html');

  await loadAndGoOffline(page, context, browserName);
  offline = true;
  await stage(page, '1-offline');

  // The dictionary, with its receipt.
  await loadDictionary(page);
  await stage(page, '2-dictionary');

  // The proposal, role by role, with the dictionary's definition beside each attribute.
  await page.locator('#propose').click();
  await expect(page.locator('#t-propose-status')).toContainText('Schemalyser has proposed', { timeout: 60_000 });
  const birth = page.locator('#proposal [data-about="role_patient.birth_date"]');
  await expect(birth).toContainText('PERSON_MASTER.BIRTH_TS');
  await expect(birth).toContainText('The date and time on which the patient was born.');
  await expect(page.locator('#proposal [data-role="role_unit_stay"]')).toContainText(d.roleUndrafted);
  await stage(page, '4-proposed');

  // The tables and columns query, and its result.
  await page.locator('#database-options input[value="training"]').check();
  await page.locator('#tables-write').click();
  await expect(page.locator('#tables-query')).toContainText('INFORMATION_SCHEMA.COLUMNS');
  await expect(page.locator('#tables-query')).toContainText("N'OBS_READING'");
  await page.locator('#tables-paste').fill(tablesResult());
  await page.locator('#tables-read').click();
  await expect(page.locator('#t-tables-status')).toContainText('Schemalyser has read the result of the tables and columns query');
  const value = page.locator('#confirm [data-about="role_reading.value"]');
  await expect(value).toContainText(d.presence.large('OBS_READING', 25_000_000));
  await stage(page, '5-tables');

  // Some bindings confirmed, one corrected by hand, one not sure.
  const total = Number((await page.locator('#t-tally').innerText()).match(/^Of (\d+) bindings/)![1]);
  expect(total).toBeGreaterThan(40);
  await page.locator('#confirm [data-about="role_patient.birth_date"]').getByRole('button', { name: d.yes }).click();
  await expect(page.locator('#confirm [data-about="role_patient.birth_date"]')).toContainText('Confirmed on');
  await page.locator('#confirm [data-about="role_reading.value"]').getByRole('button', { name: d.notSure }).click();
  const patient = page.locator('#confirm [data-about="role_anaesthetic.patient_key"]');
  await patient.getByRole('button', { name: d.another }).click();
  await patient.locator('input[type=text]').fill('THEATRE_CASE.NO_SUCH');
  await patient.getByRole('button', { name: d.anotherUse }).click();
  await expect(patient.locator('.problem-note')).toHaveText('The dictionary holds no column THEATRE_CASE.NO_SUCH.');
  await patient.locator('input[type=text]').fill('THEATRE_CASE.PERSON_KEY');
  await patient.getByRole('button', { name: d.anotherUse }).click();
  await expect(page.locator('#confirm [data-about="role_anaesthetic.patient_key"]')).toContainText('Corrected to THEATRE_CASE.PERSON_KEY');
  await expect(page.locator('#t-tally')).toHaveText(d.tally({ confirmed: 1, corrected: 1, not_sure: 1, remaining: total - 3, total }));
  await expect(page.locator('#questions')).toContainText('role_reading.value: Please confirm whether');
  await stage(page, '6-confirmed');

  // The codes of the readings, from a pasted list of what is charted.
  await page.locator('#year').fill('2024');
  await page.locator('#year').dispatchEvent('change');
  const readings = page.locator('[data-key="role_reading.kind"]');
  await readings.getByRole('button', { name: d.chartedWrite }).click();
  await expect(readings.locator('pre')).toContainText('INTO   #cohort');
  await readings.locator('textarea').fill(charted);
  await readings.getByRole('button', { name: d.chartedRead }).click();
  await expect(readings).toContainText(d.chartedReceipt(4, 2024));
  await readings.locator('select[data-code="52"]').selectOption('map_arterial');
  await readings.locator('select[data-code="51"]').selectOption('map_cuff');
  await readings.getByRole('button', { name: d.codesSave }).click();
  await expect(page.locator('[data-key="role_reading.kind"]')).toContainText('Schemalyser recorded 2 codes for this vocabulary on');
  await stage(page, '7-codes');

  // A count, pasted and judged.
  await page.locator('#counts-write').click();
  const block = page.locator('[data-count="coverage_by_year"]');
  await expect(block).toContainText(d.countSafe);
  await expect(page.locator('[data-count="readings_by_kind"] pre')).toContainText("IN ('52') THEN 'map_arterial'");
  await block.locator('textarea').fill(coverage);
  await block.getByRole('button', { name: d.countRead }).click();
  await expect(page.locator('[data-count="coverage_by_year"]')).toContainText(d.countReceipt(2));
  await page.locator('[data-count="coverage_by_year"] input[value="yes"]').check();
  await page.locator('[data-count="coverage_by_year"]').getByRole('button', { name: d.lookRightSave }).click();
  await expect(page.locator('[data-count="coverage_by_year"]')).toContainText('this count looks right');
  await stage(page, '8-counts');

  // The folder, as a zip, without the dictionary.
  await expect(page.locator('#keep-dictionary')).not.toBeChecked();
  const download = page.waitForEvent('download');
  await page.locator('#write-zip').click();
  const saved = await download;
  expect(saved.suggestedFilename()).toBe('hospital-folder.zip');
  await expect(page.locator('#t-write-status')).toContainText('Schemalyser has saved the hospital folder as a zip of');
  await stage(page, '9-written');
  const folder = join(mkdtempSync(join(tmpdir(), 'hospital-')), 'hospital-folder');
  const zipPath = folder + '.zip';
  await saved.saveAs(zipPath);
  execFileSync('python3', ['-c', 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', zipPath, folder]);
  for (const name of ['README.md', 'journal.json', 'confirmations.csv', 'settings.json', 'map/map.json', 'map/role_reading.sql',
    'queries/01-tables-and-columns.sql', 'results/01-tables-and-columns.tsv', 'codes/role_reading.kind.json']) {
    expect(existsSync(join(folder, name)), name).toBe(true);
  }
  expect(existsSync(join(folder, 'dictionary'))).toBe(false);
  expect(readFileSync(join(folder, 'map/role_reading.sql'), 'utf8')).toContain("IN ('52') THEN 'map_arterial'");
  // No description of the dictionary is in any query.
  expect(readFileSync(join(folder, 'queries/01-tables-and-columns.sql'), 'utf8')).not.toContain('on which the patient was born');
  expect(requestsWhileOffline).toEqual([]);

  // The page returned to: the folder restores what was settled.
  await setOnline(page, context, browserName, true);
  offline = false;
  await expect(page.locator('#t-locked')).toBeVisible();
  await loadAndGoOffline(page, context, browserName);
  offline = true;
  await page.locator('#hospital-folder').setInputFiles(folder);
  await expect(page.locator('#t-folder-status')).toContainText('Schemalyser has restored the map from the hospital folder.');
  await expect(page.locator('#t-tally')).toHaveText(d.tally({ confirmed: 1, corrected: 1, not_sure: 1, remaining: total - 3, total }));
  await expect(page.locator('#confirm [data-about="role_reading.value"]')).toContainText(d.presence.large('OBS_READING', 25_000_000));
  await expect(page.locator('[data-key="role_reading.kind"] select[data-code="52"]')).toHaveValue('map_arterial');
  await stage(page, '10-restored');

  // The check: without the dictionary it lists the queries only, and with it the rebuilt map is the same.
  await page.locator('#check').click();
  await expect(page.locator('#check-result')).toContainText(d.checkNoDictionary);
  await expect(page.locator('#check-result [data-query="tables-and-columns"]')).toBeVisible();
  await loadDictionary(page);
  await page.locator('#check').click();
  await expect(page.locator('#check-result')).toContainText(d.checkSame, { timeout: 60_000 });
  const again = page.locator('#check-result [data-query="tables-and-columns"]');
  await again.locator('textarea').fill(tablesResult().split('\n').filter((line) => !line.includes('\tVISIT_DIAGNOSIS\t')).join('\n'));
  await again.getByRole('button', { name: d.checkCompare }).click();
  await expect(again).toContainText('The table VISIT_DIAGNOSIS was in the earlier result and is not in the new one.');
  await stage(page, '11-checked');
  expect(requestsWhileOffline).toEqual([]);
});
