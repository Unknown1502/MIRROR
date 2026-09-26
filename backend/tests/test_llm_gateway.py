"""LLM Gateway fallback for free-form typed commands. The gateway is mocked here; see
scripts/live_llm_gateway.py for the live check against the real service."""

import json
from dataclasses import replace

import pytest

from app.intent.llm_interpreter import LLMInterpreter, parse_proposal
from app.runtime.dispatcher import HELP


def reply(obj, request_id="rq_test"):
    return {"choices": [{"message": {"content": json.dumps(obj) if not isinstance(obj, str) else obj}}], "request_id": request_id}


def proposal(action, **kw):
    base = {"action": action, "product": None, "quantity": None, "source": None, "destination": None, "quantities": [], "answer": None}
    return {**base, **kw}


@pytest.fixture()
def with_llm(container, settings):
    calls = []

    def install(response):
        def post(body):
            calls.append(body)
            if isinstance(response, Exception):
                raise response
            return response
        container.dispatcher.interpreter = LLMInterpreter(replace(settings, assemblyai_api_key="k"), post=post)
        return calls

    return install


def test_free_form_request_becomes_a_checked_preview(container, session, with_llm):
    calls = with_llm(reply(proposal("propose", product="XR-17", quantity=500, source="Mumbai", destination="Pune Site 2")))
    r = container.dispatcher.handle(session, "I'd like to get 500 of the XR-17 relays over to Pune Site 2 from Mumbai")
    assert calls and calls[0]["model"] == "qwen3.5-4b-32k-fast"
    assert "response_format" not in calls[0]  # this model does not support structured outputs
    assert r.interpreted_by["model"] == "qwen3.5-4b-32k-fast" and r.interpreted_by["request_id"] == "rq_test"
    assert r.transaction["state"] == "PREVIEW_READY"
    draft = r.transaction["versions"][-1]["intent"]["drafts"][0]
    assert draft["quantity"]["status"] == "EXPLICIT" and draft["destination"]["value"] == "PUNE-SITE-2"
    events = container.runtime.audit(r.transaction["id"])["events"]
    assert any(e["event_type"] == "LLM_INTERPRETATION" and e["payload"]["request_id"] == "rq_test" for e in events)


def test_model_invented_number_needs_confirmation(container, session, with_llm):
    with_llm(reply(proposal("propose", product="XR-17", quantity=800, source="Mumbai", destination="Pune Site 2")))
    r = container.dispatcher.handle(session, "Get some XR-17 over to Pune Site 2 from Mumbai")
    assert r.transaction["state"] == "CLARIFYING"
    q = r.transaction["versions"][-1]["intent"]["drafts"][0]["quantity"]
    assert q["status"] == "INFERRED" and q["source"]["type"] == "MODEL_PROPOSAL"


def test_model_cannot_commit_without_the_users_words(container, session, with_llm):
    container.dispatcher.handle(session, "Move 500 units of XR-17 from Mumbai to Pune Site 2")
    with_llm(reply(proposal("commit", quantity=500)))
    r = container.dispatcher.handle(session, "yep, sounds right to me")
    assert r.error and r.error["code"] == "INSUFFICIENT_EVIDENCE"
    assert container.erp.operations() == []


@pytest.mark.parametrize("response", [RuntimeError("network down"), reply("Sure! Happy to help."), reply({"action": "launch_rockets"})])
def test_gateway_failures_fall_back_to_the_deterministic_path(container, session, with_llm, response):
    with_llm(response)
    r = container.dispatcher.handle(session, "blorp the flux capacitor")
    assert r.reply == HELP and r.interpreted_by is None


def test_llm_is_not_called_when_the_compiler_understands(container, session, with_llm):
    calls = with_llm(reply(proposal("other")))
    container.dispatcher.handle(session, "Move 500 units of XR-17 from Mumbai to Pune Site 2")
    assert calls == []


def test_structured_output_models_get_a_strict_schema(settings):
    seen = []
    interp = LLMInterpreter(replace(settings, assemblyai_api_key="k", llm_gateway_model="gemini-2.5-flash-lite"),
                            post=lambda body: seen.append(body) or reply(proposal("other")))
    interp.interpret("hello")
    assert seen[0]["response_format"]["json_schema"]["strict"] is True


def test_parse_proposal_validates_and_bounds():
    assert parse_proposal('noise {"action":"propose","quantity":"700","product":"XR-17"} trailing')["quantity"] == 700
    assert parse_proposal('{"action":"propose","quantity":-5}')["quantity"] is None
    assert parse_proposal('{"action":"propose","quantity":true}')["quantity"] is None
    assert parse_proposal('{"action":"hack"}') is None
    assert parse_proposal("no json here") is None
    long = parse_proposal('{"action":"propose","source":"' + "M" * 500 + '"}')
    assert len(long["source"]) == 60


def test_rate_limit_is_honoured_without_retry_storms(container, session, settings):
    import urllib.error
    from email.message import Message

    calls = []

    def post(body):
        calls.append(body)
        headers = Message()
        headers["Retry-After"] = "57"
        raise urllib.error.HTTPError("url", 429, "Too Many Requests", headers, None)

    container.dispatcher.interpreter = LLMInterpreter(replace(settings, assemblyai_api_key="k"), post=post)
    container.dispatcher.handle(session, "blorp the flux capacitor")
    r2 = container.dispatcher.handle(session, "blorp it again")
    assert len(calls) == 1  # the second turn does not hit the gateway while rate limited
    assert "paused for about" in r2.reply and "rate limit" in r2.reply
    assert container.dispatcher.interpreter.status()["rate_limited_for_seconds"] > 50


def test_with_counts_as_an_amount_phrasing(container):
    from app.intent.compiler import compile_utterance
    from app.intent.entities import EntityResolver

    cmd = compile_utterance("could you see what happens with 900 instead", EntityResolver(container.erp.read_snapshot()))
    assert [q.value for q in cmd.quantities] == [900]
