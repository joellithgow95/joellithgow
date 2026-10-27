"""
Seed script — reads Markdown/MDX content files and POSTs to Astraeus CMS API.

Usage:
    python -m cms.seed --cms-url http://localhost:8000 --api-key YOUR_KEY
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import httpx as _http_lib
    _USE_HTTPX = True
except ImportError:
    import requests as _http_lib  # type: ignore[no-redef]
    _USE_HTTPX = False

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = REPO_ROOT / "src" / "content" / "blog"
PROJECTS_DIR = REPO_ROOT / "src" / "content" / "projects"
EXPERIENCE_DIR = REPO_ROOT / "src" / "content" / "experience"

# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ---…--- frontmatter from body. Returns (fm_dict, body_str)."""
    if not text.startswith("---"):
        return {}, text
    end = text.index("---", 3)
    fm_raw = text[3:end].strip()
    body = text[end + 3:].strip()
    if yaml is not None:
        fm = yaml.safe_load(fm_raw) or {}
    else:
        fm = _simple_yaml_parse(fm_raw)
    return fm, body


def _simple_yaml_parse(raw: str) -> dict[str, Any]:
    """
    Minimal YAML parser — handles flat key: value lines and simple lists.
    Falls back gracefully; structured sub-objects (like heroMedia) are skipped.
    """
    result: dict[str, Any] = {}
    lines = raw.splitlines()
    current_key: str | None = None
    current_list: list | None = None
    current_obj_key: str | None = None
    current_obj: dict | None = None
    indent_base = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        indent = len(line) - len(line.lstrip())

        # Top-level key: value
        if indent == 0 and ":" in stripped:
            # flush pending list / obj
            if current_list is not None and current_key:
                result[current_key] = current_list
                current_list = None
            if current_obj is not None and current_obj_key:
                result[current_obj_key] = current_obj
                current_obj = None

            key, _, rest = stripped.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "" or rest == "|" or rest == ">":
                # block scalar or nested — collect as list/dict on next lines
                current_key = key
                current_list = None
                current_obj = None
                current_obj_key = None
                indent_base = 0
            else:
                val = _cast_yaml_scalar(rest)
                result[key] = val
                current_key = key
                current_list = None
        elif indent > 0 and stripped.startswith("- "):
            # list item under current_key
            if current_list is None:
                current_list = []
                result[current_key] = current_list  # type: ignore[index]
            item_text = stripped[2:].strip()
            if ":" in item_text and not item_text.startswith('"') and not item_text.startswith("'"):
                # sub-object item — collect until indent drops
                obj: dict[str, Any] = {}
                k2, _, v2 = item_text.partition(":")
                obj[k2.strip()] = _cast_yaml_scalar(v2.strip())
                # peek next lines for more k:v at same+2 indent
                j = i + 1
                while j < len(lines):
                    nline = lines[j]
                    nstripped = nline.strip()
                    nindent = len(nline) - len(nline.lstrip())
                    if not nstripped or nindent <= indent:
                        break
                    if ":" in nstripped:
                        k3, _, v3 = nstripped.partition(":")
                        obj[k3.strip()] = _cast_yaml_scalar(v3.strip())
                    j += 1
                i = j - 1
                current_list.append(obj)
            else:
                current_list.append(_cast_yaml_scalar(item_text))
        elif indent > 0 and ":" in stripped:
            # nested k:v under current_key (e.g. heroMedia sub-fields)
            if current_obj is None:
                current_obj = {}
                current_obj_key = current_key
                result[current_key] = current_obj  # type: ignore[index]
            k2, _, v2 = stripped.partition(":")
            current_obj[k2.strip()] = _cast_yaml_scalar(v2.strip())
        i += 1

    return result


def _cast_yaml_scalar(val: str) -> Any:
    """Cast a raw YAML scalar string to Python type."""
    val = val.strip()
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    if val.lower() in ("null", "~", ""):
        return None
    # quoted string
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    # date: YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
        return val  # keep as string for CMS
    # year only: YYYY
    if re.match(r"^\d{4}$", val):
        return val
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return val


# ---------------------------------------------------------------------------
# MDX body_blocks parsing
# ---------------------------------------------------------------------------

