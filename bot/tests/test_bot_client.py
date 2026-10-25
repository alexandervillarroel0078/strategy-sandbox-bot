"""Cliente de bot sin servidor."""
import pandas as pd
import pytest

from bot.bot_client import ExchangeClient, ExchangeError, run_bot
from bot.broker import Params

from .conftest import Toy

UTC = lambda s: pd.Timestamp(s, tz="UTC")


def test_sin_servidor_el_error_es_claro():
    with pytest.raises(ExchangeError, match="Arranca primero el exchange"):
        run_bot(ExchangeClient("http://127.0.0.1:9", timeout=2), Toy("1h"), Params(), "15m", UTC("2024-01-20"))
