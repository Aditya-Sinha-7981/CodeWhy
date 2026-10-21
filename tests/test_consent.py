from __future__ import annotations

import pytest

from codewhy.consent import ConsentDeclined, ConsentRequired, ensure_remote_ack, loopback, url_host
from tests.support.fake_provider import FakeProvider


class Prompts:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[str] = []
        self.told: list[str] = []

    def ask(self, question: str) -> bool:
        self.asked.append(question)
        return self.answer

    def tell(self, message: str) -> None:
        self.told.append(message)


def check(provider: FakeProvider, prompts: Prompts, *, assume_yes: bool = False, ack: bool = False, tty: bool = True) -> None:
    ensure_remote_ack(
        provider, assume_yes=assume_yes, acknowledged_in_config=ack, is_tty=tty, ask=prompts.ask, tell=prompts.tell
    )


REMOTE = FakeProvider(is_remote=True, endpoint_label="generativelanguage.googleapis.com")


@pytest.mark.parametrize("tty", [True, False])
def test_local_provider_never_prompts(tty: bool) -> None:
    prompts = Prompts(answer=False)
    check(FakeProvider(is_remote=False), prompts, tty=tty)
    assert prompts.asked == prompts.told == []


@pytest.mark.parametrize(("assume_yes", "ack"), [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("tty", [True, False])
def test_remote_with_yes_or_config_ack_proceeds_without_asking(assume_yes: bool, ack: bool, tty: bool) -> None:
    prompts = Prompts(answer=False)
    check(REMOTE, prompts, assume_yes=assume_yes, ack=ack, tty=tty)
    assert prompts.asked == []


def test_remote_tty_asks_and_proceeds_on_yes() -> None:
    prompts = Prompts(answer=True)
    check(REMOTE, prompts)
    assert prompts.told == ["Your source code will be sent to generativelanguage.googleapis.com."]
    assert prompts.asked == ["Continue? [y/N]"]


def test_remote_tty_no_aborts() -> None:
    prompts = Prompts(answer=False)
    with pytest.raises(ConsentDeclined, match="nothing was sent"):
        check(REMOTE, prompts)
    assert prompts.asked == ["Continue? [y/N]"]


def test_remote_without_tty_requires_ack_and_says_how() -> None:
    prompts = Prompts(answer=True)
    with pytest.raises(ConsentRequired) as info:
        check(REMOTE, prompts, tty=False)
    message = str(info.value)
    assert "Your source code will be sent to generativelanguage.googleapis.com." in message
    assert "--yes" in message and "llm.acknowledge_remote = true" in message
    assert prompts.asked == []
    assert REMOTE.calls == []


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", True),
        ("LocalHost", True),
        ("127.0.0.1", True),
        ("127.1.2.3", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),
        ("192.168.0.10", False),
        ("localhost.example.com", False),
        ("", False),
        ("[::2]", False),
    ],
)
def test_loopback(host: str, expected: bool) -> None:
    assert loopback(host) is expected


@pytest.mark.parametrize(
    ("url", "host"),
    [("http://[::1]:11434", "::1"), ("http://localhost:11434/", "localhost"), ("localhost:11434", "localhost"), ("https://a.b/v1", "a.b")],
)
def test_url_host(url: str, host: str) -> None:
    assert url_host(url) == host
