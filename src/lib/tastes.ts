/**
 * Taste shapes (`taste_shape` documents): a shape drawn for how something
 * tastes, with an optional photo beside it. They have no feed card of their
 * own: posts show them through `taste_picks` / `tastes_range` slices, and
 * /blog/tastes lists them all.
 */
import type { AstraeusDocument, TasteShapeDoc } from './astraeus-types';

export type TasteShape = AstraeusDocument<TasteShapeDoc>;

/** The CMS the site was built from; a photo that is a media key is served from its /media. */
function cmsBase(): string {
  const url = import.meta.env.ASTRAEUS_URL || process.env.ASTRAEUS_URL || '';
  return url.replace(/\/+$/, '');
}

/**
 * An `<img src>` for a taste's photo. The field holds a media library key
 * (what the editor's picker stores) or a full URL. A key becomes an absolute
 * IIIF URL on the CMS, since the site is static and served elsewhere.
 */
export function tastePhotoUrl(photo: string | null | undefined, width = 600): string | null {
  if (!photo) return null;
  if (/^https?:\/\//.test(photo)) return photo;
  return `${cmsBase()}/media/iiif/${photo.replace(/^\/+/, '')}/full/${width},/0/default.webp`;
}

/** The anchor a taste has on /blog/tastes. */
export const tasteAnchor = (t: TasteShape) => `taste-${t._id}`;

/** Newest tasted first. */
export const byTastedDesc = (a: TasteShape, b: TasteShape) => b.tasted_on.localeCompare(a.tasted_on);
