from pathlib import Path
from types import SimpleNamespace

import pytest

from src import main as main_module


def test_missing_custom_config_reports_requested_path(monkeypatch, tmp_path):
    config_path = tmp_path / "custom" / "horizon.json"

    class MissingConfigStorage:
        def __init__(self, data_dir, config_path):
            self.config_path = Path(config_path)

        def load_config(self):
            raise FileNotFoundError

    output = []
    monkeypatch.setattr(main_module, "StorageManager", MissingConfigStorage)
    monkeypatch.setattr(main_module, "configure_logging", lambda console, level=None: None)
    monkeypatch.setattr(
        main_module,
        "console",
        SimpleNamespace(
            print=lambda *args, **kwargs: output.append(" ".join(map(str, args)))
        ),
    )
    monkeypatch.setattr("sys.argv", ["horizon", "--config", str(config_path)])

    with pytest.raises(SystemExit) as exc_info:
        main_module.main()

    rendered = "\n".join(output)
    assert exc_info.value.code == 1
    assert str(config_path) in rendered
    # The old `assert "horizon-wizard" not in rendered` here could never fail:
    # the wizard hint is gated on `--config` being absent, and this test passes
    # it. `tests/test_cli_entry_points.py` now checks every hint against
    # pyproject's scripts table, which is where that assertion was reaching.


def test_data_dir_and_config_flags_are_forwarded_to_storage_manager(monkeypatch, tmp_path):
    data_dir = tmp_path / "state"
    config_path = tmp_path / "custom" / "horizon.json"
    storage_calls = []

    class RecordingStorage:
        def __init__(self, data_dir, config_path):
            storage_calls.append({"data_dir": data_dir, "config_path": config_path})

        def load_config(self):
            raise FileNotFoundError

    monkeypatch.setattr(main_module, "StorageManager", RecordingStorage)
    monkeypatch.setattr(main_module, "configure_logging", lambda console, level=None: None)
    monkeypatch.setattr(main_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "sys.argv",
        ["horizon", "--data-dir", str(data_dir), "--config", str(config_path)],
    )

    with pytest.raises(SystemExit):
        main_module.main()

    assert storage_calls == [{"data_dir": str(data_dir), "config_path": str(config_path)}]


def test_data_dir_and_config_default_to_data_directory(monkeypatch):
    storage_calls = []

    class RecordingStorage:
        def __init__(self, data_dir, config_path):
            storage_calls.append({"data_dir": data_dir, "config_path": config_path})

        def load_config(self):
            raise FileNotFoundError

    monkeypatch.setattr(main_module, "StorageManager", RecordingStorage)
    monkeypatch.setattr(main_module, "configure_logging", lambda console, level=None: None)
    monkeypatch.setattr(main_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.argv", ["horizon"])

    with pytest.raises(SystemExit):
        main_module.main()

    assert storage_calls == [{"data_dir": "data", "config_path": None}]


def test_log_level_flag_is_forwarded_to_configure_logging(monkeypatch, tmp_path):
    logging_calls = []

    class RecordingStorage:
        def __init__(self, data_dir, config_path):
            pass

        def load_config(self):
            raise FileNotFoundError

    monkeypatch.setattr(main_module, "StorageManager", RecordingStorage)
    monkeypatch.setattr(
        main_module,
        "configure_logging",
        lambda console, level=None: logging_calls.append(level),
    )
    monkeypatch.setattr(main_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.argv", ["horizon", "--log-level", "debug"])

    with pytest.raises(SystemExit):
        main_module.main()

    # The first call is the pre-argparse default; the second reflects the CLI flag.
    assert logging_calls[-1] == "DEBUG"


API_KEY_ERROR = (
    "Missing API key environment variable configured by ai.api_key_env. "
    "ai.api_key_env should contain the environment variable name, not the key value."
)


def _run_until_the_ai_stage_fails(monkeypatch, tmp_path, *, error=API_KEY_ERROR, corpus_enabled=True):
    """Drive `main()` to the point a keyless run dies and return what it printed.

    Collection itself needs the network, so the orchestrator is replaced by a
    stub that raises the same error the real AI client raises mid-run. Everything
    the handler under test can see - the loaded config, the resolved data dir -
    is still produced by the real code path.
    """
    from src.models import Config

    config = Config.model_validate(
        {
            "ai": {
                "provider": "openai",
                "model": "test",
                "api_key_env": "OPENAI_API_KEY",
            },
            "sources": {},
            "corpus": {"enabled": corpus_enabled},
        }
    )

    class StubStorage:
        def __init__(self, data_dir, config_path):
            # Mirror the real StorageManager surface: the handler under test
            # resolves the corpus path the same way the orchestrator does.
            self.data_dir = Path(data_dir)
            self.config_path = Path(data_dir) / "config.json"

        def load_config(self):
            return config

    class ExplodingOrchestrator:
        def __init__(self, config, storage, console=None):
            self.console = console

        async def run(self, force_hours=None):
            # The real orchestrator reports the failure and then re-raises, which
            # is why a handler here that prints the message again shows it twice.
            self.console.print(f"Error: {error}")
            raise ValueError(error)

    printed = []
    monkeypatch.setattr(main_module, "StorageManager", StubStorage)
    monkeypatch.setattr(main_module, "HorizonOrchestrator", ExplodingOrchestrator)
    monkeypatch.setattr(
        main_module, "configure_logging", lambda console, level=None: None
    )
    monkeypatch.setattr(
        main_module.console,
        "print",
        lambda *args, **kwargs: printed.append(" ".join(map(str, args))),
    )
    monkeypatch.setattr(
        main_module.console, "print_exception", lambda *a, **k: printed.append("<TRACEBACK>")
    )
    monkeypatch.setattr("sys.argv", ["periscope", "--data-dir", str(tmp_path)])

    with pytest.raises((SystemExit, ValueError)) as exc_info:
        main_module.main()
    return "\n".join(printed), exc_info.value


def test_missing_api_key_prints_the_cause_once(monkeypatch, tmp_path):
    out, exc = _run_until_the_ai_stage_fails(monkeypatch, tmp_path)

    assert type(exc) is SystemExit and exc.code == 1
    assert out.count("Missing API key environment variable") == 1


def test_missing_api_key_names_the_files_this_run_used_not_the_defaults(
    monkeypatch, tmp_path
):
    out, _ = _run_until_the_ai_stage_fails(monkeypatch, tmp_path)

    assert str(tmp_path / "corpus.db") in out
    assert str(tmp_path / "config.json") in out
    # Both of these were hardcoded before: they pointed a `--data-dir` user at
    # files that were never written, which is worse than saying nothing.
    assert "data/corpus.db" not in out
    assert "data/config.json" not in out


def test_missing_api_key_does_not_promise_saved_items_when_the_corpus_is_off(
    monkeypatch, tmp_path
):
    out, _ = _run_until_the_ai_stage_fails(monkeypatch, tmp_path, corpus_enabled=False)

    assert "不会丢" not in out
    assert "corpus.db" not in out


def test_unrelated_failure_still_gets_a_traceback(monkeypatch, tmp_path):
    out, exc = _run_until_the_ai_stage_fails(
        monkeypatch, tmp_path, error="boom in the summariser"
    )

    assert type(exc) is SystemExit and exc.code == 1
    assert "<TRACEBACK>" in out
    assert "不会丢" not in out
