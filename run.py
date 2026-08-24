"""
Точка запуска.
"""
import asyncio
import logging
import sys

log = logging.getLogger(__name__)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    log.info("Запуск через run.py → bot.py…")
    from bot import main as bot_main
    await bot_main()


if __name__ == "__main__":
    asyncio.run(main())