COMPONENT_TO_BLOCK_TYPE: dict[str, str] = {
    "CaseStudyHero": "case_study_hero",
    "CaseStudyMeta": "case_study_meta",
    "ChallengeSection": "challenge_section",
    "ApproachSection": "approach_section",
    "ProcessSteps": "process_steps",
    "ImageGrid": "image_grid",
    "FullWidthMedia": "full_width_media",
    "InsightsSection": "insights_section",
    "MetricsGrid": "metrics_grid",
    "LiveLinkCTA": "live_link_cta",
    "LiveLinksSection": "live_links_cta",
    "VideoGallery": "video_gallery",
}

# Matches self-closing or open+close JSX components at top level
_COMPONENT_RE = re.compile(
    r"<(\w+)\s*((?:[^>]|\n)*?)(?:/>|>([\s\S]*?)</\1>)",
    re.MULTILINE,
)


def _resolve_prop_value(raw: str, frontmatter: dict[str, Any]) -> Any:
    """
    Resolve a single JSX prop value to a Python value.

    Handles:
    - {frontmatter.field}        -> frontmatter[field]
    - {frontmatter.field.sub}    -> frontmatter[field][sub]
    - "string literal"           -> str
    - 'string literal'           -> str
    - {[...]}                    -> parsed list
    - {true} / {false}           -> bool
    - plain number               -> float/int
    """
    raw = raw.strip()

    # Bare quoted strings (JSX attribute without braces: attr="value")
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]

    # {expr}
    if raw.startswith("{") and raw.endswith("}"):
        inner = raw[1:-1].strip()

        # frontmatter reference
        if inner.startswith("frontmatter."):
            path = inner[len("frontmatter."):].split(".")
            val: Any = frontmatter
            for part in path:
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = None
                if val is None:
                    break
            return val

        # bool literals
        if inner == "true":
            return True
        if inner == "false":
            return False

        # array literal
        if inner.startswith("["):
            return _parse_js_array(inner, frontmatter)

        # string literal inside braces
        if inner.startswith('"') and inner.endswith('"'):
            return inner[1:-1]
        if inner.startswith("'") and inner.endswith("'"):
            return inner[1:-1]

        # try number
        try:
            return int(inner)
        except ValueError:
            pass
        try:
            return float(inner)
        except ValueError:
            pass

        return inner

    # Unquoted value (shouldn't happen in JSX but be defensive)
    return raw


def _parse_js_array(text: str, frontmatter: dict[str, Any]) -> list:
    """
    Very simplified JS array parser. Handles:
    - Arrays of string literals: ["a", "b"]
    - Arrays of simple objects: [{ key: "val", key2: "val2" }, ...]
    - Nested values resolved via _resolve_prop_value
    """
    text = text.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return []
    inner = text[1:-1].strip()
    if not inner:
        return []

    # Tokenize at top-level commas, respecting brackets and quotes
    items = _split_top_level(inner, ",")
    result = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        if item.startswith("{") and item.endswith("}"):
            result.append(_parse_js_object(item[1:-1], frontmatter))
        elif item.startswith('"') and item.endswith('"'):
            result.append(item[1:-1])
        elif item.startswith("'") and item.endswith("'"):
            result.append(item[1:-1])
        else:
            result.append(item)
    return result


def _parse_js_object(text: str, frontmatter: dict[str, Any]) -> dict:
    """Parse a flat JS object body (without outer braces): key: "val", key2: "val2"."""
    obj: dict[str, Any] = {}
    pairs = _split_top_level(text, ",")
    for pair in pairs:
        pair = pair.strip()
        if not pair:
            continue
        # key: value  — split on first colon not inside a string
        colon_idx = _find_first_colon(pair)
        if colon_idx == -1:
            continue
        key = pair[:colon_idx].strip().strip('"\'')
        val_raw = pair[colon_idx + 1:].strip()
        obj[key] = _resolve_prop_value(val_raw, frontmatter)
    return obj


