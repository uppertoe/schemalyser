import type { BrowserContext, Page } from '@playwright/test';

// Takes the browser offline or back online. Playwright's offline mode in WebKit also stops the
// page reading local files, which a real browser does not, so there the browser's own report is
// changed instead and the network is left on.
export async function setOnline(page: Page, context: BrowserContext, browserName: string, online: boolean) {
  if (browserName === 'webkit') {
    await page.evaluate((value) => {
      Object.defineProperty(Navigator.prototype, 'onLine', { get: () => value, configurable: true });
      window.dispatchEvent(new Event(value ? 'online' : 'offline'));
    }, online);
  } else {
    await context.setOffline(!online);
  }
}
