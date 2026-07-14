"""
ETL: Spotify Account Data (export basico) -> Postgres

Le 3 arquivos do export basico e popula:
  - Playlist1.json     -> playlist + playlist_collaborator + playlist_track
  - YourLibrary.json   -> library_item
  - SearchQueries.json -> search_query + search_query_interaction

Estrategia:
  - Playlists: DELETE + reinsert (snapshot da configuracao atual).
  - Library:  ON CONFLICT (item_type, item_uri) DO NOTHING.
  - Searches: ON CONFLICT (searched_at, query) DO NOTHING.

Como rodar standalone:
    py import_basic_export.py

Ou via pipeline:
    from etl import import_basic_export
    stats = import_basic_export.run(conn, input_dir, log=print)
"""

import json
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values


# ============================================================
# CONFIGURACAO (usada apenas no modo standalone)
# ============================================================

DSN = os.environ.get(
    "SPOTIFY_DSN",
    "postgresql://claude_etl:umasenhaetl@localhost:5432/spotify",
)

INPUT_DIR = Path(os.environ.get(
    "SPOTIFY_BASIC_DIR",
    r"C:\Users\Pedro Felipe\Downloads\my_spotify_data\Spotify Account Data",
))


# ============================================================
# HELPERS
# ============================================================

def _load_json(input_dir: Path, name: str):
    p = input_dir / name
    if not p.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {p}")
    with open(p, encoding="utf-8") as fp:
        return json.load(fp)


def _empty_to_none(v):
    return v if v else None


# ============================================================
# PLAYLISTS
# ============================================================

def _import_playlists(cur, input_dir: Path, log=print) -> int:
    data = _load_json(input_dir, "Playlist1.json")
    playlists = data.get("playlists", [])
    log(f"  arquivo: {len(playlists)} playlists")

    cur.execute("DELETE FROM playlist;")

    total_tracks = 0
    total_collabs = 0

    for pl in playlists:
        cur.execute(
            """
            INSERT INTO playlist (name, description, last_modified, followers)
            VALUES (%s, %s, %s, %s)
            RETURNING id;
            """,
            (
                pl["name"],
                _empty_to_none(pl.get("description")),
                pl.get("lastModifiedDate"),
                pl.get("numberOfFollowers"),
            ),
        )
        pid = cur.fetchone()[0]

        collabs = [(pid, c) for c in (pl.get("collaborators") or []) if c]
        if collabs:
            execute_values(
                cur,
                "INSERT INTO playlist_collaborator (playlist_id, collaborator) VALUES %s",
                collabs,
            )
            total_collabs += len(collabs)

        track_rows = []
        for pos, item in enumerate(pl.get("items") or []):
            t = item.get("track") or {}
            if not t.get("trackUri"):
                continue
            track_rows.append((
                pid,
                pos,
                t["trackUri"],
                t.get("trackName") or "",
                t.get("artistName") or "",
                t.get("albumName") or "",
                item.get("addedDate"),
            ))
        if track_rows:
            execute_values(
                cur,
                """
                INSERT INTO playlist_track
                    (playlist_id, position, track_uri,
                     track_name_raw, artist_name_raw, album_name_raw, added_at)
                VALUES %s
                """,
                track_rows,
            )
            total_tracks += len(track_rows)

    log(f"  inseridos: {len(playlists)} playlists, {total_tracks} tracks, {total_collabs} collaborators")
    return len(playlists)


# ============================================================
# LIBRARY
# ============================================================

