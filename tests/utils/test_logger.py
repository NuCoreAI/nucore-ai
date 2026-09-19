"""``configure_logging()``/``reapply_logging_config()`` -- root logging setup,
and its resilience to a third-party import (``udi_interface``'s
``PolyLogger``) stripping root's handlers out from under us. See
``IoXWrapper.__init__``'s ``poly`` branch for the real-world trigger this
guards against: importing ``udi_interface`` in poly/PG3 mode silently wipes
whatever ``configure_logging()`` set up (console + ``--log-file``/
``NUCORE_LOG_FILE``), so ``get_logger(__name__)`` output everywhere stops
reaching it.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from utils import logger as logger_module
from utils.logger import configure_logging, reapply_logging_config


@pytest.fixture(autouse=True)
def _restore_root_logging_state():
    """configure_logging()/reapply_logging_config() mutate the real,
    process-wide root logger plus module-level globals -- save and restore
    both so this file's tests never leak logging state into other tests."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_root_configured = logger_module._ROOT_CONFIGURED
    saved_last_config = logger_module._LAST_CONFIG
    try:
        yield
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        logger_module._ROOT_CONFIGURED = saved_root_configured
        logger_module._LAST_CONFIG = saved_last_config


def _plain_stream_handlers(root):
    # RotatingFileHandler/FileHandler subclass StreamHandler, so exclude
    # them to count just the console (stdout/stderr) handlers.
    return [h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)]


def _file_handlers(root):
    return [h for h in root.handlers if isinstance(h, RotatingFileHandler)]


def test_reapply_restores_handlers_after_external_wipe(tmp_path):
    log_file = tmp_path / "nucore.log"
    configure_logging(level="DEBUG", log_file=str(log_file), console=True, force=True)

    root = logging.getLogger()
    assert len(_file_handlers(root)) == 1
    assert len(_plain_stream_handlers(root)) == 2  # stdout + stderr

    # Simulate what udi_interface's PolyLogger.set_basic_config does on
    # import: strip every root handler and install an unrelated one.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(logging.StreamHandler())
    root.setLevel(logging.WARNING)

    config = reapply_logging_config()

    assert config is not None
    assert config.log_file == str(log_file)
    assert root.level == logging.DEBUG
    assert len(_plain_stream_handlers(root)) == 2
    file_handlers = _file_handlers(root)
    assert len(file_handlers) == 1
    assert Path(file_handlers[0].baseFilename) == log_file.resolve()


def test_reapply_is_noop_before_configure_logging_ever_ran():
    logger_module._ROOT_CONFIGURED = False
    logger_module._LAST_CONFIG = None
    assert reapply_logging_config() is None


def test_get_logger_output_reaches_reapplied_file_handler(tmp_path):
    log_file = tmp_path / "nucore.log"
    configure_logging(level="DEBUG", log_file=str(log_file), console=False, force=True)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    reapply_logging_config()

    logging.getLogger("iox.iox_wrapper_test").debug("hello from the reapplied handler")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "hello from the reapplied handler" in log_file.read_text()
