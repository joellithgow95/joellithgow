"""
Slices: what a blog post shows from the collected data (astraeus ADR 026).

A slice is a saved instruction in a post, not content: "the songs I liked in
October", "these three observations", "every link tagged developer-blog". The
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
        {"block_type": "links_by_tag", "tag": "developer-blog"},
    ]
"""

from __future__ import annotations

import re
from datetime import date

from typing import Any

from pydantic import field_validator, model_validator
from pydantic_core import PydanticCustomError
from starlette_cms import DocumentRef, ListField, TextField
from starlette_cms.model_builder import build_block_model

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TAG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# Errors are PydanticCustomError, not ValueError: the CMS returns the error
# list as JSON, and a ValueError in it is not serializable.


def _iso_date(value: str) -> str:
    try:
        date.fromisoformat(value)  # rejects 2026-02-30
    except ValueError:
        value = ""
    if not _DATE.match(value):
        raise PydanticCustomError("slice_date", "must be a date as YYYY-MM-DD")
    return value


def _in_order(self: Any) -> Any:
    if self.from_date > self.to_date:
        raise PydanticCustomError("slice_range", "from_date must not be after to_date")
    return self


# ── Field sets ───────────────────────────────────────────────────────────────


class _DateRange:
    from_date: str = TextField(required=True, placeholder="2026-10-01", help_text="First day, YYYY-MM-DD")
    to_date: str = TextField(required=True, placeholder="2026-10-31", help_text="Last day, YYYY-MM-DD (included)")


class _SongPicks:
    refs: list = ListField(DocumentRef(block_type="spotify_like", on_delete="nullify"), label="Songs")


class _ObservationPicks:
    refs: list = ListField(DocumentRef(block_type="inat_observation", on_delete="nullify"), label="Observations")


class _DefinitionPicks:
    refs: list = ListField(DocumentRef(block_type="definition", on_delete="nullify"), label="Definitions")


class _LinkPicks:
    refs: list = ListField(DocumentRef(block_type="link", on_delete="nullify"), label="Links")


class _ByTag:
    tag: str = TextField(required=True, max_length=100, placeholder="developer-blog")


# ── Slice blocks ─────────────────────────────────────────────────────────────
# The docstrings land in the JSON schema (get_block_schema), so agents read them.


class MusicRange(build_block_model("music_range", _DateRange)):
    """Spotify likes whose liked_at falls between from_date and to_date (both included)."""

    _check_dates = field_validator("from_date", "to_date")(_iso_date)
    _check_order = model_validator(mode="after")(_in_order)


class NatureRange(build_block_model("nature_range", _DateRange)):
    """iNaturalist observations whose observed_on falls between from_date and to_date (both included)."""

    _check_dates = field_validator("from_date", "to_date")(_iso_date)
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
    """Every published link document carrying this tag, newest first. Grows as links are tagged."""

    @field_validator("tag")
    @classmethod
    def _tag_shape(cls, value: str) -> str:
        if not _TAG.match(value):
            raise PydanticCustomError("slice_tag", "must be a lowercase, hyphenated tag such as developer-blog")
        return value


SLICE_BLOCKS = [MusicRange, NatureRange, MusicPicks, NaturePicks, DefinitionPicks, LinkPicks, LinksByTag]
