"""
ETL: Spotify Extended Streaming History -> Postgres

Le os JSONs do Extended Streaming History e roteia cada play:
  - listen           (musica:    spotify_track_uri preenchido)
  - podcast_listen   (podcast:   spotify_episode_uri preenchido)
  - audiobook_listen (audiobook: audiobook_uri preenchido)

E idempotente: pode rodar varias vezes sem duplicar (ON CONFLICT DO NOTHING
sobre as UNIQUE compostas de cada tabela).

Como rodar standalone:
    py import_listening_history.py

Ou via pipeline (importado pelo pipeline.py):
    from etl import import_listening_history
    stats = import_listening_history.run(conn, input_dir, log=print)
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
    "SPOTIFY_DIR",
    r"C:\Users\Pedro Felipe\Downloads\my_spotify_data (1)\Spotify Extended Streaming History",
))


# ============================================================
# LEITURA DOS JSONs
# ============================================================

def load_records(input_dir: Path, log=print):
    files = sorted(input_dir.glob("Streaming_History_*.json"))
    if not files:
        raise FileNotFoundError(
            f"Nenhum JSON 'Streaming_History_*.json' encontrado em: {input_dir}"
        )

    records = []
    for f in files:
        with open(f, encoding="utf-8") as fp:
            data = json.load(fp)
        log(f"  {f.name:<45} {len(data):>6} linhas")
        records.extend(data)
    return records


# ============================================================
# ROTEAMENTO POR TIPO DE CONTEUDO
# ============================================================

def split(records):
    music, podcast, audiobook = [], [], []
    invalid_music = invalid_podcast = invalid_audiobook = 0
    empty = 0

    for r in records:
        if r.get("spotify_track_uri"):
            t  = r.get("master_metadata_track_name")
            a  = r.get("master_metadata_album_artist_name")
            al = r.get("master_metadata_album_album_name")
            if not (t and a and al):
                invalid_music += 1
                continue
            music.append((
                r["ts"],
                r["ms_played"],
                r["spotify_track_uri"],
                t, a, al,
                r.get("platform"),
                r.get("conn_country"),
                r.get("reason_start"),
                r.get("reason_end"),
                r.get("shuffle"),
                r.get("skipped"),
                r.get("offline"),
                r.get("incognito_mode"),
            ))

        elif r.get("spotify_episode_uri"):
            ep = r.get("episode_name")
            sh = r.get("episode_show_name")
            if not (ep and sh):
                invalid_podcast += 1
                continue
            podcast.append((
                r["ts"],
                r["ms_played"],
                r["spotify_episode_uri"],
                ep, sh,
                r.get("platform"),
                r.get("conn_country"),
                r.get("reason_start"),
                r.get("reason_end"),
                r.get("shuffle"),
                r.get("skipped"),
                r.get("offline"),
                r.get("incognito_mode"),
            ))

        elif r.get("audiobook_uri"):
            title = r.get("audiobook_title")
            if not title:
                invalid_audiobook += 1
                continue
            audiobook.append((
                r["ts"],
                r["ms_played"],
                r["audiobook_uri"],
                title,
                r.get("audiobook_chapter_uri"),
                r.get("audiobook_chapter_title"),
                r.get("platform"),
                r.get("conn_country"),
                r.get("reason_start"),
                r.get("reason_end"),
                r.get("shuffle"),
                r.get("skipped"),
                r.get("offline"),
                r.get("incognito_mode"),
            ))

        else:
            empty += 1

    return {
        "music": music,
        "podcast": podcast,
        "audiobook": audiobook,
        "invalid_music": invalid_music,
        "invalid_podcast": invalid_podcast,
        "invalid_audiobook": invalid_audiobook,
        "empty": empty,
    }


# ============================================================
# SQL
# ============================================================

SQL_LISTEN = """
INSERT INTO listen (
    played_at, ms_played, track_uri,
    track_name_raw, artist_name_raw, album_name_raw,
    platform, country, reason_start, reason_end,
    shuffle, skipped, offline, incognito
) VALUES %s
ON CONFLICT (played_at, track_name_raw, artist_name_raw) DO NOTHING
"""

SQL_PODCAST = """
INSERT INTO podcast_listen (
    played_at, ms_played, episode_uri,
    episode_name_raw, show_name_raw,
    platform, country, reason_start, reason_end,
    shuffle, skipped, offline, incognito
) VALUES %s
ON CONFLICT (played_at, episode_name_raw, show_name_raw) DO NOTHING
"""

SQL_AUDIOBOOK = """
INSERT INTO audiobook_listen (
    played_at, ms_played, audiobook_uri,
    audiobook_title_raw, chapter_uri, chapter_name_raw,
    platform, country, reason_start, reason_end,
    shuffle, skipped, offline, incognito
) VALUES %s
ON CONFLICT (played_at, audiobook_title_raw, chapter_uri) DO NOTHING
"""


def _insert_batch(cur, sql, rows, label, log=print, page_size=1000):
    if not rows:
        log(f"  {label:<18} (nada para inserir)")
        return 0
    total_inserted = 0
    for i in range(0, len(rows), page_size):
        chunk = rows[i:i + page_size]
        execute_values(cur, sql, chunk, page_size=len(chunk))
        total_inserted += cur.rowcount
    duplicated = len(rows) - total_inserted
    log(f"  {label:<18} inseridos: {total_inserted:>6}   ignorados/duplicados: {duplicated}")
    return total_inserted


# ============================================================
# FUNCAO PRINCIPAL (usada pelo pipeline e standalone)
# ============================================================

def run(conn, input_dir: Path, log=print) -> dict:
    """
    Importa o historico de escuta para o banco.

    Args:
        conn:      conexao psycopg2 aberta
        input_dir: pasta com os JSONs Streaming_History_*.json
        log:       callable para mensagens (default: print)

    Returns:
        dict com contagens: listen, podcast_listen, audiobook_listen
    """
    log(f"Pasta: {input_dir}\n")

    log("Lendo JSONs:")
    records = load_records(input_dir, log=log)
    log(f"\nTotal lido: {len(records)} registros\n")

    log("Roteando por tipo:")
    s = split(records)
    log(f"  musica:             {len(s['music']):>6}")
    log(f"  podcast:            {len(s['podcast']):>6}")
    log(f"  audiobook:          {len(s['audiobook']):>6}")
    log(f"  invalido (musica):  {s['invalid_music']:>6}")
    log(f"  invalido (podcast): {s['invalid_podcast']:>6}")
    log(f"  invalido (audio):   {s['invalid_audiobook']:>6}")
    log(f"  sem URI (vazio):    {s['empty']:>6}")
    log("")

    with conn.cursor() as cur:
        n_listen    = _insert_batch(cur, SQL_LISTEN,    s["music"],     "listen",          log=log)
        n_podcast   = _insert_batch(cur, SQL_PODCAST,   s["podcast"],   "podcast_listen",  log=log)
        n_audiobook = _insert_batch(cur, SQL_AUDIOBOOK, s["audiobook"], "audiobook_listen",log=log)
    conn.commit()

    return {
        "listen":           n_listen,
        "podcast_listen":   n_podcast,
        "audiobook_listen": n_audiobook,
    }


# ============================================================
# MODO STANDALONE
# ============================================================

if __name__ == "__main__":
    with psycopg2.connect(DSN) as conn:
        run(conn, INPUT_DIR)
    print("\nFim.")
