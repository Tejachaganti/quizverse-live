"""Build the existing vanilla frontend as a static Vercel artifact."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "quizverse" / "static"
OUTPUT = ROOT / "dist"


def normalize_api_origin(value: str) -> str:
    """Require an explicit HTTPS origin so a Vercel build cannot silently use itself."""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("QUIZVERSE_API_BASE_URL must be an HTTPS origin with no credentials, path, query, or fragment.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("QUIZVERSE_API_BASE_URL has an invalid port.") from error
    host = parsed.hostname.lower()
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("QUIZVERSE_API_BASE_URL must use a DNS hostname.")
    return f"https://{host}{f':{port}' if port else ''}"


def build(output: Path, api_url: str) -> None:
    api_origin = normalize_api_origin(api_url)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing build output: {output}")
    shutil.copytree(STATIC, output)
    target_config = output / "deployment-config.js"
    original_code = target_config.read_text(encoding="utf-8")
    config = f"window.QUIZVERSE_API_BASE_URL = {json.dumps(api_origin)};\n\n" + original_code
    target_config.write_text(config, encoding="utf-8")

    websocket_origin = "wss://" + api_origin.removeprefix("https://")
    policy = (
        "default-src 'self'; base-uri 'none'; form-action 'self'; "
        "object-src 'none'; img-src 'self' data:; font-src 'self' data: https://fonts.gstatic.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; script-src 'self'; "
        f"connect-src 'self' {api_origin} {websocket_origin}"
    )
    index = output / "index.html"
    html = index.read_text(encoding="utf-8")
    meta = f'<meta http-equiv="Content-Security-Policy" content="{policy}">\n'
    if "<head>" not in html:
        raise ValueError("Frontend index.html does not contain a head element.")
    index.write_text(html.replace("<head>", "<head>\n    " + meta, 1), encoding="utf-8")


def main() -> None:
    api_url = os.environ.get("QUIZVERSE_API_BASE_URL", "")
    if not api_url:
        raise SystemExit("Set QUIZVERSE_API_BASE_URL in Vercel to the Render HTTPS service origin.")
    build(OUTPUT, api_url)
    print(f"Built static frontend in {OUTPUT.relative_to(ROOT)} for {normalize_api_origin(api_url)}")


if __name__ == "__main__":
    main()
