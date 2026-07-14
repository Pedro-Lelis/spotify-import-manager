"""
Banco Spotify — Import Manager
Interface grafica (tkinter) para o pipeline ETL.

Como rodar:
    py app.py
"""

import sys
import json
import queue
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

# Garante que a raiz do projeto esta no path para importar etl.*
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import psycopg2
except ImportError:
    psycopg2 = None

# ============================================================
# CONSTANTES
# ============================================================

APP_TITLE  = "Banco Spotify — Import Manager"
WIN_SIZE   = "960x680"
ACCENT     = "#1DB954"   # verde Spotify
BG_DARK    = "#191414"
BG_CARD    = "#282828"
FG_MAIN    = "#FFFFFF"
FG_MUTED   = "#B3B3B3"

STEPS = [
    ("history", "Historico de escuta",                        "import_listening_history.py"),
    ("basic",   "Dados da conta (playlists, buscas...)",      "import_basic_export.py"),
    ("enrich",  "Enriquecer catalogo (Spotify API)",          "enrich_catalog.py"),
    ("preview", "Recuperar preview_url (Embed Scraping)",     "enrich_preview_urls.py"),
    ("audio",   "Features de audio (Librosa — demora)",       "compute_audio_features.py"),
    ("extra",   "Features extras (danceability, valence...)", "compute_extra_features.py"),
    ("bio",     "Biografias de artistas (TheAudioDB)",       "enrich_artist_bio.py"),
]

# Etapas que levam muito tempo — mostradas com aviso na UI
SLOW_STEPS = {"preview", "audio", "extra", "bio"}

CONFIG_FILE = PROJECT_ROOT / "config.json"
DEFAULT_CONFIG = {
    "db": {
        "mode":     "embedded",      # "embedded" (padrao, turnkey) | "external"
        "host":     "localhost",     # usados no modo external
        "port":     "5432",
        "dbname":   "spotify",
        "user":     "claude_etl",
        "password": "",
        "embedded": {                # usados no modo embedded
            "port":               "5433",
            "dbname":             "spotify",
            "app_user":           "spotify_app",
            "app_password":       "",    # DEFINIDA pelo usuario (aba Configuracoes)
            "superuser_password": "",    # interna, gerada no 1o run
        },
    },
    "spotify": {
        "client_id":     "",
        "client_secret": "",
    },
    "paths": {
        "extended_history_dir": "",
        "basic_export_dir":     "",
    },
    "lastfm": {
        "api_key": "",
    },
    "discogs": {
        "user_token": "",
    },
}


# ============================================================
# CONFIG HELPERS
# ============================================================

def load_config() -> dict:
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
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def build_dsn(db: dict) -> str:
    return (
        f"postgresql://{db['user']}:{db['password']}"
        f"@{db['host']}:{db['port']}/{db['dbname']}"
    )


# ============================================================
# TEMA
# ============================================================

