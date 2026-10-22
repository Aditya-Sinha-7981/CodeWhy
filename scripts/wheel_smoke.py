"""Install-and-scan check for a built wheel against the stub Ollama server (CI `wheel` job; also runs locally).

    python scripts/wheel_smoke.py --codewhy <path to the codewhy executable from a clean venv>

Starts tests/support/stub_ollama.py on a free port, copies tests/fixtures/flat_scripts
to a temporary folder, points codewhy at the stub, and checks:

1. `codewhy --version` prints the package version;
2. `codewhy scan <copy> --yes` exits 0 and writes PROJECT_DOCS.md with codewhy frontmatter;
3. a second scan exits 0 and the stub served zero further /api/chat calls (the cache
   works from the installed wheel).

Run it from outside the source tree's import path so the installed wheel, not
`src/`, is what runs. Exits non-zero with a message on the first failed check.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STUB = REPO / "tests" / "support" / "stub_ollama.py"
FIXTURE = REPO / "tests" / "fixtures" / "flat_scripts"
MODEL = "qwen2.5-coder:14b"


def check(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAILED: {message}", file=sys.stderr)
        sys.exit(1)
    print(f"ok: {message}")


def chat_calls(port: int) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stub/stats", timeout=10) as response:
        return int(json.load(response)["chat_calls"])


def run(codewhy: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([codewhy, *args], capture_output=True, text=True, encoding="utf-8", timeout=300)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test an installed codewhy against the stub server.")
    parser.add_argument("--codewhy", required=True, help="the codewhy executable to test")
    parser.add_argument("--version", dest="expected_version", help="expected `codewhy --version` output")
    args = parser.parse_args()

    stub = subprocess.Popen([sys.executable, str(STUB), "--port", "0", "--model", MODEL], stdout=subprocess.PIPE, text=True)
    try:
        line = stub.stdout.readline().strip() if stub.stdout else ""
        check(line.startswith("listening on "), f"stub server started ({line!r})")
        port = int(line.rsplit(" ", 1)[1])

        version = run(args.codewhy, "--version")
        check(version.returncode == 0, f"codewhy --version exits 0 ({version.stdout.strip()!r})")
        if args.expected_version:
            check(version.stdout.strip() == f"codewhy {args.expected_version}", "version matches")

        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "flat scripts"  # a space in the path, on purpose
            shutil.copytree(FIXTURE, project)
            (project / "codewhy.toml").write_text(f'[llm]\nhost = "http://127.0.0.1:{port}"\n', encoding="utf-8")

            first = run(args.codewhy, "scan", str(project), "--yes")
            check(first.returncode == 0, f"first scan exits 0 (stderr: {first.stderr.strip()!r})")
            docs = project / "PROJECT_DOCS.md"
            check(docs.is_file(), "PROJECT_DOCS.md written")
            check(docs.read_text(encoding="utf-8").startswith("---\ngenerated_by: codewhy v"), "codewhy frontmatter")
            served = chat_calls(port)
            check(served > 0, f"first scan called the model ({served} /api/chat calls)")
            before = docs.read_bytes()

            second = run(args.codewhy, "scan", str(project), "--yes")
            check(second.returncode == 0, f"second scan exits 0 (stderr: {second.stderr.strip()!r})")
            check(chat_calls(port) == served, "second scan made zero /api/chat calls (served from the cache)")
            check(docs.read_bytes() == before, "second scan output is byte-identical")
    finally:
        stub.terminate()
        stub.wait(timeout=10)
    print("wheel smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
