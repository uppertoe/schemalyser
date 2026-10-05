// The messages that pass between the page and its fetch window (github.html). Both are served from
// this site, and each checks that a message comes from the other and from this origin.

export interface Wanted {
  repository: string; // owner/name
  ref: string; // a branch, tag or commit, or empty for the default branch
}

// Page to window. The token travels once, in memory, and the page forgets it at once.
export interface FetchRequest {
  type: 'github-fetch';
  requests: Wanted;
  state: Wanted | null;
  token: string;
}

export interface RepositoryReport {
  repository: string;
  commit: string;
  count: number;
  skipped: number;
  tooMany: boolean;
  truncated: boolean;
}

export interface FetchedFile {
  from: 'requests' | 'state';
  path: string;
  bytes: ArrayBuffer;
}

export type Problem = 'unauthorised' | 'not-found' | 'rate-limited' | 'offline' | 'other' | 'policy-before' | 'policy-after';

// Window to page.
export type FetchReply =
  | { type: 'github-ready' }
  | { type: 'github-fetched'; reports: RepositoryReport[]; files: FetchedFile[] }
  | { type: 'github-failed'; problem: Problem; repository: string; ref: string };

export const REPOSITORY = /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/;
// The files taken from Schemalyser's own repository, in the layout of the boundary's state folder:
// five files at its root, everything under conversion/, and the .sql files directly under targets/.
export const STATE_FILES = ['catalogue.csv', 'site-rules.json', 'checks.csv', 'core-profile.csv', 'boundary.json'];
export const isStateFile = (path: string) =>
  STATE_FILES.includes(path) || /^conversion\/[^/]/.test(path) || /^targets\/[^/]+\.sql$/i.test(path);
export const MAX_BYTES = 2 * 1024 * 1024;
export const MAX_FILES = 5000;
