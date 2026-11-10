"""Double-checks each decision with fixed rules, then writes report.md / report.json."""
import json
import os

from app import rules


def double_check(answer, req, tools):
    """The model can be wrong. Re-read every comment it cited:
    - comment doesn't exist          -> dropped
    - comment is about another build -> moved to rejected
    - PASS/FAIL with no cited comment about the target build -> INSUFFICIENT_EVIDENCE
    """
    notes, used, rejected = [], [], list(answer.get("evidence_rejected") or [])

    for ev in answer.get("evidence_used") or []:
        try:
            comment = tools.find_comment(ev["issue"], ev["comment_id"])
        except Exception:
            comment = None
        if comment is None:
            notes.append(f"{ev['issue']} comment {ev['comment_id']} does not exist; ignored.")
            continue
        verdict = rules.check_build(req["target"], comment["text"], req["needs_prompt"])["verdict"]
        if verdict == "does_not_apply":
            notes.append(f"{ev['issue']} comment {ev['comment_id']} is about another build; not counted.")
            rejected.append({**ev, "note": "Wrong build (checked by code). " + ev.get("note", "")})
        else:
            used.append({**ev, "build_check": verdict})

    decision = answer["decision"]
    if decision != "INSUFFICIENT_EVIDENCE" and not any(e["build_check"] == "applies" for e in used):
        notes.append(f"Model said {decision}, but none of the cited comments is clearly about the "
                     "target build. Changed to INSUFFICIENT_EVIDENCE.")
        decision = "INSUFFICIENT_EVIDENCE"

    return {**answer, "decision": decision, "model_decision": answer["decision"],
            "evidence_used": used, "evidence_rejected": rejected, "checks": notes}


def evidence_lines(items, tools):
    lines = []
    for ev in items:
        try:
            comment = tools.find_comment(ev["issue"], ev["comment_id"])
        except Exception:
            comment = None
        url = tools.jira.link_url(tools.issue(ev["issue"])["key"] if comment else ev["issue"], ev["comment_id"])
        lines.append(f"- [{ev['issue']} comment {ev['comment_id']}]({url}): {ev.get('note', '')}")
        if comment:
            body = comment["text"].split("\n")
            lines.append(f"  - {body[0]} by {rules.who_and_when(comment['text'])}")
            lines.append(f"  - > {' '.join(body[2:])[:250]}")
    return lines


def write(folder, run, tools):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "report.json"), "w", encoding="utf-8") as f:
        json.dump(run, f, indent=2, ensure_ascii=False)
    with open(os.path.join(folder, "metrics.json"), "w") as f:
        json.dump(run["metrics"], f, indent=2)

    m = run["metrics"]
    out = [
        f"# Release readiness report: {run['release']}",
        "",
        f"## Overall decision: **{run['overall']}**",
        "",
        f"- Jira project `{run['project']}`, dataset label `{run['label']}`",
        f"- Generated {run['generated']} with model `{m['model']}`",
        f"- {m['seconds']} s, {m['model_calls']} model calls, {m['tool_calls']} tool calls, "
        f"{m['input_tokens']} input / {m['output_tokens']} output tokens",
        "",
        "BLOCKED if any mandatory requirement fails; NEEDS_REVIEW if none fails but some lack "
        "evidence; READY only if all pass.",
        "",
    ]
    if run["warnings"]:
        out += ["## Warnings", ""] + [f"- {w}" for w in run["warnings"]] + [""]

    out += ["## Summary", "", "| Requirement | Jira | Title | Mandatory | Decision |", "|---|---|---|---|---|"]
    for r in run["results"]:
        mandatory = {True: "yes", False: "no", None: "?"}[r["mandatory"]]
        out.append(f"| {r['id']} | {r['key']} | {r['title']} | {mandatory} | **{r['decision']}** |")
    out.append("")

    for r in run["results"]:
        out += [f"## {r['id']}: {r['title']}", "",
                f"- Decision: **{r['decision']}**"
                + (f" (model said {r['model_decision']}, changed by the code check)"
                   if r["model_decision"] != r["decision"] else ""),
                f"- Criterion: {r['criterion']}",
                f"- Target build: {', '.join(f'{k} {v}' for k, v in r['target'].items())}",
                f"- Jira status: {r['status']} (not proof on its own)",
                "", r["explanation"], ""]
        if r.get("error"):
            out += [f"**Run error:** {r['error']}", ""]
        if r["evidence_used"]:
            out += ["**Evidence used**", ""] + evidence_lines(r["evidence_used"], tools) + [""]
        if r["evidence_rejected"]:
            out += ["**Looked at, not counted**", ""] + evidence_lines(r["evidence_rejected"], tools) + [""]
        if r.get("outstanding"):
            out += [f"**Still needed:** {r['outstanding']}", ""]
        if r["checks"]:
            out += ["**Code check notes**", ""] + [f"- {n}" for n in r["checks"]] + [""]

    path = os.path.join(folder, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path
