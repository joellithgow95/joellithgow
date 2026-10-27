/**
 * Astraeus CMS TypeScript types for joellithgow
 *
 * Field naming: snake_case (mirrors Python schema)
 * Date fields: ISO 8601 strings — wrap with new Date() before use
 */

/** Astraeus API document metadata fields (prefixed with _ to avoid collisions) */
export interface AstraeusDocumentMeta {
  _id: string;
  _doc_type?: string;
  _published?: boolean;
  _created_at?: string;
  _updated_at?: string;
}

/** Generic wrapper for any Astraeus document — intersection keeps all body fields flat */
export type AstraeusDocument<T> = T & AstraeusDocumentMeta;

export interface BlogImage {
  src: string;
  alt: string;
  type?: 'image' | 'video';
  fallbackSrc?: string;
  poster?: string;
  link?: string;
}

export interface LinkItem {
  url: string;
  title: string;
  description: string;
  author?: string;
  tags?: string[];
  /** ISO 8601 — wrap with new Date() */
  date_added: string;
  collections?: string[];
}

export interface BlogPost {
  slug: string;
  title: string;
  description: string;
  /** ISO 8601 — wrap with new Date() */
  publish_date: string;
  body_markdown?: string;
  excerpt?: string;
  author?: string;
  image?: BlogImage | null;
  links?: LinkItem[] | null;
  tags?: string[] | null;
  featured?: boolean;
  reading_time?: number | null;
}

export interface HeroMedia {
  src: string;
  alt: string;
  type: 'image' | 'video';
  fallbackSrc?: string;
  poster?: string;
  caption?: string;
}

export interface LiveLink {
  title: string;
  url: string;
  description?: string;
}

export interface LiveLinks {
  title: string;
  description?: string;
  links: Array<{ title: string; url: string }>;
}

/** Typed block for project body_blocks */
export interface BodyBlock {
  block_type: string;
  [key: string]: unknown;
}

export interface ProjectPage {
  slug: string;
  number: number;
  project_type: string;
  title: string;
  description: string;
  impact?: string;
  technologies?: string[] | null;
  subtitle?: string;
  overview?: string;
  hero_media?: HeroMedia | null;
  duration?: string;
  team?: string;
  role?: string;
  tools?: string;
  live_link?: LiveLink | null;
  live_links?: LiveLinks | null;
  /** ISO 8601 — wrap with new Date() */
  publish_date?: string;
  featured?: boolean;
  tags?: string[] | null;
  body_blocks?: BodyBlock[] | null;
}

export interface ExperienceEntry {
  slug: string;
  company: string;
  title: string;
  location?: string;
  /** Format: "2017" or "2017-03" */
  start_date: string;
  /** Format: "2017" or "2017-03" — absent means current role */
  end_date?: string;
  employment_type: 'full-time' | 'part-time' | 'contract' | 'student' | 'internship';
  description?: string | null;
  responsibilities?: string[];
  achievements?: string[];
  featured?: boolean;
  show_on_resume?: boolean;
  order?: number;
}

// ---------------------------------------------------------------------------
// Spotify liked dump
// ---------------------------------------------------------------------------

export interface SpotifySong {
  track_name: string;
  artist_name: string;
  album_name?: string;
  album_art_url?: string;
  spotify_url: string;
  liked_at: string;
}

export interface SpotifyDump {
  slug: string;
  title: string;
  description?: string;
  /** ISO 8601 — first day of the month (YYYY-MM-01) */
  publish_date: string;
  song_count?: number;
  songs?: SpotifySong[];
  tags?: string[] | null;
}

// ---------------------------------------------------------------------------
// iNaturalist outing
// ---------------------------------------------------------------------------

export interface NatureObservation {
  [key: string]: unknown;
}

export interface BoundingBox {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}

export interface NatureOuting {
  slug: string;
  title: string;
  description?: string;
  /** ISO 8601 — date of the outing */
  publish_date: string;
  /** Same as publish_date — explicit field for query clarity */
  outing_date: string;
  place_guess?: string;
  observation_count?: number;
  species_list?: string[];
  observations?: NatureObservation[];
  photo_urls?: string[];
  bounding_box?: BoundingBox;
  tags?: string[] | null;
}

// ---------------------------------------------------------------------------
// Definition / Glossary
// ---------------------------------------------------------------------------

export interface Source {
  type: 'link' | 'citation';
  url?: string;
  title?: string;
  attribution?: string;
  quote?: string;
  description?: string;
}

export interface Definition {
  slug: string;
  term: string;
  /** ISO 8601 */
  publish_date: string;
  /** Markdown — the actual definition */
  definition: string;
  /** Markdown — Joel's personal thoughts/context */
  personal_notes?: string;
  /** Mix of link sources and text citations */
  sources?: Source[] | null;
  tags?: string[] | null;
}
