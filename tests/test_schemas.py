from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError

from codewhy.llm.budget import estimate_tokens
from codewhy.llm.provider import AuthError, MalformedOutput
from codewhy.summarize.schemas import FilePass, ModulePass, ProjectPass, provider_schema
from tests.support.fake_provider import BudgetCheckingFakeProvider, Call, FakeProvider, synthesize


# --- pass models ----------------------------------------------------------------


def test_valid_pass_values_are_accepted_and_stripped() -> None:
    assert FilePass(purpose="  Loads settings.  ").purpose == "Loads settings."
    assert FilePass(purpose="p", notes="  ").notes is None
    assert FilePass(purpose="p", notes=" n ").notes == "n"
    assert ModulePass(responsibility="r", how_pieces_cooperate="h").responsibility == "r"
    assert ProjectPass(overview="o", architecture="a").architecture == "a"


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (FilePass, {"purpose": "   "}),
        (FilePass, {}),
        (FilePass, {"purpose": "p", "extra": "x"}),
        (FilePass, {"purpose": "x" * 1201}),
        (FilePass, {"purpose": "p", "notes": "x" * 1201}),
        (ModulePass, {"responsibility": "r", "how_pieces_cooperate": ""}),
        (ModulePass, {"responsibility": "x" * 1501, "how_pieces_cooperate": "h"}),
        (ProjectPass, {"overview": "o"}),
        (ProjectPass, {"overview": "o", "architecture": "x" * 2501}),
        (ProjectPass, {"overview": 3, "architecture": "a"}),
    ],
)
def test_invalid_pass_values_are_rejected(model: type[BaseModel], data: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_length_limits_are_inclusive() -> None:
    FilePass(purpose="x" * 1200, notes="y" * 1200)
    ModulePass(responsibility="x" * 1500, how_pieces_cooperate="y" * 1500)
    ProjectPass(overview="x" * 2500, architecture="y" * 2500)


# --- provider_schema ---------------------------------------------------------------


def _walk(node: object) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


@pytest.mark.parametrize("model", [FilePass, ModulePass, ProjectPass])
def test_provider_schema_is_endpoint_safe(model: type[BaseModel]) -> None:
    schema = provider_schema(model)
    text = json.dumps(schema)
    for forbidden in ("$ref", "$defs", "anyOf", "title", "default", "maxLength", "minLength"):
        assert forbidden not in text, forbidden
    assert schema["type"] == "object"
    assert set(schema["properties"]) == set(model.model_fields)
    assert all(prop["type"] == "string" and prop["description"] for prop in schema["properties"].values())


def test_provider_schema_keeps_required_and_types_optional_fields_as_strings() -> None:
    schema = provider_schema(FilePass)
    assert schema["required"] == ["purpose"]
    assert schema["properties"]["notes"]["type"] == "string"
    assert provider_schema(ModulePass)["required"] == ["responsibility", "how_pieces_cooperate"]
    assert provider_schema(ProjectPass)["required"] == ["overview", "architecture"]


def test_provider_schema_inlines_nested_definitions() -> None:
    class Inner(BaseModel):
        """Inner doc."""

        name: str

    class Outer(BaseModel):
        inner: Inner
        maybe: Inner | None = None
        items: list[Inner] = []

    schema = provider_schema(Outer)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and "anyOf" not in text
    assert schema["properties"]["inner"]["properties"]["name"] == {"type": "string"}
    assert schema["properties"]["maybe"]["type"] == "object"
    assert schema["properties"]["items"]["items"]["required"] == ["name"]
    assert schema["required"] == ["inner"]


# --- FakeProvider (used by tasks 05-08) ------------------------------------------------


def test_fake_provider_synthesizes_schema_valid_deterministic_replies() -> None:
    fake = FakeProvider()
    schema = provider_schema(ModulePass)
    first = fake.generate_json("prompt A", schema, max_output_tokens=100)
    assert ModulePass.model_validate(first)
    assert fake.generate_json("prompt A", schema, max_output_tokens=100) == first
    assert fake.generate_json("prompt B", schema, max_output_tokens=100) != first
    assert FilePass.model_validate(synthesize(provider_schema(FilePass), "p")).notes is None
    assert fake.calls[0] == Call("prompt A", schema, 100, None)


def test_fake_provider_queue_callable_and_chosen_errors() -> None:
    fake = FakeProvider(results=[{"a": 1}, MalformedOutput("bad", raw="x")], respond=lambda call: {"n": len(call.prompt)})
    assert fake.generate_json("p", {}, max_output_tokens=1) == {"a": 1}
    with pytest.raises(MalformedOutput):
        fake.generate_json("p", {}, max_output_tokens=1)
    assert fake.generate_json("abc", {}, max_output_tokens=1, temperature=0.3) == {"n": 3}
    assert fake.calls[2].temperature == 0.3

    failing = FakeProvider(errors={1: AuthError("no")}, is_remote=True, endpoint_label="api.example.com")
    failing.generate_json("p", {"type": "object"}, max_output_tokens=1)
    with pytest.raises(AuthError):
        failing.generate_json("p", {"type": "object"}, max_output_tokens=1)
    assert (failing.is_remote, failing.endpoint_label, len(failing.calls)) == (True, "api.example.com", 2)


def test_fake_provider_preflight_can_fail() -> None:
    fake = FakeProvider(preflight_error=AuthError("bad key"))
    with pytest.raises(AuthError):
        fake.preflight()
    assert fake.preflight_calls == 1


def test_budget_checking_wrapper_rejects_oversized_prompts() -> None:
    inner = FakeProvider()
    checked = BudgetCheckingFakeProvider(inner, prompt_budget=10)
    checked.generate_json("x" * 30, provider_schema(FilePass), max_output_tokens=5)
    assert estimate_tokens("x" * 31) == 11
    with pytest.raises(AssertionError, match="exceeds the prompt budget of 10"):
        checked.generate_json("x" * 31, provider_schema(FilePass), max_output_tokens=5)
    assert len(checked.calls) == 1
