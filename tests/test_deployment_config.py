from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.build_vercel import build, normalize_api_origin


def test_static_build_injects_only_the_configured_api_origins(tmp_path: Path):
    output = tmp_path / "site"
    build(output, "https://quizverse-api.example.test/")

    config = (output / "deployment-config.js").read_text(encoding="utf-8")
    html = (output / "index.html").read_text(encoding="utf-8")
    first_line = config.splitlines()[0]
    assert json.loads(first_line.partition("=")[2].strip().removesuffix(";")) == "https://quizverse-api.example.test"
    assert "window.QuizVerseAPI" in config
    assert "https://quizverse-api.example.test" in html
    assert "wss://quizverse-api.example.test" in html
    assert "https://fonts.googleapis.com" in html
    assert "https://fonts.gstatic.com" in html
    assert (output / "app.js").is_file()
    assert (output / "quiz-library.js").is_file()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://quizverse-api.example.test",
        "https://user:secret@quizverse-api.example.test",
        "https://quizverse-api.example.test/api",
        "https://quizverse-api.example.test?secret=value",
        "https://quizverse-api.example.test#fragment",
    ],
)
def test_api_origin_rejects_unsafe_or_ambiguous_values(value: str):
    with pytest.raises(ValueError):
        normalize_api_origin(value)


def test_static_build_refuses_to_overwrite_existing_output(tmp_path: Path):
    output = tmp_path / "site"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        build(output, "https://quizverse-api.example.test")
    assert marker.read_text(encoding="utf-8") == "existing"


def test_browser_api_helper_routes_http_and_websocket_to_configured_origin():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the frontend URL helper check")
    source = Path(__file__).parents[1] / "quizverse" / "static" / "deployment-config.js"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const window = {
  QUIZVERSE_API_BASE_URL: 'https://api.quizverse.example',
  location: { origin: 'https://quizverse.example' },
  fetch: (url) => url,
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), { window, URL });
if (window.QuizVerseAPI.url('/api/profile') !== 'https://api.quizverse.example/api/profile') process.exit(1);
if (window.QuizVerseAPI.websocket('/ws') !== 'wss://api.quizverse.example/ws') process.exit(2);
if (window.QuizVerseAPI.fetch('/api/health') !== 'https://api.quizverse.example/api/health') process.exit(3);
"""
    subprocess.run([node, "-e", harness, str(source)], check=True, capture_output=True, text=True)


def test_static_build_preserves_quizverse_api_helper(tmp_path: Path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the frontend URL helper check")
    output = tmp_path / "site"
    build(output, "https://quizverse-api.example.test/")

    target_config = output / "deployment-config.js"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const window = {
  location: { origin: 'https://quizverse-live-gamma.vercel.app' },
  fetch: (url, opts) => ({ url, opts }),
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), { window, URL });
if (window.QUIZVERSE_API_BASE_URL !== 'https://quizverse-api.example.test') process.exit(1);
if (!window.QuizVerseAPI) process.exit(2);
if (window.QuizVerseAPI.url('/api/profile') !== 'https://quizverse-api.example.test/api/profile') process.exit(3);
if (window.QuizVerseAPI.websocket('/ws') !== 'wss://quizverse-api.example.test/ws') process.exit(4);
if (window.QuizVerseAPI.websocket('/ws/presence') !== 'wss://quizverse-api.example.test/ws/presence') process.exit(5);
const fetched = window.QuizVerseAPI.fetch('/api/health');
if (fetched.url !== 'https://quizverse-api.example.test/api/health') process.exit(6);
"""
    subprocess.run([node, "-e", harness, str(target_config)], check=True, capture_output=True, text=True)


def test_vercel_and_render_manifests_are_prepared():
    root = Path(__file__).parents[1]
    vercel = json.loads((root / "vercel.json").read_text(encoding="utf-8"))
    assert vercel["framework"] is None
    assert vercel["outputDirectory"] == "dist"
    assert vercel["buildCommand"] == "python scripts/build_vercel.py"
    render = (root / "render.yaml").read_text(encoding="utf-8")
    for required in ("plan: free", "startCommand: python -m quizverse", "key: ALLOWED_ORIGINS", "key: DATABASE_URL"):
        assert required in render
