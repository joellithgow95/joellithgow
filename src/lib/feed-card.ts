/**
 * How a post shows on the feed, decided from what it holds (docs/blog-conventions.md).
 * Shared by the feed page (src/pages/blog/[...page].astro, at build time) and the
 * live editor's draft preview (src/scripts/draft-preview.ts, in the browser), so a
 * draft beat looks like the beat it will publish as.
 *
 * - A post whose only slice picks definitions, one link, one album or taste
 *   shapes, and that has no body, is a beat: a smaller title, no date, tagline or tags.
 * - A post about one definition (or a definition beat) is titled with the
 *   quoted term, and the definition sits right under the title.
 * - A post whose only slice picks one link is titled with that link, which
 *   links out with ↗; the slice itself is not drawn.
 * - A post with no body whose only slice picks one saved album is an album
 *   beat: its title links to the album on Spotify with ↗, over the cover.
 */

import { marked } from 'marked';
import type { Album, Entry, Link, ResolvedSlice } from './slice-resolve';
import type { TasteShape } from './tastes';
import { esc } from './slice-html';

export interface FeedShape {
  /** Definitions the post's only slice picks */
  pickedDefs: Entry[] | null;
  /** The one link the post's only slice picks (not a tag collection) */
  pickedLink: Link | null;
  pickedTastes: TasteShape[] | null;
  /** The one album the post's only slice picks */
  pickedAlbum: Album | null;
  isBeat: boolean;
  /** Where a title that links out goes: a link post's link, or an album beat's album */
  outHref: string | undefined;
  /** The definitions the title is made of, when it is a definition title */
  defTitle: Entry[] | null;
}

/** The feed shape of a post with these resolved slices and this Markdown body. */
export function feedShape(slices: ResolvedSlice[], body: string): FeedShape {
  const only = slices.length === 1 ? slices[0] : null;
  const pickedDefs = only?.kind === 'definitions' ? only.definitions : null;
  const pickedTastes = only?.kind === 'tastes' ? only.tastes : null;
  const pickedAlbum = only?.kind === 'albums' && only.albums.length === 1 ? only.albums[0] : null;
  const pickedLink = only?.kind === 'links' && !only.tags && only.links.length === 1 ? only.links[0] : null;
  const isBeat = !!(pickedDefs || pickedLink || pickedTastes || pickedAlbum) && !body.trim();
  const outHref = pickedLink?.url ?? (isBeat ? pickedAlbum?.spotify_url ?? undefined : undefined);
  const defTitle = pickedDefs && (pickedDefs.length === 1 || isBeat) ? pickedDefs : null;
  return { pickedDefs, pickedLink, pickedTastes, pickedAlbum, isBeat, outHref, defTitle };
}

/**
 * A definition's credit, from its own sources: a link source links out with ↗
 * (labelled with the source's title), a citation shows its attribution.
 */
export function creditHtml(d: Entry): string {
  const parts = (d.sources ?? []).map((src) =>
    src.type === 'link' && src.url
      ? `<a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">${esc(src.title || src.url)}</a>&nbsp;↗`
      : esc(src.attribution || src.title || ''),
  ).filter(Boolean);
  return parts.length ? `<span class="quote-credit">— ${parts.join(', ')}</span>` : '';
}

/** A picked definition as a quote; `term` puts its term, linked to the dictionary, in front. */
export function quoteHtml(d: Entry, term: 'none' | 'plain' | 'linked'): string {
  return (term === 'linked' ? `<strong><a href="/blog/dictionary/${esc(d.slug)}">${esc(d.term)}</a></strong>: `
    : term === 'plain' ? `<strong>${esc(d.term)}</strong>: ` : '')
    + (marked.parseInline(d.definition ?? '') as string) + creditHtml(d);
}