def apply_theme(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    style.theme_use("clam")

    root.configure(bg=BG_DARK)

    style.configure(".",
        background=BG_DARK,
        foreground=FG_MAIN,
        fieldbackground=BG_CARD,
        troughcolor=BG_CARD,
        bordercolor=BG_CARD,
        darkcolor=BG_CARD,
        lightcolor=BG_CARD,
        insertcolor=FG_MAIN,
        font=("Segoe UI", 10),
    )

    # Notebook (abas)
    style.configure("TNotebook",
        background=BG_DARK,
        tabmargins=[0, 5, 0, 0],
    )
    style.configure("TNotebook.Tab",
        background=BG_CARD,
        foreground=FG_MUTED,
        padding=[16, 8],
        font=("Segoe UI", 10),
    )
    style.map("TNotebook.Tab",
        background=[("selected", BG_DARK)],
        foreground=[("selected", FG_MAIN)],
    )

    # Frames
    style.configure("TFrame", background=BG_DARK)
    style.configure("Card.TFrame", background=BG_CARD, relief="flat")

    # Labels
    style.configure("TLabel", background=BG_DARK, foreground=FG_MAIN)
    style.configure("Muted.TLabel", background=BG_DARK, foreground=FG_MUTED, font=("Segoe UI", 9))
    style.configure("Header.TLabel", background=BG_DARK, foreground=FG_MAIN, font=("Segoe UI", 12, "bold"))
    style.configure("Accent.TLabel", background=BG_DARK, foreground=ACCENT, font=("Segoe UI", 10, "bold"))
    style.configure("Card.TLabel", background=BG_CARD, foreground=FG_MAIN)
    style.configure("CardMuted.TLabel", background=BG_CARD, foreground=FG_MUTED, font=("Segoe UI", 9))
    style.configure("CardBig.TLabel", background=BG_CARD, foreground=ACCENT, font=("Segoe UI", 22, "bold"))

    # Entries
    style.configure("TEntry",
        fieldbackground=BG_CARD,
        foreground=FG_MAIN,
        insertcolor=FG_MAIN,
        borderwidth=0,
        relief="flat",
    )

    # Botoes
    style.configure("TButton",
        background=BG_CARD,
        foreground=FG_MAIN,
        padding=[12, 6],
        relief="flat",
        borderwidth=0,
    )
    style.map("TButton",
        background=[("active", "#3E3E3E"), ("pressed", "#555555")],
    )
    style.configure("Accent.TButton",
        background=ACCENT,
        foreground="#000000",
        font=("Segoe UI", 10, "bold"),
        padding=[16, 8],
    )
    style.map("Accent.TButton",
        background=[("active", "#1ed760"), ("pressed", "#169b43")],
    )
    style.configure("Danger.TButton",
        background="#E91429",
        foreground=FG_MAIN,
        padding=[12, 6],
    )
    style.map("Danger.TButton",
        background=[("active", "#ff1f38")],
    )

    # Checkbutton
    style.configure("TCheckbutton",
        background=BG_DARK,
        foreground=FG_MAIN,
        indicatorcolor=BG_CARD,
    )
    style.map("TCheckbutton",
        indicatorcolor=[("selected", ACCENT)],
    )

    # Progressbar
    style.configure("TProgressbar",
        troughcolor=BG_CARD,
        background=ACCENT,
        thickness=6,
    )

    # Separator
    style.configure("TSeparator", background="#333333")

    # LabelFrame
    style.configure("TLabelframe",
        background=BG_DARK,
        foreground=FG_MUTED,
        bordercolor="#333333",
        relief="solid",
        borderwidth=1,
    )
    style.configure("TLabelframe.Label",
        background=BG_DARK,
        foreground=FG_MUTED,
        font=("Segoe UI", 9),
    )

    return style


# ============================================================
# ABA: CONFIGURACOES
# ============================================================

class SettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._vars = {}
        self._build()
        self._load_from_config()

    def _build(self):
        # Scroll container
        canvas = tk.Canvas(self, bg=BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self._scroll_frame = ttk.Frame(canvas)

        self._scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self._scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Bind mouse wheel
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        f = self._scroll_frame
        pad = {"padx": 24, "pady": 6}

        # ---- Banco de Dados ----
        ttk.Label(f, text="Banco de Dados", style="Header.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=24, pady=(24, 4))
        ttk.Separator(f, orient="horizontal").grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=24, pady=(0, 12))

        fields_db = [
            ("host",     "Host",     False),
            ("port",     "Porta",    False),
            ("dbname",   "Banco",    False),
            ("user",     "Usuario",  False),
            ("password", "Senha",    True),
        ]
        for i, (key, label, is_pw) in enumerate(fields_db):
            ttk.Label(f, text=label).grid(row=2+i, column=0, sticky="e", **pad)
            var = tk.StringVar()
            entry = ttk.Entry(f, textvariable=var, width=40, show="*" if is_pw else "")
            entry.grid(row=2+i, column=1, sticky="w", **pad)
            self._vars[f"db.{key}"] = var

        # Botoes banco
        btn_frame = ttk.Frame(f)
        btn_frame.grid(row=2+len(fields_db), column=0, columnspan=3, sticky="w", padx=24, pady=8)
        ttk.Button(btn_frame, text="Testar conexao", command=self._test_conn).pack(side="left", padx=(0, 8))

        # ---- Spotify API ----
        ttk.Label(f, text="Spotify API", style="Header.TLabel").grid(
            row=10, column=0, columnspan=3, sticky="w", padx=24, pady=(24, 4))
        ttk.Separator(f, orient="horizontal").grid(
            row=11, column=0, columnspan=3, sticky="ew", padx=24, pady=(0, 12))

        ttk.Label(f, text="Client ID").grid(row=12, column=0, sticky="e", **pad)
        var_cid = tk.StringVar()
        ttk.Entry(f, textvariable=var_cid, width=50).grid(row=12, column=1, sticky="w", **pad)
        self._vars["spotify.client_id"] = var_cid

        ttk.Label(f, text="Client Secret").grid(row=13, column=0, sticky="e", **pad)
        var_cs = tk.StringVar()
        ttk.Entry(f, textvariable=var_cs, width=50, show="*").grid(row=13, column=1, sticky="w", **pad)
        self._vars["spotify.client_secret"] = var_cs

        hint = "Crie um app em developer.spotify.com → Dashboard → seu app → Settings"
        ttk.Label(f, text=hint, style="Muted.TLabel").grid(
            row=14, column=1, sticky="w", padx=24, pady=(0, 6))

        # ---- Pastas dos JSONs ----
        ttk.Label(f, text="Pastas dos JSONs", style="Header.TLabel").grid(
            row=20, column=0, columnspan=3, sticky="w", padx=24, pady=(24, 4))
        ttk.Separator(f, orient="horizontal").grid(
            row=21, column=0, columnspan=3, sticky="ew", padx=24, pady=(0, 12))

        path_fields = [
            ("extended_history_dir", "Extended History",  "Pasta com Streaming_History_*.json"),
            ("basic_export_dir",     "Account Data",      "Pasta com Playlist1.json, YourLibrary.json, SearchQueries.json"),
        ]
        for i, (key, label, hint_text) in enumerate(path_fields):
            ttk.Label(f, text=label).grid(row=22+i*2, column=0, sticky="ne", **pad)
            var = tk.StringVar()
            entry_frame = ttk.Frame(f)
            entry_frame.grid(row=22+i*2, column=1, sticky="w", **pad)
            ttk.Entry(entry_frame, textvariable=var, width=46).pack(side="left")
            ttk.Button(entry_frame, text="Buscar...",
                       command=lambda v=var: self._browse(v)).pack(side="left", padx=(6, 0))
            ttk.Label(f, text=hint_text, style="Muted.TLabel").grid(
                row=23+i*2, column=1, sticky="w", padx=24, pady=(0, 4))
            self._vars[f"paths.{key}"] = var

        # ---- Salvar ----
        ttk.Separator(f, orient="horizontal").grid(
            row=30, column=0, columnspan=3, sticky="ew", padx=24, pady=(20, 12))
        save_frame = ttk.Frame(f)
        save_frame.grid(row=31, column=0, columnspan=3, sticky="w", padx=24, pady=(0, 24))
        ttk.Button(save_frame, text="Salvar configuracoes", style="Accent.TButton",
                   command=self._save).pack(side="left")

        # Coluna de peso
        f.columnconfigure(1, weight=1)

    def _load_from_config(self):
        cfg = self.app.config
        for key, var in self._vars.items():
            section, field = key.split(".", 1)
            var.set(str(cfg.get(section, {}).get(field, "")))

    def _read_to_config(self):
        cfg = self.app.config
        for key, var in self._vars.items():
            section, field = key.split(".", 1)
            cfg[section][field] = var.get()

    def _save(self):
        self._read_to_config()
        save_config(self.app.config)
        messagebox.showinfo("Salvo", "Configuracoes salvas com sucesso!")

    def _test_conn(self):
        self._read_to_config()
        if psycopg2 is None:
            messagebox.showerror("Erro", "psycopg2 nao instalado.\nRode: py -m pip install psycopg2-binary")
            return
        try:
            dsn = build_dsn(self.app.config["db"])
            conn = psycopg2.connect(dsn, connect_timeout=5)
            cur = conn.cursor()
            cur.execute("SELECT version()")
            ver = cur.fetchone()[0]
            conn.close()
            messagebox.showinfo("Conexao OK", f"Conectado com sucesso!\n\n{ver}")
        except Exception as e:
            messagebox.showerror("Erro de conexao", str(e))

    def _browse(self, var: tk.StringVar):
        initial = var.get() if var.get() else str(Path.home())
        d = filedialog.askdirectory(title="Selecionar pasta", initialdir=initial)
        if d:
            var.set(d)


# ============================================================
# ABA: IMPORTAR
# ============================================================

class ImportTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._queue     = queue.Queue()
        self._running   = False
        self._stop_event = threading.Event()
        self._step_vars = {}
        self._build()

    def _build(self):
        # Painel esquerdo: opcoes
        left = ttk.Frame(self, width=260)
        left.pack(side="left", fill="y", padx=(20, 0), pady=20)
        left.pack_propagate(False)

        ttk.Label(left, text="Etapas", style="Header.TLabel").pack(anchor="w", pady=(0, 12))

        for key, label, _ in STEPS:
            var = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(left, text=label, variable=var)
            cb.pack(anchor="w", pady=4)
            self._step_vars[key] = var

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=16)

        # Aviso etapas lentas
        self._slow_warn = ttk.Label(
            left,
            text="⚠ Embed Scraping pode levar\n  1-3h para 5000+ tracks.",
            style="Muted.TLabel",
            justify="left",
        )
        # mostrado/ocultado dinamicamente em _update_slow_warn()
        for key, var in self._step_vars.items():
            var.trace_add("write", lambda *_: self._update_slow_warn())

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=(8, 16))

        # Progresso geral
        ttk.Label(left, text="Progresso", style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        self._progress_label = ttk.Label(left, text="Aguardando...", style="Muted.TLabel")
        self._progress_label.pack(anchor="w")
        self._progress_bar = ttk.Progressbar(left, orient="horizontal",
                                              mode="indeterminate", length=220)
        self._progress_bar.pack(fill="x", pady=(6, 0))

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=16)

        self._btn_run = ttk.Button(left, text="▶  Executar pipeline",
                                   style="Accent.TButton", command=self._start)
        self._btn_run.pack(fill="x", pady=(0, 6))

        self._btn_stop = ttk.Button(left, text="⏹  Parar",
                                    command=self._stop, state="disabled")
        self._btn_stop.pack(fill="x", pady=(0, 8))

        self._btn_clear = ttk.Button(left, text="Limpar log", command=self._clear_log)
        self._btn_clear.pack(fill="x")

        # Painel direito: log
        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=20, pady=20)

        ttk.Label(right, text="Log de execucao", style="Header.TLabel").pack(anchor="w", pady=(0, 8))

        self._log_text = scrolledtext.ScrolledText(
            right,
            state="disabled",
            bg=BG_CARD,
            fg=FG_MAIN,
            insertbackground=FG_MAIN,
            font=("Consolas", 9),
            relief="flat",
            wrap="word",
            padx=12,
            pady=12,
        )
        self._log_text.pack(fill="both", expand=True)

        # Tags de cor no log
        self._log_text.tag_config("ok",    foreground=ACCENT)
        self._log_text.tag_config("err",   foreground="#E91429")
        self._log_text.tag_config("warn",  foreground="#FFA500")
        self._log_text.tag_config("head",  foreground=FG_MAIN, font=("Consolas", 9, "bold"))
        self._log_text.tag_config("muted", foreground=FG_MUTED)

    def _update_slow_warn(self):
        selected_slow = any(
            self._step_vars.get(k, tk.BooleanVar()).get()
            for k in SLOW_STEPS
        )
        if selected_slow:
            self._slow_warn.pack(anchor="w", pady=(0, 4))
        else:
            self._slow_warn.pack_forget()

    def _log(self, msg: str, tag: str = None):
        """Adiciona linha ao log (thread-safe via queue)."""
        self._queue.put(("log", msg, tag))

    def _clear_log(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _append_log(self, msg: str, tag: str = None):
        """Deve ser chamado apenas na thread principal."""
        self._log_text.configure(state="normal")
        if tag:
            self._log_text.insert("end", msg + "\n", tag)
        else:
            self._log_text.insert("end", msg + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _start(self):
        if self._running:
            return

        selected = [k for k, v in self._step_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("Nenhuma etapa", "Selecione ao menos uma etapa.")
            return

        # Valida config minima
        cfg = self.app.config
        if not cfg["db"].get("password"):
            messagebox.showwarning(
                "Configuracao incompleta",
                "Configure a senha do banco na aba Configuracoes antes de executar."
            )
            return

        if "history" in selected and not cfg["paths"].get("extended_history_dir"):
            messagebox.showwarning(
                "Pasta nao configurada",
                "Configure a pasta do Extended History na aba Configuracoes."
            )
            return

        if "basic" in selected and not cfg["paths"].get("basic_export_dir"):
            messagebox.showwarning(
                "Pasta nao configurada",
                "Configure a pasta do Account Data na aba Configuracoes."
            )
            return

        self._running = True
        self._stop_event.clear()
        self._btn_run.configure(state="disabled", text="Executando...")
        self._btn_stop.configure(state="normal", text="⏹  Parar")
        self._progress_bar.start(12)
        self._progress_label.configure(text="Rodando...")

        # Salva config atual antes de rodar
        save_config(cfg)

        thread = threading.Thread(
            target=self._pipeline_thread,
            args=(cfg, selected),
            daemon=True,
        )
        thread.start()
        self.after(100, self._check_queue)

    def _stop(self):
        """Sinaliza parada cooperativa ao pipeline."""
        if not self._running:
            return
        self._stop_event.set()
        self._btn_stop.configure(state="disabled", text="Parando...")
        self._append_log("\n⏹ Parada solicitada — aguardando workers terminarem...", "warn")

    def _pipeline_thread(self, cfg: dict, steps: list):
        try:
            from etl import pipeline
            pipeline.run(
                config=cfg,
                steps=steps,
                log=lambda msg: self._queue.put(("log", msg, self._tag_for(msg))),
                on_step_start=lambda step, idx, total: self._queue.put(
                    ("step", step, idx, total)
                ),
                on_step_done=lambda step, stats: self._queue.put(("done_step", step, stats)),
                stop_event=self._stop_event,
            )
            if self._stop_event.is_set():
                self._queue.put(("stopped", None))
            else:
                self._queue.put(("finished", None))
        except Exception as e:
            self._queue.put(("error", str(e)))

    def _tag_for(self, msg: str) -> str:
        """Detecta tag de cor com base no conteudo da mensagem."""
        ml = msg.lower()
        if any(x in ml for x in ["erro", "error", "fail", "❌"]):
            return "err"
        if any(x in ml for x in ["aviso", "warn", "pula", "skip", "⚠"]):
            return "warn"
        if any(x in ml for x in ["===", "---", "etapa"]):
            return "head"
        if any(x in ml for x in ["total:", "inseridos:", "ok=", "processados:"]):
            return "ok"
        if any(x in ml for x in ["nada a fazer", "ja na db", "ja em audio"]):
            return "muted"
        return None

    def _check_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]

                if kind == "log":
                    _, text, tag = msg
                    self._append_log(text, tag)

                elif kind == "step":
                    _, step, idx, total = msg
                    pass  # progresso ja vai pelo log

                elif kind == "done_step":
                    _, step, stats = msg
                    pass

                elif kind == "finished":
                    self._pipeline_done(success=True)
                    return

                elif kind == "stopped":
                    self._pipeline_done(success=True, stopped=True)
                    return

                elif kind == "error":
                    _, err = msg
                    self._append_log(f"\nERRO: {err}", "err")
                    self._pipeline_done(success=False, error=err)
                    return

        except queue.Empty:
            pass

        if self._running:
            self.after(100, self._check_queue)

    def _pipeline_done(self, success: bool, error: str = None, stopped: bool = False):
        self._running = False
        self._progress_bar.stop()
        self._btn_run.configure(state="normal", text="▶  Executar pipeline")
        self._btn_stop.configure(state="disabled", text="⏹  Parar")

        if stopped:
            self._progress_label.configure(text="Interrompido")
            self._append_log("\n⏹ Pipeline interrompido. Progresso salvo — rode novamente para continuar.", "warn")
            try:
                self.app.status_tab.refresh()
            except Exception:
                pass
        elif success:
            self._progress_label.configure(text="Concluido!")
            self._append_log("\n✓ Pipeline finalizado com sucesso.", "ok")
            try:
                self.app.status_tab.refresh()
            except Exception:
                pass
        else:
            self._progress_label.configure(text="Erro!")
            messagebox.showerror("Erro no pipeline", error or "Erro desconhecido.")


# ============================================================
# ABA: STATUS
# ============================================================

class StatusTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._cards = {}
        self._build()
        self.after(300, self.refresh)   # carrega ao abrir

    def _build(self):
        # Cabecalho
        header = ttk.Frame(self)
        header.pack(fill="x", padx=24, pady=(20, 0))
        ttk.Label(header, text="Status do Banco", style="Header.TLabel").pack(side="left")
        ttk.Button(header, text="Atualizar", command=self.refresh).pack(side="right")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=24, pady=12)

        # Grid de cards
        grid = ttk.Frame(self)
        grid.pack(fill="both", expand=True, padx=24)

        card_defs = [
            ("plays",        "Plays totais",          "listen"),
            ("tracks",       "Tracks unicas",         "track"),
            ("artists",      "Artistas",               "artist"),
            ("albums",       "Albums",                 "album"),
            ("genres",       "Generos",                "genre"),
            ("playlists",    "Playlists",              "playlist"),
            ("searches",     "Buscas",                 "search_query"),
            ("audio_feat",   "Com audio features",    "audio_features"),
        ]

        cols = 4
        for i, (key, label, _) in enumerate(card_defs):
            row_i = i // cols
            col_i = i % cols

            card = ttk.Frame(grid, style="Card.TFrame", padding=16)
            card.grid(row=row_i, column=col_i, padx=8, pady=8, sticky="nsew")

            ttk.Label(card, text=label, style="CardMuted.TLabel").pack(anchor="w")
            val_lbl = ttk.Label(card, text="—", style="CardBig.TLabel")
            val_lbl.pack(anchor="w", pady=(6, 0))
            self._cards[key] = val_lbl

        for c in range(cols):
            grid.columnconfigure(c, weight=1)

        # Linha de coberturas
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=24, pady=(16, 8))
        cov_frame = ttk.Frame(self)
        cov_frame.pack(fill="x", padx=24, pady=(0, 20))
        ttk.Label(cov_frame, text="Coberturas", style="Header.TLabel").pack(anchor="w", pady=(0, 8))

        cov_grid = ttk.Frame(cov_frame)
        cov_grid.pack(fill="x")

        cov_defs = [
            ("cov_audio",    "Audio features  "),
            ("cov_backfill", "Backfill track_id"),
        ]
        for i, (key, label) in enumerate(cov_defs):
            ttk.Label(cov_grid, text=label, style="Muted.TLabel").grid(
                row=0, column=i*2, sticky="w", padx=(0, 4))
            bar = ttk.Progressbar(cov_grid, orient="horizontal", length=200,
                                  mode="determinate")
            bar.grid(row=0, column=i*2+1, padx=(0, 24))
            lbl = ttk.Label(cov_grid, text="—%", style="Accent.TLabel")
            lbl.grid(row=0, column=i*2+2, padx=(4, 24))
            self._cards[f"{key}_bar"] = bar
            self._cards[f"{key}_lbl"] = lbl

        # Ultima atualizacao
        self._last_updated = ttk.Label(self, text="", style="Muted.TLabel")
        self._last_updated.pack(anchor="e", padx=24, pady=(0, 12))

    def refresh(self):
        if psycopg2 is None:
            return
        try:
            dsn = build_dsn(self.app.config["db"])
            conn = psycopg2.connect(dsn, connect_timeout=3)
            cur = conn.cursor()

            queries = {
                "plays":     "SELECT count(*) FROM listen",
                "tracks":    "SELECT count(*) FROM track",
                "artists":   "SELECT count(*) FROM artist",
                "albums":    "SELECT count(*) FROM album",
                "genres":    "SELECT count(*) FROM genre",
                "playlists": "SELECT count(*) FROM playlist",
                "searches":  "SELECT count(*) FROM search_query",
                "audio_feat":"SELECT count(*) FROM audio_features",
            }

            for key, sql in queries.items():
                cur.execute(sql)
                val = cur.fetchone()[0]
                self._cards[key].configure(text=f"{val:,}".replace(",", "."))

            # Coberturas
            cur.execute("""
                SELECT
                    (SELECT count(*) FROM audio_features) AS af,
                    (SELECT count(*) FROM track) AS tr,
                    (SELECT count(*) FROM listen WHERE track_id IS NOT NULL) AS bf,
                    (SELECT count(*) FROM listen) AS total_l
            """)
            af, tr, bf, total_l = cur.fetchone()

            pct_audio = round(af / tr * 100, 1) if tr else 0
            pct_back  = round(bf / total_l * 100, 1) if total_l else 0

            self._cards["cov_audio_bar"]["value"]    = pct_audio
            self._cards["cov_audio_lbl"].configure(text=f"{pct_audio}%")
            self._cards["cov_backfill_bar"]["value"] = pct_back
            self._cards["cov_backfill_lbl"].configure(text=f"{pct_back}%")

            conn.close()

            from datetime import datetime
            now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            self._last_updated.configure(text=f"Atualizado em {now}")

        except Exception as e:
            self._last_updated.configure(text=f"Erro ao conectar: {e}")


