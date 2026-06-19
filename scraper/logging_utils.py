from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(log_dir: str | Path = "logs") -> None:
    root = logging.getLogger()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    root.setLevel(logging.INFO)

    has_console = any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, RotatingFileHandler)
        for handler in root.handlers
    )
    if not has_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    try:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / "escape_room_monitor.log"
        has_file = any(
            isinstance(handler, RotatingFileHandler)
            and Path(getattr(handler, "baseFilename", "")) == log_path
            for handler in root.handlers
        )
        if not has_file:
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
    except OSError:
        root.warning("File logging is unavailable; continuing with console logs.")
