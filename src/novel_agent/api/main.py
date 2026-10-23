from __future__ import annotations

from pathlib import Path

import uvicorn
from dotenv import load_dotenv

# 保证从任意 CWD 启动也能读到包旁 .env
_pkg_root = Path(__file__).resolve().parents[3]
load_dotenv(_pkg_root / ".env")
load_dotenv()


def run() -> None:
    from novel_agent.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    uvicorn.run(
        "novel_agent.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
