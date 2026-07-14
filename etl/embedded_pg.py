"""
Gerencia um PostgreSQL portatil embutido (Windows).

Inicializa (initdb), inicia/para (pg_ctl) e prepara (banco + usuario + schema)
um cluster PostgreSQL que vive dentro da pasta do app, sem instalacao no
sistema. Usado quando db.mode == "embedded".

Layout esperado:
  <raiz>/pgsql/bin/   -> binarios portateis (initdb.exe, pg_ctl.exe, psql.exe)
  <raiz>/pgdata/      -> cluster de dados (criado no 1o run; NAO versionar)

Windows only (usa os .exe e CREATE_NO_WINDOW para nao piscar console na GUI).

Uso tipico:
    pg = EmbeddedPostgres(bin_dir, data_dir, port, superuser, superuser_pwd,
                          app_user, app_pwd, dbname, log=print)
    pg.ensure_ready(caminho_do_setup_banco_sql)   # init + start + prepara
    ...
    pg.stop()                                     # ao fechar o app
"""

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import psycopg2
from psycopg2 import sql

# Evita piscar uma janela de console a cada subprocesso quando rodando na GUI.
CREATE_NO_WINDOW = 0x08000000


class EmbeddedPostgres:
    def __init__(self, bin_dir, data_dir, port,
                 superuser, superuser_pwd,
                 app_user, app_pwd, dbname, log=print):
        self.bin_dir = Path(bin_dir)
        self.data_dir = Path(data_dir)
        self.port = int(port)
        self.superuser = superuser
        self.superuser_pwd = superuser_pwd
        self.app_user = app_user
        self.app_pwd = app_pwd
        self.dbname = dbname
        self.log = log

        self.initdb = self.bin_dir / "initdb.exe"
        self.pg_ctl = self.bin_dir / "pg_ctl.exe"
        self.psql = self.bin_dir / "psql.exe"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, cmd, env=None):
        """Roda um comando sem piscar janela de console. Retorna CompletedProcess."""
        return subprocess.run(
            [str(c) for c in cmd],
            capture_output=True, text=True, env=env,
            creationflags=CREATE_NO_WINDOW,
        )

    def _connect_super(self, dbname):
        return psycopg2.connect(
            host="127.0.0.1", port=self.port,
            user=self.superuser, password=self.superuser_pwd,
            dbname=dbname,
        )

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def is_initialized(self) -> bool:
        """Cluster ja foi criado? (marcado pelo arquivo PG_VERSION)"""
        return (self.data_dir / "PG_VERSION").exists()

    def is_running(self) -> bool:
        """Ha algo escutando em localhost:porta?"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            try:
                s.connect(("127.0.0.1", self.port))
                return True
            except OSError:
                return False

    # ------------------------------------------------------------------
    # Ciclo de vida do cluster
    # ------------------------------------------------------------------

    def initialize(self):
        """Cria o cluster com initdb (no-op se ja inicializado)."""
        if self.is_initialized():
            return
        self.log("  inicializando cluster (initdb)...")
        self.data_dir.parent.mkdir(parents=True, exist_ok=True)

        pwfile = None
        try:
            # Senha do superusuario via arquivo temporario (nunca na linha de comando).
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".pwd",
                                             encoding="utf-8") as f:
                f.write(self.superuser_pwd)
                pwfile = f.name

            r = self._run([
                self.initdb,
                "-D", self.data_dir,
                "-U", self.superuser,
                "--auth=scram-sha-256",
                f"--pwfile={pwfile}",
                "--encoding=UTF8",
                "--locale=C",
            ])
            if r.returncode != 0:
                raise RuntimeError(f"initdb falhou:\n{r.stdout}\n{r.stderr}")
        finally:
            if pwfile and os.path.exists(pwfile):
                os.unlink(pwfile)

        self._configure()
        self.log("  cluster inicializado.")

    def _configure(self):
        """Fixa porta e escuta apenas em localhost (append sobrescreve defaults)."""
        conf = self.data_dir / "postgresql.conf"
        with open(conf, "a", encoding="utf-8") as f:
            f.write(
                "\n# --- Spotify Import Manager (embedded) ---\n"
                f"port = {self.port}\n"
                "listen_addresses = 'localhost'\n"
            )

    def start(self):
        """Sobe o servidor. Reutiliza se ja estiver rodando; limpa pid orfao."""
        if self.is_running():
            self.log(f"  servidor ja rodando na porta {self.port} - reutilizando.")
            return

        logfile = self.data_dir / "server.log"
        cmd = [str(self.pg_ctl), "start", "-D", str(self.data_dir), "-l", str(logfile)]

        def _launch():
            # IMPORTANTE: NAO capturar stdout/stderr aqui. O postgres continua
            # rodando e herda esses handles; sendo um daemon, nunca fecha o pipe,
            # entao subprocess.run(capture_output=True) travaria para sempre.
            # O log do servidor ja vai para o arquivo via -l; mandamos o resto
            # para DEVNULL.
            return subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )

        r = _launch()
        if r.returncode != 0:
            # Caso classico: app fechado a forca deixou postmaster.pid orfao.
            pid_file = self.data_dir / "postmaster.pid"
            if pid_file.exists() and not self.is_running():
                self.log("  postmaster.pid orfao - limpando e tentando de novo.")
                pid_file.unlink()
                r = _launch()
            if r.returncode != 0:
                raise RuntimeError(
                    f"falha ao iniciar Postgres (rc={r.returncode}); veja o log: {logfile}"
                )

        # pg_ctl ja espera por padrao (-w desde o PG 10), mas confirmamos a
        # prontidao para nao seguir antes do servidor aceitar conexoes.
        for _ in range(120):
            if self.is_running():
                self.log(f"  servidor embutido iniciado (porta {self.port}).")
                return
            time.sleep(0.5)
        raise RuntimeError(
            f"servidor nao respondeu na porta {self.port} a tempo; veja o log: {logfile}"
        )

    def stop(self):
        """Encerra o servidor (modo fast). No-op se nao estiver rodando."""
        if not self.is_running():
            return
        self._run([self.pg_ctl, "stop", "-D", self.data_dir, "-m", "fast",
                   "-w", "-t", "60"])
        self.log("  servidor embutido encerrado.")

    # ------------------------------------------------------------------
    # Preparacao do banco
    # ------------------------------------------------------------------

    def prepare_database(self, sql_file):
        """Cria usuario+banco, garante permissao no schema public e roda o DDL."""
        # 1. Usuario e banco (conectado como superusuario na base 'postgres').
        conn = self._connect_super("postgres")
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (self.app_user,))
            if cur.fetchone():
                cur.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(self.app_user), sql.Literal(self.app_pwd)))
            else:
                cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(self.app_user), sql.Literal(self.app_pwd)))

            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.dbname,))
            if not cur.fetchone():
                cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(self.dbname), sql.Identifier(self.app_user)))
        finally:
            conn.close()

        # 2. No PG 15+ o schema public nao concede CREATE por padrao. Como o
        #    app_user vai criar as tabelas, garantimos que ele e dono do public.
        conn2 = self._connect_super(self.dbname)
        conn2.autocommit = True
        try:
            cur2 = conn2.cursor()
            cur2.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                sql.Identifier(self.app_user)))
            cur2.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(
                sql.Identifier(self.app_user)))
        finally:
            conn2.close()

        # 3. Aplica o schema (setup_banco.sql e idempotente).
        env = os.environ.copy()
        env["PGPASSWORD"] = self.app_pwd
        r = self._run([
            self.psql, "-h", "127.0.0.1", "-p", str(self.port),
            "-U", self.app_user, "-d", self.dbname,
            "-v", "ON_ERROR_STOP=1", "-f", sql_file,
        ], env=env)
        if r.returncode != 0:
            raise RuntimeError(f"setup_banco.sql falhou:\n{r.stdout}\n{r.stderr}")
        self.log("  banco/usuario/schema prontos.")

    # ------------------------------------------------------------------
    # Orquestracao
    # ------------------------------------------------------------------

    def ensure_ready(self, sql_file):
        """init (se preciso) -> start -> prepara banco. Idempotente."""
        self.initialize()
        self.start()
        self.prepare_database(sql_file)
