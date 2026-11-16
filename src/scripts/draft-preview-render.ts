/**
 * Draws a draft's feed card the way the build will (see draft-preview.ts for the
 * event contract). Everything that decides what a card shows is shared with the
 * build: slice resolution (src/lib/slice-resolve.ts), slice markup
 * (src/lib/slice-html.ts) and the beat rules (src/lib/feed-card.ts). What is
 * here is only fetching from the CMS and putting the pieces into the card.
 *
 * Two kinds of card:
 * - a draft card the embed drew from the page's template: fill every slot (the
 *   hero image, the beat form and title, definition quotes, slices, tags);
 * - a built card of a live post with unpublished edits: redraw its slices from
 *   the draft (and put the built ones back when the preview stops).
 *
 * The data a slice shows comes from the CMS as the visitor would see it, except
 * that an unpublished change the lens includes (a staged link, a draft
 * definition) is shown as drafted, as it will be once that changeset publishes.
 */

import { feedShape, quoteHtml } from '@lib/feed-card';
import { isProseMirrorDoc, proseMirrorDocToMarkdown } from '@lib/prosemirror-markdown';
import { feedSlicesHtml } from '@lib/slice-html';
import { SLICE_SOURCES, emptyPool, resolveSlicesFrom, type DataPool, type ResolvedSlice } from '@lib/slice-resolve';

interface RawDoc {
  id: string;
  slug?: string;
  doc_type?: string;
  body?: Record<string, unknown>;
  published?: boolean;
  draft_deleted?: boolean | null;
  has_draft?: boolean;
}

export interface PreviewDetail {
  doc: RawDoc | null;
  cmsBase: string;
  inLens?: (id: string) => boolean;
  changed?: string[];
}

type SiteDoc = Record<string, any> & { _id: string; slug?: string };

// ── Fetching ───────────────────────────────────────────────────────────────

const LIST_PAGE = 500;

async function getJson(url: string): Promise<any> {
  const res = await fetch(url, { credentials: 'include' });
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
}

/** A CMS document in the shape the site's collections give (src/content/config.ts). */
function toSite(raw: RawDoc): SiteDoc {
  const body: Record<string, any> = { ...(raw.body ?? {}) };
  for (const [key, value] of Object.entries(body)) {
    if (isProseMirrorDoc(value)) body[key] = proseMirrorDocToMarkdown(value);
  }
  return { ...body, _id: raw.id, slug: raw.slug, _published: raw.published, _hasDraft: raw.has_draft === true };
}

const items = new Map<string, Promise<SiteDoc | null>>();
const lists = new Map<string, Promise<SiteDoc[]>>();

/** One data document: its draft if the lens includes a change to it, else its live version. */
function item(cmsBase: string, id: string, inLens: (id: string) => boolean): Promise<SiteDoc | null> {
  const drafted = inLens(id);
  const key = `${id}:${drafted ? 'draft' : 'live'}`;
  if (!items.has(key)) {
    items.set(key, (async () => {
      try {
        const raw: RawDoc = await getJson(`${cmsBase}/api/documents/${encodeURIComponent(id)}${drafted ? '?draft=true' : ''}`);
        if (drafted ? raw.draft_deleted === true : raw.published !== true) return null;
        return toSite(raw);
      } catch {
        return null; // like the build: a pick that can't be shown is skipped
      }
    })());
  }
  return items.get(key)!;
}

async function listAll(cmsBase: string, docType: string, published: boolean): Promise<SiteDoc[]> {
  const out: SiteDoc[] = [];
  for (let offset = 0; ; offset += LIST_PAGE) {
    const params = new URLSearchParams({ type: docType, published: String(published), limit: String(LIST_PAGE), offset: String(offset) });
    const { documents = [], total } = await getJson(`${cmsBase}/api/documents?${params}`);
    out.push(...documents.map(toSite));
    if (documents.length < LIST_PAGE || out.length >= (total ?? out.length)) return out;
  }
}

/** Every document of `docType` a range or tag slice could show: the live ones, and unpublished ones in the lens. */
async function everything(cmsBase: string, docType: string, inLens: (id: string) => boolean): Promise<SiteDoc[]> {
  const live = `${docType}:live`;
  const drafts = `${docType}:drafts`;
  if (!lists.has(live)) lists.set(live, listAll(cmsBase, docType, true).catch(() => []));
  if (!lists.has(drafts)) lists.set(drafts, listAll(cmsBase, docType, false).catch(() => []));
  const [published, unpublished] = await Promise.all([lists.get(live)!, lists.get(drafts)!]);
  // A live one with an unpublished change in the lens (a link retagged in a changeset) shows as changed.
  const shown = await Promise.all(published.map(async (d) =>
    d._hasDraft && inLens(d._id) ? (await item(cmsBase, d._id, inLens)) ?? d : d,
  ));
  return [...shown, ...unpublished.filter((d) => inLens(d._id))];
}

