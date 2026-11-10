"""Imports dataset.json into Jira. Safe to run twice: existing issues and comments are skipped.

    python -m app.importer inputs/dataset.json
    python -m app.importer inputs/dataset.json --dry-run
"""
import argparse
import json
import os
import re
import time

from app.jira import Jira


def label(prefix, value):
    # Jira labels can't contain spaces
    return prefix + "-" + re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-")


def to_jira(record, dataset_label):
    """What one dataset record looks like as a Jira issue."""
    sid = record["source_id"]
    labels = [dataset_label, label("src", sid), label("kind", record["kind"])]
    if record["kind"] == "requirement":
        labels.append("rr-requirement")  # the manifest says requirements carry this label
    if record.get("release"):
        labels.append(label("release", record["release"]))
    if record.get("recorded_status"):
        labels.append(label("status", record["recorded_status"]))

    header = (f"Source ID: {sid}\n"
              f"Kind: {record['kind']}\n"
              f"Release: {record.get('release', '')}\n"
              f"Recorded status: {record.get('recorded_status', '')}\n"
              f"Links: {', '.join(record.get('links', [])) or 'none'}\n"
              "----\n")

    # The comment text is kept exactly as in the dataset. It already starts with the
    # scenario's "Recorded event <time> | actor <name> | role <role>" line.
    comments = [f"[{sid} comment {n}]\n{text}" for n, text in enumerate(record["comments"], 1)]

    return {
        "summary": f"[{sid}] {record['summary']}"[:250],
        "description": header + record.get("description", ""),
        "labels": labels,
        "comments": comments,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.dataset, encoding="utf-8") as f:
        data = json.load(f)
    dataset_label = data["dataset_label"]
    records = data["records"]
    print(f"{len(records)} records, {sum(len(r['comments']) for r in records)} comments, label {dataset_label}")

    if args.dry_run:
        print(json.dumps(to_jira(records[0], dataset_label), indent=2))
        return

    jira = Jira(label=dataset_label)
    start = time.time()

    # What is already there? source id -> Jira key
    keys = {}
    for issue in jira.search(jira.in_scope(), limit=100000):
        for lab in issue["fields"]["labels"]:
            if lab.startswith("src-"):
                keys[lab[4:]] = issue["key"]
    print(f"{len(keys)} already imported")

    stats = {"created": 0, "comments": 0, "links": 0, "status_not_set": []}

    for n, record in enumerate(records, 1):
        sid = record["source_id"]
        issue = to_jira(record, dataset_label)

        if sid in keys:
            done = {c["text"].split("\n")[0] for c in jira.get_comments(keys[sid])}
        else:
            keys[sid] = jira.create_issue(issue["summary"], issue["description"], issue["labels"])
            stats["created"] += 1
            done = set()
            status = record.get("recorded_status")
            if status and not jira.set_status(keys[sid], status):
                stats["status_not_set"].append(f"{sid}: {status}")

        for text in issue["comments"]:
            if text.split("\n")[0] not in done:
                jira.add_comment(keys[sid], text)
                stats["comments"] += 1

        if n % 25 == 0:
            print(f"  {n}/{len(records)}  ({time.time() - start:.0f}s)")

    print("Linking...")
    for record in records:
        if not record.get("links"):
            continue
        key = keys[record["source_id"]]
        issue = jira.call("GET", f"/rest/api/2/issue/{key}", params={"fields": "issuelinks"})
        linked = {(l.get("outwardIssue") or l.get("inwardIssue"))["key"] for l in issue["fields"]["issuelinks"]}
        for target in record["links"]:
            if target in keys and keys[target] not in linked:
                jira.add_link(key, keys[target])
                stats["links"] += 1

    stats["seconds"] = round(time.time() - start)
    stats["keys"] = keys
    os.makedirs("outputs", exist_ok=True)
    with open("outputs/import_summary.json", "w") as f:
        json.dump(stats, f, indent=2)
    print({k: v for k, v in stats.items() if k != "keys"})
    print("Saved outputs/import_summary.json")


if __name__ == "__main__":
    main()
