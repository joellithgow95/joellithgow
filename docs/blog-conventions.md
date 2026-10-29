# Blog conventions

How content on joellithgow.com is shaped. For field names and types, `cms/schema.py` is the source of truth. This doc covers the parts the schema can't tell you.

## Which document type?

Content is either a **post** (something Joel writes) or **data** (a record a post can show). Posts have pages and feed cards; data has neither, and reaches the site through a post's slices.

| You're adding... | Use | Kind | Notes |
|---|---|---|---|
| An opinion, devlog, explainer, short thought or list of links | `blog_post` | post | The only post type; see below |
| A term and what it means | `definition` | data | Keeps its own page under `/blog/dictionary/`; see *Definitions* |
| A link worth keeping (a blog, a tool, an article) | `link` | data | Stored once; posts show it with `link_picks` or `links_by_tag` |
| A case study | `project_page` | | |
| A job on the resume | `experience_entry` | | |

`spotify_like` and `inat_observation` are data too, but only the gateways write them (filed under **Data** in the editor). Don't create or edit those by hand: a re-sync overwrites every field. See *Collected data* below.

## `blog_post`

There is one kind of post. What it looks like follows from what it holds:

- Every post has its own page at `/blog/<slug>`.
- On the feed, a post with an `excerpt` shows the excerpt. Without one, a short body (up to about 800 characters of Markdown) shows in full, and a longer one is cut off with "Continue reading". So a short thought needs no excerpt, and a long piece should have one.
- **Data a post shows goes in `data`, as slices** (`cms/slices.py`). A slice says which songs, observations, definitions or links to show; the site looks them up when it builds and draws them under the body (a compact version on the feed card). Kinds: `music_range` / `nature_range` (`{from_date, to_date}`, YYYY-MM-DD, both included), `music_picks` / `nature_picks` / `definition_picks` / `link_picks` (`{refs: [document ids]}`, shown in that order) and `links_by_tag` (`{tag}`, every `link` document with that tag). The CMS refuses a bad slice on save. A month of music is one `music_range`; "the birds from this trip" is a `nature_picks`; a reading list is a `links_by_tag`.
- A slice is a pointer, not a copy: a range picks up songs synced after the post was written, and an edit to a definition or link shows in every post that picks it.

`post_type` and `has_detail_page` are gone. The CMS drops them if an old post still carries them the next time it is saved; don't set them.

## Collected data

- **Songs and observations.** The `spotify-likes` and `inaturalist-observations` gateways write one document per liked song and per sighting, published, with no page or card of their own. The `gateways-daily` CronJob (jlithgow-ops) syncs both every morning at 04:00 Pacific; `sync_gateway` (gateway MCP) or the `/gateways` admin page runs one by hand. `/blog/music` and `/blog/nature` browse all of it by month and by day.
- **The monthly music post.** At 09:00 Pacific on the 1st, the `music-month` CronJob runs `cms.compose_music_month`, which makes last month's "What I've been listening to — <month>" as a **draft** `blog_post` in Staging, holding one `music_range`. It never syncs and never publishes: open it, add words if you like, and publish Staging.
- **Outings are a post's choice.** No gateway groups sightings into outings any more. To write about a trip, make a `blog_post` with a `nature_picks` (the sightings you want) or a `nature_range` (every sighting in those days). Joel writes these himself: nothing suggests or drafts outing posts, and none is planned (ADR 026 §4's suggestion helper was dropped on 2026-10-29).
- The old `spotify_liked_dump` months and `inaturalist_outing` posts were converted to ordinary `blog_post`s with slices on the same slugs (`spotify-dump-YYYY-MM`, `nature-outing-YYYY-MM-DD`), and those types and their gateways are gone.

## Definitions

A definition is dictionary data: the term, what it means, and where that comes from. Joel's context, stories and opinions about it belong in a post that picks it, not in the definition.

- `term` and `definition`: the factual definition, in Markdown. Keep it neutral.
- `sources`: a list mixing `{type: "link", ...}` and `{type: "citation", ...}` entries. Every entry needs its `type` or the site build fails. Leave it empty rather than inventing a source.
- `personal_notes`: leave it empty on new definitions; the take goes in the post. Older definitions still have notes here, and the dictionary page shows them.
- A definition gets **no feed card of its own**, and is not on tag pages or in RSS; it shows on the feed only when a post picks it with `definition_picks`. Its page under `/blog/dictionary/<slug>` stays, and its old `/blog/<slug>` URL redirects there. A draft definition is not drawn on `/blog` while editing either; check it in the editor shell, or through the draft post that picks it.

