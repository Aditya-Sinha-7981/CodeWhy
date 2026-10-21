from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from codewhy.config import Config
from codewhy.extraction.model import FileStructure
from codewhy.cli import inspect_project
from tests.helpers import make_project, run_cli, snapshot_tree


FIXTURE = Path(__file__).parent / "fixtures" / "nested_modules"


def test_inspect_is_hidden_from_help() -> None:
    result = run_cli(["--help"])

    assert result.exit_code == 0
    assert "inspect" not in result.stdout


def test_inspect_is_deterministic_read_only_and_source_free() -> None:
    before = snapshot_tree(FIXTURE)

    first = run_cli(["inspect", str(FIXTURE)])
    second = run_cli(["inspect", str(FIXTURE)])

    assert first.exit_code == 0, first.stderr
    assert second.exit_code == 0, second.stderr
    assert first.stdout == second.stdout
    assert "source_text" not in first.stdout
    assert snapshot_tree(FIXTURE) == before

    payload = json.loads(first.stdout)
    assert payload["entry_point"] == "src/app/cli.py"
    assert payload["edges"]
    assert all(len(edge) == 2 for edge in payload["edges"])


def test_inspect_has_no_provider_or_network_dependency(monkeypatch) -> None:
    def fail_socket(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr("socket.socket", fail_socket)

    result = run_cli(["inspect", str(FIXTURE)])

    assert result.exit_code == 0, result.stderr


def test_inspect_reports_config_error_cleanly(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "", "codewhy.toml": '[project]\nentry_point = "missing.py"\n'})

    result = run_cli(["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "project.entry_point 'missing.py' does not exist in the scanned project" in result.stderr
    assert "Traceback" not in result.stderr


def test_inspect_is_independent_of_file_creation_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    files = {
        "main.py": "import utils\n",
        "utils.py": "",
        "README.md": "readme\n",
    }
    make_project(first, files)
    make_project(second, dict(reversed(list(files.items()))))

    first_result = run_cli(["inspect", str(first)])
    second_result = run_cli(["inspect", str(second)])

    assert first_result.exit_code == second_result.exit_code == 0
    first_payload = json.loads(first_result.stdout)
    second_payload = json.loads(second_result.stdout)
    assert first_payload["root_name"] == "first"
    assert second_payload["root_name"] == "second"
    first_payload.pop("root_name")
    second_payload.pop("root_name")
    assert first_payload == second_payload


def test_inspect_includes_skipped_structure_without_graph_node(monkeypatch, tmp_path: Path) -> None:
    graph = nx.DiGraph()
    project = SimpleNamespace(
        entry_point=None,
        graph=graph,
        modules=[],
        name="project",
        other_files=[],
        skips=[],
        structures={
            "broken.py": FileStructure(
                path="broken.py",
                language="python",
                source_text="",
                line_count=1,
                status="skipped",
                warning="SyntaxError: invalid syntax (line 1)",
            )
        },
    )
    monkeypatch.setattr("codewhy.cli.load_or_fail", lambda root: Config())
    monkeypatch.setattr("codewhy.cli.build_project", lambda root, config: project)

    result = run_cli(["inspect", str(tmp_path)])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    skipped = payload["files"]["broken.py"]
    assert skipped["status"] == "skipped"
    assert skipped["warning"] == "SyntaxError: invalid syntax (line 1)"
    assert skipped["imports_resolved"] == []
    assert skipped["imported_by"] == []
    assert skipped["external_imports"] == []
    assert skipped["unresolved_imports"] == []


def test_inspect_includes_oversized_file_as_skipped(tmp_path: Path) -> None:
    make_project(
        tmp_path,
        {
            "main.py": "x = 1\n",
            "codewhy.toml": "[extraction]\nmax_file_bytes = 1\n",
        },
    )

    result = run_cli(["inspect", str(tmp_path)])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["files"] == {}
    assert payload["skips"] == [
        {
            "detail": "6 bytes exceeds max_file_bytes (1)",
            "path": "main.py",
            "reason": "too_large",
        }
    ]


def test_inspect_writes_utf8_json_with_cp1252_stdout(monkeypatch, tmp_path: Path) -> None:
    graph = nx.DiGraph()
    graph.add_node("main.py", external_imports=[], unresolved_imports=[])
    project = SimpleNamespace(
        entry_point=None,
        graph=graph,
        modules=[],
        name="project",
        other_files=[],
        skips=[],
        structures={
            "main.py": FileStructure(
                path="main.py",
                language="python",
                source_text="",
                line_count=0,
                warning="→",
            )
        },
    )
    monkeypatch.setattr("codewhy.cli.load_or_fail", lambda root: Config())
    monkeypatch.setattr("codewhy.cli.build_project", lambda root, config: project)

    class Cp1252Stdout:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def flush(self) -> None:
            pass

    stdout = Cp1252Stdout()
    monkeypatch.setattr("codewhy.cli.sys.stdout", stdout)

    inspect_project(tmp_path)

    output = stdout.buffer.getvalue()
    assert "→".encode("utf-8") in output
    payload = json.loads(output.decode("utf-8"))
    assert payload["files"]["main.py"]["warning"] == "→"


def test_scan_reports_missing_explicit_entry_point_cleanly(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "", "codewhy.toml": '[project]\nentry_point = "missing.py"\n'})

    result = run_cli(["scan", str(tmp_path)])

    assert result.exit_code == 1
    assert result.stderr == (
        "error: invalid configuration: project.entry_point 'missing.py' does not exist in the scanned project\n"
    )


def test_inspect_survives_malformed_metadata(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "", "pyproject.toml": b"\xff\xfe", "package.json": "[]"})

    result = run_cli(["inspect", str(tmp_path)])
    assert result.exit_code == 0, result.stderr
    assert "Traceback" not in result.stderr
