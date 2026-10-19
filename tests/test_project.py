import os
from pathlib import Path

from codewhy.config import resolve_config
from codewhy.project import Module, ProjectModel, build_project
from tests.fixtures.builders import build_ignored_content
from tests.helpers import make_project


def project_of(root: Path) -> ProjectModel:
    return build_project(root, resolve_config(root))


def modules_of(root: Path) -> list[tuple[str, list[str]]]:
    return [(m.path, m.files) for m in project_of(root).modules]


def test_root_with_source_files_is_module_dot(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "", "utils.py": ""})
    assert modules_of(tmp_path) == [(".", ["main.py", "utils.py"])]


def test_folder_with_only_subfolders_is_not_a_module(tmp_path: Path) -> None:
    make_project(tmp_path, {"src/pkg/a.py": "", "src/pkg/b.py": "", "src/README.md": "", "src/other/c.ts": ""})
    assert modules_of(tmp_path) == [("src/other", ["src/other/c.ts"]), ("src/pkg", ["src/pkg/a.py", "src/pkg/b.py"])]


def test_modules_are_non_recursive(tmp_path: Path) -> None:
    make_project(tmp_path, {"a/x.py": "", "a/b/y.py": "", "a/b/c/z.py": ""})
    assert modules_of(tmp_path) == [("a", ["a/x.py"]), ("a/b", ["a/b/y.py"]), ("a/b/c", ["a/b/c/z.py"])]


def test_single_file_folder_is_still_a_module(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "", "lonely/only.js": ""})
    assert modules_of(tmp_path) == [(".", ["main.py"]), ("lonely", ["lonely/only.js"])]


def test_root_without_source_files_has_no_root_module(tmp_path: Path) -> None:
    make_project(tmp_path, {"README.md": "", "pkg/a.py": ""})
    assert modules_of(tmp_path) == [("pkg", ["pkg/a.py"])]


def test_unsupported_and_skipped_files_do_not_create_modules(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "", "docs/guide.md": "", "broken/bad.py": b"\xff"})
    project = project_of(tmp_path)
    assert [m.path for m in project.modules] == ["."]
    assert project.other_files == ["docs/guide.md"]
    assert [s.path for s in project.skips] == ["broken/bad.py"]


def test_root_module_first_then_sorted_by_path(tmp_path: Path) -> None:
    # "-x" sorts before "." as a plain string; the root module must still come first.
    make_project(tmp_path, {"z/a.py": "", "-x/a.py": "", "m.py": "", "a/a.py": ""})
    assert [m.path for m in project_of(tmp_path).modules] == [".", "-x", "a", "z"]


def test_files_mapping_and_modules_cover_every_source(tmp_path: Path) -> None:
    root = build_ignored_content(tmp_path)
    project = project_of(root)
    assert list(project.files) == sorted(project.files)
    assert sorted(p for m in project.modules for p in m.files) == list(project.files)
    assert project.files["main.py"].language == "python"
    assert project.modules == [
        Module(".", ["main.py"]),
        Module("app", ["app/models.py"]),
        Module("build", ["build/release.py"]),
        Module("scripts", ["scripts/deploy.py"]),
        Module("web", ["web/index.js"]),
    ]


def test_name_is_real_folder_name_for_dot(tmp_path: Path) -> None:
    root = make_project(tmp_path / "my-app", {"main.py": ""})
    previous = Path.cwd()
    os.chdir(root)
    try:
        project = build_project(Path("."), resolve_config(Path(".")))
    finally:
        os.chdir(previous)
    assert project.name == "my-app"
    assert project.root == root.resolve()
    assert list(project.files) == ["main.py"]
