import { createHash } from 'node:crypto';
import type { BrowserContext } from '@playwright/test';

// A stand-in for https://api.github.com, served by Playwright's router. No test reaches the real
// GitHub. The router is set on the browser context, so that it also serves the fetch window, which
// is a page of its own.

export interface MockFile {
  content?: Buffer;
  size?: number; // the size GitHub reports, when the content is not given
}

export interface MockRepository {
  defaultBranch: string;
  refs: Record<string, string>; // branch or tag to commit SHA
  files: Record<string, MockFile>;
  truncated?: boolean;
}

export interface Seen {
  method: string;
  url: string;
  authorization: string | undefined;
}

export const sha = (text: string) => createHash('sha1').update(text).digest('hex');

const cors = {
  'access-control-allow-origin': '*',
  'access-control-expose-headers': 'x-ratelimit-remaining',
};

export async function mockGitHub(
  context: BrowserContext,
  repositories: Record<string, MockRepository>,
  token: string,
  options: { abort?: boolean } = {},
) {
  const seen: Seen[] = [];
  const blobs = new Map<string, Buffer>();
  for (const [name, repository] of Object.entries(repositories)) {
    for (const [path, file] of Object.entries(repository.files)) blobs.set(sha(`${name}:${path}`), file.content ?? Buffer.alloc(0));
  }

  await context.route('https://api.github.com/**', async (route) => {
    const request = route.request();
    const headers = await request.allHeaders();
    seen.push({ method: request.method(), url: request.url(), authorization: headers['authorization'] });
    if (options.abort) return route.abort('internetdisconnected');
    if (request.method() !== 'GET') return route.fulfill({ status: 405, headers: cors });
    if (headers['authorization'] !== `Bearer ${token}`) {
      return route.fulfill({ status: 401, headers: cors, json: { message: 'Bad credentials' } });
    }

    const url = new URL(request.url());
    const match = url.pathname.match(/^\/repos\/([^/]+\/[^/]+)(\/.*)?$/);
    const name = match ? decodeURIComponent(match[1]) : '';
    const repository = repositories[name];
    const rest = match?.[2] ?? '';
    const notFound = () => route.fulfill({ status: 404, headers: cors, json: { message: 'Not Found' } });
    if (!repository) return notFound();

    if (rest === '') return route.fulfill({ headers: cors, json: { default_branch: repository.defaultBranch } });

    const commit = rest.match(/^\/commits\/(.+)$/);
    if (commit) {
      const ref = decodeURIComponent(commit[1]);
      const found = repository.refs[ref] ?? (Object.values(repository.refs).includes(ref) ? ref : undefined);
      if (!found) return route.fulfill({ status: 422, headers: cors, json: { message: 'No commit found' } });
      return route.fulfill({ headers: cors, contentType: 'application/vnd.github.sha', body: found });
    }

    const tree = rest.match(/^\/git\/trees\/([0-9a-f]+)$/);
    if (tree && url.searchParams.get('recursive') === '1' && Object.values(repository.refs).includes(tree[1])) {
      const entries = Object.entries(repository.files).map(([path, file]) => ({
        path,
        mode: '100644',
        type: 'blob',
        sha: sha(`${name}:${path}`),
        size: file.size ?? file.content?.length ?? 0,
      }));
      return route.fulfill({ headers: cors, json: { sha: tree[1], tree: entries, truncated: Boolean(repository.truncated) } });
    }

    const blob = rest.match(/^\/git\/blobs\/([0-9a-f]+)$/);
    if (blob && blobs.has(blob[1])) {
      return route.fulfill({ headers: cors, contentType: 'application/octet-stream', body: blobs.get(blob[1]) });
    }
    return notFound();
  });
  return seen;
}
