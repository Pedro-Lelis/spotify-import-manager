"""
ETL: Computa as 7 features extras de audio via librosa (paralelo)

O Spotify retornava estas colunas pelo endpoint /audio-features (deprecado).
Aqui recalculamos como aproximacoes baseadas em analise de sinal:

  danceability     — regularidade ritmica + forca do beat + zona de BPM dancante
  time_signature   — compasso estimado (tipicamente 3 ou 4)
  speechiness      — presenca de fala/voz (ZCR calibrado)
  acousticness     — instrumento acustico vs eletronico (spectral flatness)
  instrumentalness — ausencia de vocal (ZCR invertido, conservador)
  liveness         — gravacao ao vivo vs estudio (dynamic range)
  valence          — positividade musical (tom maior/menor + tempo + brilho)

AVISO: Os valores NAO sao identicos aos do Spotify — sao aproximacoes baseadas
em sinais acousticos do preview de 30s. Sao musicalmente coerentes e uteis
para consultas e agrupamentos, mas nao devem ser comparados diretamente com
dados historicos da API do Spotify.

Arquitetura paralela:
  - ThreadPoolExecutor com N workers (padrao: 8)
  - Workers fazem: busca de URL → download MP3 → calculo librosa → retornam resultado
  - Thread principal: coleta via as_completed() → escreve no banco → commit/50
  - Uma requests.Session por thread (threading.local), nunca compartilhada
  - psycopg2 so e acessado pela thread principal (cursor nao e thread-safe)

Preview source: track.preview_url (populado pelo enrich_preview_urls.py)
Fallback:       Deezer ISRC → Deezer search → iTunes search

Como rodar standalone:
    py compute_extra_features.py

Ou via pipeline:
    from etl import compute_extra_features
    stats = compute_extra_features.run(conn, log=print)
"""

import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import psycopg2
import requests

try:
    import librosa
except ImportError:
    librosa = None


# ============================================================
# CONFIG
# ============================================================

DSN = os.environ.get(
    "SPOTIFY_DSN",
    "postgresql://claude_etl:umasenhaetl@localhost:5432/spotify",
)

DEFAULT_WORKERS = 8
SAMPLE_RATE     = 22050
COMMIT_EVERY    = 50
USER_AGENT      = "BancoSpotifyPessoal/1.0"

_thread_local = threading.local()


def _get_session() -> requests.Session:
    """Uma Session por thread — criada na primeira chamada, reutilizada."""
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT})
        _thread_local.session = s
    return _thread_local.session


# ============================================================
# KEY ESTIMATION (Krumhansl-Schmuckler)
# ============================================================

KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _estimate_key_mode(chroma_mean):
    best = (-np.inf, 0, 1)
    for shift in range(12):
        cm = np.corrcoef(chroma_mean, np.roll(KS_MAJOR, shift))[0, 1]
        ci = np.corrcoef(chroma_mean, np.roll(KS_MINOR, shift))[0, 1]
        if cm > best[0]: best = (cm, shift, 1)
        if ci > best[0]: best = (ci, shift, 0)
    return best[1], best[2]   # key (0-11), mode (0=minor, 1=major)


# ============================================================
# TIME SIGNATURE ESTIMATION
# ============================================================

