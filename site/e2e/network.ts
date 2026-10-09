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

// Watches every request of the browser context, the worker's as well as the page's, and returns the addresses asked
// for while online. A route handler on the context sees the requests of a worker started from a blob, which the page's
// own request event can miss, and the context's request event is watched beside it. Every request is let through
// unchanged; one made while isOffline() holds is added to whileOffline, where a test expects to find nothing.
export async function watchRequests(context: BrowserContext, isOffline: () => boolean, whileOffline: string[]) {
  const online: string[] = [];
  const note = (url: string) => {
    if (url.startsWith('blob:') || url.startsWith('data:')) return;
    (isOffline() ? whileOffline : online).push(url);
  };
  await context.route('**/*', async (route) => {
    note(route.request().url());
    await route.continue().catch(() => {});
  });
  context.on('request', (request) => note(request.url()));
  return online;
}
