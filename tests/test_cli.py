import sys
from unittest.mock import Mock

import dotenv
import pytest
import uvicorn

from herbfold import cli


@pytest.fixture
def serve(monkeypatch):
    for name in ("HERBFOLD_HOST", "HERBFOLD_PORT", "HERBFOLD_API_TOKEN", "HERBFOLD_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: None)
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", ["herbfold", "serve"])
    return run


def test_serve_defaults_to_loopback_on_port_9018(serve):
    cli.main()

    serve.assert_called_once_with("herbfold.api:create_app", host="127.0.0.1", port=9018, factory=True)


def test_serve_uses_environment_host_and_integer_port(serve, monkeypatch):
    monkeypatch.setenv("HERBFOLD_HOST", "0.0.0.0")
    monkeypatch.setenv("HERBFOLD_PORT", "9019")
    monkeypatch.setenv("HERBFOLD_API_TOKEN", "test-token")

    cli.main()

    serve.assert_called_once_with("herbfold.api:create_app", host="0.0.0.0", port=9019, factory=True)


def test_serve_loads_dotenv_before_resolving_host_and_port(serve, monkeypatch):
    def load_dotenv():
        monkeypatch.setenv("HERBFOLD_HOST", "0.0.0.0")
        monkeypatch.setenv("HERBFOLD_PORT", "9020")
        monkeypatch.setenv("HERBFOLD_API_TOKEN", "test-token")

    monkeypatch.setattr(dotenv, "load_dotenv", load_dotenv)

    cli.main()

    serve.assert_called_once_with("herbfold.api:create_app", host="0.0.0.0", port=9020, factory=True)


def test_serve_explicit_arguments_override_environment(serve, monkeypatch):
    monkeypatch.setenv("HERBFOLD_HOST", "0.0.0.0")
    monkeypatch.setenv("HERBFOLD_PORT", "9020")
    monkeypatch.setattr(sys, "argv", ["herbfold", "serve", "--host", "localhost", "--port", "9021"])

    cli.main()

    serve.assert_called_once_with("herbfold.api:create_app", host="localhost", port=9021, factory=True)


@pytest.mark.parametrize("from_environment", [True, False])
def test_serve_rejects_external_binding_without_token(serve, monkeypatch, capsys, from_environment):
    if from_environment:
        monkeypatch.setenv("HERBFOLD_HOST", "0.0.0.0")
    else:
        monkeypatch.setattr(sys, "argv", ["herbfold", "serve", "--host", "0.0.0.0"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
    assert "Set HERBFOLD_API_TOKEN" in capsys.readouterr().err
    serve.assert_not_called()
