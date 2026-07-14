"""
ETL: TheAudioDB + Wikipedia + Last.fm + Discogs -> artist.bio_en / bio_pt / bio_source

Fallback chain:
  1. TheAudioDB  — EN + PT quando disponivel (chave publica, sem cadastro)
  2. Wikipedia   — PT via pt.wikipedia.org, EN via en.wikipedia.org (gratuita, so User-Agent)
  3. Last.fm     — EN truncado (requer api_key em config["lastfm"]["api_key"])
  4. Discogs     — EN apenas; 2 requests por artista (search + fetch)
                   Anonimo: 25 req/min | Com token: 60 req/min
                   Token opcional em config["discogs"]["user_token"]

Idempotente: processa apenas artistas onde bio_source IS NULL.

bio_source registra a(s) fonte(s) usadas:
  'theaudiodb'            — ambas as bios vieram do TheAudioDB
  'theaudiodb+wikipedia'  — EN do TheAudioDB, PT da Wikipedia (ou vice-versa)
  'wikipedia'             — ambas da Wikipedia
  'lastfm'                — EN do Last.fm (bio_pt permanece NULL)
  'discogs'               — EN do Discogs (bio_pt permanece NULL)
  'not_found'             — nenhuma fonte retornou resultado

Como rodar standalone:
    py -3.10 enrich_artist_bio.py

Ou via pipeline:
    from etl import enrich_artist_bio
    stats = enrich_artist_bio.run(conn, log=print)
"""

import os
import time
import unicodedata

import psycopg2
import requests


# ============================================================
# CONFIG (modo standalone)
# ============================================================

DSN = os.environ.get(
    "SPOTIFY_DSN",
    "postgresql://claude_etl:umasenhaetl@localhost:5432/spotify",
)

TADB_URL        = "https://www.theaudiodb.com/api/v1/json/2/search.php"
LFM_URL         = "https://ws.audioscrobbler.com/2.0/"
WIKI_SUMMARY    = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
DISCOGS_SEARCH  = "https://api.discogs.com/database/search"
DISCOGS_ARTIST  = "https://api.discogs.com/artists/{id}"
WIKI_UA         = "BancoSpotify/1.0 (banco de dados musical pessoal)"

DELAY           = 0.4   # segundos entre artistas (bottleneck: TheAudioDB free tier)
DELAY_DISCOGS   = 2.5   # extra apos chamar Discogs (2 requests; anonimo: 25 req/min)
COMMIT_EVERY    = 50


# ============================================================
# HELPERS
# ============================================================

