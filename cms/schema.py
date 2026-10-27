"""
Document type schemas for joellithgow CMS.
"""
from __future__ import annotations

from starlette_cms import (
    CMS,
    BoolField,
    JSONField,
    LinksField,
    NumberField,
    RichTextField,
    SelectField,
    TagsField,
    TextField,
)


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
        post_type: str = SelectField(choices=["article", "thought", "collection"], required=True)
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
        # title and date_added are required by the site's Zod schema (src/content/config.ts)
        links: list | None = LinksField(
            required_keys=["title", "date_added"],
            help_text=(
                "Only for `collection` posts (and short posts with no detail page): a list of "
                "{url, title, description, date_added}. Items also appear on /blog/links and the tag "
                "pages. A thought about one link puts that link inline in the body instead."
            ),
        )
        tags: list | None = TagsField()
        featured: bool = BoolField(default=False)
        has_detail_page: bool = BoolField(default=True)
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

    @cms.document("spotify_liked_dump")
    class SpotifyLikedDumpDocument:
        title: str = TextField(required=True, max_length=500)           # "Liked songs — June 2025 (23 tracks)"
        description: str = TextField(max_length=1000)
        publish_date: str = TextField(required=True)                    # ISO 8601: first day of the liked month
        song_count: float = NumberField(precision=0)
        songs: list | None = JSONField()                                # [{track_name, artist_name, album_name, spotify_url, liked_at}]
        tags: list | None = TagsField()

    @cms.document("inaturalist_outing")
    class INaturalistOutingDocument:
        title: str = TextField(required=True, max_length=500)           # seeded from the place name; editable
        description: str = TextField(max_length=1000)
        publish_date: str = TextField(required=True)                    # ISO 8601 outing date
        outing_date: str = TextField(required=True)                     # same as publish_date, explicit
        place_guess: str = TextField(max_length=500)
        observation_count: float = NumberField(precision=0)
        species_list: list | None = JSONField()                         # [str] unique common names
        observations: list | None = JSONField()                         # slim records, see gateways/inaturalist_field_trips.curate_observation
        photo_urls: list | None = JSONField()
        bounding_box: dict | None = JSONField()                         # {lat_min, lat_max, lon_min, lon_max}
        tags: list | None = TagsField()

    @cms.document("definition")
    class DefinitionDocument:
        term: str = TextField(required=True, max_length=500)
        publish_date: str = TextField(required=True)                    # ISO 8601
        definition: str = TextField(required=True)                      # markdown — the actual definition
        personal_notes: str = TextField()                               # markdown — Joel's thoughts/context
        sources: list | None = JSONField()                              # list[Source] — mix of links + text citations
        tags: list | None = TagsField()