def _find_first_colon(text: str) -> int:
    """Return index of first colon not inside quotes or brackets."""
    depth = 0
    in_dq = False
    in_sq = False
    for i, ch in enumerate(text):
        if ch == '"' and not in_sq:
            in_dq = not in_dq
        elif ch == "'" and not in_dq:
            in_sq = not in_sq
        elif not in_dq and not in_sq:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == ":" and depth == 0:
                return i
    return -1


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split text on sep at depth 0 (ignores separators inside brackets/quotes)."""
    parts: list[str] = []
    depth = 0
    in_dq = False
    in_sq = False
    current: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"' and not in_sq:
            in_dq = not in_dq
        elif ch == "'" and not in_dq:
            in_sq = not in_sq
        elif not in_dq and not in_sq:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == sep and depth == 0:
                parts.append("".join(current))
                current = []
                i += 1
                continue
        current.append(ch)
        i += 1
    if current:
        parts.append("".join(current))
    return parts


def _parse_jsx_props(props_str: str, frontmatter: dict[str, Any]) -> dict[str, Any]:
    """
    Parse the props string of a JSX component into a dict.

    e.g.: title={frontmatter.title} subtitle="foo" steps={[...]}
    """
    props: dict[str, Any] = {}
    # Tokenise attribute assignments at top level
    # attr={...} or attr="..." or attr='...'
    pos = 0
    text = props_str.strip()

    while pos < len(text):
        # Skip whitespace
        while pos < len(text) and text[pos] in " \t\n\r":
            pos += 1
        if pos >= len(text):
            break

        # Read attribute name (up to '=')
        name_start = pos
        while pos < len(text) and text[pos] not in "= \t\n\r":
            pos += 1
        attr_name = text[name_start:pos].strip()
        if not attr_name:
            pos += 1
            continue

        # Skip whitespace
        while pos < len(text) and text[pos] in " \t\n\r":
            pos += 1

        if pos >= len(text) or text[pos] != "=":
            # boolean prop with no value
            props[attr_name] = True
            continue

        pos += 1  # skip '='

        # Skip whitespace
        while pos < len(text) and text[pos] in " \t\n\r":
            pos += 1

        if pos >= len(text):
            break

        # Read value
        val_str, new_pos = _read_jsx_value(text, pos)
        pos = new_pos
        props[attr_name] = _resolve_prop_value(val_str, frontmatter)

    return props


def _read_jsx_value(text: str, start: int) -> tuple[str, int]:
    """Read one JSX attribute value starting at start. Returns (raw_value, next_pos)."""
    pos = start
    ch = text[pos]

    if ch == '"':
        end = text.index('"', pos + 1)
        return text[pos:end + 1], end + 1

    if ch == "'":
        end = text.index("'", pos + 1)
        return text[pos:end + 1], end + 1

    if ch == "{":
        depth = 0
        in_dq = False
        in_sq = False
        i = pos
        while i < len(text):
            c = text[i]
            if c == '"' and not in_sq:
                in_dq = not in_dq
            elif c == "'" and not in_dq:
                in_sq = not in_sq
            elif not in_dq and not in_sq:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return text[pos:i + 1], i + 1
            i += 1
        return text[pos:], len(text)

    # Unquoted: read until whitespace
    end = pos
    while end < len(text) and text[end] not in " \t\n\r":
        end += 1
    return text[pos:end], end


def _strip_mdx_imports(body: str) -> str:
    """Remove import lines from MDX body."""
    lines = body.splitlines()
    out = []
    for line in lines:
        if re.match(r"^\s*import\s+", line):
            continue
        out.append(line)
    return "\n".join(out).strip()


def parse_mdx_body_blocks(body: str, frontmatter: dict[str, Any]) -> list[dict]:
    """
    Parse MDX body into a list of block dicts.

    Each block has at minimum: { block_type: str, ...props }
    Children text is stored as `children` key.
    """
    body = _strip_mdx_imports(body)
    blocks: list[dict[str, Any]] = []

    for match in _COMPONENT_RE.finditer(body):
        component_name = match.group(1)
        block_type = COMPONENT_TO_BLOCK_TYPE.get(component_name)
        if block_type is None:
            continue

        props_str = match.group(2) or ""
        children_text = (match.group(3) or "").strip()

        props = _parse_jsx_props(props_str, frontmatter)
        block: dict[str, Any] = {"block_type": block_type, **props}
        if children_text:
            block["children"] = children_text
        blocks.append(block)

    return blocks


# ---------------------------------------------------------------------------
# Content loaders
# ---------------------------------------------------------------------------

def _slug_from_path(path: Path) -> str:
    return path.stem


def load_blog_post(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)

    slug = _slug_from_path(path)
    # Normalize image field — drop 'link' sub-key (not in CMS schema)
    image = fm.get("image")
    if isinstance(image, dict):
        image = {k: v for k, v in image.items() if k != "link"}

    # Normalize links
    links = fm.get("links")
    if isinstance(links, list):
        normalized_links = []
        for link in links:
            if isinstance(link, dict):
                # dateAdded may be a date object or string — normalise to ISO str
                date_added = link.get("dateAdded")
                if hasattr(date_added, "isoformat"):
                    date_added = date_added.isoformat()
                elif date_added is not None:
                    date_added = str(date_added)
                entry: dict[str, Any] = {
                    "url": link.get("url"),
                    "title": link.get("title"),
                }
                if link.get("description") is not None:
                    entry["description"] = link["description"]
                if link.get("author") is not None:
                    entry["author"] = link["author"]
                if link.get("tags") is not None:
                    entry["tags"] = link["tags"]
                if date_added is not None:
                    entry["date_added"] = date_added
                if link.get("collections") is not None:
                    entry["collections"] = link["collections"]
                normalized_links.append(entry)
        links = normalized_links

    publish_date = fm.get("publishDate")
    if hasattr(publish_date, "isoformat"):
        publish_date = publish_date.isoformat()
    elif publish_date is not None:
        publish_date = str(publish_date)

    return {
        "slug": slug,
        "title": fm.get("title", ""),
        "description": fm.get("description", ""),
        "publish_date": publish_date or "",
        "body_markdown": body if body else None,
        "excerpt": fm.get("excerpt"),
        "author": fm.get("author", "Joel Lithgow"),
        "image": image,
        "links": links,
        "tags": fm.get("tags"),
        "featured": fm.get("featured", False),
        "reading_time": fm.get("readingTime"),
    }


def load_project(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)

    slug = fm.get("id") or _slug_from_path(path)

    # Normalize heroMedia
    hero_media = fm.get("heroMedia")
    if isinstance(hero_media, dict):
        hero_media = dict(hero_media)  # copy

    # Normalize liveLink
    live_link = fm.get("liveLink")
    # Normalize liveLinks
    live_links_raw = fm.get("liveLinks")
    live_links = None
    if isinstance(live_links_raw, dict):
        # Normalize nested links list
        links_list = live_links_raw.get("links", [])
        live_links = {
            "title": live_links_raw.get("title"),
            "description": live_links_raw.get("description"),
            "links": links_list if isinstance(links_list, list) else [],
        }

    publish_date = fm.get("publishDate")
    if hasattr(publish_date, "isoformat"):
        publish_date = publish_date.isoformat()
    elif publish_date is not None:
        publish_date = str(publish_date)

    # Parse body blocks from MDX
    body_blocks = parse_mdx_body_blocks(body, fm) if body else []

    return {
        "slug": slug,
        "number": fm.get("number", 0),
        "project_type": fm.get("type", ""),
        "title": fm.get("title", ""),
        "description": fm.get("description", ""),
        "impact": fm.get("impact", ""),
        "technologies": fm.get("technologies"),
        "subtitle": fm.get("subtitle", ""),
        "overview": fm.get("overview", ""),
        "hero_media": hero_media,
        "duration": fm.get("duration", ""),
        "team": fm.get("team", ""),
        "role": fm.get("role", ""),
        "tools": fm.get("tools", ""),
        "live_link": live_link,
        "live_links": live_links,
        "publish_date": publish_date,
        "featured": fm.get("featured", False),
        "tags": fm.get("tags"),
        "body_blocks": body_blocks if body_blocks else None,
    }


def _slugify_experience(company: str, title: str) -> str:
    """Generate a URL-safe slug from company + title."""
    raw = f"{company}-{title}".lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw)
    raw = raw.strip("-")
    return raw


def load_experience(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    fm, _ = _parse_frontmatter(text)

    company = fm.get("company", "")
    title = fm.get("title", "")
    slug = _slugify_experience(company, title)

    return {
        "slug": slug,
        "company": company,
        "title": title,
        "location": fm.get("location"),
        "start_date": str(fm.get("startDate", "")),
        "end_date": str(fm.get("endDate", "")) if fm.get("endDate") else None,
        "employment_type": fm.get("type", "full-time"),
        "description": fm.get("description"),
        "responsibilities": fm.get("responsibilities"),
        "achievements": fm.get("achievements"),
        "featured": fm.get("featured", False),
        "show_on_resume": fm.get("showOnResume", True),
        "order": fm.get("order"),
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class CMSClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        resp = _http_lib.request(method, url, headers=self.headers, **kwargs)
        return resp.status_code, resp.json() if resp.content else {}

    def get(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, data: Any, **kwargs: Any) -> tuple[int, Any]:
        return self._request("POST", path, json=data, **kwargs)

    def find_by_slug(self, doc_type: str, slug: str) -> str | None:
        """Return existing document ID if one exists with this slug, else None."""
        status, body = self.get(
            "/api/documents",
            params={"type": doc_type, "slug": slug},
        )
        if status == 200 and isinstance(body, dict):
            # API returns { documents: [...], total: N }
            items = body.get("documents") or body.get("items") or body.get("results") or []
            if isinstance(items, list) and items:
                return str(items[0].get("id"))
        return None

    def create_document(self, doc_type: str, fields: dict[str, Any]) -> tuple[str | None, str]:
        """POST /api/documents. Returns (id, error_message)."""
        # slug is a CMS system field — hoist it out of body into the top-level payload
        slug = fields.pop("slug", "") or ""
        payload = {"doc_type": doc_type, "slug": slug, "body": fields}
        status, body = self.post("/api/documents", payload)
        if status in (200, 201):
            doc_id = body.get("id") or (body.get("document") or {}).get("id")
            if doc_id:
                return str(doc_id), ""
            return None, f"No id in response: {body}"
        return None, f"HTTP {status}: {body}"

    def update_document(self, doc_id: str, fields: dict[str, Any]) -> str:
        """PATCH /api/documents/{id}. Returns error message or ''."""
        slug = fields.pop("slug", None)
        payload: dict[str, Any] = {"body": fields}
        if slug:
            payload["slug"] = slug
        status, body = self._request("PATCH", f"/api/documents/{doc_id}", json=payload)
        if status in (200, 201, 204):
            return ""
        return f"HTTP {status}: {body}"

    def publish_document(self, doc_id: str) -> str:
        """POST /api/documents/{id}/publish. Returns error message or ''."""
        status, body = self.post(f"/api/documents/{doc_id}/publish", {})
        if status in (200, 201, 204):
            return ""
        return f"HTTP {status}: {body}"


# ---------------------------------------------------------------------------
# Seed logic
# ---------------------------------------------------------------------------

def _strip_none_values(d: dict[str, Any]) -> dict[str, Any]:
    """Remove keys with None values so the API doesn't complain about missing required fields."""
    return {k: v for k, v in d.items() if v is not None}


