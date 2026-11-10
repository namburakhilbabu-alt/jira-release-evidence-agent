"""Talks to the Jira Cloud REST API (v2)."""
import time

import requests

from app import config


class JiraError(Exception):
    pass


class Jira:
    def __init__(self, project=None, label=None):
        self.url = config.get("JIRA_URL").rstrip("/")
        self.project = project or config.get("JIRA_PROJECT")
        self.label = label or config.get("DATASET_LABEL")
        self.session = requests.Session()
        self.session.auth = (config.get("JIRA_EMAIL"), config.get("JIRA_API_TOKEN"))
        self.session.headers["Accept"] = "application/json"

    def call(self, method, path, **kwargs):
        # Jira answers 429 when we go too fast; wait and try again.
        for attempt in range(6):
            try:
                resp = self.session.request(method, self.url + path, timeout=30, **kwargs)
            except requests.RequestException as e:
                error = str(e)
            else:
                if resp.ok:
                    return resp.json() if resp.content else {}
                if resp.status_code != 429 and resp.status_code < 500:
                    raise JiraError(f"{method} {path}: {resp.status_code} {resp.text[:300]}")
                error = f"HTTP {resp.status_code}"
            time.sleep(min(2 ** attempt, 30))
        raise JiraError(f"{method} {path} failed after retries: {error}")

    # ---- reading ----

    def search(self, jql, fields=("summary", "labels"), limit=50):
        issues, token = [], None
        while len(issues) < limit:
            body = {"jql": jql, "fields": list(fields), "maxResults": min(100, limit - len(issues))}
            if token:
                body["nextPageToken"] = token
            page = self.call("POST", "/rest/api/2/search/jql", json=body)
            issues += page.get("issues", [])
            token = page.get("nextPageToken")
            if not token:
                break
        return issues

    def in_scope(self, extra=""):
        """JQL that only matches this project + this dataset's label."""
        jql = f'project = "{self.project}" AND labels = "{self.label}"'
        return f"{jql} AND {extra}" if extra else jql

    def key_for(self, ref):
        """'REQ-12' or 'TYN-5' -> 'TYN-5'"""
        ref = ref.strip().upper()
        if ref.startswith(self.project.upper() + "-"):
            return ref
        found = self.search(self.in_scope(f'labels = "src-{ref}"'), limit=1)
        if not found:
            raise JiraError(f"{ref} not found in this dataset")
        return found[0]["key"]

    def get_issue(self, ref):
        key = self.key_for(ref)
        fields = "summary,labels,description,issuelinks"
        issue = self.call("GET", f"/rest/api/2/issue/{key}", params={"fields": fields})
        if self.label not in issue["fields"]["labels"]:
            raise JiraError(f"{key} is not part of dataset {self.label}")
        return issue

    def get_comments(self, key):
        comments, start = [], 0
        while True:
            page = self.call("GET", f"/rest/api/2/issue/{key}/comment",
                             params={"startAt": start, "maxResults": 100})
            for c in page["comments"]:
                comments.append({"id": str(c["id"]), "text": c["body"]})
            start += len(page["comments"])
            if not page["comments"] or start >= page["total"]:
                return comments

    def link_url(self, key, comment_id=None):
        url = f"{self.url}/browse/{key}"
        return f"{url}?focusedCommentId={comment_id}" if comment_id else url

    # ---- writing (importer) ----

    def create_issue(self, summary, description, labels):
        fields = {
            "project": {"key": self.project},
            "issuetype": {"name": config.get("JIRA_ISSUE_TYPE", "Task")},
            "summary": summary,
            "description": description,
            "labels": labels,
        }
        return self.call("POST", "/rest/api/2/issue", json={"fields": fields})["key"]

    def add_comment(self, key, text):
        self.call("POST", f"/rest/api/2/issue/{key}/comment", json={"body": text})

    def add_link(self, from_key, to_key):
        self.call("POST", "/rest/api/2/issueLink", json={
            "type": {"name": config.get("JIRA_LINK_TYPE", "Relates")},
            "inwardIssue": {"key": from_key},
            "outwardIssue": {"key": to_key},
        })

    def set_status(self, key, status):
        """Move to a status with this name if the workflow has one. Returns True if it did."""
        transitions = self.call("GET", f"/rest/api/2/issue/{key}/transitions")["transitions"]
        for t in transitions:
            if t["to"]["name"].lower() == status.lower():
                self.call("POST", f"/rest/api/2/issue/{key}/transitions", json={"transition": {"id": t["id"]}})
                return True
        return False
