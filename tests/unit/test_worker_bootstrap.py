"""O worker precisa inicializar a mesma infraestrutura que a API.

`main()` chamava `init_db` e não `init_redis`. A consequência era silenciosa:
qualquer caminho do worker que usasse `get_redis()` levantava
"Redis not initialized", e o erro era absorvido como degradação. Apareceu no
estado compartilhado do gateway simulado — a API autorizava gravando no Redis
e o worker capturava contra a memória vazia do próprio processo.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.shared.config import Settings


def _settings() -> Settings:
    s = MagicMock(spec=Settings)
    s.database_url = "postgresql+psycopg://a:a@localhost/a"
    s.redis_url = "redis://localhost:6379/0"
    return s


def test_bootstrap_initializes_db_and_redis() -> None:
    from src.worker import main as worker_main

    with (
        patch.object(worker_main, "init_db") as init_db,
        patch.object(worker_main, "init_redis") as init_redis,
    ):
        worker_main.bootstrap_infrastructure(_settings())

    init_db.assert_called_once()
    init_redis.assert_called_once()


def test_worker_and_api_initialize_the_same_clients() -> None:
    """Divergência entre os dois processos deve ser detectada aqui.

    Se um dos dois passar a inicializar um cliente que o outro não inicializa,
    este teste falha — que é o ponto: a divergência anterior passou meses
    invisível.
    """
    import inspect

    from src.api import main as api_main
    from src.worker import main as worker_main

    worker_src = inspect.getsource(worker_main.bootstrap_infrastructure)
    # A API inicializa no lifespan, nao em create_app.
    api_src = inspect.getsource(api_main._lifespan)

    for client in ("init_db", "init_redis"):
        assert client in worker_src, f"worker não inicializa {client}"
        assert client in api_src, f"api não inicializa {client}"
