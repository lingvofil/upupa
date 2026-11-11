"""Реестр экспериментов лаборатории.

Новый эксперимент добавляется сюда одной строкой. Ключ попадает в файл флагов
и в журнал событий, поэтому после первого запуска его не переименовывают.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Experiment:
    key: str
    title: str
    aliases: tuple[str, ...] = ()
    # Открыт всем: работает в любом чате и в личке без флага «лаба вкл».
    public: bool = False


EXPERIMENTS: tuple[Experiment, ...] = (
    Experiment("crocodile_lab", "Кракадил: экспериментальные режимы", ("кракадил", "крокодил")),
    Experiment("oracle", "Оракул", ("оракул",)),
    Experiment("guru", "Гуру", ("гуру",), public=True),
    Experiment("brain_chat", "Мозг чата", ("мозг",)),
)


def _normalize(name: str) -> str:
    return name.strip().lower().replace("ё", "е")


_BY_NAME: dict[str, Experiment] = {}
for _experiment in EXPERIMENTS:
    for _name in (_experiment.key, *_experiment.aliases):
        _normalized = _normalize(_name)
        if _normalized in _BY_NAME:
            raise ValueError(f"Имя эксперимента «{_name}» занято дважды")
        _BY_NAME[_normalized] = _experiment


def find_experiment(name: str) -> Experiment | None:
    """Найти эксперимент по ключу или русскому псевдониму."""
    return _BY_NAME.get(_normalize(name))


def get_experiment(key: str) -> Experiment:
    """Эксперимент по точному ключу; неизвестный ключ — ошибка программиста."""
    for experiment in EXPERIMENTS:
        if experiment.key == key:
            return experiment
    raise KeyError(f"Неизвестный эксперимент: {key}")
