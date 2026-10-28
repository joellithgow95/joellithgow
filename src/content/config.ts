import { defineCollection, z } from 'astro:content';
import { loadAstraeusDocuments } from '../lib/astraeus-loader';

// ---------------------------------------------------------------------------
// Shared sub-schemas (mirror astraeus-types.ts interfaces)
// ---------------------------------------------------------------------------

const blogImageSchema = z.object({
  src: z.string(),
  alt: z.string(),
  type: z.enum(['image', 'video']).optional(),
  fallbackSrc: z.string().optional(),
  poster: z.string().optional(),
  link: z.string().optional(),
});

const linkItemSchema = z.object({
  url: z.string(),
  title: z.string(),
  description: z.string().optional(),
  author: z.string().optional(),
  tags: z.array(z.string()).optional(),
  date_added: z.string(),
  collections: z.array(z.string()).optional(),
});

const heroMediaSchema = z.object({
  src: z.string(),
  alt: z.string(),
  type: z.enum(['image', 'video']),
  fallbackSrc: z.string().optional(),
  poster: z.string().optional(),
  caption: z.string().optional(),
});

const liveLinkSchema = z.object({
  title: z.string(),
  url: z.string(),
  description: z.string().optional(),
});

const liveLinksSchema = z.object({
  title: z.string(),
  description: z.string().optional(),
  links: z.array(z.object({ title: z.string(), url: z.string() })),
});

const bodyBlockSchema = z.object({
  block_type: z.string(),
}).catchall(z.unknown());

// ---------------------------------------------------------------------------
// Blog post schema
// ---------------------------------------------------------------------------

