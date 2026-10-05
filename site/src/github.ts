// The fetch window. The page opens it, sends it what to fetch and the token, and receives the files
// back. It reads from https://api.github.com with GET requests only, and its policy allows no
// other site. When the fetch ends, whether it succeeded or not, the window forgets the token, adds
// a stricter policy that removes api.github.com, confirms that GitHub is now refused, and only then
// hands the files over and closes.

import {
  MAX_BYTES,
  MAX_FILES,
  isStateFile,
  type FetchReply,
  type FetchRequest,
  type FetchedFile,
  type Problem,
  type RepositoryReport,
  type Wanted,
} from './github-protocol';
import { githubStrings } from './strings';

const API = 'https://api.github.com';
const PROBE = 'https://policy-check.invalid/';
const BLOBS_AT_ONCE = 6;

class Refusal extends Error {
  constructor(readonly problem: Problem) {
    super(problem);
  }
}

// Held only here, only while the fetch runs.
let token = '';
let started = false;

// Every request to GitHub goes through this function, and it can only make a GET.
async function get(path: string, accept: string): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(API + path, {
      method: 'GET',
      headers: { Accept: accept, Authorization: `Bearer ${token}`, 'X-GitHub-Api-Version': '2022-11-28' },
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      mode: 'cors',
    });
  } catch {
    throw new Refusal('offline');
  }
  if (response.ok) return response;
  const exhausted = response.headers.get('x-ratelimit-remaining') === '0';
  if (response.status === 429 || (response.status === 403 && exhausted)) throw new Refusal('rate-limited');
  if (response.status === 401 || response.status === 403) throw new Refusal('unauthorised');
  if ([404, 409, 422].includes(response.status)) throw new Refusal('not-found');
  throw new Refusal('other');
}

interface TreeEntry {
  path: string;
  type: string;
  sha: string;
  size?: number;
}

async function fetchRepository(
  wanted: Wanted,
  from: FetchedFile['from'],
  pick: (path: string) => boolean,
): Promise<{ report: RepositoryReport; files: FetchedFile[] }> {
  const [owner, name] = wanted.repository.split('/');
  const base = `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;
  const json = 'application/vnd.github+json';

  let ref = wanted.ref.trim();
  if (!ref) ref = String((await (await get(base, json)).json()).default_branch ?? '');
  if (!ref) throw new Refusal('not-found');
  const commit = (await (await get(`${base}/commits/${encodeURIComponent(ref)}`, 'application/vnd.github.sha')).text()).trim();
  if (!/^[0-9a-f]{40}([0-9a-f]{24})?$/.test(commit)) throw new Refusal('other');

  const tree = (await (await get(`${base}/git/trees/${commit}?recursive=1`, json)).json()) as {
    tree?: TreeEntry[];
    truncated?: boolean;
  };
  const listed = (tree.tree ?? [])
    .filter((entry) => entry.type === 'blob' && pick(entry.path))
    .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  const small = listed.filter((entry) => (entry.size ?? 0) <= MAX_BYTES);
  let skipped = listed.length - small.length;
  const taken = small.slice(0, MAX_FILES);

  const files: (FetchedFile | null)[] = new Array(taken.length).fill(null);
  let next = 0;
  const fetchSome = async () => {
    while (next < taken.length) {
      const i = next++;
      const response = await get(`${base}/git/blobs/${taken[i].sha}`, 'application/vnd.github.raw+json');
      const bytes = await response.arrayBuffer();
      files[i] = bytes.byteLength > MAX_BYTES ? null : { from, path: taken[i].path, bytes };
    }
  };
  await Promise.all(Array.from({ length: Math.min(BLOBS_AT_ONCE, taken.length) }, fetchSome));
  const kept = files.filter((file): file is FetchedFile => file !== null);
  skipped += taken.length - kept.length;

  return {
    report: {
      repository: wanted.repository,
      commit,
      count: kept.length,
      skipped,
      tooMany: small.length > MAX_FILES,
      truncated: Boolean(tree.truncated),
    },
    files: kept,
  };
}

async function fetchAll(request: FetchRequest): Promise<FetchReply> {
  const reports: RepositoryReport[] = [];
  const files: FetchedFile[] = [];
  const jobs: [Wanted, FetchedFile['from'], (path: string) => boolean][] = [
    [request.requests, 'requests', (path) => /\.sql$/i.test(path)],
  ];
  if (request.state) jobs.push([request.state, 'state', isStateFile]);
  for (const [wanted, from, pick] of jobs) {
    try {
      const fetched = await fetchRepository(wanted, from, pick);
      reports.push(fetched.report);
      files.push(...fetched.files);
    } catch (error) {
      const problem = error instanceof Refusal ? error.problem : 'other';
      return { type: 'github-failed', problem, repository: wanted.repository, ref: wanted.ref.trim() };
    }
  }
  return { type: 'github-fetched', reports, files };
}

// Asks for an address and reports whether this window's policy refused it.
async function refused(address: string): Promise<boolean> {
  const origin = new URL(address).origin;
  let seen = false;
  const note = (event: SecurityPolicyViolationEvent) => {
    if (event.blockedURI.startsWith(origin)) seen = true;
  };
  document.addEventListener('securitypolicyviolation', note);
  try {
    await fetch(address, { mode: 'no-cors', credentials: 'omit', cache: 'no-store' });
  } catch {
    // Refused by the policy, or unreachable. Only the policy raises the event.
  }
  await new Promise((resolve) => setTimeout(resolve, 100));
  document.removeEventListener('securitypolicyviolation', note);
  return seen;
}

// Policies intersect, so a second policy can only take permissions away. This one removes
// api.github.com, and from now on the window can reach only its own site.
function closeGitHub() {
  const meta = document.createElement('meta');
  meta.httpEquiv = 'Content-Security-Policy';
  meta.content = "connect-src 'self'";
  document.head.append(meta);
}

const opener = window.opener as Window | null;

window.addEventListener('message', async (event: MessageEvent) => {
  if (!opener || event.source !== opener || event.origin !== location.origin || started) return;
  const request = event.data as FetchRequest;
  if (request?.type !== 'github-fetch') return;
  started = true;
  token = request.token;
  request.token = '';
  document.getElementById('t-github-progress')!.textContent = githubStrings.progress;

  let reply: FetchReply;
  try {
    reply = (await refused(PROBE))
      ? await fetchAll(request)
      : { type: 'github-failed', problem: 'policy-before', repository: '', ref: '' };
  } finally {
    token = '';
  }

  closeGitHub();
  if (!(await refused(API + '/'))) {
    reply = { type: 'github-failed', problem: 'policy-after', repository: '', ref: '' };
  }
  const transfer = reply.type === 'github-fetched' ? reply.files.map((file) => file.bytes) : [];
  opener.postMessage(reply, location.origin, transfer);
  window.close();
});

if (opener) opener.postMessage({ type: 'github-ready' } satisfies FetchReply, location.origin);