def seed_document(
    client: CMSClient,
    doc_type: str,
    slug: str,
    fields: dict[str, Any],
    label: str,
    update: bool = False,
) -> bool:
    """
    Create + publish one document. Returns True on success.

    If update=True and a document with this slug already exists, patches it
    in place instead of skipping.
    """
    # Check for existing
    try:
        existing_id = client.find_by_slug(doc_type, slug)
    except Exception as exc:
        print(f"  [WARN] Could not check for existing {label}: {exc}")
        existing_id = None

    if existing_id:
        if not update:
            print(f"  [SKIP] {label} — already exists (id={existing_id})")
            return True
        # Update in place
        clean_fields = _strip_none_values(fields)
        err = client.update_document(existing_id, clean_fields)
        if err:
            print(f"  [FAIL] Update {label} (id={existing_id}): {err}")
            return False
        pub_err = client.publish_document(existing_id)
        if pub_err:
            print(f"  [WARN] Publish {label} (id={existing_id}): {pub_err}")
        else:
            print(f"  [UPDATE] {label} (id={existing_id})")
        return True

    # Create
    clean_fields = _strip_none_values(fields)
    doc_id, err = client.create_document(doc_type, clean_fields)
    if err or not doc_id:
        print(f"  [FAIL] Create {label}: {err}")
        return False

    # Publish
    pub_err = client.publish_document(doc_id)
    if pub_err:
        print(f"  [WARN] Publish {label} (id={doc_id}): {pub_err}")
        # Don't treat publish failure as fatal — document was created
    else:
        print(f"  [OK]   {label} (id={doc_id})")

    return True


