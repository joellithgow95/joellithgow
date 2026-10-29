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
  Definition,
} from './astraeus-types';

// ---------------------------------------------------------------------------
// Blog feed
// ---------------------------------------------------------------------------

/**
 * One item on the blog feed. Only posts: a definition reaches the feed through
 * a post that picks it (a `definition_picks` slice), and has its own page under
 * /blog/dictionary/. `kind` stays so a later feed item type can join the union.
 */
export type AnyBlogPost = AstraeusDocument<BlogPost> & { kind: 'post' };

// ---------------------------------------------------------------------------
// Blog posts
// ---------------------------------------------------------------------------

/**
 * Fetch all published blog posts, sorted by publish_date descending.
 */
export async function fetchBlogPosts(): Promise<AnyBlogPost[]> {
  const blogEntries = await getCollection('blog');

  const posts: AnyBlogPost[] = blogEntries.map((e) => ({
    ...(e.data as AstraeusDocument<BlogPost>),
    kind: 'post' as const,
  }));

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
