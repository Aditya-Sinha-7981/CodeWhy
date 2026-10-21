from __future__ import annotations

import pytest

from codewhy.config import Config
from codewhy.extraction.python_ast import extract_python
from codewhy.extraction.ts_treesitter import extract_typescript
from codewhy.llm.provider import TransientError
from codewhy.summarize.file_pass import EMPTY_FILE_PURPOSE, is_effectively_empty, run_file_pass
from codewhy.summarize.schemas import FilePass
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider
from tests.test_chunking import oversized_case


@pytest.fixture
def oversized(tmp_path):
    fs, config, _chunk_budget = oversized_case(tmp_path)
    return fs, config


def run(fs, fake: FakeProvider, config: Config | None = None, imports=(), imported_by=()):
    return run_file_pass(
        fs, list(imports), list(imported_by), provider=fake, config=config or Config(), clock=lambda: FIXED_TIME
    )


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("pkg/__init__.py", ""),
        ("pkg/__init__.py", "\n\n   \n"),
        ("pkg/__init__.py", "# just a comment\n\n    # another\n"),
        ("pkg/__init__.py", "#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n"),
        ("web/index.js", "// nothing yet\n"),
        ("web/index.ts", "/* license\n * text\n */\n\n// todo\n"),
        ("web/index.ts", "/* a */ /* b */ // c\n"),
    ],
)
def test_empty_and_comment_only_files_need_no_call(path: str, text: str) -> None:
    fs = extract_python(path, text) if path.endswith(".py") else extract_typescript(path, text)
    fake = FakeProvider()
    result = run(fs, fake)
    assert fake.calls == []
    assert result.failure is None
    assert result.data == FilePass(purpose=EMPTY_FILE_PURPOSE)
    assert result.generated_at == FIXED_TIME
    assert result.metadata == {}


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ('"""Only a docstring."""\n', "python"),
        ("VALUE = 1  # comment after code\n", "python"),
        ("/* a */ start();\n", "javascript"),
        ("const s = '/*';\n// x\n", "javascript"),
        ("/* open\n still comment */ code();\n", "typescript"),
    ],
)
def test_files_with_code_are_not_empty(text: str, language: str) -> None:
    assert not is_effectively_empty(text, language)


def test_a_400_line_file_that_fits_gets_its_full_source_in_one_call() -> None:
    lines = [f"VALUE_{n} = {n}  # setting {n}" for n in range(400)]
    source = "\n".join(lines) + "\n"
    fs = extract_python("settings.py", source)
    fake = FakeProvider()
    result = run(fs, fake, imported_by=["main.py"])
    assert result.failure is None
    assert len(fake.calls) == 1
    prompt = fake.calls[0].prompt
    assert "## Full source" in prompt
    assert source in prompt
    assert "main.py" in prompt
    assert fake.calls[0].max_output_tokens == Config().llm.effective_max_output_tokens
    assert fake.calls[0].temperature is None


def test_oversized_file_never_sends_a_full_source_prompt(oversized) -> None:
    fs, config = oversized
    fake = FakeProvider()
    result = run(fs, fake, config, imported_by=["main.py"])
    assert result.failure is None
    assert len(fake.calls) > 2
    assert not any("## Full source" in call.prompt for call in fake.calls)


def test_a_failed_chunk_fails_the_whole_file_without_combining(oversized) -> None:
    fs, config = oversized
    fake = FakeProvider(errors={1: TransientError("endpoint down")})
    result = run(fs, fake, config, imported_by=["main.py"])
    assert result.data is None
    assert result.failure is not None and result.failure.startswith("chunk 2 failed: provider unavailable")
    assert len(fake.calls) == 2
    assert not any("Part summaries" in call.prompt for call in fake.calls)
