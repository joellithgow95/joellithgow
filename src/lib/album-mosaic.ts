/**
 * Group liked songs by album so duplicates collapse into one cover.
 * `count` = how many liked songs came from that album (drives the cover size).
 * Works on legacy month songs and on `spotify_like` documents alike.
 */

export interface MosaicSong {
  track_name: string;
  artist_name?: string | null;
  album_name?: string | null;
  album_art_url?: string | null;
  spotify_url?: string | null;
}

export type AlbumCell = { art: string; name: string; artist: string; count: number; href?: string };

export function albumMosaic(songs: MosaicSong[]): AlbumCell[] {
  const map = new Map<string, AlbumCell>();
  for (const s of songs) {
    if (!s.album_art_url) continue;
    const key = s.album_name || s.album_art_url;
    const existing = map.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      map.set(key, {
        // Stored art is Spotify's 64px thumbnail (…00004851); swap to the
        // 640px variant (…0000b273) so enlarged mosaic cells stay crisp.
        art: s.album_art_url.replace('ab67616d00004851', 'ab67616d0000b273'),
        name: s.album_name || s.track_name,
        artist: s.artist_name ?? '',
        count: 1,
        href: s.spotify_url ?? undefined,
      });
    }
  }
  // Biggest first so dense packing backfills the gaps neatly.
  return Array.from(map.values()).sort((a, b) => b.count - a.count);
}

/** Cover size in grid cells: 1, 2 or 3 per side. */
export const coverSpan = (count: number) => (count >= 4 ? 3 : count >= 2 ? 2 : 1);
