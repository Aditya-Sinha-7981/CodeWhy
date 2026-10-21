import tomllib
from pathlib import Path

import pytest

from codewhy.config import (
    Config,
    ConfigError,
    load_config,
    render_toml,
    resolve_config,
)
from tests.helpers import make_project


def write_toml(tmp_path: Path, text: str) -> Path:
    return make_project(tmp_path, {"codewhy.toml": text})


def problems_of(tmp_path: Path, text: str, overrides: dict[str, object] | None = None) -> list[str]:
    write_toml(tmp_path, text)
    with pytest.raises(ConfigError) as info:
        resolve_config(tmp_path, overrides)
    return info.value.problems


# --- defaults -------------------------------------------------------------


def test_defaults_match_architecture(tmp_path: Path) -> None:
    config = resolve_config(tmp_path)
    assert config.source_path is None
    llm = config.llm
    assert llm.provider == "ollama"
    assert llm.effective_model == "qwen2.5-coder:14b"
    assert llm.host == "http://localhost:11434"
    assert llm.effective_context_tokens == 16384
    assert llm.effective_max_output_tokens == 1024
    assert llm.temperature == 0
    assert llm.seed == 0
    assert llm.timeout_seconds == 300
    assert llm.max_transient_retries == 3
    assert llm.acknowledge_remote is False
    for unset in (
        "model",
        "base_url",
        "api_key_env",
        "context_tokens",
        "max_output_tokens",
        "structured_output",
        "send_seed",
        "reasoning_effort",
    ):
        assert getattr(llm, unset) is None, unset
    assert config.output.mode == "single-file"
    assert config.output.dir == "codewhy-docs"
    assert config.output.single_file_name == "PROJECT_DOCS.md"
    assert config.output.token_budget == 20000
    assert config.extraction.languages == ["python", "javascript", "typescript"]
    assert config.extraction.max_file_bytes == 500000
    assert config.project.entry_point is None


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("ollama", 16384), ("openai-compat", 32768), ("gemini", 32768)],
)
def test_effective_context_tokens_per_provider(tmp_path: Path, provider: str, expected: int) -> None:
    config = resolve_config(tmp_path, {"llm.provider": provider})
    assert config.llm.effective_context_tokens == expected


def test_explicit_context_tokens_wins(tmp_path: Path) -> None:
    write_toml(tmp_path, '[llm]\nprovider = "gemini"\ncontext_tokens = 20000\n')
    assert resolve_config(tmp_path).llm.effective_context_tokens == 20000


@pytest.mark.parametrize(("provider", "expected"), [("ollama", "qwen2.5-coder:14b"), ("gemini", None), ("openai-compat", None)])
def test_hosted_providers_have_no_default_model(tmp_path: Path, provider: str, expected: str | None) -> None:
    assert resolve_config(tmp_path, {"llm.provider": provider}).llm.effective_model == expected


def test_every_architecture_key_is_accepted(tmp_path: Path) -> None:
    write_toml(
        tmp_path,
        """
[llm]
provider = "openai-compat"
model = "m"
host = "http://localhost:11434"
base_url = "https://example.invalid/v1"
api_key_env = "CODEWHY_API_KEY"
context_tokens = 32768
max_output_tokens = 512
temperature = 0.2
seed = 7
timeout_seconds = 60
max_transient_retries = 0
structured_output = "json"
send_seed = false
reasoning_effort = "low"
acknowledge_remote = true

[output]
mode = "both"
dir = "out/docs"
single_file_name = "DOCS.md"
token_budget = 5000

[extraction]
languages = ["python"]
max_file_bytes = 1000

[project]
entry_point = "src/main.py"
""",
    )
    config = resolve_config(tmp_path)
    assert config.source_path == tmp_path / "codewhy.toml"
    assert config.llm.structured_output == "json"
    assert config.output.dir == "out/docs"
    assert config.project.entry_point == "src/main.py"


# --- error reporting ------------------------------------------------------


def test_unknown_key_suggests_close_match(tmp_path: Path) -> None:
    assert problems_of(tmp_path, '[llm]\nmodle = "x"\n') == ['unknown key [llm].modle (did you mean "model"?)']


def test_unknown_section_suggests_close_match(tmp_path: Path) -> None:
    assert problems_of(tmp_path, '[ouput]\nmode = "both"\n') == ['unknown section [ouput] (did you mean "output"?)']