const blogPostSchema = z.object({
  slug: z.string(),  // convenience alias for entry.id, injected by loader from CMS system field
  title: z.string(),
  description: z.string(),
  /** ISO 8601 — wrap with new Date() at use site */
  publish_date: z.string(),
  body_markdown: z.string().optional(),
  excerpt: z.string().optional(),
  author: z.string().optional(),
  image: blogImageSchema.optional(),
  links: z.array(linkItemSchema).optional(),
  tags: z.array(z.string()).optional(),
  /** Slices (cms/slices.py). The CMS validates them on save, so only the
   *  discriminator is checked here: an unexpected shape is skipped by
   *  src/lib/slices.ts rather than failing the build. */
  data: z.array(z.object({ block_type: z.string() }).passthrough()).nullable().optional(),
  featured: z.boolean().optional(),
  /** null = reading time explicitly unset; absent = not calculated */
  reading_time: z.number().nullable().optional(),
  // CMS metadata
  _id: z.string(),
  _published: z.boolean().optional(),
  _created_at: z.string().optional(),
  _updated_at: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Project page schema
// ---------------------------------------------------------------------------

const projectPageSchema = z.object({
  slug: z.string(),  // convenience alias for entry.id, injected by loader from CMS system field
  number: z.number(),
  project_type: z.string(),
  title: z.string(),
  description: z.string(),
  impact: z.string().optional(),
  technologies: z.array(z.string()).optional(),
  subtitle: z.string().optional(),
  overview: z.string().optional(),
  hero_media: heroMediaSchema.optional(),
  duration: z.string().optional(),
  team: z.string().optional(),
  role: z.string().optional(),
  tools: z.string().optional(),
  live_link: liveLinkSchema.optional(),
  live_links: liveLinksSchema.optional(),
  publish_date: z.string().optional(),
  featured: z.boolean().optional(),
  tags: z.array(z.string()).optional(),
  body_blocks: z.array(bodyBlockSchema).optional(),
  // CMS metadata
  _id: z.string(),
  _published: z.boolean().optional(),
  _created_at: z.string().optional(),
  _updated_at: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Experience entry schema
// ---------------------------------------------------------------------------

const experienceEntrySchema = z.object({
  slug: z.string(),  // convenience alias for entry.id, injected by loader from CMS system field
  company: z.string(),
  title: z.string(),
  location: z.string().optional(),
  /** Format: "2017" or "2017-03" */
  start_date: z.string(),
  /** Format: "2017" or "2017-03" — null means current role (intentional), absent means unknown */
  end_date: z.string().nullable().optional(),
  employment_type: z.enum(['full-time', 'part-time', 'contract', 'student', 'internship']),
  description: z.string().optional(),
  responsibilities: z.array(z.string()).optional(),
  achievements: z.array(z.string()).optional(),
  featured: z.boolean().optional(),
  show_on_resume: z.boolean().optional(),
  order: z.number().optional(),
  // CMS metadata
  _id: z.string(),
  _published: z.boolean().optional(),
  _created_at: z.string().optional(),
  _updated_at: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Interview round types for applications collection (unchanged)
// ---------------------------------------------------------------------------

const interviewRoundSchema = z.enum([
  'phone-screen',
  'recruiter-call',
  'technical',
  'take-home',
  'onsite',
  'hiring-manager',
  'team-panel',
  'final',
  'other'
]);

const applicationStatusEnum = z.enum([
  'draft',
  'preparing',
  'applied',
  'interviewing',
  'offered',
  'rejected',
  'withdrawn',
  'ghosted'
]);

const statusEventSchema = z.object({
  status: applicationStatusEnum,
  date: z.date(),
  round: interviewRoundSchema.optional(),
  notes: z.string().optional(),
});

const applicationSchema = z.object({
  company: z.string(),
  position: z.string(),
  location: z.string(),
  salary: z.string().optional(),
  jobUrl: z.string().url(),
  status: applicationStatusEnum.default('draft'),
  statusHistory: z.array(statusEventSchema).default([]),
  appliedDate: z.date().optional(),
  deadline: z.date().optional(),
  coverLetterDate: z.date().optional(),
  coverLetter: z.object({
    opening: z.string().optional(),
    paragraphs: z.array(z.string()).optional(),
    closing: z.string().optional(),
  }).optional(),
  resume: z.object({
    summary: z.string().optional(),
    skills: z.object({
      frontend: z.string().optional(),
      testing: z.string().optional(),
      cloud: z.string().optional(),
      tools: z.string().optional(),
      accessibility: z.string().optional(),
      learning: z.string().optional(),
      creative: z.string().optional(),
      ai: z.string().optional(),
    }).optional(),
  }).optional(),
  featured: z.boolean().default(false),
  public: z.boolean().default(true),
  fit: z.enum(['very-strong', 'strong', 'moderate', 'stretch']).optional(),
  description: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Definition / Glossary schema
// ---------------------------------------------------------------------------

const sourceSchema = z.object({
  type: z.enum(['link', 'citation']),
  url: z.string().optional(),
  title: z.string().optional(),
  attribution: z.string().optional(),
  quote: z.string().optional(),
  description: z.string().optional(),
});

const definitionSchema = z.object({
  slug: z.string(),
  term: z.string(),
  /** ISO 8601 */
  publish_date: z.string(),
  /** Markdown — the actual definition text */
  definition: z.string(),
  /** Markdown — Joel's personal thoughts/context */
  personal_notes: z.string().optional(),
  /** Mix of link sources and text citations */
  sources: z.array(sourceSchema).optional(),
  tags: z.array(z.string()).optional(),
  // CMS metadata
  _id: z.string(),
  _published: z.boolean().optional(),
  _created_at: z.string().optional(),
  _updated_at: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Data documents (ADR 026): one per item, shown only through posts' slices.
// Lenient on purpose: every field the gateway may leave empty is optional, so
// one odd record cannot fail the build. Keyed by document id, which is what a
// picked slice stores.
// ---------------------------------------------------------------------------

const dataMeta = {
  _id: z.string(),
  _published: z.boolean().optional(),
  _created_at: z.string().optional(),
  _updated_at: z.string().optional(),
};

const spotifyLikeSchema = z.object({
  title: z.string().nullish(),
  track_id: z.string(),
  track_name: z.string(),
  artist_name: z.string().nullish(),
  album_name: z.string().nullish(),
  album_art_url: z.string().nullish(),
  spotify_url: z.string().nullish(),
  liked_at: z.string(),
  ...dataMeta,
});

const inatObservationSchema = z.object({
  title: z.string().nullish(),
  observation_id: z.number().nullish(),
  uri: z.string().nullish(),
  observed_on: z.string(),
  time_observed_at: z.string().nullish(),
  taxon_name: z.string().nullish(),
  common_name: z.string().nullish(),
  iconic_taxon: z.string().nullish(),
  place_guess: z.string().nullish(),
  lat: z.number().nullish(),
  lon: z.number().nullish(),
  photo_urls: z.array(z.string()).nullish(),
  tags: z.array(z.string()).nullish(),
  ...dataMeta,
});

const linkDocSchema = z.object({
  title: z.string(),
  url: z.string(),
  description: z.string().nullish(),
  date_added: z.string(),
  tags: z.array(z.string()).nullish(),
  ...dataMeta,
});

/** A loader for a data type: entries keyed by CMS document id, not slug. */
function dataLoader(docType: string) {
  return async () => {
    const docs = await loadAstraeusDocuments(docType);
    return docs.map((d) => ({
      id: d.id,
      ...(d.body as Record<string, unknown>),
      _id: d.id,
      _published: d.published,
      _created_at: d.created_at,
      _updated_at: d.updated_at,
    }));
  };
}

// ---------------------------------------------------------------------------
// Collection definitions
// ---------------------------------------------------------------------------

export const collections = {
  // ── Local MDX (unchanged) ────────────────────────────────────────────────
  applications: defineCollection({
    type: 'content',
    schema: applicationSchema,
  }),

  // ── Astraeus CMS — blog posts ─────────────────────────────────────────────
  blog: defineCollection({
    loader: async () => {
      const docs = await loadAstraeusDocuments('blog_post');
      return docs.map((d) => ({
        id: d.slug,
        slug: d.slug,  // convenience alias for entry.id — used by page routes and components
        ...(d.body as Record<string, unknown>),
        _id: d.id,
        _published: d.published,
        _created_at: d.created_at,
        _updated_at: d.updated_at,
      }));
    },
    schema: blogPostSchema,
  }),

  // ── Astraeus CMS — project pages ─────────────────────────────────────────
  projects: defineCollection({
    loader: async () => {
      const docs = await loadAstraeusDocuments('project_page');
      return docs.map((d) => ({
        id: d.slug,
        slug: d.slug,  // convenience alias for entry.id — used by page routes and components
        ...(d.body as Record<string, unknown>),
        _id: d.id,
        _published: d.published,
        _created_at: d.created_at,
        _updated_at: d.updated_at,
      }));
    },
    schema: projectPageSchema,
  }),

  // ── Astraeus CMS — experience entries ────────────────────────────────────
  experience: defineCollection({
    loader: async () => {
      const docs = await loadAstraeusDocuments('experience_entry');
      return docs.map((d) => ({
        id: d.slug,
        slug: d.slug,  // convenience alias for entry.id — used by page routes and components
        ...(d.body as Record<string, unknown>),
        _id: d.id,
        _published: d.published,
        _created_at: d.created_at,
        _updated_at: d.updated_at,
      }));
    },
    schema: experienceEntrySchema,
  }),

  // ── Astraeus CMS — definitions / glossary ────────────────────────────────
  definitions: defineCollection({
    loader: async () => {
      const docs = await loadAstraeusDocuments('definition');
      return docs.map((d) => ({
        id: d.slug,
        slug: d.slug,
        ...(d.body as Record<string, unknown>),
        _id: d.id,
        _published: d.published,
        _created_at: d.created_at,
        _updated_at: d.updated_at,
      }));
    },
    schema: definitionSchema,
  }),

  // ── Astraeus CMS — data documents (ADR 026) ──────────────────────────────
  spotify_likes: defineCollection({ loader: dataLoader('spotify_like'), schema: spotifyLikeSchema }),
  inat_observations: defineCollection({ loader: dataLoader('inat_observation'), schema: inatObservationSchema }),
  links: defineCollection({ loader: dataLoader('link'), schema: linkDocSchema }),
};

// ---------------------------------------------------------------------------
// Re-exported types (kept for backward compat; prefer z.infer<> at use sites)
// ---------------------------------------------------------------------------

export type Application = z.infer<typeof applicationSchema>;
export type InterviewRound = z.infer<typeof interviewRoundSchema>;
export type StatusEvent = z.infer<typeof statusEventSchema>;
export type BlogPost = z.infer<typeof blogPostSchema>;
export type ProjectPage = z.infer<typeof projectPageSchema>;
export type ExperienceEntry = z.infer<typeof experienceEntrySchema>;
export type Definition = z.infer<typeof definitionSchema>;