/** Forget what changed, so the next preview fetches it again. */
function forget(changed: string[] = []) {
  if (!changed.length) return;
  for (const key of [...items.keys()]) if (changed.some((id) => key.startsWith(`${id}:`))) items.delete(key);
  for (const key of [...lists.keys()]) if (key.endsWith(':drafts')) lists.delete(key);
}

/** The data documents a post's slices name, gathered into a pool to resolve them against. */
async function poolFor(slices: unknown, cmsBase: string, inLens: (id: string) => boolean): Promise<DataPool> {
  const pool = emptyPool();
  if (!Array.isArray(slices)) return pool;
  await Promise.all(slices.map(async (slice: any) => {
    const source = SLICE_SOURCES[slice?.block_type];
    if (!source) return;
    const docs = Array.isArray(slice.refs)
      ? (await Promise.all(slice.refs.map((id: unknown) => (typeof id === 'string' ? item(cmsBase, id, inLens) : null)))).filter(Boolean)
      : await everything(cmsBase, source.docType, inLens);
    const into = pool[source.pool] as SiteDoc[];
    const have = new Set(into.map((d) => d._id));
    for (const d of docs as SiteDoc[]) if (!have.has(d._id)) into.push(d);
  }));
  return pool;
}

// ── Drawing ────────────────────────────────────────────────────────────────

const built = new WeakMap<Element, string>();

function proto(card: Element, name: string): HTMLElement | null {
  const el = card.querySelector<HTMLElement>(`[data-cms-preview-protos] [data-proto="${name}"]`);
  if (!el) return null;
  const copy = el.cloneNode(true) as HTMLElement;
  copy.removeAttribute('data-proto');
  copy.setAttribute('data-cms-preview-added', '');
  return copy;
}

function slot(card: Element, name: string): HTMLElement | null {
  return card.querySelector<HTMLElement>(name === 'slices' ? '[data-cms-slices]' : `[data-cms-preview="${name}"]`);
}

function setHtml(el: HTMLElement | null, html: string) {
  if (el && el.innerHTML !== html) el.innerHTML = html;
}

/** Title as the feed draws it: a link out (link and album beats), quoted terms, or the plain editable title. */
function drawTitle(card: HTMLElement, shape: ReturnType<typeof feedShape>) {
  const h2 = card.querySelector('.post-title');
  const field = h2?.querySelector<HTMLElement>('[data-cms-field="title"]');
  if (!h2 || !field) return;
  // Leave the title alone when its form hasn't changed: moving the field while
  // someone types in it would drop their cursor.
  const form = shape.defTitle ? `def:${shape.defTitle.map((d) => d.term).join('|')}` : shape.outHref ? `link:${shape.outHref}` : 'plain';
  if ((h2 as HTMLElement).dataset.previewTitle === form) return;
  (h2 as HTMLElement).dataset.previewTitle = form;
  // Back to the template's form, then dress it.
  h2.prepend(field);
  h2.querySelectorAll('[data-cms-preview-added]').forEach((el) => el.remove());
  field.style.display = '';

  if (shape.defTitle) {
    // The title is the quoted term(s); the post's own title is not shown, as on the built card.
    field.style.display = 'none';
    const text = proto(card, 'title-text');
    if (!text) return;
    shape.defTitle.forEach((d, i) => {
      if (i > 0) text.append(' / ');
      const open = proto(card, 'title-quote');
      const close = proto(card, 'title-quote');
      if (close) close.textContent = '”';
      if (open) text.append(open);
      text.append(d.term ?? '');
      if (close) text.append(close);
    });
    h2.prepend(text);
  } else if (shape.outHref) {
    const link = proto(card, 'title-link');
    const ext = proto(card, 'title-ext');
    if (!link) return;
    link.setAttribute('href', shape.outHref);
    h2.prepend(link);
    link.append(field);
    if (ext) link.append(ext);
  }
}

function drawImage(card: HTMLElement, post: SiteDoc) {
  const box = slot(card, 'image');
  if (!box) return;
  const image = post.image as { src?: string; alt?: string; poster?: string; type?: string } | null | undefined;
  const src = image?.type === 'video' ? image?.poster : image?.src;
  box.querySelectorAll('[data-cms-preview-added]').forEach((el) => el.remove());
  box.hidden = !src;
  hide(box, !src);
  if (!src) return;
  const img = proto(card, 'image');
  if (!img) return;
  img.setAttribute('src', src);
  img.setAttribute('alt', image?.alt ?? '');
  box.append(img);
}

