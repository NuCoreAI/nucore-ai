from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    json_output: bool = False
    log_file: str | None = None
    console: bool = True
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_TEXT_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_ROOT_CONFIGURED = False


def _to_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_level(level: str | int | None) -> int:
    if level is None:
        level = os.getenv("NUCORE_LOG_LEVEL", "INFO")
    if isinstance(level, int):
        return level
    normalized = str(level).strip().upper()
    return logging._nameToLevel.get(normalized, logging.INFO)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": record.process,
            "thread": record.threadName,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in {
                "args",
                "asctime",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }
            and not key.startswith("_")
        }
        if extras:
            payload["context"] = extras

        return json.dumps(payload, default=str)


class ContextLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        extra = kwargs.get("extra", {})
        merged = {**self.extra, **extra}
        kwargs["extra"] = merged
        return msg, kwargs


def _build_formatter(json_output: bool) -> logging.Formatter:
    if json_output:
        return JsonFormatter()
    return logging.Formatter(_DEFAULT_TEXT_FORMAT, datefmt=_DEFAULT_DATE_FORMAT)


def _build_config(
    *,
    level: str | int | None,
    json_output: bool | None,
    log_file: str | None,
    console: bool | None,
    max_bytes: int,
    backup_count: int,
) -> LoggingConfig:
    return LoggingConfig(
        level=logging.getLevelName(_normalize_level(level)),
        json_output=_to_bool(json_output if json_output is not None else os.getenv("NUCORE_LOG_JSON"), default=False),
        log_file=log_file if log_file is not None else os.getenv("NUCORE_LOG_FILE"),
        console=_to_bool(console if console is not None else os.getenv("NUCORE_LOG_CONSOLE"), default=True),
        max_bytes=max_bytes,
        backup_count=backup_count,
    )


def configure_logging(
    *,
    level: str | int | None = None,
    json_output: bool | None = None,
    log_file: str | None = None,
    console: bool | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    force: bool = False,
) -> LoggingConfig:
    """Configure root logging once for the whole process.

    Environment variable fallbacks:
    - NUCORE_LOG_LEVEL
    - NUCORE_LOG_JSON
    - NUCORE_LOG_FILE
    - NUCORE_LOG_CONSOLE
    """

    global _ROOT_CONFIGURED

    if _ROOT_CONFIGURED and not force:
        return _build_config(
            level=level,
            json_output=json_output,
            log_file=log_file,
            console=console,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )

    config = _build_config(
        level=level,
        json_output=json_output,
        log_file=log_file,
        console=console,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )

    root = logging.getLogger()
    root.setLevel(_normalize_level(config.level))

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = _build_formatter(config.json_output)

    if config.console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    if config.log_file:
        path = Path(config.log_file).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=path,
            maxBytes=config.max_bytes,
            backupCount=config.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    _ROOT_CONFIGURED = True
    return config


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(name or "nucore")


def bind_logger(logger: logging.Logger, **context: Any) -> ContextLoggerAdapter:
    return ContextLoggerAdapter(logger, context)
