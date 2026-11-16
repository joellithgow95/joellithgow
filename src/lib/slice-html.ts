/**
 * The feed card's slice markup, as HTML strings.
 *
 * One source for two renderers: the Astro slice components draw a published
 * post's feed card with these at build time (`<Fragment set:html>`), and the
 * live editor's draft preview (src/scripts/draft-preview.ts) draws a draft's
 * card with the same functions in the browser. Change a slice's feed form here
 * and both follow; its styles are global (`<style is:global>`, scoped by the
 * slice's own class) so markup drawn in the browser is styled too.
 *
 * Only the forms a feed card shows live here (compact and beat). A post page's
 * full forms stay in the components.
 */

import { albumMosaic, coverSpan, type MosaicSong } from './album-mosaic';
import type { Drawing } from './astraeus-types';
import type { Album, Entry, Link, Observation, ResolvedSlice, Song } from './slice-resolve';
import { tasteAnchor, tastePhotoUrl, type TasteShape } from './tastes';

/** Escape text for an HTML text node or a double-quoted attribute. */
export const esc = (value: unknown): string =>
  String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

const attr = (name: string, value: unknown) =>
  value === undefined || value === null || value === false ? '' : ` ${name}="${esc(value)}"`;

const plural = (n: number, word: string) => `${n} ${word}${n !== 1 ? 's' : ''}`;

// ── Shared pieces ──────────────────────────────────────────────────────────

/** Liked songs as a grid of album covers (AlbumMosaic). */
export function albumMosaicHtml(songs: MosaicSong[]): string {
  const albums = albumMosaic(songs);
  if (albums.length === 0) return '';
  const cells = albums.map((album) => {
    const label = `${album.name} — ${album.artist} · ${plural(album.count, 'song')}`;
    const img = `<img src="${esc(album.art)}" alt="${esc(label)}" loading="lazy" />`;
    const span = coverSpan(album.count);
    return album.href
      ? `<a href="${esc(album.href)}" target="_blank" rel="noopener noreferrer" class="cover-cell" data-span="${span}" title="${esc(label)}">${img}</a>`
      : `<span class="cover-cell" data-span="${span}" title="${esc(label)}">${img}</span>`;
  });
  return `<div class="spotify-cover-grid">${cells.join('')}</div>`;
}

let drawingSeq = 0;