function drawTags(card: HTMLElement, post: SiteDoc, isBeat: boolean) {
  const box = slot(card, 'tags');
  if (!box) return;
  const tags: string[] = Array.isArray(post.tags) ? post.tags.filter((t: unknown) => typeof t === 'string') : [];
  box.replaceChildren();
  box.hidden = isBeat || tags.length === 0;
  hide(box, box.hidden);
  if (box.hidden) return;
  for (const tag of tags) {
    const badge = proto(card, 'tag');
    if (!badge) return;
    badge.setAttribute('href', `/blog/tags/${encodeURIComponent(tag)}`);
    badge.textContent = `#${tag}`;
    box.append(badge);
  }
}

/** Hide an element (inline, since the page's styles may set its display and beat `hidden`). */
function hide(el: HTMLElement | null, hidden: boolean) {
  if (el) el.style.display = hidden ? 'none' : '';
}

/** Show a template element only when it has something, or while it is being edited. */
function showIf(el: HTMLElement | null, show: boolean) {
  if (el) hide(el, !show && !el.isContentEditable && !el.querySelector('[contenteditable="true"], .ProseMirror'));
}

function drawDraftCard(card: HTMLElement, post: SiteDoc, slices: ResolvedSlice[]) {
  const body = typeof post.body_markdown === 'string' ? post.body_markdown : '';
  const shape = feedShape(slices, body);
  const { isBeat, pickedDefs, pickedLink, defTitle } = shape;

  if (isBeat) card.dataset.size = 'beat';
  else delete card.dataset.size;

  hide(card.querySelector<HTMLElement>('.record-date'), isBeat);
  showIf(card.querySelector<HTMLElement>('.post-description'), !isBeat && !!post.description);
  showIf(card.querySelector<HTMLElement>('[data-cms-field="body_markdown"]'), !isBeat && !!body.trim());

  drawTitle(card, shape);
  drawImage(card, post);

  // Under the title: a definition title's quotes, or a link beat's description
  const leadBox = slot(card, 'lead');
  leadBox?.replaceChildren(...quotes(card, (defTitle ?? []).map((d) => quoteHtml(d, defTitle!.length > 1 ? 'plain' : 'none'))));
  if (isBeat && pickedLink?.description) {
    const desc = proto(card, 'link-description');
    const p = desc?.querySelector('p');
    if (desc && p) {
      p.textContent = pickedLink.description;
      leadBox?.append(desc);
    }
  }

  // Under the body: picked definitions that are not the title
  const quoteBox = slot(card, 'quotes');
  quoteBox?.replaceChildren(...quotes(card, pickedDefs && !defTitle ? pickedDefs.map((d) => quoteHtml(d, 'linked')) : []));

  setHtml(slot(card, 'slices'), !pickedLink && !pickedDefs ? feedSlicesHtml(slices, { beat: isBeat }) : '');
  drawTags(card, post, isBeat);
}

/** Styled blockquotes (cloned from the template, so the page's scoped styles apply) holding `htmls`. */
function quotes(card: HTMLElement, htmls: string[]): HTMLElement[] {
  return htmls.flatMap((html) => {
    const q = proto(card, 'quote');
    if (!q) return [];
    q.innerHTML = html;
    return [q];
  });
}

function drawLiveCard(card: HTMLElement, post: SiteDoc, slices: ResolvedSlice[]) {
  const box = slot(card, 'slices');
  if (!box) return;
  if (!built.has(box)) built.set(box, box.innerHTML);
  const body = typeof post.body_markdown === 'string' ? post.body_markdown : '';
  const { pickedLink, pickedDefs, isBeat } = feedShape(slices, body);
  setHtml(box, !pickedLink && !pickedDefs ? feedSlicesHtml(slices, { beat: isBeat }) : '');
}

function restore(card: HTMLElement) {
  const box = slot(card, 'slices');
  if (box && built.has(box)) {
    setHtml(box, built.get(box)!);
    built.delete(box);
  }
}

export async function renderPreview(card: HTMLElement, detail: PreviewDetail) {
  if (!card || !detail) return;
  forget(detail.changed);
  if (!detail.doc) return restore(card);

  const inLens = detail.inLens ?? (() => true);
  const post = toSite(detail.doc);
  const pool = await poolFor(post.data, detail.cmsBase, inLens);
  const slices = resolveSlicesFrom(post.data, pool);

  if (card.hasAttribute('data-cms-draft-card')) drawDraftCard(card, post, slices);
  else drawLiveCard(card, post, slices);
}