**The usual flow, in a Claude session with the CMS MCP tools:**

1. Joel names the term and gives the description, the references and his note or anecdote.
2. `create_document` a `definition` with the term, the definition and the sources. No personal notes.
3. `create_document` a `blog_post` whose body is Joel's note in his words (see *Who writes what*), with a `definition_picks` slice holding the new definition's id, plus a tagline and tags.
4. Both land in the Staging changeset as drafts. Publish when Joel asks: `publish_changeset` on Staging, or the editor's Publish.

One definition can be picked by many posts, so a later post can pick it again (or pick two near-synonyms side by side) without copying it.

## Links

A `link` document is one URL, stored once, with a `title`, `description`, `date_added` and `tags`. Tags are plain topic tags, and some name a list, for example `developer-blog`, `inspo`, `friends` or `reading`. A post shows links with `link_picks` (these, in this order) or `links_by_tag` (every link with this tag, so the list grows when a new link is tagged). Links sit in the main content list in the editor, not under **Data**.

A thought about one link still puts that link inline in its body (see *Who writes what*); a `link` document is for a link worth listing.

## Who writes what

Joel writes his posts. He wants very little to no AI-written prose in them, and is frustrated when an agent turns what he said into writing and pads it with filler. Matching his voice is not the goal; adding less is.

- **Body: his words.** When Joel gives you the text (a "short thought on a link" is the common case), the body is that text. Fix obvious typos and punctuation only. Don't rephrase, expand, add a summary of the source, add a conclusion, or leave `[JOEL: ...]` placeholders. If a word looks like a typo but might be a joke or pun, keep it and ask, or flag it in your reply.
- **Tagline: the one place an agent writes.** The `description` field is shown in the editor as **Tagline**. It is the short line under the title (and the page meta description and RSS summary). If Joel doesn't give one, write one: short, with a take, in the register of "Is this the formalization of commercial world-view classifier models?" rather than a literal restatement of the post. He may tune this over time.
- **A thought about one link:** the link goes inline in the body (his text, then "Via [source title](url)"), with no slice. A list of links is `link` documents shown through a slice (see *Links*).
- **Title, excerpt and tags** are small metadata, not prose: keep them plain and factual, reuse existing tags (see *Tags*), and say what you chose so he can change it.

### When Joel explicitly asks for a drafted piece

Only then, longer drafting is fine. Joel's posts, going by the existing ones, are first person, conversational and curious, and they say when something is a guess. Observations come before predictions, and uncertainty is stated openly ("I don't know the answer", "live blogging/QAing, I guess"), so drafts should do the same.

- Mark places for his own experience with a `[JOEL: ...]` placeholder, and remove them all before publishing.
- Separate what a source says from what Joel thinks. Don't present a guess about someone else's reasoning as fact.
- Paraphrase sources and link them in a `Sources:` list at the end. Keep quotes short.

## Tags

Tags are lowercase and hyphenated. Reuse the existing ones where they fit before inventing new ones. The most common in the current posts: `claude-code`, `tools`, `resources`, `links`, `game-design`, `design`, `ai`. Others include `minecraft`, `modding`, `learning`, `dev-journal`, `cli` and `reflection`.

## Publishing

- Posts are published from the editor UI (the toolbar's Publish ships the open changeset, else "Staging"), with `publish_changeset`, or one at a time with `publish_document`. Gateway syncs publish their own data documents; the monthly music post is only a draft.
- New drafts made with `create_document` collect in an open "Staging" changeset; publishing one document on its own takes it out of Staging. See `CLAUDE.md`, *Drafts on the blog index*.
- Publishing, unpublishing and deleting rebuild the site automatically through a CMS webhook to Netlify (about a minute). The editor's "Rebuild site" button (prod only) is for a rebuild with no content change. Never publish unless Joel asked you to.
- The body field is stored as ProseMirror JSON, not markdown, despite its name (`body_markdown`). Check the MCP tool's input schema for how it accepts the body before relying on markdown going in.