def seed_chat_config(client: CMSClient) -> None:
    """Seed the default ModelConfig and SystemPrompt for the chat session."""
    print("\n=== Chat config ===")
    seed_document(
        client, "model_config", "default",
        {
            "slug": "default",
            "persona": "default",
            "provider": "openai",
            "model_name": os.environ.get("CHAT_MODEL", "local-model"),
            "temperature": 1.0,
            "max_tokens": 4096,
        },
        "model_config/default",
    )
    seed_document(
        client, "system_prompt", "default",
        {
            "slug": "default",
            "persona": "default",
            "content": (
                "You are an AI editor assistant for Joel Lithgow's personal site. "
                "You help draft, edit, and improve content. Be concise and direct. "
                "When editing documents, explain your changes briefly."
            ),
            "change_rationale": "Initial default prompt",
            "authored_by": "joel",
        },
        "system_prompt/default",
    )


def run_seed(cms_url: str, api_key: str, update: bool = False) -> None:
    client = CMSClient(cms_url, api_key)

    # --- Blog posts ---
    blog_files = sorted(BLOG_DIR.glob("*.md")) + sorted(BLOG_DIR.glob("*.mdx"))
    print(f"\n=== Blog posts ({len(blog_files)} files) ===")
    for path in blog_files:
        label = f"blog/{path.name}"
        try:
            data = load_blog_post(path)
            slug = data["slug"]
            seed_document(client, "blog_post", slug, data, label, update=update)
        except Exception as exc:
            print(f"  [FAIL] Load {label}: {exc}")

    # --- Projects ---
    project_files = sorted(PROJECTS_DIR.glob("*.md")) + sorted(PROJECTS_DIR.glob("*.mdx"))
    print(f"\n=== Projects ({len(project_files)} files) ===")
    for path in project_files:
        label = f"projects/{path.name}"
        try:
            data = load_project(path)
            slug = data["slug"]
            seed_document(client, "project_page", slug, data, label, update=update)
        except Exception as exc:
            print(f"  [FAIL] Load {label}: {exc}")

    # --- Experience ---
    experience_files = sorted(EXPERIENCE_DIR.glob("*.md")) + sorted(EXPERIENCE_DIR.glob("*.mdx"))
    print(f"\n=== Experience ({len(experience_files)} files) ===")
    for path in experience_files:
        label = f"experience/{path.name}"
        try:
            data = load_experience(path)
            slug = data["slug"]
            seed_document(client, "experience_entry", slug, data, label, update=update)
        except Exception as exc:
            print(f"  [FAIL] Load {label}: {exc}")

    print("\nSeed complete.")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed CMS with content from Astro content files")
    parser.add_argument("--cms-url", default="http://localhost:8000", help="Base URL of the CMS API")
    parser.add_argument("--api-key", required=True, help="API key (X-API-Key header)")
    parser.add_argument("--dry-run", action="store_true", help="Parse files but do not make HTTP calls")
    parser.add_argument("--update", action="store_true", help="Patch existing documents instead of skipping them")
    parser.add_argument("--chat-config", action="store_true", help="Seed ModelConfig + SystemPrompt for AI chat")
    args = parser.parse_args()

    if args.dry_run:
        _dry_run()
        return

    if args.chat_config:
        client = CMSClient(args.cms_url, args.api_key)
        seed_chat_config(client)
        print("\nChat config seed complete.")
        return

    run_seed(args.cms_url, args.api_key, update=args.update)


