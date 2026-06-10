# === config.py — ФАСАД ОБРАТНОЙ СОВМЕСТИМОСТИ ===
#
# Реальный код живёт в core/:
#   core/settings.py       — ключи и константы
#   core/logging_setup.py  — логирование
#   core/state.py          — рантайм-состояние
#   core/ai_clients.py     — AI-клиенты
#   core/loader.py         — bot / dp / router
#
# Старые импорты `from config import X` продолжают работать.
# В новом коде импортируй напрямую из core.* — этот фасад со временем уйдёт.

from core.logging_setup import *  # noqa: F401,F403  (логирование — первым)
from core.settings import *       # noqa: F401,F403
from core.state import *          # noqa: F401,F403
from core.ai_clients import *     # noqa: F401,F403
from core.loader import *         # noqa: F401,F403

from core.logging_setup import logger  # noqa: F401  (явно: * не тащит приватные)