def _normalize(s: str) -> str:
    """Minusculas + remove acentos. Usado para comparar nomes de artistas."""
    s = s.casefold().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _theaudiodb(session: requests.Session, artist_name: str):
    """
    Busca bio no TheAudioDB pelo nome do artista.
    Retorna (bio_en, bio_pt) — qualquer campo pode ser None.
    Rejeita o resultado se o nome retornado nao bater com o buscado.
    """
    try:
        r = session.get(TADB_URL, params={"s": artist_name}, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None, None

    artists = data.get("artists") or []
    if not artists:
        return None, None

    a = artists[0]
    if _normalize(a.get("strArtist", "")) != _normalize(artist_name):
        return None, None

    bio_en = a.get("strBiography") or None   # campo principal (EN)
    bio_pt = a.get("strBiographyPT") or None
    return bio_en, bio_pt


def _wikipedia(session: requests.Session, artist_name: str, lang: str) -> str | None:
    """
    Busca resumo na Wikipedia pelo nome do artista.
    lang: 'pt' ou 'en'
    Retorna o campo 'extract' (texto limpo, sem HTML) ou None.
    Rejeita paginas de disambiguacao.
    """
    title = artist_name.replace(" ", "_")
    url = WIKI_SUMMARY.format(lang=lang, title=title)
    try:
        r = session.get(url, headers={"User-Agent": WIKI_UA}, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    if data.get("type") == "disambiguation":
        return None

    return data.get("extract") or None


def _discogs(session: requests.Session, artist_name: str, token: str = "") -> str | None:
    """
    Busca perfil no Discogs (so EN).
    Faz 2 requests: search para obter o ID, depois fetch para obter o profile.
    token: Personal Access Token do Discogs (opcional; sem token: 25 req/min anonimo).
    """
    headers = {"User-Agent": WIKI_UA}
    if token:
        headers["Authorization"] = f"Discogs token={token}"

    # Request 1: busca pelo nome
    try:
        r = session.get(
            DISCOGS_SEARCH,
            params={"q": artist_name, "type": "artist"},
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception:
        return None

    artist_id = None
    for result in results[:5]:
        if result.get("type") == "artist" and _normalize(result.get("title", "")) == _normalize(artist_name):
            artist_id = result.get("id")
            break

    if not artist_id:
        return None

    # Request 2: fetch do artista para obter o profile
    try:
        r = session.get(
            DISCOGS_ARTIST.format(id=artist_id),
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    return data.get("profile") or None


def _lastfm(session: requests.Session, artist_name: str, api_key: str) -> str | None:
    """
    Busca bio no Last.fm pelo nome do artista (ultimo fallback, so EN).
    Aviso: retorno truncado a ~300 caracteres pela plataforma.
    """
    try:
        r = session.get(
            LFM_URL,
            params={
                "method":  "artist.getinfo",
                "artist":  artist_name,
                "api_key": api_key,
                "format":  "json",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    bio = (data.get("artist") or {}).get("bio") or {}
    content = bio.get("content") or ""
    # Last.fm adiciona rodape com link HTML — remove
    content = content.split("\n\n<a href=")[0].strip() or None
    return content


# ============================================================
# FUNCAO PRINCIPAL
# ============================================================

def run(conn, log=print, lastfm_api_key: str = "", discogs_token: str = "", stop_event=None) -> dict:
    """
    Enriquece artist.bio_en / bio_pt / bio_source para todos os artistas
    que ainda nao foram tentados (bio_source IS NULL).

    Fallback chain por campo:
      bio_en: TheAudioDB → Wikipedia EN → Last.fm → Discogs
      bio_pt: TheAudioDB → Wikipedia PT

    Args:
        conn:           conexao psycopg2 aberta
        log:            callable para mensagens (default: print)
        lastfm_api_key: chave da API Last.fm (opcional)
        discogs_token:  Personal Access Token do Discogs (opcional)
        stop_event:     threading.Event opcional — quando set(), encerra o loop
                        apos salvar o progresso ja feito (idempotente)

    Returns:
        {"bio_updated": int, "bio_not_found": int}
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name
          FROM artist
         WHERE bio_source IS NULL
         ORDER BY name
    """)
    artists = cur.fetchall()
    total = len(artists)
    log(f"  artistas sem bio: {total}")

    if not total:
        return {"bio_updated": 0, "bio_not_found": 0}

    session = requests.Session()
    updated = not_found = 0
    stopped = False

    for i, (artist_id, artist_name) in enumerate(artists, 1):

        # Parada cooperativa — salva o que ja foi feito e encerra
        if stop_event is not None and stop_event.is_set():
            conn.commit()
            log("  Parada solicitada — progresso salvo, encerrando etapa.")
            stopped = True
            break

        # --- Camada 1: TheAudioDB ---
        bio_en, bio_pt = _theaudiodb(session, artist_name)
        en_src = "theaudiodb" if bio_en else None
        pt_src = "theaudiodb" if bio_pt else None

        # --- Camada 2: Wikipedia PT (fallback para bio_pt) ---
        if not bio_pt:
            bio_pt = _wikipedia(session, artist_name, lang="pt")
            if bio_pt:
                pt_src = "wikipedia"

        # --- Camada 2: Wikipedia EN (fallback para bio_en) ---
        if not bio_en:
            bio_en = _wikipedia(session, artist_name, lang="en")
            if bio_en:
                en_src = "wikipedia"

        # --- Camada 3: Last.fm EN ---
        if not bio_en and lastfm_api_key:
            bio_en = _lastfm(session, artist_name, lastfm_api_key)
            if bio_en:
                en_src = "lastfm"

        # --- Camada 4: Discogs EN (ultimo recurso; 2 requests por artista) ---
        used_discogs = False
        if not bio_en:
            bio_en = _discogs(session, artist_name, token=discogs_token)
            if bio_en:
                en_src = "discogs"
                used_discogs = True

        # --- Persiste ---
        if bio_en or bio_pt:
            sources = sorted({s for s in (en_src, pt_src) if s})
            source = "+".join(sources)   # ex: 'theaudiodb', 'theaudiodb+wikipedia'
            cur.execute(
                """
                UPDATE artist
                   SET bio_en     = %s,
                       bio_pt     = %s,
                       bio_source = %s
                 WHERE id = %s
                """,
                (bio_en, bio_pt, source, artist_id),
            )
            updated += 1
        else:
            cur.execute(
                "UPDATE artist SET bio_source = 'not_found' WHERE id = %s",
                (artist_id,),
            )
            not_found += 1

        if i % COMMIT_EVERY == 0 or i == total:
            conn.commit()
            log(f"  {i:>4}/{total}  com bio={updated}  sem bio={not_found}")

        time.sleep(DELAY_DISCOGS if used_discogs else DELAY)

    if stopped:
        log("  ETAPA INTERROMPIDA (rode de novo para continuar de onde parou)")
    log(f"  TOTAL: {updated} bios encontradas, {not_found} artistas sem bio")
    return {"bio_updated": updated, "bio_not_found": not_found, "stopped": stopped}


# ============================================================
# MODO STANDALONE
# ============================================================

if __name__ == "__main__":
    lastfm_key = os.environ.get("LASTFM_API_KEY", "")

    conn = psycopg2.connect(DSN)
    try:
        run(conn, lastfm_api_key=lastfm_key)
    finally:
        conn.close()
    print("\nFim.")
