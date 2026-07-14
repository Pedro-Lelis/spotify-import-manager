# Spotify Import Manager

Pipeline ETL + interface gráfica para construir um banco de dados pessoal com todo o histórico de escuta do Spotify.

---

## Objetivo

O Spotify oferece um arquivo de exportação com o histórico completo de escutas, mas os dados brutos são JSONs fragmentados e sem metadados ricos. Este projeto transforma esses JSONs num banco PostgreSQL estruturado, enriquecido com dados da API do Spotify e features de áudio calculadas via análise de sinal — pronto para consultas SQL analíticas e futura visualização em dashboard.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Banco de dados | PostgreSQL 14+ |
| ETL / backend | Python 3.10–3.13 |
| Interface gráfica | tkinter (built-in) |
| Análise de áudio | librosa + pyloudnorm |
| Enriquecimento de catálogo | Spotify Web API |
| Recuperação de previews | Spotify Embed Player (scraping) |
| Previews alternativos | Deezer API + iTunes Search API |
| Dependências | psycopg2-binary, requests, numpy, scipy |

---

## Arquitetura

```
Dados do Spotify (JSON)
        │
        ▼
┌───────────────────────────────────────────────────────┐
│                  Import Manager (app.py)               │
│              Interface tkinter — 3 abas               │
│    Configurações │ Importar (pipeline) │ Status        │
└───────────────────────────┬───────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │    pipeline.py          │
              │  Orquestrador das etapas│
              └──────────┬──────────────┘
                         │
        ┌────────────────┼────────────────────────┐
        ▼                ▼                         ▼
 Etapa 1 & 2      Etapa 3                    Etapa 4
 Histórico +      Enriquecer catálogo        Recuperar
 Dados da conta   (Spotify API)              preview_url
 (JSONs locais)   tracks/artists/albums      (Embed scraping
                                              10 workers)
        │                │                         │
        └────────────────┴─────────────┬───────────┘
                                       ▼
                              Etapas 5 & 6
                         Features de áudio (librosa)
                         Features extras (8 workers)
                                       │
                                       ▼
                             PostgreSQL — 17 tabelas
```

---

## Etapas do Pipeline

| # | Etapa | O que faz | Fonte |
|---|---|---|---|
| 1 | **Histórico de escuta** | Importa `Streaming_History_Audio_*.json` e `Video_*.json` | JSON local |
| 2 | **Dados da conta** | Importa playlists, biblioteca, buscas | JSON local |
| 3 | **Enriquecer catálogo** | Busca metadados de tracks, artistas e álbuns | Spotify Web API |
| 4 | **Recuperar preview_url** | Scraping do Embed Player para obter URLs de preview MP3 (10 workers paralelos) | Spotify Embed |
| 5 | **Features de áudio** | Calcula tempo, tom, loudness e energy via Librosa | Preview MP3 |
| 6 | **Features extras** | Calcula danceability, valence, speechiness, acousticness, instrumentalness, liveness, time_signature (8 workers paralelos) | Preview MP3 |
| 7 | **Biografias de artistas** | Busca bios em cascata | TheAudioDB → Wikipedia → Last.fm → Discogs |

Todas as etapas são **idempotentes** — podem ser interrompidas e reexecutadas sem duplicar dados. O pipeline tem botão de parada graciosa que salva o progresso antes de encerrar.

---

## Schema do Banco

17 tabelas organizadas em 3 domínios:

**Catálogo musical**
- `track`, `artist`, `album`, `genre`
- `track_artist`, `album_artist`, `artist_genre`
- `audio_features`

**Histórico de escuta**
- `listen`, `podcast_listen`, `audiobook_listen`

**Dados da conta**
- `playlist`, `playlist_track`, `playlist_collaborator`
- `library_item`, `search_query`, `search_query_interaction`

---

## Features de Áudio

### Calculadas via Librosa (análise de sinal)
| Feature | Método |
|---|---|
| `tempo` | Beat tracking |
| `key` | Krumhansl-Schmuckler sobre chroma CQT |
| `mode` | Krumhansl-Schmuckler (maior/menor) |
| `loudness` | LUFS via pyloudnorm |
| `energy` | RMS normalizado |

### Aproximadas via análise espectral
> Estes valores são coerentes musicalmente mas não idênticos aos da API do Spotify (deprecada em nov/2024).

| Feature | Proxy utilizado |
|---|---|
| `danceability` | Regularidade de beats + força do onset + BPM na zona dancável |
| `time_signature` | Correlação do onset envelope com padrões 3/4 e 4/4 |
| `speechiness` | Zero-crossing rate acima da baseline musical |
| `acousticness` | Spectral flatness (instrumentos acústicos têm flatness maior que sintetizadores) |
| `instrumentalness` | ZCR invertido (voz aumenta ZCR; instrumental tem ZCR mais baixo) |
| `liveness` | Dynamic range (gravações ao vivo: 22–45 dB; estúdio: 6–20 dB) |
| `valence` | Tom maior/menor + contribuição de BPM + brilho espectral |

