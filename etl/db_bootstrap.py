"""
Bootstrap da conexao com o banco a partir da config.

Decide entre dois modos e devolve um DatabaseHandle pronto:

- mode "external": usa host/port/user/password do config (comportamento historico).
- mode "embedded": gerencia o cluster PostgreSQL embutido (EmbeddedPostgres),
  garante que esta pronto (init/start/schema) e devolve a conexao para ele.

Senhas (modo embutido):
- app_password (usuario do banco): DEFINIDA pelo usuario na config. Se vazia, erro.
- superuser_password (postgres): interna. Gerada uma unica vez e persistida via
  save_fn; reutilizada nas execucoes seguintes.

O DatabaseHandle expoe os parametros de conexao e, no modo embutido, stop()
para encerrar o servidor ao fechar o app.
"""

import secrets
from pathlib import Path

from etl.embedded_pg import EmbeddedPostgres

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EMBEDDED_PORT = 5433


class DatabaseHandle:
    def __init__(self, params: dict, embedded: EmbeddedPostgres = None):
        self.params = params            # host, port, dbname, user, password
        self.mode = "embedded" if embedded is not None else "external"
        self._embedded = embedded

    def as_db_dict(self) -> dict:
        """Formato compativel com config['db'] / cfg.build_dsn."""
        return {
            "host":     self.params["host"],
            "port":     str(self.params["port"]),
            "dbname":   self.params["dbname"],
            "user":     self.params["user"],
            "password": self.params["password"],
        }

    def stop(self):
        """Encerra o servidor embutido (no-op no modo external)."""
        if self._embedded is not None:
            self._embedded.stop()


def prepare(config: dict, log=print, save_fn=None, project_root=PROJECT_ROOT) -> DatabaseHandle:
    """
    Prepara o banco conforme config['db']['mode'] e devolve um DatabaseHandle.

    Args:
        config:       dict de config (secao 'db')
        log:          callable para mensagens
        save_fn:      callable(config) para persistir o config quando a senha
                      do superusuario for gerada (ex: cfg.save). Opcional.
        project_root: raiz do projeto (para localizar pgsql/, pgdata/, setup_banco.sql)
    """
    db = config.get("db", {})
    mode = db.get("mode", "external")

    # ---------- modo external (comportamento historico) ----------
    if mode != "embedded":
        params = {
            "host":     db.get("host", "localhost"),
            "port":     db.get("port", "5432"),
            "dbname":   db.get("dbname", "spotify"),
            "user":     db.get("user", ""),
            "password": db.get("password", ""),
        }
        return DatabaseHandle(params)

    # ---------- modo embedded ----------
    emb = db.get("embedded", {})
    project_root = Path(project_root)
    bin_dir   = Path(emb.get("bin_dir")  or (project_root / "pgsql" / "bin"))
    data_dir  = Path(emb.get("data_dir") or (project_root / "pgdata"))
    port      = int(emb.get("port", DEFAULT_EMBEDDED_PORT))
    dbname    = emb.get("dbname", "spotify")
    app_user  = emb.get("app_user", "spotify_app")
    app_pwd   = emb.get("app_password", "")
    super_pwd = emb.get("superuser_password", "")

    if not app_pwd:
        raise RuntimeError(
            "Modo embutido: defina a senha do banco (Configuracoes) antes de "
            "preparar. O campo db.embedded.app_password esta vazio."
        )

    # Senha do superusuario e interna: gerada uma vez, persistida e reutilizada.
    if not super_pwd:
        super_pwd = secrets.token_urlsafe(24)
        emb["superuser_password"] = super_pwd
        db["embedded"] = emb
        config["db"] = db
        if save_fn is not None:
            save_fn(config)

    pg = EmbeddedPostgres(
        bin_dir=bin_dir, data_dir=data_dir, port=port,
        superuser="postgres", superuser_pwd=super_pwd,
        app_user=app_user, app_pwd=app_pwd, dbname=dbname, log=log,
    )
    pg.ensure_ready(str(project_root / "setup_banco.sql"))

    params = {
        "host": "127.0.0.1", "port": port, "dbname": dbname,
        "user": app_user, "password": app_pwd,
    }
    return DatabaseHandle(params, embedded=pg)
