"""JavaScript and TypeScript extraction using prebuilt tree-sitter grammars."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .model import ClassInfo, FileStructure, FunctionInfo, ImportInfo, TypeInfo, UnitInfo


_GRAMMAR_EXTENSIONS = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
}

# `class` is the node for `export default class { ... }` (no name).
_CLASS_TYPES = {"class_declaration", "abstract_class_declaration", "class"}


def _language_for(extension: str) -> Any:
    from tree_sitter import Language

    import tree_sitter_javascript
    import tree_sitter_typescript

    if extension == ".tsx":
        capsule = tree_sitter_typescript.language_tsx()
    elif _GRAMMAR_EXTENSIONS.get(extension) == "typescript":
        capsule = tree_sitter_typescript.language_typescript()
    else:
        capsule = tree_sitter_javascript.language()
    return Language(capsule)


def _text(source: bytes, node: Any) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _line(node: Any) -> int:
    return node.start_point[0] + 1


def _end_line(node: Any) -> int:
    return node.end_point[0] + 1


def _named_children(node: Any) -> list[Any]:
    return list(node.named_children)


def _field(node: Any, name: str) -> Any | None:
    return node.child_by_field_name(name)


def _identifier(node: Any, source: bytes, default: str = "default") -> str:
    name = _field(node, "name")
    if name is not None:
        return _text(source, name)
    for child in _named_children(node):
        if child.type in {"identifier", "property_identifier", "type_identifier"}:
            return _text(source, child)
    return default


def _body_node(node: Any) -> Any | None:
    body = _field(node, "body")
    if body is not None:
        return body
    for descendant in _walk(node):
        if descendant.type in {"statement_block", "class_body", "interface_body", "enum_body"}:
            return descendant
    return None


def _walk(node: Any) -> Iterable[Any]:
    yield node
    for child in _named_children(node):
        yield from _walk(child)


def _declaration(node: Any) -> Any | None:
    if node.type != "export_statement":
        return node
    for child in _named_children(node):
        if child.type in {
            "function_declaration",
            "generator_function_declaration",
            *_CLASS_TYPES,
            "lexical_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
            "arrow_function",
            "function_expression",
        }:
            return child
    return None


def _jsdoc(source: bytes, node: Any) -> str | None:
    prefix = source[: node.start_byte].decode("utf-8")
    current_line_start = prefix.rfind("\n") + 1
    if prefix[current_line_start:].strip():
        return None
    previous_text = prefix[:current_line_start].rstrip("\n")
    lines = previous_text.splitlines()
    if not lines:
        return None
    index = len(lines) - 1
    if not lines[index].strip().endswith("*/"):
        return None
    comment: list[str] = []
    while index >= 0:
        comment.insert(0, lines[index].strip())
        if lines[index].strip().startswith("/**"):
            value = " ".join(comment)
            value = re.sub(r"^/\*\*\s*", "", value)
            value = re.sub(r"\s*\*/$", "", value)
            value = re.sub(r"\s*\*\s?", " ", value)
            return re.sub(r"\s+", " ", value).strip() or None
        index -= 1
    return None


def _is_async(node: Any) -> bool:
    """True when the function node itself carries the `async` keyword token."""
    return any(child.type == "async" for child in node.children)


def _signature(source: bytes, node: Any, target: Any | None = None) -> str:
    target = target or node
    body = _body_node(target)
    start = node.start_byte
    variable_prefix = ""
    is_first_variable = True
    if target.type == "variable_declarator":
        value = _field(target, "value")
        body = _field(value, "body") if value is not None else body
        container = target.parent
        if container is not None and container.type == "lexical_declaration":
            first_variable = next(
                (item for item in _named_children(container) if item.type == "variable_declarator"),
                target,
            )
            prefix_start = node.start_byte
            prefix_end = first_variable.start_byte
            variable_prefix = source[prefix_start:prefix_end].decode("utf-8")
            is_first_variable = target.start_byte == first_variable.start_byte
            if is_first_variable:
                start = node.start_byte
    elif target.type == "lexical_declaration":
        variable = next((item for item in _walk(target) if item.type == "variable_declarator"), None)
        value = _field(variable, "value") if variable is not None else None
        body = _field(value, "body") if value is not None else body
    end = body.start_byte if body is not None else node.end_byte
    if target.type == "variable_declarator":
        if not is_first_variable:
            value = variable_prefix + source[target.start_byte:end].decode("utf-8")
        else:
            value = source[start:end].decode("utf-8")
    else:
        value = source[node.start_byte:end].decode("utf-8")
    return re.sub(r"\s+", " ", value).strip()


def _function_info(source: bytes, node: Any, name: str | None = None, target: Any | None = None) -> FunctionInfo:
    function_name = name or _identifier(node, source)
    function_node = target if target is not None else node
    if function_node.type == "variable_declarator":
        function_node = _field(function_node, "value") or function_node
    return FunctionInfo(
        name=function_name,
        signature=_signature(source, node, target),
        docstring=_jsdoc(source, node),
        start_line=_line(node),
        end_line=_end_line(node),
        is_async=_is_async(function_node),
    )


def _class_info(source: bytes, node: Any) -> ClassInfo:
    body = _field(node, "body")
    methods: list[FunctionInfo] = []
    if body is not None:
        for child in _named_children(body):
            if child.type == "method_definition":
                method_name = _field(child, "name")
                methods.append(
                    FunctionInfo(
                        name=_text(source, method_name) if method_name is not None else "default",
                        signature=_signature(source, child),
                        docstring=_jsdoc(source, child),
                        start_line=_line(child),
                        end_line=_end_line(child),
                        is_async=_is_async(child),
                    )
                )
    bases: list[str] = []
    heritage = _field(node, "superclass")
    if heritage is not None:
        bases.append(_text(source, heritage))
    return ClassInfo(
        name=_identifier(node, source),
        bases=tuple(bases),
        docstring=_jsdoc(source, node),
        methods=tuple(methods),
        start_line=_line(node),
        end_line=_end_line(node),
    )


def _type_info(source: bytes, node: Any) -> TypeInfo | None:
    kind_map = {
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
    }
    kind = kind_map.get(node.type)
    if kind is None:
        return None
    return TypeInfo(_identifier(node, source), kind, _line(node), _end_line(node))


def _binding_names(node: Any, source: bytes, node_types: set[str]) -> tuple[str, ...]:
    names: list[str] = []

    def collect(current: Any) -> None:
        for child in _named_children(current):
            if child.type in node_types:
                names.append(_text(source, child))
            else:
                collect(child)

    collect(node)
    return tuple(names)


def _literal_value(source: bytes, node: Any) -> str | None:
    value = _text(source, node)
    if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
        return value[1:-1]
    return None


def _imports(source: bytes, root: Any) -> tuple[ImportInfo, ...]:
    found: list[ImportInfo] = []
    for node in _walk(root):
        if node.type == "import_statement":
            module_node = _field(node, "source")
            if module_node is None:
                continue
            module = _literal_value(source, module_node)
            if module is None:
                continue
            clause = next((child for child in _named_children(node) if child.type == "import_clause"), None)
            names = () if clause is None else _binding_names(clause, source, {"identifier", "namespace_import", "import_specifier"})
            found.append(
                # `import type` has an anonymous `type` token; `import typeorm` does not.
                ImportInfo("js", module, names, 0, _line(node), any(c.type == "type" and not c.is_named for c in node.children))
            )
        elif node.type == "export_statement":
            module_node = _field(node, "source")
            if module_node is not None:
                module = _literal_value(source, module_node)
                if module is not None:
                    clause = next((child for child in _named_children(node) if child.type == "export_clause"), None)
                    names = () if clause is None else _binding_names(clause, source, {"export_specifier"})
                    found.append(ImportInfo("js", module, names, 0, _line(node), False))
        elif node.type == "call_expression":
            function_node = _field(node, "function")
            arguments = _field(node, "arguments")
            if function_node is None or arguments is None or function_node.type not in {"identifier", "import"}:
                continue
            function_name = _text(source, function_node)
            if function_name not in {"require", "import"}:
                continue
            argument_nodes = _named_children(arguments)
            if len(argument_nodes) != 1:
                continue
            module = _literal_value(source, argument_nodes[0])
            if module is not None:
                found.append(ImportInfo("js", module, (), 0, _line(node), False))
    unique = {(item.kind, item.module, item.names, item.level, item.line): item for item in found}
    return tuple(unique[key] for key in sorted(unique, key=lambda item: (item[4], item[1], item[2])))


def _definitions(source: bytes, root: Any) -> list[tuple[Any, Any, str, str]]:
    result: list[tuple[Any, Any, str, str]] = []
    for node in _named_children(root):
        declaration = _declaration(node)
        if declaration is None:
            continue
        if declaration.type in {"function_declaration", "generator_function_declaration"}:
            result.append((node, declaration, _identifier(declaration, source, "default"), "function"))
        elif declaration.type in _CLASS_TYPES:
            result.append((node, declaration, _identifier(declaration, source), "class"))
        elif declaration.type in {"arrow_function", "function_expression"}:
            result.append((node, declaration, "default", "function"))
        elif declaration.type == "lexical_declaration":
            for child in _named_children(declaration):
                if child.type == "variable_declarator":
                    value = _field(child, "value")
                    if value is not None and value.type in {"arrow_function", "function_expression"}:
                        name_node = _field(child, "name")
                        result.append((node, child, _text(source, name_node) if name_node is not None else "default", "function"))
        elif declaration.type in {"interface_declaration", "type_alias_declaration", "enum_declaration"}:
            result.append((node, declaration, _identifier(declaration, source), "type"))
    return result


def _units(source: bytes, root: Any, line_count: int, path: str) -> tuple[UnitInfo, ...]:
    definitions = _definitions(source, root)
    if not definitions:
        module_name = path.rsplit("/", 1)[-1]
        return (UnitInfo(module_name, "module", 1, line_count),) if line_count else ()
    units: list[UnitInfo] = []
    previous_end = 0
    seen_statements: set[tuple[int, int]] = set()
    for node, target, name, kind in definitions:
        statement_key = (node.start_byte, node.end_byte)
        if statement_key in seen_statements:
            continue
        seen_statements.add(statement_key)
        declaration = _declaration(node)
        children: tuple[UnitInfo, ...] = ()
        if declaration is not None and declaration.type in _CLASS_TYPES:
            body = _field(declaration, "body")
            methods = [] if body is None else [child for child in _named_children(body) if child.type == "method_definition"]
            child_units: list[UnitInfo] = []
            child_end = _line(declaration)
            for method in methods:
                child_units.append(UnitInfo(_identifier(method, source), "function", child_end + 1, _end_line(method)))
                child_end = _end_line(method)
            if child_units:
                child_units[-1] = UnitInfo(child_units[-1].name, "function", child_units[-1].start_line, _end_line(declaration))
            children = tuple(child_units)
        units.append(UnitInfo(name, kind, previous_end + 1, _end_line(node), children))  # type: ignore[arg-type]
        previous_end = _end_line(node)
    units[-1] = UnitInfo(units[-1].name, units[-1].kind, units[-1].start_line, line_count, units[-1].children)
    return tuple(units)


def extract_typescript(path: str, source: str) -> FileStructure:
    """Extract JavaScript or TypeScript facts without executing source code."""
    line_count = len(source.splitlines())
    extension = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    language_name = "typescript" if _GRAMMAR_EXTENSIONS.get(extension) in {"typescript", "tsx"} else "javascript"
    try:
        language = _language_for(extension)
    except Exception as exception:
        return FileStructure(path, language_name, source, line_count, status="skipped", warning=f"tree-sitter grammar unavailable: {exception}")
    try:
        from tree_sitter import Parser

        parser = Parser(language)
        source_bytes = source.encode("utf-8")
        tree = parser.parse(source_bytes)
    except Exception as exception:
        return FileStructure(path, language_name, source, line_count, status="skipped", warning=f"internal extractor error: {exception.__class__.__name__}: {exception}")

    error_node = next((node for node in _walk(tree.root_node) if node.type == "ERROR" or node.is_missing), None)
    if tree.root_node.has_error or error_node is not None:
        line = _line(error_node) if error_node is not None else 1
        return FileStructure(path, language_name, source, line_count, status="skipped", warning=f"syntax errors (first at line {line})")

    source_bytes = source.encode("utf-8")
    functions: list[FunctionInfo] = []
    classes: list[ClassInfo] = []
    types: list[TypeInfo] = []
    for wrapper, target, name, kind in _definitions(source_bytes, tree.root_node):
        declaration = _declaration(wrapper)
        if declaration is None:
            continue
        if kind == "function":
            if declaration.type == "lexical_declaration":
                variable = target
                value = _field(variable, "value")
                if value is not None:
                    info = _function_info(source_bytes, wrapper, name, variable)
                    functions.append(info)
            else:
                functions.append(_function_info(source_bytes, wrapper, name, target))
        elif kind == "class":
            classes.append(_class_info(source_bytes, declaration))
        else:
            info = _type_info(source_bytes, declaration)
            if info is not None:
                types.append(info)
    return FileStructure(
        path=path,
        language=language_name,
        source_text=source,
        line_count=line_count,
        functions=tuple(functions),
        classes=tuple(classes),
        types=tuple(types),
        imports=_imports(source_bytes, tree.root_node),
        top_level_units=_units(source_bytes, tree.root_node, line_count, path),
    )