---

## Estado Atual

> Última atualização: junho 2026

| Tabela / Feature | Registros | Cobertura |
|---|---|---|
| `track` | 5.226 | 100% |
| `artist`, `album` | populados via API | 100% |
| `listen` | 32.227 | 100% |
| `preview_url` | 5.166 / 5.226 | 98.9% |
| `audio_features` básicas | 5.211 / 5.226 | 99.7% |
| `audio_features` extras | 5.199 / 5.226 | 99.5% |

As ~60–75 tracks sem cobertura total são músicas removidas de todas as plataformas de streaming (sem áudio disponível em Spotify, Deezer ou iTunes).

---

## O que Está Planejado

### Curto prazo
- [ ] **Dashboard de visualização** — objetivo original do projeto; visualizar padrões de escuta, artistas mais ouvidos por período, evolução de features ao longo do tempo

### Médio prazo
- [ ] **Essentia + TensorFlow** — substituir as aproximações espectrais por modelos ML pré-treinados do Music Technology Group (Barcelona), especialmente para `valence` e `liveness` que são os mais difíceis de estimar sem ML
- [ ] **Atualização incremental** — hoje o pipeline processa todos os dados a cada execução; implementar detecção de novos JSONs para processar apenas o delta

### Longo prazo
- [ ] **Suporte a múltiplos usuários** — o projeto foi pensado inicialmente para uso pessoal, mas a arquitetura suporta dados de diferentes contas com adição de coluna `user_id`

---

## Como Rodar

### Primeira vez
1. Instale Python 3.10 a 3.13 em [python.org](https://python.org) (marque **"Add to PATH"**)
2. Instale PostgreSQL em [postgresql.org](https://postgresql.org) (anote a senha do usuário `postgres`)
3. Crie o banco de dados vazio:
   ```
   psql -U postgres -c "CREATE DATABASE spotify;"
   ```
4. Crie as tabelas dentro dele:
   ```
   psql -U postgres -d spotify -f setup_banco.sql
   ```
5. Execute `instalar.bat` (instala as dependências Python; encontra a versão certa automaticamente)
6. Execute `abrir_app.bat`
7. Na aba **Configurações**, escolha **"Conectar a um PostgreSQL existente"** e preencha host, porta, banco (`spotify`), usuário e senha → clique em **"Testar conexão"**

> A opção **"Banco embutido"** (que dispensa instalar o PostgreSQL) está em desenvolvimento. Por enquanto, use **"Conectar a um PostgreSQL existente"**.

### Execuções seguintes
Apenas `abrir_app.bat` → selecionar etapas → **Executar pipeline**

### Credenciais Spotify API (opcional — Etapa 3)
1. Acesse [developer.spotify.com](https://developer.spotify.com)
2. Crie um app (gratuito)
3. Copie Client ID e Client Secret para a aba Configurações

### Obtendo seus dados do Spotify
1. Acesse [account.spotify.com](https://account.spotify.com) → Privacidade → Baixar meus dados
2. Solicite **Extended Streaming History** (demora ~5 dias úteis)
3. Aponte os caminhos das pastas na aba Configurações

---

## Estrutura de Arquivos

```
Spotify Import Manager\
  ├── app.py                        ← Interface gráfica (tkinter)
  ├── abrir_app.bat                 ← Atalho para iniciar
  ├── instalar.bat                  ← Instala dependências Python
  ├── desinstalar.bat               ← Remove dependências Python
  ├── find_python.bat               ← Detecta a versão do Python adequada
  ├── requirements.txt              ← Lista de dependências
  ├── setup_banco.sql               ← Schema completo do banco
  ├── config.json.example           ← Template de configuração
  └── etl\
      ├── __init__.py               ← Torna etl/ um pacote Python
      ├── config.py                 ← Carrega/salva config.json
      ├── db_bootstrap.py           ← Decide embutido/externo, prepara a conexão
      ├── embedded_pg.py            ← Gerencia o PostgreSQL embutido (em dev)
      ├── ssh_tunnel.py             ← Túnel SSH para bancos remotos
      ├── pipeline.py               ← Orquestrador das etapas
      ├── import_listening_history.py
      ├── import_basic_export.py
      ├── enrich_catalog.py         ← Spotify Web API
      ├── enrich_preview_urls.py    ← Embed scraping (paralelo)
      ├── compute_audio_features.py ← Librosa básico
      ├── compute_extra_features.py ← Librosa extras (paralelo)
      └── enrich_artist_bio.py      ← Biografias de artistas
```
