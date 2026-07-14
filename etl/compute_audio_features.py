"""
ETL: Audio features via Librosa (com preview de fonte alternativa)

Como o Spotify removeu o preview_url do endpoint /tracks pra apps pos-nov/2024,
buscamos o preview em fontes alternativas, na ordem de prioridade:

  0. track.preview_url no banco (populado pelo enrich_preview_urls.py via embed scraping)
  1. Deezer (lookup por ISRC) - exato e rapido
  2. Deezer (search por artist+name) - fallback
  3. iTunes Search API (por artist+name) - ultimo recurso

Calculamos 5 features confiaveis:
  - tempo (BPM)
  - key (0-11), mode (0=minor, 1=major)
  - loudness (LUFS)
  - energy (RMS-based)

Idempotente: pula tracks ja em audio_features.

Como rodar standalone:
    py compute_audio_features.py

Ou via pipeline:
    from etl import compute_audio_features
    stats = compute_audio_features.run(conn, log=print)
"""

import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests
import numpy as np

try:
    import librosa
except ImportError:
    librosa = None

try:
    import pyloudnorm as pyln
except ImportError:
    pyln = None


# ============================================================
# CONFIG (modo standalone)
# ============================================================

DSN = os.environ.get(
    "SPOTIFY_DSN",
    "postgresql://claude_etl:umasenhaetl@localhost:5432/spotify",
)

SAMPLE_RATE = 22050
COMMIT_EVERY = 25
USER_AGENT = "BancoSpotifyPessoal/1.0"
DELAY_BETWEEN_LOOKUPS_S = 0.2


# ============================================================
# KEY ESTIMATION (Krumhansl-Schmuckler)
# ============================================================

KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _estimate_key(chroma_mean):
    best = (-np.inf, 0, 1)
    for shift in range(12):
        prof_major = np.roll(KS_MAJOR, shift)
        prof_minor = np.roll(KS_MINOR, shift)
        cm = np.corrcoef(chroma_mean, prof_major)[0, 1]
        ci = np.corrcoef(chroma_mean, prof_minor)[0, 1]
        if cm > best[0]:
            best = (cm, shift, 1)
        if ci > best[0]:
            best = (ci, shift, 0)
    return best[1], best[2]


# ============================================================
# PREVIEW LOOKUP (4 fontes em cascata)
# ============================================================

def _find_preview_url(session, stored_url, isrc, artist, name):
    """
    Tenta achar o URL de preview em 4 fontes, em ordem de prioridade.
    Retorna (url, fonte) ou (None, None).

    Fonte 0: track.preview_url ja salvo no banco (via embed scraping)
    Fonte 1: Deezer por ISRC (mais preciso)
    Fonte 2: Deezer search por artist+name
    Fonte 3: iTunes search
    """

    # 0. URL ja salva no banco (resultado do enrich_preview_urls.py)
    if stored_url:
        try:
            r = session.head(stored_url, timeout=8, allow_redirects=True)
            if r.status_code == 200:
                return stored_url, "spotify_embed"
        except Exception:
            pass  # URL pode ter expirado — cai para os proximos

    # 1. Deezer por ISRC (mais preciso)
    if isrc:
        try:
            r = session.get(f"https://api.deezer.com/track/isrc:{isrc}", timeout=10)
            if r.ok:
                data = r.json()
                if data.get("preview"):
                    return data["preview"], "deezer_isrc"
        except Exception:
            pass

    # 2. Deezer search por artist+name
    query = f"{artist} {name}".strip()
    if query:
        try:
            r = session.get(
                "https://api.deezer.com/search/track",
                params={"q": query[:120], "limit": 1},
                timeout=10,
            )
            if r.ok:
                data = r.json()
                items = data.get("data") or []
                if items and items[0].get("preview"):
                    return items[0]["preview"], "deezer_search"
        except Exception:
            pass

    # 3. iTunes search
    if query:
        try:
            r = session.get(
                "https://itunes.apple.com/search",
                params={"term": query[:200], "media": "music", "limit": 1},
                timeout=10,
            )
            if r.ok:
                data = r.json()
                results = data.get("results") or []
                if results and results[0].get("previewUrl"):
                    return results[0]["previewUrl"], "itunes_search"
        except Exception:
            pass

    return None, None


