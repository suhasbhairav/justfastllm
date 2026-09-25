from __future__ import annotations

import os

from justfastllm.config import load_settings


def main() -> None:
    settings = load_settings()
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - exercised only without dependency
        raise SystemExit("Install uvicorn or run: pip install -e .") from exc

    uvicorn.run(
        "justfastllm.app:app",
        host=os.getenv("JUSTFASTLLM_HOST", settings.host),
        port=int(os.getenv("JUSTFASTLLM_PORT", str(settings.port))),
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

