"""
Document type schemas for joellithgow CMS.
"""
from __future__ import annotations

from starlette_cms import (
    CMS,
    BoolField,
    DateField,
    JSONField,
    ListField,
    NumberField,
    RichTextField,
    SelectField,
    TagsField,
    TextField,
    URLField,
)

from cms.slices import SLICE_BLOCKS


def register_documents(cms: CMS) -> None:
    """Register all document types with the CMS instance."""

    @cms.document("blog_post")
    class BlogPostDocument:
        title: str = TextField(required=True, max_length=500)
        # Stored as `description` (the site's meta description, RSS summary and
        # card subtitle all read it) but shown as "Tagline": it is used as a
        # short one-line take under the title, not a summary of the post.
        description: str = TextField(
            required=True,
            max_length=1000,
            label="Tagline",
            help_text=(
                "One short line under the title (also the meta description and RSS summary). "
                "Opinionated, not a restatement of the post. If none is given, write one."
            ),
        )
        publish_date: str = TextField(required=True)          # ISO 8601 — wrap with new Date()
        # ProseMirror JSON, not Markdown, despite the field name — stored rich
        # so the live editor gets WYSIWYG + collaborative editing. The Astro
        # loader renders it back to Markdown at build (see prosemirror-markdown).
        # Existing content was migrated in place (scripts/richtext-migrate).
        body_markdown: dict = RichTextField()
        excerpt: str = TextField(max_length=2000)
        author: str = TextField(max_length=200)
        image: dict | None = JSONField(                       # { src, alt, type, fallbackSrc?, poster? }
            help_text=(
                "The card and header image, as an object: {\"src\": ..., \"alt\": ...}. Both are "
                "required and both are strings. Optional: type (\"image\" or \"video\"), fallbackSrc, "
                "poster, link. For an image uploaded to the media library, src is the absolute URL "
                "https://cms.joellithgow.com/media/iiif/<asset key>/full/1200,/0/default.webp (width "
                "after full/ is yours to pick), because the site is static and a relative src would "
                "resolve against Netlify. Find the asset key in the media library at /media/admin, "
                "or with the media MCP's list_assets."
            ),
        )
        tags: list | None = TagsField()
        # What the post shows from the data documents below (ADR 026): each
        # block is an instruction (a date range, picked refs, a tag) the site
        # resolves at build time. Validated on save; see cms/slices.py.
        data: list | None = ListField(
            blocks=SLICE_BLOCKS,
            help_text=(
                "Data this post shows, as a list of slices: songs or observations from a date range "
                "(music_range, nature_range), hand-picked songs, observations, definitions or links "
                "(music_picks, nature_picks, definition_picks, link_picks), or every link with a tag "
                "(links_by_tag). Shown after the body, in this order."
            ),
        )
        featured: bool = BoolField(default=False)
        reading_time: float | None = NumberField(min_value=0.0, precision=0)

    @cms.document("project_page")
    class ProjectPageDocument:
        number: float = NumberField(required=True, precision=0)
        project_type: str = TextField(required=True)
        title: str = TextField(required=True, max_length=300)
        description: str = TextField(required=True, max_length=1000)
        impact: str = TextField(max_length=500)
        technologies: list | None = JSONField()
        subtitle: str = TextField(max_length=300)
        overview: str = TextField(max_length=2000)
        hero_media: dict | None = JSONField()                 # { src, alt, type, fallbackSrc?, poster? }
        duration: str = TextField(max_length=200)
        team: str = TextField(max_length=500)
        role: str = TextField(max_length=300)
        tools: str = TextField(max_length=500)
        live_link: dict | None = JSONField()
        live_links: dict | None = JSONField()
        publish_date: str = TextField()
        featured: bool = BoolField(default=False)
        tags: list | None = TagsField()
        body_blocks: list | None = JSONField()

    @cms.document("experience_entry")
    class ExperienceEntryDocument:
        company: str = TextField(required=True, max_length=300)
        title: str = TextField(required=True, max_length=300)
        location: str = TextField(max_length=200)
        start_date: str = TextField(required=True)
        end_date: str = TextField()
        employment_type: str = SelectField(
            choices=["full-time", "part-time", "contract", "student", "internship"],
            required=True,
        )
        description: str = TextField(max_length=2000)
        responsibilities: list | None = JSONField()
        achievements: list | None = JSONField()
        featured: bool = BoolField(default=False)
        show_on_resume: bool = BoolField(default=True)
        order: float | None = NumberField(precision=0)

    # ── Data (ADR 026 in astraeus) ────────────────────────────────────────────
    # One document per collected item, written by a gateway and published so the
    # static build can read it, but with no page or feed card of its own: a post
    # shows them through a slice. Every field belongs to the gateway, which
    # overwrites the record on a re-sync. `title` is a label made from the record,
    # for the editor's list and the reference pickers.

    @cms.document("spotify_like", group="Data")
    class SpotifyLikeDocument:
        title: str = TextField(max_length=1000)                         # "Track — Artist"
        track_id: str = TextField(required=True, max_length=100)        # Spotify track id
        track_name: str = TextField(required=True, max_length=500)
        artist_name: str = TextField(max_length=500)
        album_name: str = TextField(max_length=500)
        album_art_url: str = TextField(max_length=1000)
        spotify_url: str = TextField(max_length=1000)
        liked_at: str = TextField(required=True)                        # ISO 8601 datetime, UTC

    @cms.document("inat_observation", group="Data")
    class INatObservationDocument:
        title: str = TextField(max_length=1000)                         # "Species — Place"
        observation_id: float = NumberField(required=True, precision=0)
        uri: str = TextField(max_length=1000)
        observed_on: str = TextField(required=True)                     # YYYY-MM-DD
        time_observed_at: str = TextField()                             # ISO 8601, with offset
        taxon_name: str = TextField(max_length=500)                     # scientific name
        common_name: str = TextField(max_length=500)
        iconic_taxon: str = TextField(max_length=100)                   # e.g. "Aves"
        place_guess: str = TextField(max_length=500)
        lat: float | None = NumberField(min_value=-90.0, max_value=90.0)    # None when obscured
        lon: float | None = NumberField(min_value=-180.0, max_value=180.0)
        photo_urls: list | None = JSONField()                           # [str]
        tags: list | None = TagsField()                                 # taxon group, e.g. ["birds"]

    @cms.document("link", label="Bookmarks")
    class LinkDocument:
        # One external link, stored once and shown through posts' link slices
        # (links_by_tag, link_picks). Tags group links into lists, e.g.
        # developer-blog, inspo, friends, reading.
        title: str = TextField(required=True, max_length=500)
        url: str = URLField(required=True)
        description: str = TextField(max_length=2000)
        date_added: str = DateField(required=True)
        tags: list | None = TagsField()

    @cms.document("definition")
    class DefinitionDocument:
        # Dictionary data: a term, what it means and where that comes from. No
        # feed card of its own; a post shows it through a definition_picks slice,
        # and it keeps a page under /blog/dictionary/<slug>.
        term: str = TextField(required=True, max_length=500, help_text="The word or phrase being defined.")
        publish_date: str = TextField(required=True)                    # ISO 8601
        definition: str = TextField(
            required=True,
            help_text="What the term means, in Markdown. Factual and neutral: Joel's take goes in a post that picks this definition.",
        )
        personal_notes: str = TextField(
            help_text="Legacy: older definitions keep their notes here. Leave it empty on a new definition; the note belongs in the post that picks it.",
        )
        sources: list | None = JSONField(                              # list[Source] — mix of links + text citations
            help_text=(
                "The references, as a list. A link is {\"type\": \"link\", \"url\": ..., \"title\": ..., "
                "\"description\": ...}; a citation is {\"type\": \"citation\", \"title\": ..., "
                "\"attribution\": ..., \"quote\": ...}. Every entry needs its type or the site build fails. "
                "Leave it empty rather than inventing a source."
            ),
        )
        tags: list | None = TagsField()