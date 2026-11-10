"""Fixed rules that don't need AI: reading build details and the overall decision."""
import json
import re

# How build details appear in comments, e.g.
# "release R2.4; commit b7a91f2; service atlas-assistant; environment production; prompt p7"
PATTERNS = {
    "release": r"\brelease:?\s+([A-Za-z]*\d+(?:\.\d+)*)",
    "commit": r"\bcommit:?\s+([0-9a-f]{7,40})\b",
    "service": r"\bservice:?\s+([a-z0-9][\w-]*)",
    "environment": r"\b(?:environment|approval scope):?\s+([a-z][\w-]*)",
    "prompt": r"\bprompt(?:\s+version|\s+bundle)?:?\s+(p\d+)\b",
}


def read_requirement(text):
    target = {}
    m = re.search(r"Target:\s*(\{.*?\})", text, re.S)
    if m:
        target = json.loads(m.group(1))

    m = re.search(r"Mandatory:\s*(\w+)", text)
    mandatory = m.group(1).lower() in ("yes", "true") if m else None

    m = re.search(r"Acceptance criterion:\s*(.+)", text)
    criterion = m.group(1).strip() if m else ""

    # Manifest rule: only prompt-quality checks have to match the prompt version.
    return {"target": target, "mandatory": mandatory, "criterion": criterion,
            "needs_prompt": "prompt" in criterion.lower()}


def check_build(target, text, needs_prompt):
    """Does this comment talk about the target build?

    applies         every needed field is there and matches
    does_not_apply  some field is different (staging, R2.3, another commit...)
    unclear         some needed field is not mentioned
    """
    fields, wrong, missing = {}, [], []
    for name, wanted in target.items():
        if name not in PATTERNS:
            continue
        found = sorted(set(v.lower() for v in re.findall(PATTERNS[name], text, re.I)))
        wanted = str(wanted).lower()

        if not found:
            if name == "prompt" and not needs_prompt:
                fields[name] = "not mentioned (not needed)"
                continue
            fields[name] = "not mentioned"
            missing.append(name)
        elif found == [wanted]:
            fields[name] = "match"
        elif wanted in found:
            fields[name] = "several values: " + ", ".join(found)
            missing.append(name)
        else:
            fields[name] = "WRONG: " + ", ".join(found)
            wrong.append(name)

    if wrong:
        verdict = "does_not_apply"
    elif missing:
        verdict = "unclear"
    else:
        verdict = "applies"
    return {"verdict": verdict, "fields": fields}


def who_and_when(text):
    m = re.search(r"Recorded event (\S+) \| actor ([^|]+?) \| role ([^\n]+)", text)
    if not m:
        return ""
    return f"{m.group(2)} ({m.group(3).strip()}), {m.group(1)}"


def overall(results):
    # Unknown "mandatory" counts as mandatory, to be safe.
    decisions = [r["decision"] for r in results if r["mandatory"] is not False]
    if not decisions:
        return "NEEDS_REVIEW"
    if "FAIL" in decisions:
        return "BLOCKED"
    if "INSUFFICIENT_EVIDENCE" in decisions:
        return "NEEDS_REVIEW"
    return "READY"
