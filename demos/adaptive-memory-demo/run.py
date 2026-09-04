from __future__ import annotations

import uvicorn

from memory_demo.config import Settings

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run(
        "memory_demo.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