# ============================================================
# FEATURE EXTRACTION
# ============================================================

def _compute_features(audio_path):
    if librosa is None:
        raise ImportError("librosa nao instalado. Rode: py -m pip install librosa pyloudnorm")

    y, sr = librosa.load(audio_path, sr=SAMPLE_RATE, mono=True)

    if len(y) < sr:
        raise ValueError(f"audio muito curto: {len(y)/sr:.1f}s")

    # Tempo
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo_val = float(tempo) if np.isscalar(tempo) else float(tempo[0])

    # Key + mode
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = chroma.mean(axis=1)
    if chroma_mean.std() > 0:
        key, mode = _estimate_key(chroma_mean)
    else:
        key, mode = 0, 1

    # Loudness (LUFS)
    loud = None
    if pyln is not None:
        try:
            meter = pyln.Meter(sr)
            loud = float(meter.integrated_loudness(y))
            if not np.isfinite(loud):
                loud = None
        except Exception:
            loud = None

    # Energy
    rms = float(librosa.feature.rms(y=y).mean())
    energy = float(np.clip(rms / 0.3, 0.0, 1.0))

    return {
        "tempo":    tempo_val,
        "key":      int(key),
        "mode":     int(mode),
        "loudness": loud,
        "energy":   energy,
    }


# ============================================================
# FUNCAO PRINCIPAL (usada pelo pipeline e standalone)
# ============================================================

