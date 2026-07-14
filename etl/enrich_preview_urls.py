"""
ETL: Recupera preview_url via Spotify Embed Player (scraping publico)

O endpoint /tracks da Spotify API nao retorna mais preview_url para apps
criados apos novembro/2024. No entanto, o player de incorporacao publico
(open.spotify.com/embed/track/<id>) ainda expoe o link MP3 de 30s dentro
do objeto __NEXT_DATA__ da pagina — sem precisar de autenticacao.

Fluxo:
  1. Busca tracks sem preview_url no banco
  2. Dispara N workers em paralelo (padrao: 10)
  3. Cada worker acessa o embed player, extrai a URL e retorna
  4. Thread principal escreve no banco e exibe progresso

Circuit breaker: se qualquer worker receber 429, todos param e esperam
o Retry-After antes de continuar.

Idempotente: pula tracks que ja tem preview_url.

Como rodar standalone:
    py enrich_preview_urls.py

Ou via pipeline:
    from etl import enrich_preview_urls
    stats = enrich_preview_urls.run(conn, log=print)
"""

import json
import os
import re
import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import requests


# ============================================================
# CONFIG
# ============================================================

DSN = os.environ.get(
    "SPOTIFY_DSN",
    "postgresql://claude_etl:umasenhaetl@localhost:5432/spotify",
)

EMBED_URL = "https://open.spotify.com/embed/track/{track_id}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

DEFAULT_WORKERS = 10
DELAY_MIN       = 0.5   # segundos por worker entre requisicoes
DELAY_MAX       = 1.0
COMMIT_EVERY    = 50


# ============================================================
# PARSER DO HTML DO EMBED
# ============================================================

def _extract_preview(html: str):
    """
    Extrai a URL do preview MP3 do HTML do Embed Player.
    Retorna (url, estrategia) ou (None, None).
    """
    # Estrategia 1: __NEXT_DATA__ JSON
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            paths = [
                ("props", "pageProps", "state", "data", "entity", "audioPreview", "url"),
                ("props", "pageProps", "state", "data", "entity", "attributes", "previewAudio", "href"),
                ("props", "pageProps", "track", "audioPreview", "url"),
                ("props", "pageProps", "track", "preview_url"),
                ("props", "pageProps", "entity", "audioPreview", "url"),
            ]
            for path in paths:
                try:
                    node = data
                    for key in path:
                        node = node[key]
                    if node and isinstance(node, str) and "scdn.co" in node:
                        return node, "next_data"
                except (KeyError, TypeError):
                    continue
        except (json.JSONDecodeError, Exception):
            pass

    # Estrategia 2: regex CDN direto no HTML
    scdn = re.search(r'"(https://p\.scdn\.co/mp3-preview/[a-f0-9]{40}[^"]*)"', html)
    if scdn:
        return scdn.group(1), "regex_scdn"

    # Estrategia 3: regex audioPreview
    ap = re.search(
        r'["\']audioPreview["\']:\s*\{[^}]*?["\']url["\']:\s*["\']([^"\']+)["\']',
        html, re.DOTALL,
    )
    if ap:
        url = ap.group(1)
        if url.startswith("http"):
            return url, "regex_ap"

    return None, None


# ============================================================
# WORKER (roda em thread separada)
# ============================================================

_thread_local    = threading.local()
_rate_limit_lock = threading.Lock()


def _get_session():
    """Uma session por thread — reutilizavel e thread-safe."""
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.headers.update(HEADERS)
        _thread_local.session = s
    return _thread_local.session


def _fetch_one(args):
    """
    Busca o preview de uma track.
    Retorna (track_id, preview_url, strategy, error_msg).
    error_msg = None indica sucesso ou ausencia de preview (nao erro).
    """
    track_id, name, rate_limit_ref = args

    # Respeita pause global disparado por 429
    now = time.time()
    if rate_limit_ref[0] > now:
        wait = rate_limit_ref[0] - now + random.uniform(1, 3)
        time.sleep(wait)

    session = _get_session()

    try:
        r = session.get(
            EMBED_URL.format(track_id=track_id),
            timeout=20,
            allow_redirects=True,
        )

        if r.status_code == 200:
            preview_url, strategy = _extract_preview(r.text)
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            return track_id, preview_url, strategy, None

        elif r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "60"))
            with _rate_limit_lock:
                # Todos os workers pausam ate esse timestamp
                rate_limit_ref[0] = max(rate_limit_ref[0], time.time() + wait + 5)
            time.sleep(wait + random.uniform(5, 10))
            # Retenta uma vez
            r2 = session.get(EMBED_URL.format(track_id=track_id), timeout=20)
            if r2.status_code == 200:
                preview_url, strategy = _extract_preview(r2.text)
                return track_id, preview_url, strategy, "retry_ok"
            return track_id, None, None, f"429 retry falhou: HTTP {r2.status_code}"

        elif r.status_code == 404:
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            return track_id, None, None, None   # track removida do Spotify

        else:
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            return track_id, None, None, f"HTTP {r.status_code}"

    except requests.exceptions.Timeout:
        return track_id, None, None, "timeout"
    except Exception as e:
        return track_id, None, None, f"{type(e).__name__}: {str(e)[:60]}"


