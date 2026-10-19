import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # live_llm tests talk to a real model; they run only when selected explicitly.
    if "live_llm" in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="needs a live model; run with -m live_llm")
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip)
