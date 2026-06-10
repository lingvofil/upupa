"""Объекты aiogram: bot, dp, router."""
from aiogram import Bot, Dispatcher, Router

from core.settings import API_TOKEN

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()
