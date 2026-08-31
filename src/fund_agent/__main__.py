from __future__ import annotations

import uvicorn

from fund_agent.application import create_application
from fund_agent.config.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    application = create_application()
    application.state.fund_agent.start()
    try:
        uvicorn.run(application, host=settings.host, port=settings.port)
    finally:
        application.state.fund_agent.stop()


if __name__ == "__main__":
    main()
