/**
 * Slices — turn a post's `data` field into the data items it shows (ADR 026).
 *
 * A slice is a saved instruction (a date range, picked ids, a tag), validated
 * by the CMS on save (cms/slices.py). It is resolved here, at build time,
 * against the published data documents, so a range picks up items synced
 * after the post was written.
 *
 * Nothing here throws on content: a picked id that is not published (deleted,
 * or still a draft) is skipped, and a slice of an unknown kind or shape is
 * dropped. A slice that resolves to nothing is left out.
 */

import { getCollection } from 'astro:content';
import type {
  AstraeusDocument,
  Definition,
  INatObservation,
  LinkDoc,
  Slice,
  SpotifyLike,
} from './astraeus-types';

export type Song = AstraeusDocument<SpotifyLike>;
export type Observation = AstraeusDocument<INatObservation>;
export type Link = AstraeusDocument<LinkDoc>;
export type Entry = AstraeusDocument<Definition>;

export type ResolvedSlice =
  | { kind: 'music'; songs: Song[] }
  | { kind: 'nature'; observations: Observation[] }
  | { kind: 'definitions'; definitions: Entry[] }
  | { kind: 'links'; links: Link[]; tags?: string[] };

export interface DataPool {
  songs: Song[];
  observations: Observation[];
  definitions: Entry[];
  links: Link[];
}

let pool: Promise<DataPool> | undefined;

/** Every published data document, loaded once per build. */
export function loadDataPool(): Promise<DataPool> {
  pool ??= (async () => {
    const [songs, observations, definitions, links] = await Promise.all([
      getCollection('spotify_likes'),
      getCollection('inat_observations'),
      getCollection('definitions'),
      getCollection('links'),
    ]);
    return {
      songs: songs.map((e) => e.data as Song),
      observations: observations.map((e) => e.data as Observation),
      definitions: definitions.map((e) => e.data as Entry),
      links: links.map((e) => e.data as Link),
    };
  })();
  return pool;
}

/** `value`'s date part (YYYY-MM-DD) is within from..to, both included. */
function inRange(value: string | undefined, from: string, to: string): boolean {
  const day = (value ?? '').slice(0, 10);
  return day.length === 10 && day >= from && day <= to;
}

function pick<T extends { _id: string }>(items: T[], refs: unknown): T[] {
  if (!Array.isArray(refs)) return [];
  const byId = new Map(items.map((item) => [item._id, item]));
  return refs.flatMap((id) => (typeof id === 'string' && byId.has(id) ? [byId.get(id)!] : []));
}

const newestLike = (a: Song, b: Song) => b.liked_at.localeCompare(a.liked_at);
const inTimeOrder = (a: Observation, b: Observation) =>
  (a.time_observed_at || a.observed_on).localeCompare(b.time_observed_at || b.observed_on);
const newestLink = (a: Link, b: Link) => b.date_added.localeCompare(a.date_added);

/** Resolve one slice against the pool; null when it is unknown or malformed. */
export function resolveSlice(slice: Slice | Record<string, unknown>, data: DataPool): ResolvedSlice | null {
  const s = slice as Record<string, any>;
  const ranged = typeof s.from_date === 'string' && typeof s.to_date === 'string';
  switch (s.block_type) {
    case 'music_range':
      if (!ranged) return null;
      return { kind: 'music', songs: data.songs.filter((x) => inRange(x.liked_at, s.from_date, s.to_date)).sort(newestLike) };
    case 'nature_range':
      if (!ranged) return null;
      return {
        kind: 'nature',
        observations: data.observations.filter((x) => inRange(x.observed_on, s.from_date, s.to_date)).sort(inTimeOrder),
      };
    case 'music_picks':
      return { kind: 'music', songs: pick(data.songs, s.refs) };
    case 'nature_picks':
      return { kind: 'nature', observations: pick(data.observations, s.refs) };
    case 'definition_picks':
      return { kind: 'definitions', definitions: pick(data.definitions, s.refs) };
    case 'link_picks':
      return { kind: 'links', links: pick(data.links, s.refs) };
    case 'links_by_tag': {
      // Any of `tags`; a slice saved before collections could name several tags holds one `tag`.
      const tags: string[] | null = Array.isArray(s.tags) ? s.tags : typeof s.tag === 'string' ? [s.tag] : null;
      if (!tags?.length) return null;
      const links = data.links.filter((l) => l.tags?.some((t) => tags.includes(t))).sort(newestLink);
      return { kind: 'links', tags, links };
    }
    default:
      return null;
  }
}

function isEmpty(r: ResolvedSlice): boolean {
  switch (r.kind) {
    case 'music': return r.songs.length === 0;
    case 'nature': return r.observations.length === 0;
    case 'definitions': return r.definitions.length === 0;
    case 'links': return r.links.length === 0;
  }
}

/** A post's slices, resolved, in the post's order, without the empty ones. */
export async function resolveSlices(slices: unknown): Promise<ResolvedSlice[]> {
  if (!Array.isArray(slices) || slices.length === 0) return [];
  const data = await loadDataPool();
  return slices
    .map((s) => (s && typeof s === 'object' ? resolveSlice(s, data) : null))
    .filter((r): r is ResolvedSlice => r !== null && !isEmpty(r));
}

/** The ids of the songs and observations a post's slices show. */
export async function sliceItemIds(slices: unknown): Promise<Set<string>> {
  const ids = new Set<string>();
  for (const r of await resolveSlices(slices)) {
    if (r.kind === 'music') r.songs.forEach((x) => ids.add(x._id));
    if (r.kind === 'nature') r.observations.forEach((x) => ids.add(x._id));
  }
  return ids;
}

export interface PostRef {
  slug: string;
  title: string;
}

/** For each song or observation id, the posts whose slices show it. */
export async function postsByItem(posts: Array<PostRef & { data?: unknown }>): Promise<Map<string, PostRef[]>> {
  const byItem = new Map<string, PostRef[]>();
  for (const post of posts) {
    for (const id of await sliceItemIds(post.data)) {
      const list = byItem.get(id) ?? [];
      list.push({ slug: post.slug, title: post.title });
      byItem.set(id, list);
    }
  }
  return byItem;
}

/** The distinct posts showing any of `ids`, in first-seen order. */
export function postsShowing(ids: string[], byItem: Map<string, PostRef[]>): PostRef[] {
  const seen = new Map<string, PostRef>();
  for (const id of ids) for (const p of byItem.get(id) ?? []) seen.set(p.slug, p);
  return [...seen.values()];
}
