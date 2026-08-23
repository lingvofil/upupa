"""Минимальная граница для JSON-хранилищ и файловая реализация."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol


class JsonRepository(Protocol):
    """Интерфейс JSON-хранилища, не привязанный к конкретному backend."""

    def load(self) -> Any:
        """Прочитать и декодировать сохранённое значение."""

    def save(self, value: Any) -> None:
        """Сохранить значение."""


class JsonFileRepository:
    """JSON-файл с атомарной заменой при записи.

    Временный файл создаётся рядом с целевым, поэтому ``os.replace`` остаётся
    атомарным в пределах одной файловой системы. При неудаче старый файл не
    перезаписывается частично.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        ensure_ascii: bool = False,
        indent: int | None = 4,
    ) -> None:
        self.path = Path(path)
        self.ensure_ascii = ensure_ascii
        self.indent = indent

    def load(self) -> Any:
        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def save(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None

        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                json.dump(
                    value,
                    temp_file,
                    ensure_ascii=self.ensure_ascii,
                    indent=self.indent,
                )
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)

            os.replace(temp_path, self.path)
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise
