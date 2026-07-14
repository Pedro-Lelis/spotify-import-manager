"""
ETL: Spotify Web API -> Postgres (catalogo)

Pega URIs ja gravados nas tabelas (listen, playlist_track, library_item) e
busca os metadados completos via Spotify Web API.

Fases:
  1. Tracks   -> /tracks?ids=...   popula track + stubs em artist e album
  2. Artists  -> /artists?ids=...  enriquece artist + popula genre + artist_genre
  3. Albums   -> /albums?ids=...   enriquece album com label/image_url
  4. SKIP audio_features (deprecado pra apps pos-nov/2024 - retorna 403)
  5. Backfill -> UPDATE listen.track_id e playlist_track.track_id

Idempotente: pode interromper e re-rodar a qualquer momento.

Como rodar standalone:
    py enrich_catalog.py

Ou via pipeline:
    from etl import enrich_catalog
    stats = enrich_catalog.run(conn, client_id, client_secret, log=print)

Configuracao standalone via env vars:
    SPOTIFY_DSN            (default: claude_etl@localhost/spotify)
    SPOTIFY_CLIENT_ID      (obrigatorio no modo standalone)
    SPOTIFY_CLIENT_SECRET  (obrigatorio no modo standalone)
"""

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
import requests


# ============================================================
# CONFIG (modo standalone)
# ============================================================

DSN = os.environ.get(
    "SPOTIFY_DSN",
    "postgresql://claude_etl:umasenhaetl@localhost:5432/spotify",
)

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"


# ============================================================
# TOKEN
# ============================================================

class Token:
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self.value = None
        self.expires_at = datetime.min.replace(tzinfo=timezone.utc)

    def get(self) -> str:
        now = datetime.now(timezone.utc)
        if self.value and now < self.expires_at - timedelta(seconds=60):
            return self.value
        r = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            timeout=30,
        )
        r.raise_for_status()
        j = r.json()
        self.value = j["access_token"]
        self.expires_at = now + timedelta(seconds=j["expires_in"])
        return self.value


# ============================================================
# HELPERS
# ============================================================

def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


URI_RE = re.compile(r"^spotify:(track|album|artist):([0-9A-Za-z]{22})$")


def _uri_to_id(uri):
    if not uri:
        return None
    m = URI_RE.match(uri)
    return m.group(2) if m else None


def _first_image(images):
    return images[0]["url"] if images else None


def _parse_release_date(s, precision):
    if not s:
        return None
    if precision == "year" or len(s) == 4:
        return f"{s}-01-01"
    if precision == "month" or len(s) == 7:
        return f"{s}-01"
    return s


# ============================================================
# ENRICHER (encapsula token + api_get + todas as fases)
# ============================================================

