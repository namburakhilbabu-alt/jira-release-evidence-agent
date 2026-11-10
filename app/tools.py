"""The tools the model can call. They all read from Jira, never from dataset.json."""
import json
import re
import time

from app import rules

PAGE_SIZE = 25         # comments per page
MAX_CHARS = 12000      # cut very long tool results


def tool(name, description, params=None, required=()):
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": params or {}, "required": list(required)},
    }}


ISSUE = {"type": "string", "description": "Jira key (TYN-12) or source id (REQ-12, REC-5957)"}

READ_TOOLS = [
    tool("read_manifest", "Read the release manifest: target build and assessment rules."),
    tool("search_issues", "Search this dataset by kind (requirement, evidence, work, manifest), "
         "release (e.g. R2.4) and/or text. All filters optional. Returns short rows.",
         {"kind": {"type": "string"}, "release": {"type": "string"}, "text": {"type": "string"}}),
    tool("get_issue", "Read one issue: description and linked issues.", {"issue": ISSUE}, ["issue"]),
    tool("get_comments", f"Read comments of an issue, {PAGE_SIZE} per page. If next_offset is not null "
         "there are more: call again with that offset (results are often at the end).",
         {"issue": ISSUE, "offset": {"type": "integer"}}, ["issue"]),
    tool("check_build", "Check if one comment is about the requirement's target build "
         "(release, commit, service, environment, and prompt when needed).",
         {"requirement": ISSUE, "issue": ISSUE, "comment_id": {"type": "string"}},
         ["requirement", "issue", "comment_id"]),
]

# Step 1 only needs these two, so the model can't drift into assessing.
FIND_TOOLS = READ_TOOLS[:2]

EVIDENCE = {"type": "array", "items": {"type": "object", "properties": {
    "issue": {"type": "string"}, "comment_id": {"type": "string"}, "note": {"type": "string"}},
    "required": ["issue", "comment_id", "note"]}}

SUBMIT_REQUIREMENTS = tool(
    "submit_requirements", "Finish: the list of requirement source ids to assess (e.g. REQ-01).",
    {"requirements": {"type": "array", "items": {"type": "string"}}, "notes": {"type": "string"}},
    ["requirements"])

RECORD_DECISION = tool(
    "record_decision", "Finish: your decision for this requirement.",
    {"decision": {"type": "string", "enum": ["PASS", "FAIL", "INSUFFICIENT_EVIDENCE"]},
     "explanation": {"type": "string", "description": "2-5 sentences"},
     "evidence_used": EVIDENCE,
     "evidence_rejected": EVIDENCE,
     "outstanding": {"type": "string", "description": "what still needs verifying, or empty"}},
    ["decision", "explanation", "evidence_used", "outstanding"])


def short(issue):
    """Small view of a Jira issue: key, source id, kind, release, status, summary."""
    info = {"key": issue["key"], "summary": issue["fields"]["summary"]}
    for lab in issue["fields"]["labels"]:
        for prefix in ("src", "kind", "release"):
            if lab.startswith(prefix + "-"):
                info[prefix] = lab[len(prefix) + 1:]
    m = re.search(r"^Recorded status: (.*)$", issue["fields"].get("description") or "", re.M)
    if m:
        info["recorded_status"] = m.group(1)
    return info


class Tools:
    def __init__(self, jira, log):
        self.jira = jira
        self.log = log
        self.issues = {}     # cache, so we never ask Jira twice
        self.comments = {}

    def issue(self, ref):
        if ref not in self.issues:
            issue = self.jira.get_issue(ref)
            self.issues[ref] = self.issues[issue["key"]] = issue
        return self.issues[ref]

    def all_comments(self, ref):
        key = self.issue(ref)["key"]
        if key not in self.comments:
            self.comments[key] = self.jira.get_comments(key)
        return self.comments[key]

    def find_comment(self, ref, comment_id):
        for c in self.all_comments(ref):
            if c["id"] == str(comment_id):
                return c
        return None

    def run(self, name, args, phase):
        start = time.time()
        try:
            result = json.dumps(getattr(self, "tool_" + name)(**args))
            ok = True
        except Exception as e:  # goes back to the model as text, the run continues
            result = f"ERROR: {e}"
            ok = False
        if len(result) > MAX_CHARS:
            result = result[:MAX_CHARS] + " ...[cut]"
        self.log.tool(phase, name, args, ok, result, time.time() - start)
        return result

    # ---- the tools ----

    def tool_read_manifest(self):
        found = self.jira.search(self.jira.in_scope('labels = "kind-manifest"'), limit=1)
        if not found:
            raise LookupError("no manifest issue found; was the importer run?")
        return {"key": found[0]["key"], "text": self.issue(found[0]["key"])["fields"]["description"]}

    def tool_search_issues(self, kind="", release="", text=""):
        jql = self.jira.in_scope()
        if kind:
            jql += f' AND labels = "kind-{kind}"'
        if release:
            jql += f' AND labels = "release-{release}"'
        if text:
            jql += ' AND text ~ "{}"'.format(text.replace('"', " "))
        found = self.jira.search(jql + " ORDER BY key", ("summary", "labels", "description"), limit=31)
        return {"results": [short(i) for i in found[:30]], "more": len(found) > 30}

    def tool_get_issue(self, issue):
        data = self.issue(issue)
        links = []
        for l in data["fields"]["issuelinks"]:
            other = l.get("outwardIssue") or l.get("inwardIssue")
            links.append({"key": other["key"], "summary": other["fields"]["summary"]})
        return {**short(data), "description": data["fields"]["description"], "linked_issues": links}

    def tool_get_comments(self, issue, offset=0):
        comments = self.all_comments(issue)
        offset = int(offset or 0)
        page = comments[offset:offset + PAGE_SIZE]
        more = offset + len(page) < len(comments)
        return {"issue": self.issue(issue)["key"], "total": len(comments), "comments": page,
                "next_offset": offset + len(page) if more else None}

    def tool_check_build(self, requirement, issue, comment_id):
        req = rules.read_requirement(self.issue(requirement)["fields"]["description"])
        comment = self.find_comment(issue, comment_id)
        if comment is None:
            raise LookupError(f"no comment {comment_id} on {issue}")
        return {"target": req["target"], "prompt_needed": req["needs_prompt"],
                **rules.check_build(req["target"], comment["text"], req["needs_prompt"])}