# ============================================================
# FUNCAO PRINCIPAL
# ============================================================

def run(conn, log=print, workers=DEFAULT_WORKERS, stop_event=None) -> dict:
    """
    Preenche track.preview_url via scraping paralelo do Spotify Embed Player.

    Args:
        conn:    conexao psycopg2 aberta
        log:     callable para mensagens (default: print)
        workers: numero de threads paralelas (default: 10)

    Returns:
        dict: found, not_found, errors
    """
    cur = conn.cursor()

    cur.execute("SELECT count(*) FROM track WHERE preview_url IS NOT NULL")
    already = cur.fetchone()[0]

    cur.execute("SELECT id, name FROM track WHERE preview_url IS NULL ORDER BY id")
    tracks = cur.fetchall()
    total = len(tracks)

    log(f"  ja com preview_url: {already}")
    log(f"  a processar agora:  {total}")

    if not total:
        log("Nada a fazer.")
        return {"found": 0, "not_found": 0, "errors": 0}

    est_s   = total * ((DELAY_MIN + DELAY_MAX) / 2) / workers
    est_min = int(est_s / 60)
    log(f"\n  Workers:         {workers}")
    log(f"  Delay/worker:    {DELAY_MIN}–{DELAY_MAX}s")
    log(f"  Tempo estimado:  ~{est_min} minutos")
    log(f"  (circuit breaker ativo — pausa automatica em caso de 429)\n")

    # Estado compartilhado: timestamp ate quando pausar
    rate_limit_ref = [0.0]

    found      = 0
    not_found  = 0
    errors     = 0
    retries    = 0
    stopped    = False
    strategies = {"next_data": 0, "regex_scdn": 0, "regex_ap": 0}

    args_list = [(tid, name, rate_limit_ref) for tid, name in tracks]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_one, args): args[0] for args in args_list}

        for i, future in enumerate(as_completed(futures), 1):

            if stop_event is not None and stop_event.is_set():
                log("  Parada solicitada — cancelando futures pendentes...")
                for f in futures:
                    f.cancel()
                stopped = True
                break

            track_id = futures[future]
            try:
                tid, preview_url, strategy, error = future.result()
            except Exception as e:
                errors += 1
                log(f"  [{i}/{total}] excecao inesperada: {e}")
                continue

            if error == "retry_ok":
                retries += 1
                error = None

            if preview_url:
                cur.execute(
                    "UPDATE track SET preview_url = %s WHERE id = %s",
                    (preview_url, tid),
                )
                found += 1
                if strategy in strategies:
                    strategies[strategy] += 1
            elif error:
                errors += 1
                if errors <= 8:
                    log(f"  ERRO [{tid}]: {error}")
            else:
                not_found += 1

            if i % COMMIT_EVERY == 0:
                conn.commit()
                pct = i / total * 100
                paused = " [PAUSADO 429]" if rate_limit_ref[0] > time.time() else ""
                log(f"  [{i:>4}/{total}] {pct:>5.1f}%  "
                    f"ok={found}  sem={not_found}  err={errors}  retries={retries}"
                    f"{paused}")

    conn.commit()

    if stopped:
        log(f"\n  Interrompido pelo usuario ({found} tracks salvas).")

    log(f"\n  Resultado final:")
    log(f"    encontrados:  {found}  ({round(found/total*100,1) if total else 0}%)")
    log(f"    sem preview:  {not_found}")
    log(f"    erros:        {errors}")
    log(f"    retries 429:  {retries}")
    log(f"    estrategias:  next_data={strategies['next_data']}  "
        f"scdn={strategies['regex_scdn']}  ap={strategies['regex_ap']}")

    return {"found": found, "not_found": not_found, "errors": errors}


# ============================================================
# MODO STANDALONE
# ============================================================

if __name__ == "__main__":
    conn = psycopg2.connect(DSN)
    try:
        run(conn)
        print("\nFim.")
    except KeyboardInterrupt:
        print("\nInterrompido. Rode de novo pra continuar de onde parou.")
        sys.exit(130)
    finally:
        conn.close()