class CatalogEnricher:
    def __init__(self, conn, client_id: str, client_secret: str, log=print):
        self.conn = conn
        self.log = log
        self.token = Token(client_id, client_secret)

    # ----------------------------------------------------------
    # HTTP helper
    # ----------------------------------------------------------

    def api_get(self, path, params=None):
        """GET na API com retry pra 429/401, e None em 404."""
        for attempt in range(5):
            r = requests.get(
                f"{API}{path}",
                headers={"Authorization": f"Bearer {self.token.get()}"},
                params=params,
                timeout=60,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:
                self.token.value = None
                continue
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "1"))
                self.log(f"    rate limit, esperando {wait}s")
                time.sleep(wait + 1)
                continue
            if r.status_code == 404:
                return None
            self.log(f"    HTTP {r.status_code}: {r.text[:200]}")
            if attempt == 4:
                r.raise_for_status()
            time.sleep(2 ** attempt)

    # ----------------------------------------------------------
    # Fase 1: tracks
    # ----------------------------------------------------------

    def phase1_tracks(self) -> dict:
        log = self.log
        log("\n=== FASE 1: tracks ===")
        cur = self.conn.cursor()

        cur.execute("""
            SELECT DISTINCT uri FROM (
                SELECT track_uri AS uri FROM listen WHERE track_uri IS NOT NULL
                UNION
                SELECT track_uri FROM playlist_track WHERE track_uri IS NOT NULL
                UNION
                SELECT item_uri FROM library_item WHERE item_type = 'track'
            ) t
        """)
        all_uris = [row[0] for row in cur.fetchall()]

        track_ids = [_uri_to_id(u) for u in all_uris if _uri_to_id(u)]
        skipped = len(all_uris) - len(track_ids)
        log(f"  URIs distintos: {len(all_uris)}  (validos: {len(track_ids)}, ignorados: {skipped})")

        if not track_ids:
            return {"tracks": 0, "artists": 0, "albums": 0}

        cur.execute("SELECT id FROM track WHERE id = ANY(%s)", (track_ids,))
        existing = {row[0] for row in cur.fetchall()}
        to_fetch = [tid for tid in track_ids if tid not in existing]
        log(f"  ja na DB: {len(existing)}    a buscar: {len(to_fetch)}")

        if not to_fetch:
            return {"tracks": 0, "artists": 0, "albums": 0}

        n_batches = (len(to_fetch) + 49) // 50
        inserted_tracks = inserted_artists = inserted_albums = 0

        for batch_idx, batch in enumerate(_chunks(to_fetch, 50), 1):
            data = self.api_get("/tracks", params={"ids": ",".join(batch)})
            if not data:
                continue

            tracks = [t for t in data["tracks"] if t]
            artist_rows, album_rows, track_rows = [], [], []
            track_artist_rows, album_artist_rows = [], []
            seen_artists, seen_albums = set(), set()

            for t in tracks:
                for pos, a in enumerate(t["artists"]):
                    if a["id"] not in seen_artists:
                        seen_artists.add(a["id"])
                        artist_rows.append((a["id"], a["name"]))
                    track_artist_rows.append((t["id"], a["id"], pos))

                alb = t["album"]
                if alb["id"] not in seen_albums:
                    seen_albums.add(alb["id"])
                    album_rows.append((
                        alb["id"],
                        alb["name"],
                        alb.get("album_type"),
                        _parse_release_date(alb.get("release_date"), alb.get("release_date_precision")),
                        alb.get("release_date_precision"),
                        alb.get("total_tracks"),
                        _first_image(alb.get("images") or []),
                    ))
                    for pos, a in enumerate(alb["artists"]):
                        if a["id"] not in seen_artists:
                            seen_artists.add(a["id"])
                            artist_rows.append((a["id"], a["name"]))
                        album_artist_rows.append((alb["id"], a["id"], pos))

                track_rows.append((
                    t["id"],
                    t["name"],
                    alb["id"],
                    t["duration_ms"],
                    t.get("explicit"),
                    t.get("popularity"),
                    t.get("disc_number"),
                    t.get("track_number"),
                    (t.get("external_ids") or {}).get("isrc"),
                    t.get("preview_url"),
                ))

            if artist_rows:
                execute_values(
                    cur,
                    "INSERT INTO artist (id, name) VALUES %s ON CONFLICT (id) DO NOTHING",
                    artist_rows,
                )
                inserted_artists += cur.rowcount

            if album_rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO album (id, name, album_type, release_date,
                                       release_date_precision, total_tracks, image_url)
                    VALUES %s
                    ON CONFLICT (id) DO NOTHING
                    """,
                    album_rows,
                )
                inserted_albums += cur.rowcount

            if track_rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO track (id, name, album_id, duration_ms, explicit,
                                       popularity, disc_number, track_number, isrc,
                                       preview_url, fetched_at)
                    VALUES %s
                    ON CONFLICT (id) DO NOTHING
                    """,
                    [r + (datetime.now(timezone.utc),) for r in track_rows],
                )
                inserted_tracks += cur.rowcount

            if track_artist_rows:
                execute_values(
                    cur,
                    "INSERT INTO track_artist (track_id, artist_id, position) VALUES %s ON CONFLICT (track_id, artist_id) DO NOTHING",
                    track_artist_rows,
                )

            if album_artist_rows:
                execute_values(
                    cur,
                    "INSERT INTO album_artist (album_id, artist_id, position) VALUES %s ON CONFLICT (album_id, artist_id) DO NOTHING",
                    album_artist_rows,
                )

            if batch_idx % 10 == 0 or batch_idx == n_batches:
                log(f"  batch {batch_idx:>3}/{n_batches}  tracks={inserted_tracks}  artists={inserted_artists}  albums={inserted_albums}")
                self.conn.commit()

        self.conn.commit()
        log(f"  TOTAL: {inserted_tracks} tracks, {inserted_artists} artists, {inserted_albums} albums")
        return {"tracks": inserted_tracks, "artists": inserted_artists, "albums": inserted_albums}

    # ----------------------------------------------------------
    # Fase 2: artists
    # ----------------------------------------------------------

    def phase2_artists(self) -> dict:
        log = self.log
        log("\n=== FASE 2: artists ===")
        cur = self.conn.cursor()

        cur.execute("SELECT id FROM artist WHERE fetched_at IS NULL ORDER BY id")
        ids = [row[0] for row in cur.fetchall()]
        log(f"  artists a enriquecer: {len(ids)}")
        if not ids:
            return {"artist_updates": 0, "genre_links": 0}

        n_batches = (len(ids) + 49) // 50
        enriched = 0
        new_genre_links = 0

        for batch_idx, batch in enumerate(_chunks(ids, 50), 1):
            data = self.api_get("/artists", params={"ids": ",".join(batch)})
            if not data:
                continue
            artists = [a for a in data["artists"] if a]

            all_genres = set()
            for a in artists:
                for g in a.get("genres") or []:
                    all_genres.add(g)
            if all_genres:
                execute_values(
                    cur,
                    "INSERT INTO genre (name) VALUES %s ON CONFLICT (name) DO NOTHING",
                    [(g,) for g in all_genres],
                )

            gmap = {}
            if all_genres:
                cur.execute("SELECT id, name FROM genre WHERE name = ANY(%s)", (list(all_genres),))
                gmap = {n: gid for gid, n in cur.fetchall()}

            for a in artists:
                cur.execute(
                    """
                    UPDATE artist
                       SET popularity = %s,
                           followers  = %s,
                           image_url  = %s,
                           fetched_at = now()
                     WHERE id = %s
                    """,
                    (
                        a.get("popularity"),
                        (a.get("followers") or {}).get("total"),
                        _first_image(a.get("images") or []),
                        a["id"],
                    ),
                )
                enriched += 1

            ag_rows = [
                (a["id"], gmap[g])
                for a in artists
                for g in (a.get("genres") or [])
                if g in gmap
            ]
            if ag_rows:
                execute_values(
                    cur,
                    "INSERT INTO artist_genre (artist_id, genre_id) VALUES %s ON CONFLICT DO NOTHING",
                    ag_rows,
                )
                new_genre_links += cur.rowcount

            if batch_idx % 5 == 0 or batch_idx == n_batches:
                log(f"  batch {batch_idx:>3}/{n_batches}  enriched={enriched}  genre_links={new_genre_links}")
                self.conn.commit()

        self.conn.commit()
        log(f"  TOTAL: {enriched} artists, {new_genre_links} artist_genre links")
        return {"artist_updates": enriched, "genre_links": new_genre_links}

    # ----------------------------------------------------------
    # Fase 3: albums
    # ----------------------------------------------------------

    def phase3_albums(self) -> dict:
        log = self.log
        log("\n=== FASE 3: albums ===")
        cur = self.conn.cursor()

        cur.execute("SELECT id FROM album WHERE fetched_at IS NULL ORDER BY id")
        ids = [row[0] for row in cur.fetchall()]
        log(f"  albums a enriquecer: {len(ids)}")
        if not ids:
            return {"album_updates": 0}

        n_batches = (len(ids) + 19) // 20
        enriched = 0

        for batch_idx, batch in enumerate(_chunks(ids, 20), 1):
            data = self.api_get("/albums", params={"ids": ",".join(batch)})
            if not data:
                continue
            albums = [a for a in data["albums"] if a]

            for a in albums:
                cur.execute(
                    """
                    UPDATE album
                       SET label        = %s,
                           total_tracks = %s,
                           image_url    = COALESCE(%s, image_url),
                           fetched_at   = now()
                     WHERE id = %s
                    """,
                    (
                        a.get("label"),
                        a.get("total_tracks"),
                        _first_image(a.get("images") or []),
                        a["id"],
                    ),
                )
                enriched += 1

            if batch_idx % 10 == 0 or batch_idx == n_batches:
                log(f"  batch {batch_idx:>3}/{n_batches}  enriched={enriched}")
                self.conn.commit()

        self.conn.commit()
        log(f"  TOTAL: {enriched} albums")
        return {"album_updates": enriched}

    # ----------------------------------------------------------
    # Fase 5: backfill
    # ----------------------------------------------------------

    def phase5_backfill(self) -> dict:
        log = self.log
        log("\n=== FASE 5: backfill track_id ===")
        cur = self.conn.cursor()

        cur.execute("""
            UPDATE listen
               SET track_id = SUBSTRING(track_uri FROM 15)
             WHERE track_uri LIKE 'spotify:track:%'
               AND track_id IS NULL
               AND EXISTS (
                   SELECT 1 FROM track WHERE track.id = SUBSTRING(listen.track_uri FROM 15)
               )
        """)
        n_listen = cur.rowcount
        log(f"  listen:         {n_listen} linhas atualizadas")

        cur.execute("""
            UPDATE playlist_track
               SET track_id = SUBSTRING(track_uri FROM 15)
             WHERE track_uri LIKE 'spotify:track:%'
               AND track_id IS NULL
               AND EXISTS (
                   SELECT 1 FROM track WHERE track.id = SUBSTRING(playlist_track.track_uri FROM 15)
               )
        """)
        n_playlist = cur.rowcount
        log(f"  playlist_track: {n_playlist} linhas atualizadas")

        self.conn.commit()
        return {"backfill_listen": n_listen, "backfill_playlist": n_playlist}

    # ----------------------------------------------------------
    # Execucao completa
    # ----------------------------------------------------------

    def run(self) -> dict:
        stats = {}
        stats.update(self.phase1_tracks())
        stats.update(self.phase2_artists())
        stats.update(self.phase3_albums())
        self.log("\n=== FASE 4: audio_features SKIP (app pos-nov/2024) ===")
        stats.update(self.phase5_backfill())
        return stats


# ============================================================
# FUNCAO PRINCIPAL (usada pelo pipeline e standalone)
# ============================================================

def run(conn, client_id: str, client_secret: str, log=print) -> dict:
    """
    Enriquece o catalogo de tracks/artists/albums via Spotify API.

    Args:
        conn:          conexao psycopg2 aberta
        client_id:     Spotify Client ID
        client_secret: Spotify Client Secret
        log:           callable para mensagens (default: print)

    Returns:
        dict com contagens de cada fase
    """
    enricher = CatalogEnricher(conn, client_id, client_secret, log=log)
    return enricher.run()


# ============================================================
# MODO STANDALONE
# ============================================================

if __name__ == "__main__":
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("ERRO: SPOTIFY_CLIENT_ID e SPOTIFY_CLIENT_SECRET nao setados")

    conn = psycopg2.connect(DSN)
    try:
        run(conn, client_id, client_secret)
    finally:
        conn.close()
    print("\nFim.")