def test_unknown_key_without_close_match(tmp_path: Path) -> None:
    assert problems_of(tmp_path, "[llm]\nzzzzzz = 1\n") == ["unknown key [llm].zzzzzz"]


def test_invalid_toml_reports_line_and_column(tmp_path: Path) -> None:
    write_toml(tmp_path, '[llm]\nprovider = "ollama"\nmodel = \n')
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path)
    message = str(info.value)
    assert "codewhy.toml" in message
    assert "line 3, column 9" in message


def test_multiple_errors_reported_together(tmp_path: Path) -> None:
    problems = problems_of(
        tmp_path,
        '[llm]\nmodle = "x"\ntemperature = 5\nprovider = "nope"\n[output]\ntoken_budget = 0\n',
    )
    assert len(problems) == 4
    joined = "\n".join(problems)
    for fragment in ("[llm].modle", "[llm].temperature", "[llm].provider", "[output].token_budget"):
        assert fragment in joined


def test_config_error_message_lists_every_problem(tmp_path: Path) -> None:
    write_toml(tmp_path, "[llm]\ntemperature = -1\nseed = 1.5\n")
    with pytest.raises(ConfigError) as info:
        resolve_config(tmp_path)
    message = str(info.value)
    assert "2 problems" in message
    assert "[llm].temperature" in message and "[llm].seed" in message


# --- validation rules -----------------------------------------------------


@pytest.mark.parametrize("value", ["0", "2", "0.7", "1.99"])
def test_temperature_in_range_accepted(tmp_path: Path, value: str) -> None:
    write_toml(tmp_path, f"[llm]\ntemperature = {value}\n")
    assert resolve_config(tmp_path).llm.temperature == float(value)


@pytest.mark.parametrize("value", ["-0.1", "2.01", '"0"'])
def test_temperature_out_of_range_rejected(tmp_path: Path, value: str) -> None:
    assert problems_of(tmp_path, f"[llm]\ntemperature = {value}\n")[0].startswith("[llm].temperature")


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("llm", "context_tokens"),
        ("llm", "max_output_tokens"),
        ("llm", "timeout_seconds"),
        ("output", "token_budget"),
        ("extraction", "max_file_bytes"),
    ],
)
def test_positive_ints_required(tmp_path: Path, section: str, key: str) -> None:
    assert problems_of(tmp_path, f"[{section}]\n{key} = 0\n")[0].startswith(f"[{section}].{key}")


def test_retries_may_be_zero_but_not_negative(tmp_path: Path) -> None:
    write_toml(tmp_path, "[llm]\nmax_transient_retries = 0\n")
    assert resolve_config(tmp_path).llm.max_transient_retries == 0
    assert problems_of(tmp_path, "[llm]\nmax_transient_retries = -1\n")[0].startswith("[llm].max_transient_retries")


def test_strict_types(tmp_path: Path) -> None:
    problems = problems_of(tmp_path, '[llm]\nseed = "1"\nacknowledge_remote = "yes"\n[extraction]\nlanguages = ["python", "rust"]\n')
    assert len(problems) == 3


@pytest.mark.parametrize("key", ["dir", "single_file_name"])
@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("/abs/out", "relative"),
        ("C:/out", "relative"),
        ("../out", ".."),
        ("a/../../out", ".."),
        ("out\\\\docs", "'/'"),
        ("", "empty"),
        (".", "root"),
    ],
)
def test_output_paths_must_be_relative(tmp_path: Path, key: str, value: str, fragment: str) -> None:
    problems = problems_of(tmp_path, f'[output]\n{key} = "{value}"\n')
    assert problems[0].startswith(f"[output].{key}")
    assert fragment in problems[0]


def test_output_paths_normalized(tmp_path: Path) -> None:
    write_toml(tmp_path, '[output]\ndir = "./out/docs/"\n')
    assert resolve_config(tmp_path).output.dir == "out/docs"


@pytest.mark.parametrize(
    ("dir_value", "file_value"),
    [("docs", "docs"), ("docs", "docs/ALL.md"), ("out/README.md/x", "out/README.md"), ("./docs", "docs")],
)
def test_output_dir_and_file_must_not_collide(tmp_path: Path, dir_value: str, file_value: str) -> None:
    problems = problems_of(tmp_path, f'[output]\ndir = "{dir_value}"\nsingle_file_name = "{file_value}"\n')
    assert problems == [f"[output]: dir ({dir_value.removeprefix('./')!r}) and single_file_name ({file_value!r}) must not overlap"]