def _dry_run() -> None:
    """Parse all files and print JSON to stdout without making any HTTP calls."""
    import json

    all_docs: list[dict] = []

    blog_files = sorted(BLOG_DIR.glob("*.md")) + sorted(BLOG_DIR.glob("*.mdx"))
    for path in blog_files:
        try:
            data = load_blog_post(path)
            all_docs.append({"type": "blog_post", "fields": data})
            print(f"[DRY] blog/{path.name}: ok")
        except Exception as exc:
            print(f"[DRY] blog/{path.name}: ERROR {exc}", file=sys.stderr)

    project_files = sorted(PROJECTS_DIR.glob("*.md")) + sorted(PROJECTS_DIR.glob("*.mdx"))
    for path in project_files:
        try:
            data = load_project(path)
            all_docs.append({"type": "project_page", "fields": data})
            print(f"[DRY] projects/{path.name}: ok")
        except Exception as exc:
            print(f"[DRY] projects/{path.name}: ERROR {exc}", file=sys.stderr)

    experience_files = sorted(EXPERIENCE_DIR.glob("*.md")) + sorted(EXPERIENCE_DIR.glob("*.mdx"))
    for path in experience_files:
        try:
            data = load_experience(path)
            all_docs.append({"type": "experience_entry", "fields": data})
            print(f"[DRY] experience/{path.name}: ok")
        except Exception as exc:
            print(f"[DRY] experience/{path.name}: ERROR {exc}", file=sys.stderr)

    print(f"\n--- Parsed {len(all_docs)} documents ---")
    print(json.dumps(all_docs, indent=2, default=str))


if __name__ == "__main__":
    main()