def _estimate_time_sig(onset_env: np.ndarray, beats: np.ndarray) -> int:
    """
    Estima o compasso (3 ou 4) via correlacao do onset envelope com
    padroes ritmicos. Retorna 3 ou 4 (default forte para 4).
    """
    if len(beats) < 8:
        return 4

    valid    = beats[beats < len(onset_env)]
    beat_str = onset_env[valid].astype(float)

    if len(beat_str) < 6:
        return 4

    n         = len(beat_str)
    pattern_4 = np.tile([1.0, 0.3, 0.6, 0.3], n // 4 + 1)[:n]
    pattern_3 = np.tile([1.0, 0.3, 0.3],       n // 3 + 1)[:n]

    corr_4 = float(np.corrcoef(beat_str, pattern_4)[0, 1])
    corr_3 = float(np.corrcoef(beat_str, pattern_3)[0, 1])

    if np.isnan(corr_4): corr_4 = 0.0
    if np.isnan(corr_3): corr_3 = 0.0

    return 3 if corr_3 > corr_4 + 0.15 else 4


# ============================================================
# FEATURE EXTRACTION
# ============================================================

def _compute_extra(audio_path: str) -> dict:
    """
    Calcula as 7 features extras a partir de um arquivo MP3.
    Chamada pelos workers em threads paralelas.
    """
    y, sr = librosa.load(audio_path, sr=SAMPLE_RATE, mono=True)

    if len(y) < sr:
        raise ValueError(f"audio muito curto: {len(y)/sr:.1f}s")

    # — Ritmo —
    tempo_arr, beats = librosa.beat.beat_track(y=y, sr=sr)
    tempo_val = float(tempo_arr) if np.isscalar(tempo_arr) else float(tempo_arr[0])
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)

    # — Separacao harmonica / percussiva —
    y_harm, _ = librosa.effects.hpss(y)

    # — ZCR —
    zcr_mean = float(librosa.feature.zero_crossing_rate(y).mean())

    # — Features espectrais —
    centroid_mean = float(librosa.feature.spectral_centroid(y=y, sr=sr).mean())
    flatness_mean = float(librosa.feature.spectral_flatness(y=y).mean())

    # — RMS por frame (para dynamic range) —
    rms_frames = librosa.feature.rms(y=y, frame_length=2048, hop_length=512).flatten()

    # — Chroma → key/mode —
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    _, mode = _estimate_key_mode(chroma.mean(axis=1))

    # ── 1. DANCEABILITY ─────────────────────────────────────
    # Regularidade dos beats + forca dos ataques + BPM na zona dancavel
    if len(beats) > 2:
        beat_times = librosa.frames_to_time(beats, sr=sr, hop_length=512)
        ibi = np.diff(beat_times)
        beat_regularity = float(np.clip(1.0 - np.std(ibi) / (np.mean(ibi) + 1e-8), 0, 1))
    else:
        beat_regularity = 0.5

    onset_norm  = float(np.clip(onset_env.mean() / 8.0, 0, 1))
    tempo_dance = float(np.clip(1.0 - abs(tempo_val - 118) / 90.0, 0, 1))

    danceability = float(np.clip(
        beat_regularity * 0.40 + onset_norm * 0.35 + tempo_dance * 0.25,
        0, 1
    ))

    # ── 2. TIME_SIGNATURE ───────────────────────────────────
    time_signature = int(_estimate_time_sig(onset_env, beats))

    # ── 3. SPEECHINESS ──────────────────────────────────────
    # ZCR acima da baseline musical (0.04) normalizado sobre faixa de rap (0.20)
    # Potencia 1.5 penaliza a zona intermediaria (guitarra acustica tem ZCR alto
    # por strumming mas nao e fala — valores ficam menores que rap real).
    # Calibracao: pop tipico 0.03-0.15, rap 0.25-0.50+
    zcr_above = max(0.0, zcr_mean - 0.04)
    speechiness = float(np.clip((zcr_above / 0.20) ** 1.5, 0, 1))

    # ── 4. ACOUSTICNESS ─────────────────────────────────────
    # Instrumentos acusticos tem spectral flatness MAIOR que sintetizadores:
    # sintetizadores tem timbres consistentes e tonais (flatness baixa),
    # enquanto instrumentos acusticos tem ataques e harmonicos variados.
    # flatness 0.010-0.015 → sintetizado  | flatness 0.030-0.060 → acustico
    # Calibrado: flatness * 25 normaliza o range pop tipico para 0-1.
    acousticness = float(np.clip(flatness_mean * 25.0, 0, 1))

    # ── 5. INSTRUMENTALNESS ─────────────────────────────────
    # Voz e ataques vocais aumentam o ZCR. Tracks instrumentais (pads,
    # cordas sustentadas, sintetizadores) tem ZCR mais baixo.
    # Formula conservadora: max ~0.38 para ZCR=0 (nao forcamos certeza).
    instr_from_zcr = max(0.0, 0.07 - zcr_mean) / 0.07
    instrumentalness = float(np.clip(instr_from_zcr * 0.55, 0, 1))

    # ── 6. LIVENESS ─────────────────────────────────────────
    # Gravacoes ao vivo tem dynamic range maior (plateia, reverberacao).
    # Pop/EDM comprimido: 6-12 dB | Rock/acustico: 12-22 dB | Ao vivo: 22-45 dB
    rms_db        = 20.0 * np.log10(rms_frames + 1e-10)
    dynamic_range = float(np.percentile(rms_db, 95) - np.percentile(rms_db, 10))
    liveness      = float(np.clip((dynamic_range - 12.0) / 25.0, 0, 1))

    # ── 7. VALENCE ──────────────────────────────────────────
    # Tom maior = mais positivo; menor = mais negativo
    mode_contrib   = 0.58 if mode == 1 else 0.22
    tempo_contrib  = float(np.clip((tempo_val - 65.0) / 145.0, 0, 1)) * 0.25
    bright_contrib = float(np.clip((centroid_mean - 600.0) / 5000.0, 0, 1)) * 0.17
    valence        = float(np.clip(mode_contrib + tempo_contrib + bright_contrib, 0, 1))

    return {
        "danceability":     round(danceability,     4),
        "time_signature":   time_signature,
        "speechiness":      round(speechiness,      4),
        "acousticness":     round(acousticness,     4),
        "instrumentalness": round(instrumentalness, 4),
        "liveness":         round(liveness,          4),
        "valence":          round(valence,            4),
    }


# ============================================================
# PREVIEW LOOKUP
# ============================================================

def _find_preview(stored_url, isrc, artist, name):
    """
    Busca URL do preview em cascata (chamada em thread worker).
    Retorna (url, fonte) ou (None, None).
    """
    session = _get_session()

    # 0. URL salva no banco (via enrich_preview_urls)
    if stored_url:
        try:
            if session.head(stored_url, timeout=8, allow_redirects=True).status_code == 200:
                return stored_url, "spotify_embed"
        except Exception:
            pass

    # 1. Deezer por ISRC
    if isrc:
        try:
            r = session.get(f"https://api.deezer.com/track/isrc:{isrc}", timeout=10)
            if r.ok and r.json().get("preview"):
                return r.json()["preview"], "deezer_isrc"
        except Exception:
            pass

    q = f"{artist} {name}".strip()

    # 2. Deezer search
    if q:
        try:
            r = session.get("https://api.deezer.com/search/track",
                            params={"q": q[:120], "limit": 1}, timeout=10)
            if r.ok:
                items = r.json().get("data") or []
                if items and items[0].get("preview"):
                    return items[0]["preview"], "deezer_search"
        except Exception:
            pass

    # 3. iTunes
    if q:
        try:
            r = session.get("https://itunes.apple.com/search",
                            params={"term": q[:200], "media": "music", "limit": 1}, timeout=10)
            if r.ok:
                res = r.json().get("results") or []
                if res and res[0].get("previewUrl"):
                    return res[0]["previewUrl"], "itunes_search"
        except Exception:
            pass

    return None, None


# ============================================================
# WORKER (roda em thread)
# ============================================================

def _process_one(args):
    """
    Executa em thread: busca preview → baixa MP3 → calcula features.
    Retorna (track_id, features_dict, source, error_str).
    Nunca acessa o banco de dados.
    """
    track_id, stored_url, name, isrc, artist = args

    # Busca preview
    preview_url, source = _find_preview(stored_url, isrc, artist or "", name or "")
    if not preview_url:
        return track_id, None, None, "sem_preview"

    # Download do MP3
    tmp_path = None
    try:
        session  = _get_session()
        r        = session.get(preview_url, timeout=30)
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tf.write(r.content)
            tmp_path = tf.name
    except Exception as e:
        return track_id, None, None, f"download: {type(e).__name__}: {str(e)[:50]}"

    # Calculo das features
    try:
        feats = _compute_extra(tmp_path)
        return track_id, feats, source, None
    except Exception as e:
        return track_id, None, None, f"analise: {type(e).__name__}: {str(e)[:50]}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ============================================================
# FUNCAO PRINCIPAL
# ============================================================

def run(conn, log=print, workers=DEFAULT_WORKERS, stop_event=None) -> dict:
    """
    Calcula as 7 features extras em paralelo para tracks com danceability NULL.

    Args:
        conn:       conexao psycopg2 aberta
        log:        callable para mensagens (default: print)
        workers:    threads paralelas (default: 8)
        stop_event: threading.Event opcional — quando set(), para graciosamente
                    apos completar as tracks em andamento nos workers.

    Returns:
        dict: processed, no_preview, download_fail, analysis_fail
    """
    if librosa is None:
        raise ImportError("librosa nao instalado. Rode: py -m pip install librosa")

    cur = conn.cursor()

    cur.execute("""
        SELECT af.track_id, t.preview_url, t.name, t.isrc, ar.name AS artist_name
        FROM audio_features af
        JOIN track t ON t.id = af.track_id
        LEFT JOIN track_artist ta ON ta.track_id = t.id AND ta.position = 0
        LEFT JOIN artist ar ON ar.id = ta.artist_id
        WHERE af.danceability IS NULL
        ORDER BY t.preview_url IS NULL, af.track_id
    """)
    rows  = cur.fetchall()
    total = len(rows)

    cur.execute("SELECT count(*) FROM audio_features WHERE danceability IS NOT NULL")
    ja_feito = cur.fetchone()[0]

    log(f"  ja com features extras: {ja_feito}")
    log(f"  a processar agora:      {total}")

    if not total:
        log("Nada a fazer.")
        return {"processed": 0, "no_preview": 0, "download_fail": 0, "analysis_fail": 0}

    com_url = sum(1 for r in rows if r[1])
    est_s   = total * 3.5 / workers
    est_min = int(est_s / 60)

    log(f"\n  Workers:          {workers}")
    log(f"  com preview_url:  {com_url} / {total}")
    log(f"  Tempo estimado:   ~{est_min} minutos\n")

    processed     = 0
    no_preview    = 0
    download_fail = 0
    analysis_fail = 0
    stopped       = False
    sources = {"spotify_embed": 0, "deezer_isrc": 0, "deezer_search": 0, "itunes_search": 0}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_one, row): row[0] for row in rows}

        for i, future in enumerate(as_completed(futures), 1):

            # Verifica sinal de parada logo apos cada track completar
            if stop_event is not None and stop_event.is_set():
                log("  Parada solicitada — cancelando futures pendentes...")
                for f in futures:
                    f.cancel()
                stopped = True
                break

            track_id = futures[future]
            try:
                tid, feats, source, error = future.result()
            except Exception as e:
                analysis_fail += 1
                log(f"  [{i}/{total}] excecao inesperada: {e}")
                continue

            if error == "sem_preview":
                no_preview += 1
            elif error and error.startswith("download:"):
                download_fail += 1
                if download_fail <= 5:
                    log(f"  dl FAIL [{tid[:8]}]: {error[9:]}")
            elif error:
                analysis_fail += 1
                if analysis_fail <= 5:
                    log(f"  an FAIL [{tid[:8]}]: {error[8:]}")
            else:
                cur.execute("""
                    UPDATE audio_features SET
                        danceability     = %s,
                        time_signature   = %s,
                        speechiness      = %s,
                        acousticness     = %s,
                        instrumentalness = %s,
                        liveness         = %s,
                        valence          = %s
                    WHERE track_id = %s
                """, (
                    feats["danceability"],
                    feats["time_signature"],
                    feats["speechiness"],
                    feats["acousticness"],
                    feats["instrumentalness"],
                    feats["liveness"],
                    feats["valence"],
                    tid,
                ))
                processed += 1
                sources[source] = sources.get(source, 0) + 1

            if i % COMMIT_EVERY == 0:
                conn.commit()
                pct = i / total * 100
                log(f"  [{i:>4}/{total}] {pct:>5.1f}%  "
                    f"ok={processed}  sem={no_preview}  "
                    f"dl_fail={download_fail}  an_fail={analysis_fail}  "
                    f"| embed={sources['spotify_embed']} "
                    f"dz={sources['deezer_isrc']+sources['deezer_search']} "
                    f"it={sources['itunes_search']}")

    conn.commit()

    if stopped:
        log(f"\n  Interrompido pelo usuario ({processed} tracks salvas).")

    log(f"\n  Resultado final:")
    log(f"    processados:    {processed} / {total}")
    log(f"    sem preview:    {no_preview}")
    log(f"    download fail:  {download_fail}")
    log(f"    analise fail:   {analysis_fail}")
    log(f"    fontes:  embed={sources['spotify_embed']}  "
        f"deezer={sources['deezer_isrc']+sources['deezer_search']}  "
        f"itunes={sources['itunes_search']}")

    return {
        "processed":     processed,
        "no_preview":    no_preview,
        "download_fail": download_fail,
        "analysis_fail": analysis_fail,
    }


# ============================================================
# MODO STANDALONE
# ============================================================

if __name__ == "__main__":
    if librosa is None:
        sys.exit("ERRO: librosa nao instalado. Rode: py -m pip install librosa")

    conn = psycopg2.connect(DSN)
    try:
        run(conn)
        print("\nFim.")
    except KeyboardInterrupt:
        print("\nInterrompido. Rode de novo pra continuar de onde parou.")
        sys.exit(130)
    finally:
        conn.close()
