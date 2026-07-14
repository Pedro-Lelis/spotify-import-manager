"""
Gerenciamento de configuracao do Banco Spotify.
Carrega e salva config.json na raiz do projeto.
"""

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / "config.json"

DEFAULT_CONFIG = {
    "db": {
        "mode": "embedded",          # "embedded" (padrao, turnkey) | "external"
        "host": "localhost",         # usados no modo external
        "port": "5432",
        "dbname": "spotify",
        "user": "claude_etl",
        "password": "",
        "embedded": {                # usados no modo embedded
            "port": "5433",
            "dbname": "spotify",
            "app_user": "spotify_app",
            "app_password": "",          # DEFINIDA pelo usuario (aba Configuracoes)
            "superuser_password": "",    # interna, gerada no 1o run
        },
        "ssh": {                     # tunel SSH (opcional, modo external)
            "enabled": False,
            "host": "",
            "port": "22",
            "user": "",
            "key_path": "",
        },
    },
    "spotify": {
        "client_id": "",
        "client_secret": "",
    },
    "paths": {
        "extended_history_dir": "",
        "basic_export_dir": "",
    },
    "lastfm": {
        "api_key": "",
    },
    "discogs": {
        "user_token": "",
    },
}


def _deep_merge(default: dict, override: dict) -> dict:
    """Merge recursivo: preserva sub-chaves do default nao presentes no override."""
    result = dict(default)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load() -> dict:
    """Carrega config.json, fazendo deep-merge com os defaults."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return _deep_merge(DEFAULT_CONFIG, data)
    return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy


def save(cfg: dict) -> None:
    """Salva config.json."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def build_dsn(db: dict) -> str:
    """Monta a string de conexao DSN a partir da secao 'db' do config."""
    return (
        f"postgresql://{db['user']}:{db['password']}"
        f"@{db['host']}:{db['port']}/{db['dbname']}"
    )