def _import_library(cur, input_dir: Path, log=print) -> int:
    data = _load_json(input_dir, "YourLibrary.json")
    rows = []

    for it in data.get("albums") or []:
        rows.append(("album", it["uri"], it.get("album") or "", it.get("artist")))
    for it in data.get("shows") or []:
        rows.append(("show", it["uri"], it.get("name") or "", it.get("publisher")))
    for it in data.get("artists") or []:
        rows.append(("artist", it["uri"], it.get("name") or "", None))
    for it in data.get("tracks") or []:
        rows.append(("track", it["uri"], it.get("track") or it.get("name") or "", it.get("artist")))
    for it in data.get("episodes") or []:
        rows.append(("episode", it["uri"], it.get("name") or "", it.get("show") or it.get("publisher")))
    for it in data.get("bannedTracks") or []:
        rows.append(("banned_track", it["uri"], it.get("track") or it.get("name") or "", it.get("artist")))
    for it in data.get("bannedArtists") or []:
        rows.append(("banned_artist", it["uri"], it.get("name") or "", None))

    log(f"  arquivo: {len(rows)} itens")

    if not rows:
        log("  (nada para inserir)")
        return 0

    seen = set()
    deduped = []
    for r in rows:
        key = (r[0], r[1])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    cur.execute("SELECT count(*) FROM library_item;")
    before = cur.fetchone()[0]

    execute_values(
        cur,
        """
        INSERT INTO library_item (item_type, item_uri, name_raw, secondary_raw)
        VALUES %s
        ON CONFLICT (item_type, item_uri) DO NOTHING
        """,
        deduped,
    )

    cur.execute("SELECT count(*) FROM library_item;")
    after = cur.fetchone()[0]
    inserted = after - before
    log(f"  inseridos: {inserted}   ja existentes: {len(deduped) - inserted}")
    return inserted


# ============================================================
# SEARCH QUERIES
# ============================================================

def _import_searches(cur, input_dir: Path, log=print) -> int:
    data = _load_json(input_dir, "SearchQueries.json")
    log(f"  arquivo: {len(data)} buscas")

    cur.execute("SELECT count(*) FROM search_query;")
    before_q = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM search_query_interaction;")
    before_i = cur.fetchone()[0]

    for entry in data:
        ts = entry["searchTime"]
        if ts.endswith("[UTC]"):
            ts = ts[:-5]

        cur.execute(
            """
            INSERT INTO search_query (searched_at, query, platform)
            VALUES (%s, %s, %s)
            ON CONFLICT (searched_at, query) DO NOTHING
            RETURNING id;
            """,
            (ts, entry["searchQuery"], _empty_to_none(entry.get("platform"))),
        )
        row = cur.fetchone()
        if not row:
            continue
        sq_id = row[0]

        uris = entry.get("searchInteractionURIs") or []
        if uris:
            execute_values(
                cur,
                "INSERT INTO search_query_interaction (search_id, position, target_uri) VALUES %s",
                [(sq_id, pos, uri) for pos, uri in enumerate(uris)],
            )

    cur.execute("SELECT count(*) FROM search_query;")
    after_q = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM search_query_interaction;")
    after_i = cur.fetchone()[0]

    log(f"  inseridos: {after_q - before_q} buscas (ja existentes: {len(data) - (after_q - before_q)})")
    log(f"  inseridos: {after_i - before_i} interactions")
    return after_q - before_q


# ============================================================
# FUNCAO PRINCIPAL (usada pelo pipeline e standalone)
# ============================================================

def run(conn, input_dir: Path, log=print) -> dict:
    """
    Importa dados basicos da conta Spotify para o banco.

    Args:
        conn:      conexao psycopg2 aberta
        input_dir: pasta com Playlist1.json, YourLibrary.json, SearchQueries.json
        log:       callable para mensagens (default: print)

    Returns:
        dict com contagens: playlists, library_items, searches
    """
    log(f"Pasta: {input_dir}\n")

    with conn.cursor() as cur:
        log("[1/3] Playlists")
        n_playlists = _import_playlists(cur, input_dir, log=log)

        log("\n[2/3] Library")
        n_library = _import_library(cur, input_dir, log=log)

        log("\n[3/3] Searches")
        n_searches = _import_searches(cur, input_dir, log=log)

    conn.commit()

    return {
        "playlists":     n_playlists,
        "library_items": n_library,
        "searches":      n_searches,
    }


# ============================================================
# MODO STANDALONE
# ============================================================

if __name__ == "__main__":
    with psycopg2.connect(DSN) as conn:
        run(conn, INPUT_DIR)
    print("\nFim.")
