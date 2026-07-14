"""
Tunel SSH para conectar a um PostgreSQL externo que so aceita conexao via SSH.

Abre um SSHTunnelForwarder encaminhando uma porta local (127.0.0.1, escolhida
automaticamente) ate o Postgres como visto pela maquina SSH remota (tipicamente
localhost:PGPORT dentro da VM). O app entao conecta o psycopg2 nessa porta local.

Espelha o modelo do DataGrip/pgAdmin: a aba SSH aponta para IP_publico:22 + chave,
e o Postgres e alcancado por localhost:PGPORT do outro lado do tunel.
"""

from sshtunnel import SSHTunnelForwarder


class SSHTunnel:
    def __init__(self, ssh_host, ssh_port, ssh_user, key_path,
                 remote_host, remote_port, log=print):
        self.ssh_host = ssh_host
        self.ssh_port = int(ssh_port or 22)
        self.ssh_user = ssh_user
        self.key_path = key_path
        self.remote_host = remote_host or "localhost"
        self.remote_port = int(remote_port)
        self.log = log
        self._server = None

    @property
    def is_active(self) -> bool:
        return self._server is not None and self._server.is_active

    @property
    def local_port(self):
        return self._server.local_bind_port if self._server else None

    def start(self):
        """Abre o tunel (reutiliza se ja estiver ativo). Retorna a porta local."""
        if self.is_active:
            return self.local_port

        self._server = SSHTunnelForwarder(
            (self.ssh_host, self.ssh_port),
            ssh_username=self.ssh_user,
            ssh_pkey=self.key_path,
            remote_bind_address=(self.remote_host, self.remote_port),
            # porta local escolhida automaticamente (evita colisao com outros tuneis)
        )
        self._server.start()
        self.log(
            f"  tunel SSH aberto: 127.0.0.1:{self.local_port} -> "
            f"{self.remote_host}:{self.remote_port} (via {self.ssh_user}@{self.ssh_host})"
        )
        return self.local_port

    def stop(self):
        if self._server is not None:
            try:
                self._server.stop()
            finally:
                self._server = None
