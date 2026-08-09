-- ============================================================
-- MODELO ESTRELA (v2) — interatividade total no dashboard
-- Fato: fato_escuta (grao = 1 reproducao)
-- Dimensoes: dim_tempo, dim_artista, dim_genero
-- Relacionamentos no Power BI: dimensao (1) -> fato (*)
-- ============================================================

-- Dimensao Tempo (uma linha por dia do calendario)
CREATE OR REPLACE VIEW dim_tempo AS
WITH datas AS (
    SELECT generate_series(
             (SELECT min((played_at AT TIME ZONE 'America/Sao_Paulo')::date) FROM listen),
             (SELECT max((played_at AT TIME ZONE 'America/Sao_Paulo')::date) FROM listen),
             interval '1 day'
           )::date AS data
)
SELECT data,
       extract(year  FROM data)::int  AS ano,
       extract(month FROM data)::int  AS mes,
       to_char(data, 'YYYY-MM')       AS ano_mes,
       extract(isodow FROM data)::int AS dia_semana,
       CASE extract(isodow FROM data)::int
            WHEN 1 THEN 'Segunda' WHEN 2 THEN 'Terca'  WHEN 3 THEN 'Quarta'
            WHEN 4 THEN 'Quinta'  WHEN 5 THEN 'Sexta'   WHEN 6 THEN 'Sabado'
            WHEN 7 THEN 'Domingo'
       END AS nome_dia
FROM datas;

-- Dimensao Artista (uma linha por artista)
CREATE OR REPLACE VIEW dim_artista AS
SELECT DISTINCT artist_name_raw AS artista
FROM listen
WHERE artist_name_raw IS NOT NULL;

-- Fato Escuta (grao = 1 reproducao)
-- genero: 1 genero principal por faixa (artista position 0, menor genre id)
-- features de audio: valence/energy/danceability da faixa
CREATE OR REPLACE VIEW fato_escuta AS
SELECT (li.played_at AT TIME ZONE 'America/Sao_Paulo')::date              AS data,
       extract(hour FROM li.played_at AT TIME ZONE 'America/Sao_Paulo')::int AS hora,
       li.artist_name_raw AS artista,
       li.track_name_raw  AS faixa,
       li.album_name_raw  AS album,
       li.ms_played,
       (li.ms_played >= 30000) AS is_valido,
       li.skipped,
       g.genero,
       af.valence,
       af.energy,
       af.danceability
FROM listen li
LEFT JOIN audio_features af ON af.track_id = li.track_id
LEFT JOIN LATERAL (
    SELECT ge.name AS genero
    FROM track_artist ta
    JOIN artist_genre ag ON ag.artist_id = ta.artist_id
    JOIN genre ge        ON ge.id = ag.genre_id
    WHERE ta.track_id = li.track_id AND ta.position = 0
    ORDER BY ge.id
    LIMIT 1
) g ON true;

-- Dimensao Genero (uma linha por genero principal presente no fato)
CREATE OR REPLACE VIEW dim_genero AS
SELECT DISTINCT genero
FROM fato_escuta
WHERE genero IS NOT NULL;