def run(conn, log=print, stop_event=None) -> dict:
    """
    Computa audio features para tracks ainda nao processadas.

    Busca previews na ordem: track.preview_url (banco) → Deezer ISRC
    → Deezer search → iTunes search.

    Args:
        conn:       conexao psycopg2 aberta
        log:        callable para mensagens (default: print)
        stop_event: threading.Event opcional — quando set(), encerra o loop
                    apos salvar o progresso ja feito (idempotente)

    Returns:
        dict com contagens: processed, no_preview, download_fail, analysis_fail
    """
    if librosa is None:
        raise ImportError("librosa nao instalado. Rode: py -m pip install librosa pyloudnorm")

    cur = conn.cursor()

    # Inclui preview_url salvo no banco como fonte 0
    cur.execute("""
        SELECT t.id, t.isrc, t.name, ar.name AS artist_name, t.preview_url
        FROM track t
        LEFT JOIN audio_features af ON af.track_id = t.id
        LEFT JOIN track_artist ta ON ta.track_id = t.id AND ta.position = 0
        LEFT JOIN artist ar ON ar.id = ta.artist_id
        WHERE af.track_id IS NULL
        ORDER BY
            t.preview_url IS NULL,   -- tracks com preview_url salvo primeiro
            t.id
    """)
    rows = cur.fetchall()
    total = len(rows)

    cur.execute("SELECT count(*) FROM audio_features")
    ja_feito = cur.fetchone()[0]
    log(f"  ja em audio_features: {ja_feito}")
    log(f"  a processar agora:    {total}")

    # Quantas tem preview_url salvo (fonte 0 disponivel)
    com_stored = sum(1 for r in rows if r[4])
    log(f"  com preview_url no banco (fonte 0): {com_stored}")
    log(f"  sem preview_url no banco:           {total - com_stored}\n")

    if not total:
        log("Nada a fazer.")
        return {"processed": 0, "no_preview": 0, "download_fail": 0, "analysis_fail": 0}

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    processed     = 0
    no_preview    = 0
    download_fail = 0
    analysis_fail = 0
    sources = {
        "spotify_embed": 0,
        "deezer_isrc":   0,
        "deezer_search": 0,
        "itunes_search": 0,
    }

    stopped = False
    for idx, (track_id, isrc, name, artist_name, stored_url) in enumerate(rows, 1):

        # Parada cooperativa — salva o que ja foi feito e encerra
        if stop_event is not None and stop_event.is_set():
            conn.commit()
            log("  Parada solicitada — progresso salvo, encerrando etapa.")
            stopped = True
            break

        preview_url, source = _find_preview_url(
            session, stored_url, isrc, artist_name or "", name or ""
        )

        if not preview_url:
            no_preview += 1
            time.sleep(DELAY_BETWEEN_LOOKUPS_S)
            if idx % COMMIT_EVERY == 0:
                conn.commit()
                log(f"  [{idx:>4}/{total}]  ok={processed}  sem_preview={no_preview}  "
                    f"dl_fail={download_fail}  an_fail={analysis_fail}  | "
                    f"embed={sources['spotify_embed']} deezer_isrc={sources['deezer_isrc']} "
                    f"deezer_s={sources['deezer_search']} itunes={sources['itunes_search']}")
            continue

        try:
            r = session.get(preview_url, timeout=30)
            r.raise_for_status()
            mp3_bytes = r.content
        except Exception as e:
            download_fail += 1
            if download_fail <= 5:
                log(f"  [{idx}/{total}] download FAIL: {type(e).__name__}: {str(e)[:60]}")
            continue

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                tf.write(mp3_bytes)
                tmp_path = tf.name

            features = _compute_features(tmp_path)

        except Exception as e:
            analysis_fail += 1
            if analysis_fail <= 5:
                log(f"  [{idx}/{total}] analise FAIL ({(name or '')[:40]}): "
                    f"{type(e).__name__}: {str(e)[:60]}")
            continue

        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        cur.execute("""
            INSERT INTO audio_features
                (track_id, tempo, key, mode, loudness, energy, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (track_id) DO NOTHING
        """, (
            track_id,
            features["tempo"],
            features["key"],
            features["mode"],
            features["loudness"],
            features["energy"],
        ))
        processed += 1
        sources[source] = sources.get(source, 0) + 1

        if idx % COMMIT_EVERY == 0:
            conn.commit()
            log(f"  [{idx:>4}/{total}]  ok={processed}  sem_preview={no_preview}  "
                f"dl_fail={download_fail}  an_fail={analysis_fail}  | "
                f"embed={sources['spotify_embed']} deezer_isrc={sources['deezer_isrc']} "
                f"deezer_s={sources['deezer_search']} itunes={sources['itunes_search']}")

    conn.commit()

    if stopped:
        log(f"\n  ETAPA INTERROMPIDA (rode de novo para continuar de onde parou)")
    log(f"\n  processados:    {processed}")
    log(f"  sem preview:    {no_preview}")
    log(f"  download fail:  {download_fail}")
    log(f"  analise fail:   {analysis_fail}")
    log(f"  fontes: embed={sources['spotify_embed']}  "
        f"deezer_isrc={sources['deezer_isrc']}  "
        f"deezer_search={sources['deezer_search']}  "
        f"itunes={sources['itunes_search']}")

    return {
        "processed":     processed,
        "no_preview":    no_preview,
        "download_fail": download_fail,
        "analysis_fail": analysis_fail,
        "stopped":       stopped,
    }


# ============================================================
# MODO STANDALONE
# ============================================================

if __name__ == "__main__":
    if librosa is None:
        sys.exit("ERRO: librosa nao instalado. Rode: py -m pip install librosa pyloudnorm")

    conn = psycopg2.connect(DSN)
    try:
        run(conn)
        print("\nFim.")
    except KeyboardInterrupt:
        print("\nInterrompido. Pode rodar de novo, vai continuar de onde parou.")
        sys.exit(130)
    finally:
        conn.close()
