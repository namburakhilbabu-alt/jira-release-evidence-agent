"""Offline tests, no Jira and no AI needed:   python -m pytest -q"""
import json
from types import SimpleNamespace

from app import rules
from app.agent import Agent
from app.report import double_check
from app.tools import RECORD_DECISION, Tools

TARGET = {"release": "R2.4", "commit": "b7a91f2", "prompt": "p7",
          "environment": "production", "service": "atlas-assistant"}

REQ_TEXT = ('Source ID: REQ-07\n----\nRequirement ID: REQ-07\nTarget: ' + json.dumps(TARGET) +
            '\nMandatory: yes\nAcceptance criterion: QA must verify that sensitive identifiers are redacted.')

GOOD = ("[REC-1 comment 1]\nRecorded event 2026-11-08T14:00:00+05:30 | actor Priya Nair | role QA Lead\n"
        "Retest on release R2.4; commit b7a91f2; service atlas-assistant; environment production: 28 of 28 pass.")
STAGING = ("[REC-2 comment 1]\nRecorded event 2026-11-08T16:30:00+05:30 | actor Priya Nair | role QA Lead\n"
           "Run: release R2.4; commit b7a91f2; service atlas-assistant; environment staging; prompt p7.")


class FakeJira:
    """Just enough of app.jira.Jira for the tests."""
    def __init__(self):
        self.data = {
            "TYN-1": ({"summary": "[REQ-07] Sensitive log redaction", "description": REQ_TEXT}, []),
            "TYN-2": ({"summary": "[REC-1] Redaction retest", "description": ""}, [GOOD]),
            "TYN-3": ({"summary": "[REC-2] Staging run", "description": ""}, [STAGING]),
            "TYN-4": ({"summary": "[REC-3] Long thread", "description": ""}, [f"note {i}" for i in range(60)]),
        }

    def get_issue(self, ref):
        fields, _ = self.data[ref]
        return {"key": ref, "fields": {**fields, "labels": ["tyn-release-assessment-v2"], "issuelinks": []}}

    def get_comments(self, key):
        return [{"id": f"{key}-{i}", "text": t} for i, t in enumerate(self.data[key][1])]


class FakeLog:
    def __init__(self):
        self.calls = []

    def tool(self, *args):
        self.calls.append(args[1])

    def model(self, *args):
        pass

    def write(self, **kwargs):
        pass


# ---- rules ----

def test_read_requirement():
    req = rules.read_requirement(REQ_TEXT)
    assert req["target"] == TARGET and req["mandatory"] is True and not req["needs_prompt"]


def test_check_build():
    assert rules.check_build(TARGET, GOOD, needs_prompt=False)["verdict"] == "applies"
    assert rules.check_build(TARGET, GOOD, needs_prompt=True)["verdict"] == "unclear"
    assert rules.check_build(TARGET, STAGING, needs_prompt=False)["verdict"] == "does_not_apply"
    approval = "Approved: release R2.4, commit b7a91f2, service atlas-assistant. Approval scope: staging."
    assert rules.check_build(TARGET, approval, needs_prompt=False)["verdict"] == "does_not_apply"


def test_who_and_when():
    assert rules.who_and_when(GOOD) == "Priya Nair (QA Lead), 2026-11-08T14:00:00+05:30"


def test_overall():
    def r(decision, mandatory=True):
        return {"decision": decision, "mandatory": mandatory}
    assert rules.overall([r("PASS"), r("PASS")]) == "READY"
    assert rules.overall([r("PASS"), r("INSUFFICIENT_EVIDENCE")]) == "NEEDS_REVIEW"
    assert rules.overall([r("FAIL"), r("INSUFFICIENT_EVIDENCE")]) == "BLOCKED"
    assert rules.overall([r("PASS"), r("FAIL", mandatory=False)]) == "READY"
    assert rules.overall([]) == "NEEDS_REVIEW"


# ---- tools and the code double-check ----

def test_comments_come_in_pages():
    tools = Tools(FakeJira(), FakeLog())
    first = json.loads(tools.run("get_comments", {"issue": "TYN-4"}, "test"))
    last = json.loads(tools.run("get_comments", {"issue": "TYN-4", "offset": 50}, "test"))
    assert first["total"] == 60 and first["next_offset"] == 25
    assert last["comments"][-1]["text"] == "note 59" and last["next_offset"] is None


def test_tool_errors_do_not_crash():
    result = Tools(FakeJira(), FakeLog()).run("get_issue", {"issue": "TYN-99"}, "test")
    assert result.startswith("ERROR")


def test_double_check():
    tools = Tools(FakeJira(), FakeLog())
    req = rules.read_requirement(REQ_TEXT)

    good = {"decision": "PASS", "explanation": "", "outstanding": "",
            "evidence_used": [{"issue": "TYN-2", "comment_id": "TYN-2-0", "note": "retest"}]}
    assert double_check(good, req, tools)["decision"] == "PASS"

    wrong_build = {**good, "evidence_used": [{"issue": "TYN-3", "comment_id": "TYN-3-0", "note": "staging"}]}
    checked = double_check(wrong_build, req, tools)
    assert checked["decision"] == "INSUFFICIENT_EVIDENCE" and checked["evidence_rejected"]

    made_up = {**good, "evidence_used": [{"issue": "TYN-2", "comment_id": "nope", "note": ""}]}
    assert double_check(made_up, req, tools)["decision"] == "INSUFFICIENT_EVIDENCE"


# ---- the agent loop, with a scripted model ----

def reply(name, args, call_id="1"):
    call = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    msg = SimpleNamespace(tool_calls=[call], model_dump=lambda **kw: {"role": "assistant"})
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def scripted_agent(monkeypatch, replies):
    for name, value in {"LLM_API_KEY": "x", "LLM_BASE_URL": "http://localhost", "LLM_MODEL": "test",
                        "LLM_SECONDS_BETWEEN_CALLS": "0"}.items():
        monkeypatch.setenv(name, value)
    agent = Agent(FakeLog())
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: next(replies))))
    return agent


def test_agent_runs_tools_then_finishes(monkeypatch):
    replies = iter([
        reply("get_issue", {"issue": "TYN-1"}),
        reply("record_decision", {"decision": "PASS", "explanation": "ok", "outstanding": "",
                                  "evidence_used": []}),
    ])
    tools = Tools(FakeJira(), FakeLog())
    answer, error = scripted_agent(monkeypatch, replies).run("t", "go", RECORD_DECISION, tools, 5)
    assert error is None and answer["decision"] == "PASS"
    assert tools.log.calls == ["get_issue"]


def test_agent_stops_at_step_limit(monkeypatch):
    replies = iter([reply("get_issue", {"issue": "TYN-1"})] * 10)
    tools = Tools(FakeJira(), FakeLog())
    answer, error = scripted_agent(monkeypatch, replies).run("t", "go", RECORD_DECISION, tools, 3)
    assert answer is None and "3 steps" in error
