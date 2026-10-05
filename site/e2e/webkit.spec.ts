import { expect, test } from '@playwright/test';
import { strings } from '../src/strings';

test.skip(({ browserName }) => browserName !== 'webkit', 'This test describes WebKit only.');

test('in WebKit the page refuses to start, because it cannot confirm that its policy holds', async ({ page }) => {
  await page.goto('./');
  await expect(page.getByText(strings.policyFailed)).toBeVisible({ timeout: 120_000 });
  await expect(page.locator('#step-3 .body')).toBeHidden();
  await page.goto('./sandbox.html');
  await expect(page.getByText(strings.policyFailed)).toBeVisible({ timeout: 120_000 });
});