# ============================================================
# JANELA PRINCIPAL
# ============================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(WIN_SIZE)
        self.minsize(800, 560)

        # Config
        self.config_data = load_config()

        # Atalho para app.config (alias limpo)
        self.config = self.config_data

        # Tema
        apply_theme(self)

        # Icone (ignora erro se nao encontrar)
        try:
            self.iconbitmap(PROJECT_ROOT / "icon.ico")
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        # Barra de titulo customizada (linha de status)
        status_bar = ttk.Frame(self, height=28)
        status_bar.pack(side="bottom", fill="x")
        ttk.Label(
            status_bar,
            text=f"  Banco: {self.config['db']['dbname']} @ {self.config['db']['host']}:{self.config['db']['port']}",
            style="Muted.TLabel",
        ).pack(side="left", pady=4)

        # Notebook
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        settings_tab = SettingsTab(nb, self)
        nb.add(settings_tab, text="  ⚙  Configuracoes  ")

        import_tab = ImportTab(nb, self)
        nb.add(import_tab, text="  ▶  Importar  ")

        self.status_tab = StatusTab(nb, self)
        nb.add(self.status_tab, text="  ◉  Status  ")

        # Abre configuracoes se senha vazia
        if not self.config["db"].get("password"):
            nb.select(0)
        else:
            nb.select(1)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    if psycopg2 is None:
        print("ERRO: psycopg2 nao instalado. Rode: py -m pip install psycopg2-binary")
        sys.exit(1)

    app = App()
    app.mainloop()
