"""Runs the release assessment.

    python -m app.main                      # all requirements, settings from .env
    python -m app.main --only REQ-12        # one requirement (quick test)
    python -m app.main --out submission     # choose the output folder
"""
import argparse
import datetime
import os

from app import config, report, rules
from app.agent import Agent
from app.jira import Jira
from app.tools import FIND_TOOLS, RECORD_DECISION, SUBMIT_REQUIREMENTS, Tools, short
from app.trace import Trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--label")
    parser.add_argument("--release")
    parser.add_argument("--only", help="assess just this requirement, e.g. REQ-12")
    parser.add_argument("--out", help="output folder (default outputs/run-<time>)")
    args = parser.parse_args()

    release = args.release or config.get("TARGET_RELEASE")
    max_steps = int(config.get("MAX_STEPS", "20"))
    folder = args.out or "outputs/" + datetime.datetime.now().strftime("run-%Y%m%d-%H%M%S")
    os.makedirs(folder, exist_ok=True)

    jira = Jira(args.project, args.label)
    log = Trace(os.path.join(folder, "trace.jsonl"))
    tools = Tools(jira, log)
    agent = Agent(log)
    warnings = []
    print(f"Release {release}, project {jira.project}, label {jira.label}, model {agent.model}")

    # Step 1: the model finds the requirements to assess
    if args.only:
        ids = [args.only]
    else:
        print("Finding requirements...")
        answer, error = agent.run(
            "find_requirements",
            f"Target release: {release}. Only find the requirements, do not assess them. Read the "
            "manifest and search Jira for every requirement that must be assessed, then call "
            "submit_requirements with their ids.",
            SUBMIT_REQUIREMENTS, tools, max_steps, allowed=FIND_TOOLS)
        ids = answer["requirements"] if answer else []
        if error:
            warnings.append(f"Could not get the requirement list: {error}")

        # simple cross-check so a skipped requirement is at least visible
        found = jira.search(jira.in_scope(f'labels = "kind-requirement" AND labels = "release-{release}"'),
                            limit=200)
        for issue in found:
            sid = next(l[4:] for l in issue["fields"]["labels"] if l.startswith("src-"))
            if sid not in ids and issue["key"] not in ids:
                warnings.append(f"{sid} is a {release} requirement but the model did not pick it.")

    # Step 2: one fresh conversation per requirement, then the code double-check
    results = []
    for n, rid in enumerate(ids, 1):
        print(f"[{n}/{len(ids)}] {rid}")
        try:
            issue = tools.issue(rid)
        except Exception as e:
            warnings.append(f"{rid}: could not read it ({e})")
            continue
        req = rules.read_requirement(issue["fields"]["description"])

        answer, error = agent.run(
            f"assess {rid}",
            f"Target release: {release}. Assess requirement {rid} (Jira {issue['key']}). Read it, follow "
            "its links, read all comments, check each result with check_build, then call record_decision.",
            RECORD_DECISION, tools, max_steps)
        if error:
            answer = {"decision": "INSUFFICIENT_EVIDENCE", "evidence_used": [], "outstanding": "Run again.",
                      "explanation": "No decision because the investigation did not finish."}

        checked = report.double_check(answer, req, tools)
        results.append({**checked, **req, "id": rid, "key": issue["key"], "error": error,
                        "title": issue["fields"]["summary"].split("] ", 1)[-1],
                        "status": short(issue).get("recorded_status", "")})
        print(f"     {checked['decision']}")

    # Step 3: overall decision + files
    overall = rules.overall(results)
    if not results or any(r["error"] for r in results):
        warnings.append("Incomplete run: some requirements have no model decision.")
    metrics = {"model": agent.model, "seconds": log.seconds(), "requirements": len(results),
               **{k: log.totals[k] for k in ("model_calls", "tool_calls", "tool_errors",
                                             "input_tokens", "output_tokens")}}
    run = {"release": release, "project": jira.project, "label": jira.label,
           "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "overall": overall, "warnings": warnings, "metrics": metrics, "results": results}
    log.write(type="done", overall=overall, metrics=metrics)
    path = report.write(folder, run, tools)
    print(f"\n{overall}\nReport: {path}")


if __name__ == "__main__":
    main()
