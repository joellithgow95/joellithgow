/**
 * Public API shim — delegates to Astro's Content Layer getCollection().
 *
 * All three CMS-backed content types (blog_post, project_page, experience_entry)
 * are now first-class Astro collections defined in src/content/config.ts.
 * The functions below preserve the original call signatures so no consumer
 * files need to be updated.
 *
 * Field access is identical to before: callers receive flat objects with the
 * same snake_case fields (e.g. post.title, post.publish_date, project.slug).
 * The only addition is the `_id`, `_published`, `_created_at`, `_updated_at`
 * metadata fields, which were already present in the old AstraeusDocument<T>.
 */

import { getCollection, getEntry } from 'astro:content';
import type {
  AstraeusDocument,
  BlogPost,
  ProjectPage,
  ExperienceEntry,
  SpotifyDump,
  NatureOuting,
  Definition,
} from './astraeus-types';

// ---------------------------------------------------------------------------
// Union type for all blog-feed post kinds
// ---------------------------------------------------------------------------

export type AnyBlogPost =
  | (AstraeusDocument<BlogPost> & { kind: 'post' })
  | (AstraeusDocument<SpotifyDump> & { kind: 'spotify_dump' })
  | (AstraeusDocument<NatureOuting> & { kind: 'nature_outing' })
  | (AstraeusDocument<Definition> & { kind: 'definition' });

// ---------------------------------------------------------------------------
// Blog posts
// ---------------------------------------------------------------------------

/**
 * Fetch all published blog posts, Spotify dumps, and iNaturalist outings,
 * merged and sorted by publish_date descending.
 *
 * Returns the AnyBlogPost union type — callers should branch on kind.
 */
export async function fetchBlogPosts(): Promise<AnyBlogPost[]> {
  const [blogEntries, dumpEntries, outingEntries, definitionEntries] = await Promise.all([
    getCollection('blog'),
    getCollection('spotify_dumps'),
    getCollection('nature_outings'),
    getCollection('definitions'),
  ]);

  const posts: AnyBlogPost[] = [
    ...blogEntries.map((e) => ({
      ...(e.data as AstraeusDocument<BlogPost>),
      kind: 'post' as const,
    })),
    ...dumpEntries.map((e) => ({
      ...(e.data as AstraeusDocument<SpotifyDump>),
      kind: 'spotify_dump' as const,
    })),
    ...outingEntries.map((e) => ({
      ...(e.data as AstraeusDocument<NatureOuting>),
      kind: 'nature_outing' as const,
    })),
    ...definitionEntries.map((e) => ({
      ...(e.data as AstraeusDocument<Definition>),
      kind: 'definition' as const,
    })),
  ];

  return posts.sort((a, b) => {
    const dateA = new Date(a.publish_date || '').getTime();
    const dateB = new Date(b.publish_date || '').getTime();
    return dateB - dateA;
  });
}

/**
 * Fetch a single blog post by slug.
 * Returns null when the slug is not found (matches previous behaviour).
 */
export async function fetchBlogPost(slug: string): Promise<AstraeusDocument<BlogPost> | null> {
  const entry = await getEntry('blog', slug);
  return entry ? (entry.data as AstraeusDocument<BlogPost>) : null;
}

// ---------------------------------------------------------------------------
// Spotify dumps
// ---------------------------------------------------------------------------

/**
 * Fetch all published Spotify liked song dumps, sorted by publish_date descending.
 */
export async function fetchSpotifyDumps(): Promise<AstraeusDocument<SpotifyDump>[]> {
  const entries = await getCollection('spotify_dumps');
  return entries
    .map((e) => e.data as AstraeusDocument<SpotifyDump>)
    .sort((a, b) => {
      const dateA = new Date(a.publish_date || '').getTime();
      const dateB = new Date(b.publish_date || '').getTime();
      return dateB - dateA;
    });
}

/**
 * Fetch a single Spotify dump by slug.
 */
export async function fetchSpotifyDump(slug: string): Promise<AstraeusDocument<SpotifyDump> | null> {
  const entry = await getEntry('spotify_dumps', slug);
  return entry ? (entry.data as AstraeusDocument<SpotifyDump>) : null;
}

// ---------------------------------------------------------------------------
// iNaturalist outings
// ---------------------------------------------------------------------------

/**
 * Fetch all published iNaturalist outings, sorted by publish_date descending.
 */
export async function fetchNatureOutings(): Promise<AstraeusDocument<NatureOuting>[]> {
  const entries = await getCollection('nature_outings');
  return entries
    .map((e) => e.data as AstraeusDocument<NatureOuting>)
    .sort((a, b) => {
      const dateA = new Date(a.publish_date || '').getTime();
      const dateB = new Date(b.publish_date || '').getTime();
      return dateB - dateA;
    });
}

/**
 * Fetch a single iNaturalist outing by slug.
 */
export async function fetchNatureOuting(slug: string): Promise<AstraeusDocument<NatureOuting> | null> {
  const entry = await getEntry('nature_outings', slug);
  return entry ? (entry.data as AstraeusDocument<NatureOuting>) : null;
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

/**
 * Fetch all published project pages, sorted by number ascending.
 * Equivalent to the previous HTTP-based fetchProjects().
 */
export async function fetchProjects(): Promise<AstraeusDocument<ProjectPage>[]> {
  const entries = await getCollection('projects');
  return entries
    .map((e) => e.data as AstraeusDocument<ProjectPage>)
    .sort((a, b) => (a.number ?? 0) - (b.number ?? 0));
}

/**
 * Fetch a single project page by slug.
 * Returns null when the slug is not found.
 */
export async function fetchProject(slug: string): Promise<AstraeusDocument<ProjectPage> | null> {
  const entry = await getEntry('projects', slug);
  return entry ? (entry.data as AstraeusDocument<ProjectPage>) : null;
}

// ---------------------------------------------------------------------------
// Experience
// ---------------------------------------------------------------------------

/**
 * Fetch all published experience entries, sorted by start_date descending.
 * Equivalent to the previous HTTP-based fetchExperience().
 */
export async function fetchExperience(): Promise<AstraeusDocument<ExperienceEntry>[]> {
  const entries = await getCollection('experience');
  return entries
    .map((e) => e.data as AstraeusDocument<ExperienceEntry>)
    .sort((a, b) => {
      const dateA = new Date(a.start_date || '').getTime();
      const dateB = new Date(b.start_date || '').getTime();
      return dateB - dateA;
    });
}

// ---------------------------------------------------------------------------
// Definitions / Glossary
// ---------------------------------------------------------------------------

/**
 * Fetch all published definitions, sorted by term alphabetically.
 */
export async function fetchDefinitions(): Promise<AstraeusDocument<Definition>[]> {
  const entries = await getCollection('definitions');
  return entries
    .map((e) => e.data as AstraeusDocument<Definition>)
    .sort((a, b) => a.term.localeCompare(b.term));
}

/**
 * Fetch a single definition by slug.
 * Returns null when the slug is not found.
 */
export async function fetchDefinition(slug: string): Promise<AstraeusDocument<Definition> | null> {
  const entry = await getEntry('definitions', slug);
  return entry ? (entry.data as AstraeusDocument<Definition>) : null;
}

// ---------------------------------------------------------------------------
// Cache control (no-op — Content Layer manages its own build-time cache)
// ---------------------------------------------------------------------------

/** @deprecated No-op — Content Layer caches automatically per build. */
export function clearAstraeusCache(): void {
  // intentionally empty
}
