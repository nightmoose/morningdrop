import json

from morningdrop.drops import Drop, sha256
from morningdrop.gate import Gatekeeper
from morningdrop.llm import LLM, LLMConfig
from morningdrop.summarize import summarize


def _reports(workdir):
    gate = Gatekeeper(workdir / "contracts")
    return [gate.check(Drop(p, sha256(p))) for p in sorted((workdir / "drops").iterdir())]


class Scripted:
    """Fake model: returns canned answers in order and records the prompts."""
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        return self.answers.pop(0)


GOOD = {
    "status": "red",
    "line1": "orders_2026-10-24.csv: 63 of 240 rows failed, mostly lowercase currency.",
    "line2": "Ask the storefront export owner to send currency as upper-case ISO codes.",
    "fixes": [{"file": "orders_2026-10-24.csv", "field": "currency", "kind": "fix_data",
               "suggestion": "Upper-case currency before export (usd -> USD)."}],
}


def llm(fn):
    return LLM(LLMConfig(backend="mock"), mock=fn)


def test_good_answer_accepted_first_try(workdir):
    s = summarize(_reports(workdir), llm(Scripted(json.dumps(GOOD))))
    assert s.source == "model" and s.attempts == 1 and s.status == "red"


def test_schema_violation_triggers_repair_prompt(workdir):
    bad = dict(GOOD, status="purple")
    fake = Scripted(json.dumps(bad), "```json\n" + json.dumps(GOOD) + "\n```")
    s = summarize(_reports(workdir), llm(fake))
    assert s.source == "model" and s.attempts == 2
    assert "AISchemaGate rejected" in fake.calls[1][-1]["content"]
    assert "status" in fake.calls[1][-1]["content"]


def test_invented_numbers_and_wrong_status_are_rejected(workdir):
    lie = dict(GOOD, status="green", line1="orders_2026-10-24.csv: 999 rows failed overnight.")
    s = summarize(_reports(workdir), llm(Scripted(*[json.dumps(lie)] * 3)))
    assert s.source == "fallback"
    flat = " ".join(sum(s.rejections, []))
    assert "999" in flat and "worst file status" in flat
    assert "orders_2026-10-24.csv" in s.line2


def test_ungrounded_fix_is_quarantined_not_fatal(workdir):
    extra = dict(GOOD, fixes=GOOD["fixes"] + [
        {"file": "payments.csv", "field": "amount", "kind": "fix_data", "suggestion": "Make amounts positive."}])
    s = summarize(_reports(workdir), llm(Scripted(json.dumps(extra))))
    assert s.source == "model" and len(s.fixes) == 1
    assert s.dropped_fixes[0]["file"] == "payments.csv"


def test_not_json_falls_back(workdir):
    s = summarize(_reports(workdir), llm(Scripted("Good morning!", "still prose", "nope")))
    assert s.source == "fallback" and s.attempts == 3


def test_model_down_falls_back(workdir):
    def boom(_):
        raise ConnectionError("ollama not running")
    s = summarize(_reports(workdir), llm(boom))
    assert s.source == "fallback" and "ollama not running" in s.rejections[0][0]


def test_no_raw_rows_reach_the_model(workdir):
    fake = Scripted(json.dumps(GOOD))
    summarize(_reports(workdir), llm(fake))
    prompt = json.dumps(fake.calls[0])
    # Only counts and violation messages go to the model; passing rows never do.
    assert "user0@example.com" not in prompt
    assert "ord_024000" not in prompt
