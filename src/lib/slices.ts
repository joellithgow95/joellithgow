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
import {
  resolveSlicesFrom,
  type Album,
  type DataPool,
  type Entry,
  type Link,
  type Observation,
  type ResolvedSlice,
  type Song,
} from './slice-resolve';
import type { TasteShape } from './tastes';

export * from './slice-resolve';

let pool: Promise<DataPool> | undefined;

/** Every published data document, loaded once per build. */
export function loadDataPool(): Promise<DataPool> {
  pool ??= (async () => {
    const [songs, albums, observations, definitions, links, tastes] = await Promise.all([
      getCollection('spotify_likes'),
      getCollection('spotify_albums'),
      getCollection('inat_observations'),
      getCollection('definitions'),
      getCollection('links'),
      getCollection('taste_shapes'),
    ]);
    return {
      songs: songs.map((e) => e.data as Song),
      albums: albums.map((e) => e.data as Album),
      observations: observations.map((e) => e.data as Observation),
      definitions: definitions.map((e) => e.data as Entry),
      links: links.map((e) => e.data as Link),
      tastes: tastes.map((e) => e.data as TasteShape),
    };
  })();
  return pool;
}

/** A post's slices, resolved, in the post's order, without the empty ones. */
export async function resolveSlices(slices: unknown): Promise<ResolvedSlice[]> {
  if (!Array.isArray(slices) || slices.length === 0) return [];
  return resolveSlicesFrom(slices, await loadDataPool());
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
