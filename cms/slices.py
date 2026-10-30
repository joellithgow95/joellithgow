"""
Slices: what a blog post shows from the collected data (astraeus ADR 026).

A slice is a saved instruction in a post, not content: "the songs I liked in
October", "these three observations", "every link tagged art or design". The
site resolves it at build time against the published data documents
(`src/lib/slices.ts`), so a range picks up items synced after the post was
written.

Each data source has its own block types rather than one block with a
`source` choice, so the CMS can check what a slice holds when it is saved:
a picked ref must exist and be the source's type (astraeus checks refs inside
blocks), and a range must be two real dates in order. A bad slice fails at
save instead of in the Netlify build.

Example ``data`` value on a ``blog_post``::

    [
        {"block_type": "music_range", "from_date": "2026-10-01", "to_date": "2026-10-31"},
        {"block_type": "nature_picks", "refs": ["<inat_observation id>", ...]},
        {"block_type": "links_by_tag", "tags": ["art", "design"]},
    ]
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_core import PydanticCustomError
from starlette_cms import DateField, DocumentRef, ListField, TagsField
from starlette_cms.model_builder import build_block_model

_TAG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# Errors are PydanticCustomError, not ValueError: the CMS returns the error
# list as JSON. DateField (astraeus) checks each date is a real YYYY-MM-DD.


def _in_order(self: Any) -> Any:
    if self.from_date > self.to_date:
        raise PydanticCustomError("slice_range", "from_date must not be after to_date")
    return self


# ── Field sets ───────────────────────────────────────────────────────────────


class _DateRange:
    from_date: str = DateField(required=True, help_text="First day")
    to_date: str = DateField(required=True, help_text="Last day (included)")


class _SongPicks:
    refs: list = ListField(DocumentRef(block_type="spotify_like", on_delete="nullify"), label="Songs")


class _ObservationPicks:
    refs: list = ListField(DocumentRef(block_type="inat_observation", on_delete="nullify"), label="Observations")


class _DefinitionPicks:
    refs: list = ListField(DocumentRef(block_type="definition", on_delete="nullify", label_field="term"), label="Definitions")


class _LinkPicks:
    refs: list = ListField(DocumentRef(block_type="link", on_delete="nullify"), label="Links")


class _ByTag:
    # Suggests the tags already on link documents, so a slice names a list that exists.
    tags: list = TagsField(required=True, suggest_from="link", placeholder="developer-blogs")


# ── Slice blocks ─────────────────────────────────────────────────────────────
# The docstrings land in the JSON schema (get_block_schema), so agents read them.


class MusicRange(build_block_model("music_range", _DateRange)):
    """Spotify likes whose liked_at falls between from_date and to_date (both included)."""

    _check_order = model_validator(mode="after")(_in_order)


class NatureRange(build_block_model("nature_range", _DateRange)):
    """iNaturalist observations whose observed_on falls between from_date and to_date (both included)."""

    _check_order = model_validator(mode="after")(_in_order)


class MusicPicks(build_block_model("music_picks", _SongPicks)):
    """Hand-picked spotify_like documents, shown in this order."""


class NaturePicks(build_block_model("nature_picks", _ObservationPicks)):
    """Hand-picked inat_observation documents, shown in this order."""


class DefinitionPicks(build_block_model("definition_picks", _DefinitionPicks)):
    """Hand-picked dictionary entries (definition documents), shown in this order."""


class LinkPicks(build_block_model("link_picks", _LinkPicks)):
    """Hand-picked link documents, shown in this order."""


class LinksByTag(build_block_model("links_by_tag", _ByTag)):
    """Every published link document carrying any of these tags, newest first. Grows as links are tagged.

    A post with this slice is a link collection: it is listed under Collections on /blog/links.
    """

    @model_validator(mode="before")
    @classmethod
    def _one_tag_to_tags(cls, value: Any) -> Any:
        # Slices saved before a collection could name several tags hold {"tag": "..."}.
        if isinstance(value, dict) and "tag" in value:
            value = dict(value)
            tag = value.pop("tag")
            if not value.get("tags"):
                value["tags"] = [tag]
        return value

    @field_validator("tags")
    @classmethod
    def _tag_shape(cls, value: list) -> list:
        if not value:
            raise PydanticCustomError("slice_tag", "name at least one tag")
        for tag in value:
            if not isinstance(tag, str) or not _TAG.match(tag):
                raise PydanticCustomError("slice_tag", "each tag must be lowercase and hyphenated, such as developer-blogs")
        return value


SLICE_BLOCKS = [MusicRange, NatureRange, MusicPicks, NaturePicks, DefinitionPicks, LinkPicks, LinksByTag]