def test_similar_but_distinct_output_names_allowed(tmp_path: Path) -> None:
    write_toml(tmp_path, '[output]\ndir = "docs"\nsingle_file_name = "docs.md"\n')
    assert resolve_config(tmp_path).output.single_file_name == "docs.md"


@pytest.mark.parametrize("value", ["/src/main.py", "..\\\\main.py", "../main.py", "src\\\\main.py"])
def test_entry_point_must_be_relative_posix(tmp_path: Path, value: str) -> None:
    assert problems_of(tmp_path, f'[project]\nentry_point = "{value}"\n')[0].startswith("[project].entry_point")


def test_entry_point_existence_not_checked_here(tmp_path: Path) -> None:
    write_toml(tmp_path, '[project]\nentry_point = "does/not/exist.py"\n')
    assert resolve_config(tmp_path).project.entry_point == "does/not/exist.py"


def test_bom_in_config_file_tolerated(tmp_path: Path) -> None:
    make_project(tmp_path, {"codewhy.toml": b'\xef\xbb\xbf[llm]\nseed = 3\r\n'})
    assert resolve_config(tmp_path).llm.seed == 3


# --- layering -------------------------------------------------------------


def test_overrides_layer_over_file_over_defaults(tmp_path: Path) -> None:
    write_toml(tmp_path, '[llm]\nprovider = "openai-compat"\nmodel = "from-file"\nseed = 9\n')
    config = resolve_config(tmp_path, {"llm.model": "from-cli"})
    assert config.llm.provider == "openai-compat"  # file beats default
    assert config.llm.model == "from-cli"  # override beats file
    assert config.llm.seed == 9
    assert config.llm.effective_max_output_tokens == 1024  # default survives


def test_none_overrides_mean_not_provided(tmp_path: Path) -> None:
    write_toml(tmp_path, '[llm]\nmodel = "from-file"\n')
    config = resolve_config(tmp_path, {"llm.model": None, "llm.provider": None})
    assert config.llm.model == "from-file"
    assert config.llm.provider == "ollama"


def test_overrides_without_file(tmp_path: Path) -> None:
    config = resolve_config(tmp_path, {"llm.provider": "gemini", "llm.model": "g"})
    assert (config.llm.provider, config.llm.model, config.source_path) == ("gemini", "g", None)


def test_invalid_override_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        resolve_config(tmp_path, {"llm.provider": "gemni"})
    assert "[llm].provider" in str(info.value)
    assert "command-line options" in str(info.value)


def test_load_config_returns_file_contents_only(tmp_path: Path) -> None:
    assert load_config(tmp_path).data == {}
    write_toml(tmp_path, "[llm]\nseed = 4\n")
    raw = load_config(tmp_path)
    assert raw.data == {"llm": {"seed": 4}}
    assert raw.source_path == tmp_path / "codewhy.toml"


# --- render_toml ----------------------------------------------------------


def roundtrip(config: Config) -> Config:
    return Config.model_validate(tomllib.loads(render_toml(config)))


def test_render_toml_roundtrips_defaults(tmp_path: Path) -> None:
    config = resolve_config(tmp_path)
    assert roundtrip(config).model_dump() == config.model_dump()


def test_render_toml_roundtrips_custom_values(tmp_path: Path) -> None:
    write_toml(
        tmp_path,
        '[llm]\nprovider = "gemini"\nmodel = "a \\"quoted\\" \\\\ \\u00e9 \\t id"\ntemperature = 0.3\n'
        'send_seed = true\nstructured_output = "prompt"\n'
        '[extraction]\nlanguages = ["typescript"]\n[project]\nentry_point = "src/app.ts"\n',
    )
    config = resolve_config(tmp_path)
    assert config.llm.model == 'a "quoted" \\ é \t id'
    assert roundtrip(config).model_dump() == config.model_dump()


def test_render_toml_is_deterministic(tmp_path: Path) -> None:
    config = resolve_config(tmp_path)
    assert render_toml(config) == render_toml(resolve_config(tmp_path))
    assert render_toml(config).splitlines()[0] == "[llm]"
