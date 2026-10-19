"""Verify that the prebuilt tree-sitter grammar wheels are usable offline."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def main() -> int:
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
        import tree_sitter_typescript

        grammars = {
            "javascript": Language(tree_sitter_javascript.language()),
            "typescript": Language(tree_sitter_typescript.language_typescript()),
            "tsx": Language(tree_sitter_typescript.language_tsx()),
        }
        snippets = {
            "javascript": b"const answer = 42;",
            "jsx": b"const view = () => <div />;",
            "typescript": b"type Answer = number;",
            "tsx": b"const view = (): JSX.Element => <div />;",
        }
        print(
            "tree-sitter=" + _package_version("tree-sitter")
            + " tree-sitter-javascript=" + _package_version("tree-sitter-javascript")
            + " tree-sitter-typescript=" + _package_version("tree-sitter-typescript")
        )
        for name, snippet in snippets.items():
            grammar_name = "javascript" if name in {"javascript", "jsx"} else name
            tree = Parser(grammars[grammar_name]).parse(snippet)
            if tree.root_node.has_error:
                raise RuntimeError(f"{name} snippet produced a syntax error")
        print("OK")
        return 0
    except Exception as exception:
        print(f"tree-sitter wheel check failed: {exception}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
