/**
 * The site's half of the live editor's draft preview.
 *
 * A static page is built from published documents, so a draft (or a live
 * post's unpublished edits) has never been through the build that draws its
 * slices, beat form and tags. The astraeus embed draws a draft card from the
 * page's <template data-cms-draft-template> and then asks the site to finish
 * it, by dispatching `astraeus:preview` on the card (astraeus editor README,
 * *Showing drafts on a listing*):
 *
 *   detail.doc        the document with its draft applied, or null: show the card as built
 *   detail.cmsBase    the CMS origin
 *   detail.inLens(id) whether an unpublished change to `id` is in what is being previewed
 *   detail.changed    ids whose content changed since the last preview (drop them from caches)
 *   detail.waitUntil  hand back the rendering promise
 *
 * This file only listens; the rendering (and its dependencies) loads on the
 * first event, so a visitor who is not logged in pays nothing for it.
 */

type Render = typeof import('./draft-preview-render');
let renderer: Promise<Render> | undefined;

document.addEventListener('astraeus:preview', (event) => {
  const { detail } = event as CustomEvent;
  const card = event.target as HTMLElement;
  renderer ??= import('./draft-preview-render');
  const done = renderer.then((r) => r.renderPreview(card, detail)).catch((error) => {
    console.warn('[draft-preview] could not draw', card.dataset.cmsId, error);
  });
  detail?.waitUntil?.(done);
});
