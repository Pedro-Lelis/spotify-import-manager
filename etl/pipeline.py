"""
Orquestrador do pipeline ETL do Banco Spotify.

Roda as etapas em sequencia, emitindo callbacks de progresso e log.
Usado pelo app.py — nao e pensado pra rodar standalone.

Etapas disponiveis:
  history  → import_listening_history
  basic    → import_basic_export
  enrich   → enrich_catalog (Spotify API)
  preview  → enrich_preview_urls (Embed Scraping)
  audio    → compute_audio_features (Librosa)
"""

from pathlib import Path
import psycopg2

from etl import config as cfg
from etl import import_listening_history as history_etl
from etl import import_basic_export      as basic_etl
from etl import enrich_catalog           as enrich_etl
from etl import enrich_preview_urls      as preview_etl
from etl import compute_audio_features   as audio_etl
from etl import compute_extra_features   as extra_etl
from etl import enrich_artist_bio        as bio_etl


STEP_LABELS = {
    "history": "Historico de escuta",
    "basic":   "Dados da conta (playlists, library, buscas)",
    "enrich":  "Enriquecer catalogo via Spotify API",
    "preview": "Recuperar preview_url (Embed Scraping)",
    "audio":   "Features de audio (Librosa)",
    "extra":   "Features extras (danceability, valence, ...)",
    "bio":     "Biografias de artistas (TheAudioDB / Last.fm)",
}


def run(
    config: dict,
    steps: list,
    log=print,
    on_step_start=None,
    on_step_done=None,
    stop_event=None,
) -> dict:
    """
    Executa as etapas selecionadas do pipeline.

    Args:
        config:        dict carregado de etl/config.py
        steps:         lista de etapas, ex: ['history', 'basic', 'enrich', 'preview', 'audio']
        log:           callable(str) para mensagens de progresso
        on_step_start: callable(step, index, total) — chamado ao iniciar cada etapa
        on_step_done:  callable(step, stats_dict)   — chamado ao finalizar cada etapa
        stop_event:    threading.Event opcional — quando set(), interrompe entre etapas
                       (e dentro das etapas paralelas: preview e extra)

    Returns:
        dict {step: stats_dict} para cada etapa executada
    """
    dsn = cfg.build_dsn(config["db"])
    conn = psycopg2.connect(dsn)

    results = {}
    try:
        total = len(steps)
        for idx, step in enumerate(steps):

            # Verifica parada antes de iniciar cada etapa
            if stop_event is not None and stop_event.is_set():
                log("\nPipeline interrompido pelo usuario.")
                break

            label = STEP_LABELS.get(step, step)
            if on_step_start:
                on_step_start(step, idx, total)

            log(f"\n{'='*56}")
            log(f"Etapa {idx + 1}/{total}: {label}")
            log(f"{'='*56}\n")

            if step == "history":
                input_dir = Path(config["paths"]["extended_history_dir"])
                stats = history_etl.run(conn, input_dir, log=log)

            elif step == "basic":
                input_dir = Path(config["paths"]["basic_export_dir"])
                stats = basic_etl.run(conn, input_dir, log=log)

            elif step == "enrich":
                client_id     = config["spotify"]["client_id"]
                client_secret = config["spotify"]["client_secret"]
                if not client_id or not client_secret:
                    log("AVISO: Spotify Client ID/Secret nao configurados — etapa pulada.")
                    stats = {"skipped": True}
                else:
                    stats = enrich_etl.run(conn, client_id, client_secret, log=log)

            elif step == "preview":
                stats = preview_etl.run(conn, log=log, stop_event=stop_event)

            elif step == "audio":
                stats = audio_etl.run(conn, log=log, stop_event=stop_event)

            elif step == "extra":
                stats = extra_etl.run(conn, log=log, stop_event=stop_event)

            elif step == "bio":
                lastfm_key    = (config.get("lastfm")   or {}).get("api_key",    "")
                discogs_token = (config.get("discogs")  or {}).get("user_token", "")
                stats = bio_etl.run(conn, log=log, lastfm_api_key=lastfm_key,
                                    discogs_token=discogs_token, stop_event=stop_event)

            else:
                log(f"AVISO: Etapa desconhecida '{step}' — pulada.")
                stats = {}

            results[step] = stats
            if on_step_done:
                on_step_done(step, stats)

    finally:
        conn.close()

    if stop_event is not None and stop_event.is_set():
        log("\nPipeline interrompido. Rode novamente para continuar de onde parou.")
    else:
        log("\nPipeline concluido.")

    return results
