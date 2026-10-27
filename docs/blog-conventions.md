# Blog conventions

How content on joellithgow.com is shaped. For field names and types, `cms/schema.py` is the source of truth. This doc covers the parts the schema can't tell you.

## Which document type?

| You're writing... | Use | Notes |
|---|---|---|
| An opinion, devlog, explainer, short thought or list of links | `blog_post` | One kind of post; see below |
| A term with a definition, plus your take on it | `definition` | Not a blog post. Lives under `/blog/dictionary/` |
| A case study | `project_page` | |
| A job on the resume | `experience_entry` | |

Spotify dumps and iNaturalist outings are created and auto-published by the gateways. Don't write those by hand.

## `blog_post`

There is one kind of post. What it looks like follows from what it holds:

- Every post has its own page at `/blog/<slug>`.
- On the feed, a post with an `excerpt` shows the excerpt. Without one, a short body (up to about 800 characters of Markdown) shows in full, and a longer one is cut off with "Continue reading". So a short thought needs no excerpt, and a long piece should have one.
- A list of links goes in the `links` field (a list of link items). They render under the body on the post's page, and the feed card shows a "N links" badge. Items also show on `/blog/links` and the tag pages.

Older posts still carry `post_type` and `has_detail_page` from before this change. The CMS drops both the next time the post is saved; don't set them.

## `definition` structure

- `definition`: the factual definition, in markdown. Keep it neutral.
- `personal_notes`: your take, context, predictions, in markdown. This is where first-person opinion goes.
- `sources`: a list mixing `link` and `citation` entries. Leave it empty rather than inventing a source.

## Who writes what

Joel writes his posts. He wants very little to no AI-written prose in them, and is frustrated when an agent turns what he said into writing and pads it with filler. Matching his voice is not the goal; adding less is.

- **Body: his words.** When Joel gives you the text (a "short thought on a link" is the common case), the body is that text. Fix obvious typos and punctuation only. Don't rephrase, expand, add a summary of the source, add a conclusion, or leave `[JOEL: ...]` placeholders. If a word looks like a typo but might be a joke or pun, keep it and ask, or flag it in your reply.
- **Tagline: the one place an agent writes.** The `description` field is shown in the editor as **Tagline**. It is the short line under the title (and the page meta description and RSS summary). If Joel doesn't give one, write one: short, with a take, in the register of "Is this the formalization of commercial world-view classifier models?" rather than a literal restatement of the post. He may tune this over time.
- **A thought about one link:** the link goes inline in the body (his text, then "Via [source title](url)"), and `links` stays empty. `links` is for posts that are a list of links: items there also show on `/blog/links` and the tag pages.
- **Title, excerpt and tags** are small metadata, not prose: keep them plain and factual, reuse existing tags (see *Tags*), and say what you chose so he can change it.

### When Joel explicitly asks for a drafted piece

Only then, longer drafting is fine. Joel's posts, going by the existing ones, are first person, conversational and curious, and they say when something is a guess. Observations come before predictions, and uncertainty is stated openly ("I don't know the answer", "live blogging/QAing, I guess"), so drafts should do the same.

- Mark places for his own experience with a `[JOEL: ...]` placeholder, and remove them all before publishing.
- Separate what a source says from what Joel thinks. Don't present a guess about someone else's reasoning as fact.
- Paraphrase sources and link them in a `Sources:` list at the end. Keep quotes short.

## Tags

Tags are lowercase and hyphenated. Reuse the existing ones where they fit before inventing new ones. The most common in the current posts: `claude-code`, `tools`, `resources`, `links`, `game-design`, `design`, `ai`. Others include `minecraft`, `modding`, `learning`, `dev-journal`, `cli` and `reflection`.

## Publishing

- Posts are published from the editor UI (the toolbar's Publish ships the open changeset, else "Staging") or with `publish_document`. Gateway syncs auto-publish their own documents.
- New drafts made with `create_document` collect in an open "Staging" changeset; publishing one document on its own takes it out of Staging. See `CLAUDE.md`, *Drafts on the blog index*.
- Publishing, unpublishing and deleting rebuild the site automatically through a CMS webhook to Netlify (about a minute). The editor's "Rebuild site" button (prod only) is for a rebuild with no content change. Never `publish_document` unless Joel asked you to.
- The body field is stored as ProseMirror JSON, not markdown, despite its name (`body_markdown`). Check the MCP tool's input schema for how it accepts the body before relying on markdown going in.
