/**
 * Slice resolution with no Astro dependency (ADR 026): the build (src/lib/slices.ts,
 * against every published data document) and the live editor's draft preview
 * (src/scripts/draft-preview.ts, against documents fetched from the CMS) both
 * resolve a post's `data` field here, so a draft shows the same items its
 * published card will.
 *
 * Nothing here throws on content: a picked id that is not in the pool is
 * skipped, and a slice of an unknown kind or shape is dropped.
 */

import type {
  AstraeusDocument,
  Definition,
  INatObservation,
  LinkDoc,
  Slice,
  SpotifyAlbum,
  SpotifyLike,
} from './astraeus-types';
import type { TasteShape } from './tastes';

export type Song = AstraeusDocument<SpotifyLike>;
export type Album = AstraeusDocument<SpotifyAlbum>;
export type Observation = AstraeusDocument<INatObservation>;
export type Link = AstraeusDocument<LinkDoc>;
export type Entry = AstraeusDocument<Definition>;

export type ResolvedSlice =
  | { kind: 'music'; songs: Song[] }
  | { kind: 'albums'; albums: Album[] }
  | { kind: 'nature'; observations: Observation[] }
  | { kind: 'definitions'; definitions: Entry[] }
  | { kind: 'links'; links: Link[]; tags?: string[] }
  | { kind: 'tastes'; tastes: TasteShape[] };

export interface DataPool {
  songs: Song[];
  albums: Album[];
  observations: Observation[];
  definitions: Entry[];
  links: Link[];
  tastes: TasteShape[];
}

/** Which data document type each slice kind reads, and where it sits in the pool. */
export const SLICE_SOURCES: Record<string, { docType: string; pool: keyof DataPool }> = {
  music_range: { docType: 'spotify_like', pool: 'songs' },
  music_picks: { docType: 'spotify_like', pool: 'songs' },
  album_picks: { docType: 'spotify_album', pool: 'albums' },
  nature_range: { docType: 'inat_observation', pool: 'observations' },
  nature_picks: { docType: 'inat_observation', pool: 'observations' },
  definition_picks: { docType: 'definition', pool: 'definitions' },
  link_picks: { docType: 'link', pool: 'links' },
  links_by_tag: { docType: 'link', pool: 'links' },
  taste_picks: { docType: 'taste_shape', pool: 'tastes' },
  tastes_range: { docType: 'taste_shape', pool: 'tastes' },
};

export const emptyPool = (): DataPool => ({ songs: [], albums: [], observations: [], definitions: [], links: [], tastes: [] });

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
    case 'album_picks':
      return { kind: 'albums', albums: pick(data.albums, s.refs) };
    case 'nature_picks':
      return { kind: 'nature', observations: pick(data.observations, s.refs) };
    case 'definition_picks':
      return { kind: 'definitions', definitions: pick(data.definitions, s.refs) };
    case 'link_picks':
      return { kind: 'links', links: pick(data.links, s.refs) };
    case 'taste_picks':
      return { kind: 'tastes', tastes: pick(data.tastes, s.refs) };
    case 'tastes_range':
      if (!ranged) return null;
      return {
        kind: 'tastes',
        tastes: data.tastes
          .filter((x) => inRange(x.tasted_on, s.from_date, s.to_date))
          .sort((a, b) => a.tasted_on.localeCompare(b.tasted_on)),
      };
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

export function isEmpty(r: ResolvedSlice): boolean {
  switch (r.kind) {
    case 'music': return r.songs.length === 0;
    case 'albums': return r.albums.length === 0;
    case 'nature': return r.observations.length === 0;
    case 'definitions': return r.definitions.length === 0;
    case 'links': return r.links.length === 0;
    case 'tastes': return r.tastes.length === 0;
  }
}

/** A post's slices resolved against `data`, in the post's order, without the empty ones. */
export function resolveSlicesFrom(slices: unknown, data: DataPool): ResolvedSlice[] {
  if (!Array.isArray(slices) || slices.length === 0) return [];
  return slices
    .map((s) => (s && typeof s === 'object' ? resolveSlice(s, data) : null))
    .filter((r): r is ResolvedSlice => r !== null && !isEmpty(r));
}