/** One hand-drawn shape as inline SVG (DrawingSvg). Strokes default to currentColor. */
export function drawingSvgHtml(
  drawing: Drawing,
  { className, style, pencil = false, attrs = {} }: { className?: string; style?: string; pencil?: boolean; attrs?: Record<string, string | undefined> } = {},
): string {
  const filterId = pencil ? `pencil-${(drawingSeq++).toString(36)}-${Math.random().toString(36).slice(2, 6)}` : undefined;
  const extra = Object.entries(attrs).map(([k, v]) => attr(k, v)).join('');
  const filter = filterId
    ? `<filter id="${filterId}" x="-5%" y="-5%" width="110%" height="110%">`
      + '<feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="1" seed="3" result="grain" />'
      + '<feDisplacementMap in="SourceGraphic" in2="grain" scale="0.35" /></filter>'
    : '';
  const paths = (drawing.paths ?? []).map((p) =>
    `<path d="${esc(p.d)}" stroke="${esc(p.stroke ?? 'currentColor')}" stroke-width="${esc(p.stroke_width ?? 2)}"`
    + ` fill="${esc(p.fill ?? 'none')}"${attr('opacity', p.opacity)} stroke-linecap="round" stroke-linejoin="round" />`,
  );
  return `<svg${attr('class', className)}${attr('style', style)} viewBox="${esc(drawing.viewbox)}"${extra}>`
    + `${filter}<g${attr('filter', filterId ? `url(#${filterId})` : undefined)}>${paths.join('')}</g></svg>`;
}

// ── Slices, feed forms ─────────────────────────────────────────────────────

/** music_range / music_picks on a feed card: the mosaic and a count. */
export function musicSliceCompactHtml(songs: Song[]): string {
  return `<section class="slice slice--music">${albumMosaicHtml(songs)}<p class="slice-count">${plural(songs.length, 'song')}</p></section>`;
}

const albumMeta = (a: Album) =>
  [a.artist_name, (a.release_date ?? '').slice(0, 4), a.total_tracks ? `${a.total_tracks} tracks` : ''].filter(Boolean).join(' · ');

/** album_picks: an album beat (one album, large cover) or one row per album. */
export function albumSliceHtml(albums: Album[], beat = false): string {
  const one = beat && albums.length === 1 ? albums[0] : null;
  if (one) {
    const cover = one.cover_url
      ? `<a${attr('href', one.spotify_url || undefined)} target="_blank" rel="noopener noreferrer" class="album-cover-link">`
        + `<img class="album-cover album-cover--beat" src="${esc(one.cover_url)}" alt="${esc(`${one.album_name} cover`)}" loading="lazy" /></a>`
      : '';
    return `<section class="slice slice--albums album-beat">${cover}<p class="album-meta">${esc(albumMeta(one))}</p></section>`;
  }
  const rows = albums.map((a) => {
    const cover = a.cover_url ? `<img class="album-cover" src="${esc(a.cover_url)}" alt="" loading="lazy" />` : '';
    const name = a.spotify_url
      ? `<a href="${esc(a.spotify_url)}" class="album-name" target="_blank" rel="noopener noreferrer">${esc(a.album_name)}&nbsp;↗</a>`
      : `<span class="album-name">${esc(a.album_name)}</span>`;
    return `<li class="album-row">${cover}<span class="album-text">${name}<span class="album-meta">${esc(albumMeta(a))}</span></span></li>`;
  });
  return `<section class="slice slice--albums"><ul class="album-list">${rows.join('')}</ul></section>`;
}

const SPECIES_ON_CARD = 10;
const observationName = (o: Observation) => o.common_name || o.taxon_name || 'Unknown';

/** Photo URLs a nature slice shows (iNaturalist originals, swapped for the medium size). */
export const observationPhotos = (observations: Observation[], max: number) =>
  observations
    .flatMap((o) => o.photo_urls ?? [])
    .map((url) => url.replace('/original.', '/medium.'))
    .slice(0, max);

export const observationSpecies = (observations: Observation[]) => [...new Set(observations.map(observationName))];

/** nature_range / nature_picks on a feed card: a few photos, species chips and a count. */
export function natureSliceCompactHtml(observations: Observation[]): string {
  const photos = observationPhotos(observations, 6);
  const species = observationSpecies(observations);
  const grid = photos.length
    ? `<div class="slice-photos">${photos.map((url) => `<img src="${esc(url)}" alt="iNaturalist observation photo" loading="lazy" />`).join('')}</div>`
    : '';
  const chips = species.slice(0, SPECIES_ON_CARD).map((s) => `<span class="species-chip">${esc(s)}</span>`).join('')
    + (species.length > SPECIES_ON_CARD ? `<span class="species-chip species-chip--more">+${species.length - SPECIES_ON_CARD} more</span>` : '');
  return `<section class="slice slice--nature">${grid}<div class="species-chips">${chips}</div>`
    + `<p class="slice-count">${plural(observations.length, 'observation')} · ${species.length} species</p></section>`;
}

/** definition_picks, compact: each term linked to its page, the definition cut short. */
export function definitionsSliceCompactHtml(definitions: Entry[]): string {
  const cut = (text: string) => (text.length > 200 ? `${text.slice(0, 200)}…` : text);
  const items = definitions.map((d) =>
    `<div class="slice-definition"><a href="/blog/dictionary/${esc(d.slug)}" class="slice-term">${esc(d.term)}</a>`
    + `<blockquote class="slice-definition-text">${esc(cut(d.definition ?? ''))}</blockquote></div>`,
  );
  return `<section class="slice slice--definitions">${items.join('')}</section>`;
}

const LINKS_ON_CARD = 5;

/** link_picks / links_by_tag, compact: the first few titles. */
export function linksSliceCompactHtml(links: Link[]): string {
  const items = links.slice(0, LINKS_ON_CARD).map((l) =>
    `<li><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.title)} ↗</a></li>`,
  );
  if (links.length > LINKS_ON_CARD) items.push(`<li class="slice-link-more">+${links.length - LINKS_ON_CARD} more</li>`);
  return `<section class="slice slice--links"><ul class="slice-link-list">${items.join('')}</ul></section>`;
}

const tasteHref = (t: TasteShape) => `/blog/tastes#${tasteAnchor(t)}`;
const tasteDate = (d: string) =>
  new Date(`${d}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
export const tasteMeta = (t: TasteShape) => [t.kind, t.place, t.tasted_on ? tasteDate(t.tasted_on) : ''].filter(Boolean).join(' · ');
const tasteShapeHtml = (t: TasteShape) =>
  t.shape ? drawingSvgHtml(t.shape, { className: 'taste-svg', pencil: true }) : `<span class="taste-blank">${esc(t.name)}</span>`;

/** taste_picks / tastes_range on a feed card: a taste beat (one shape) or a row of shapes. */
export function tastesSliceHtml(tastes: TasteShape[], beat = false): string {
  const one = beat && tastes.length === 1 ? tastes[0] : null;
  if (one) {
    const photo = one.photo ? `<img class="taste-photo taste-photo--beat" src="${esc(tastePhotoUrl(one.photo, 300))}" alt="${esc(one.name)}" loading="lazy" />` : '';
    return `<section class="slice slice--tastes tastes-beat">`
      + `<a href="${esc(tasteHref(one))}" class="taste-paper taste-paper--beat" aria-label="${esc(`${one.name} taste shape`)}">${tasteShapeHtml(one)}</a>`
      + photo
      + `<div class="tastes-beat-text">${one.note ? `<p class="taste-note">${esc(one.note)}</p>` : ''}<p class="taste-meta">${esc(tasteMeta(one))}</p></div>`
      + '</section>';
  }
  const tiles = tastes.slice(0, 6).map((t) =>
    `<a href="${esc(tasteHref(t))}" class="taste-tile"><span class="taste-paper">${tasteShapeHtml(t)}</span><span class="taste-name">${esc(t.name)}</span></a>`,
  );
  return `<section class="slice slice--tastes"><div class="tastes-row">${tiles.join('')}</div>`
    + `<p class="slice-count">${plural(tastes.length, 'taste shape')}</p></section>`;
}

/** A post's resolved slices as its feed card shows them (PostSlices, compact). */
export function feedSlicesHtml(slices: ResolvedSlice[], { beat = false }: { beat?: boolean } = {}): string {
  return slices.map((s) => {
    switch (s.kind) {
      case 'music': return musicSliceCompactHtml(s.songs);
      case 'albums': return albumSliceHtml(s.albums, beat);
      case 'nature': return natureSliceCompactHtml(s.observations);
      case 'definitions': return definitionsSliceCompactHtml(s.definitions);
      case 'tastes': return tastesSliceHtml(s.tastes, beat);
      case 'links': return linksSliceCompactHtml(s.links);
    }
  }).join('');
}
