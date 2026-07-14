"""
Gerenciamento de configuracao do Banco Spotify.
Carrega e salva config.json na raiz do projeto.
"""

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / "config.json"

DEFAULT_CONFIG = {
    "db": {
        "host": "localhost",
        "port": "5432",
        "dbname": "spotify",
        "user": "claude_etl",
        "password": "",
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


def load() -> dict:
    """Carrega config.json, fazendo deep-merge com os defaults."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        result = {}
        for k, v in DEFAULT_CONFIG.items():
            if k in data and isinstance(v, dict):
                result[k] = {**v, **data[k]}
            else:
                result[k] = data.get(k, v)
        return result
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
