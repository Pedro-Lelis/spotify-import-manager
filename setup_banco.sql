-- ============================================================
-- Spotify Import Manager — Schema do Banco de Dados
-- PostgreSQL 14+
--
-- Como usar:
--   1. Crie um banco de dados no PostgreSQL:
--        CREATE DATABASE spotify;
--   2. Crie um usuario (ou use o postgres padrao):
--        CREATE USER seu_usuario WITH PASSWORD 'sua_senha';
--        GRANT ALL PRIVILEGES ON DATABASE spotify TO seu_usuario;
--   3. Execute este script conectado ao banco spotify:
--        psql -U seu_usuario -d spotify -f setup_banco.sql
-- ============================================================

-- Sequencias
CREATE SEQUENCE IF NOT EXISTS genre_id_seq;
CREATE SEQUENCE IF NOT EXISTS listen_id_seq;
CREATE SEQUENCE IF NOT EXISTS podcast_listen_id_seq;
CREATE SEQUENCE IF NOT EXISTS audiobook_listen_id_seq;
CREATE SEQUENCE IF NOT EXISTS playlist_id_seq;
CREATE SEQUENCE IF NOT EXISTS library_item_id_seq;
CREATE SEQUENCE IF NOT EXISTS search_query_id_seq;

-- ============================================================
-- CATALOGO
-- ============================================================

