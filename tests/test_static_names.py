"""Статическая проверка: в коде нет обращений к неопределённым именам.

Ловит ошибки класса NameError ДО рантайма (например, хелпер остался
в другом модуле при распиле). pyflakes: undefined name — это всегда баг.
"""
import glob
import subprocess
import sys

CHECKED = (
    ["main.py", "config.py"]
    + glob.glob("handlers/*.py")
    + glob.glob("core/*.py")
    + glob.glob("features/*.py")
    + glob.glob("services/*.py")
    + glob.glob("games/*.py")
    + glob.glob("AI/*.py")
)


def test_no_undefined_names():
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", *CHECKED],
        capture_output=True, text=True,
    )
    undefined = [l for l in result.stdout.splitlines() if "undefined name '" in l]
    assert not undefined, "Неопределённые имена:\n" + "\n".join(undefined)
