from __future__ import annotations

from codewhy.cache import keys


def test_canonical_order_and_file_inputs() -> None:
    a = keys.file_key("config", "a.py", "x\n", ["b.py", "c.py"], ["d.py"])
    b = keys.file_key("config", "a.py", "x\n", ["c.py", "b.py"], ["d.py"])
    assert a == b
    assert a != keys.file_key("config", "a.py", "changed\n", ["b.py", "c.py"], ["d.py"])
    assert a != keys.file_key("config", "a.py", "x\n", ["b.py"], ["d.py"])
    assert a != keys.file_key("config", "a.py", "x\n", ["b.py", "c.py"], ["e.py"])


def test_module_and_project_inputs_are_content_addressed() -> None:
    module = keys.module_key("config", "pkg", [["a.py", "Purpose"]], "a.py -> b.py", ["requests"])
    assert module == keys.module_key("config", "pkg", [["a.py", "Purpose"]], "a.py -> b.py", ["requests"])
    assert module != keys.module_key("config", "pkg", [["a.py", "Other"]], "a.py -> b.py", ["requests"])
    assert module != keys.module_key("config", "pkg", [["a.py", "Purpose"]], "a.py -> c.py", ["requests"])
    assert module != keys.module_key("config", "pkg", [["a.py", "Purpose"]], "a.py -> b.py", ["httpx"])
    assert module != keys.module_key("config", "pkg", [["a.py", keys.FAILED_MEMBER]], "a.py -> b.py", ["requests"])

    project = keys.project_key("config", "main.py", [["pkg", "Responsibility"]], "a.py -> b.py", ["README.md"])
    assert project != keys.project_key("config", None, [["pkg", "Responsibility"]], "a.py -> b.py", ["README.md"])
    assert project != keys.project_key("config", "main.py", [["pkg", "Other"]], "a.py -> b.py", ["README.md"])
    assert project != keys.project_key("config", "main.py", [["pkg", "Responsibility"]], "a.py -> c.py", ["README.md"])
    assert project != keys.project_key("config", "main.py", [["pkg", "Responsibility"]], "a.py -> b.py", ["CHANGELOG.md"])


def test_config_fingerprint_contains_only_required_fields() -> None:
    base = {
        "provider": "ollama",
        "model": "model",
        "context_tokens": 100,
        "max_output_tokens": 10,
        "temperature": 0.0,
        "seed": 0,
        "reasoning_effort": None,
        "structured_output": "schema",
        "output.mode": "single-file",
    }
    original = keys.config_fingerprint(base)
    assert original == keys.config_fingerprint(dict(reversed(list(base.items()))))
    unchanged = dict(base)
    unchanged["output.mode"] = "docs-tree"
    assert keys.config_fingerprint(unchanged) == original
    for field in ("provider", "model", "context_tokens", "max_output_tokens", "temperature", "seed", "reasoning_effort", "structured_output"):
        changed = dict(base)
        changed[field] = "changed" if isinstance(base[field], str | type(None)) else base[field] + 1
        assert keys.config_fingerprint(changed) != original


def test_pipeline_version_is_part_of_every_key(monkeypatch) -> None:
    values = (
        keys.file_key("c", "a.py", "x", [], []),
        keys.module_key("c", "", [], "", []),
        keys.project_key("c", None, [], "", []),
    )
    monkeypatch.setattr(keys, "PIPELINE_VERSION", keys.PIPELINE_VERSION + 1)
    changed = (
        keys.file_key("c", "a.py", "x", [], []),
        keys.module_key("c", "", [], "", []),
        keys.project_key("c", None, [], "", []),
    )
    assert values != changed
