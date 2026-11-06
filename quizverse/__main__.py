"""Local and single-instance production ASGI runner."""

import os

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    uvicorn.run(
        "quizverse.app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        ws_max_size=4096,
        server_header=False,
        limit_concurrency=200,
    )


if __name__ == "__main__":
    main()