CREATE TABLE IF NOT EXISTS genre (
    id integer NOT NULL DEFAULT nextval('genre_id_seq'::regclass),
    name text NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS artist (
    id text NOT NULL,
    name text NOT NULL,
    popularity smallint,
    followers integer,
    image_url text,
    fetched_at timestamptz,
    bio_en text,
    bio_pt text,
    bio_source text,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS artist_genre (
    artist_id text NOT NULL,
    genre_id integer NOT NULL,
    PRIMARY KEY (artist_id, genre_id),
    FOREIGN KEY (artist_id) REFERENCES artist(id),
    FOREIGN KEY (genre_id) REFERENCES genre(id)
);

CREATE TABLE IF NOT EXISTS album (
    id text NOT NULL,
    name text NOT NULL,
    album_type text,
    release_date date,
    release_date_precision text,
    total_tracks smallint,
    label text,
    image_url text,
    fetched_at timestamptz,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS album_artist (
    album_id text NOT NULL,
    artist_id text NOT NULL,
    position smallint NOT NULL,
    PRIMARY KEY (album_id, artist_id),
    FOREIGN KEY (album_id) REFERENCES album(id),
    FOREIGN KEY (artist_id) REFERENCES artist(id)
);

CREATE TABLE IF NOT EXISTS track (
    id text NOT NULL,
    name text NOT NULL,
    album_id text,
    duration_ms integer NOT NULL,
    explicit boolean,
    popularity smallint,
    disc_number smallint,
    track_number smallint,
    isrc text,
    preview_url text,
    fetched_at timestamptz,
    PRIMARY KEY (id),
    FOREIGN KEY (album_id) REFERENCES album(id)
);

CREATE TABLE IF NOT EXISTS track_artist (
    track_id text NOT NULL,
    artist_id text NOT NULL,
    position smallint NOT NULL,
    PRIMARY KEY (track_id, artist_id),
    FOREIGN KEY (track_id) REFERENCES track(id),
    FOREIGN KEY (artist_id) REFERENCES artist(id)
);

CREATE TABLE IF NOT EXISTS audio_features (
    track_id text NOT NULL,
    danceability real,
    energy real,
    key smallint,
    loudness real,
    mode smallint,
    speechiness real,
    acousticness real,
    instrumentalness real,
    liveness real,
    valence real,
    tempo real,
    time_signature smallint,
    fetched_at timestamptz,
    PRIMARY KEY (track_id),
    FOREIGN KEY (track_id) REFERENCES track(id)
);

-- ============================================================
-- HISTORICO DE ESCUTA
-- ============================================================

CREATE TABLE IF NOT EXISTS listen (
    id bigint NOT NULL DEFAULT nextval('listen_id_seq'::regclass),
    played_at timestamptz NOT NULL,
    ms_played integer NOT NULL,
    track_id text,
    track_name_raw text NOT NULL,
    artist_name_raw text NOT NULL,
    album_name_raw text NOT NULL,
    platform text,
    country character(2),
    reason_start text,
    reason_end text,
    shuffle boolean,
    skipped boolean,
    offline boolean,
    incognito boolean,
    track_uri text,
    PRIMARY KEY (id),
    FOREIGN KEY (track_id) REFERENCES track(id),
    UNIQUE (played_at, track_name_raw, artist_name_raw)
);

CREATE TABLE IF NOT EXISTS podcast_listen (
    id bigint NOT NULL DEFAULT nextval('podcast_listen_id_seq'::regclass),
    played_at timestamptz NOT NULL,
    ms_played integer NOT NULL,
    episode_uri text,
    episode_name_raw text NOT NULL,
    show_name_raw text NOT NULL,
    platform text,
    country character(2),
    reason_start text,
    reason_end text,
    shuffle boolean,
    skipped boolean,
    offline boolean,
    incognito boolean,
    PRIMARY KEY (id),
    UNIQUE (played_at, episode_name_raw, show_name_raw)
);

CREATE TABLE IF NOT EXISTS audiobook_listen (
    id bigint NOT NULL DEFAULT nextval('audiobook_listen_id_seq'::regclass),
    played_at timestamptz NOT NULL,
    ms_played integer NOT NULL,
    audiobook_uri text,
    audiobook_title_raw text NOT NULL,
    chapter_uri text,
    chapter_name_raw text,
    platform text,
    country character(2),
    reason_start text,
    reason_end text,
    shuffle boolean,
    skipped boolean,
    offline boolean,
    incognito boolean,
    PRIMARY KEY (id),
    UNIQUE (played_at, audiobook_title_raw, chapter_uri)
);

-- ============================================================
-- DADOS DA CONTA
-- ============================================================

CREATE TABLE IF NOT EXISTS playlist (
    id integer NOT NULL DEFAULT nextval('playlist_id_seq'::regclass),
    name text NOT NULL,
    description text,
    last_modified date,
    followers integer,
    imported_at timestamptz DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS playlist_track (
    playlist_id integer NOT NULL,
    position integer NOT NULL,
    track_uri text NOT NULL,
    track_id text,
    track_name_raw text NOT NULL,
    artist_name_raw text NOT NULL,
    added_at date,
    album_name_raw text NOT NULL,
    PRIMARY KEY (playlist_id, position),
    FOREIGN KEY (track_id) REFERENCES track(id),
    FOREIGN KEY (playlist_id) REFERENCES playlist(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS playlist_collaborator (
    playlist_id integer NOT NULL,
    collaborator text NOT NULL,
    PRIMARY KEY (playlist_id, collaborator),
    FOREIGN KEY (playlist_id) REFERENCES playlist(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS library_item (
    id bigint NOT NULL DEFAULT nextval('library_item_id_seq'::regclass),
    item_type text NOT NULL,
    item_uri text NOT NULL,
    name_raw text NOT NULL,
    secondary_raw text,
    imported_at timestamptz DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (item_type, item_uri)
);

CREATE TABLE IF NOT EXISTS search_query (
    id bigint NOT NULL DEFAULT nextval('search_query_id_seq'::regclass),
    searched_at timestamptz NOT NULL,
    query text NOT NULL,
    platform text,
    imported_at timestamptz DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (searched_at, query)
);

CREATE TABLE IF NOT EXISTS search_query_interaction (
    search_id bigint NOT NULL,
    position smallint NOT NULL,
    target_uri text NOT NULL,
    PRIMARY KEY (search_id, position),
    FOREIGN KEY (search_id) REFERENCES search_query(id) ON DELETE CASCADE
);

-- ============================================================
-- INDICES (performance de consulta)
-- ============================================================

-- Catalogo
CREATE INDEX IF NOT EXISTS idx_album_artist ON album_artist (artist_id);
CREATE INDEX IF NOT EXISTS idx_artist_id   ON track_artist (artist_id);
CREATE INDEX IF NOT EXISTS idx_album_id    ON track (album_id);
CREATE INDEX IF NOT EXISTS idx_isrc        ON track (isrc);

-- Historico de escuta
CREATE INDEX IF NOT EXISTS idx_listen_track_uri  ON listen (track_uri);
CREATE INDEX IF NOT EXISTS idx_played_at         ON listen (played_at DESC);
CREATE INDEX IF NOT EXISTS idx_track_id          ON listen (track_id);
CREATE INDEX IF NOT EXISTS idx_track_played_at   ON listen (played_at, track_id);
CREATE INDEX IF NOT EXISTS idx_podcast_listen_played_at    ON podcast_listen (played_at DESC);
CREATE INDEX IF NOT EXISTS idx_podcast_listen_episode_uri  ON podcast_listen (episode_uri);
CREATE INDEX IF NOT EXISTS idx_audiobook_listen_played_at    ON audiobook_listen (played_at DESC);
CREATE INDEX IF NOT EXISTS idx_audiobook_listen_audiobook_uri ON audiobook_listen (audiobook_uri);

-- Dados da conta
CREATE INDEX IF NOT EXISTS idx_playlist_track_track_id   ON playlist_track (track_id);
CREATE INDEX IF NOT EXISTS idx_playlist_track_track_uri  ON playlist_track (track_uri);
CREATE INDEX IF NOT EXISTS idx_library_item_item_uri     ON library_item (item_uri);
CREATE INDEX IF NOT EXISTS idx_search_query_searched_at  ON search_query (searched_at DESC);
