"""Checks your .env before you import or run anything.   python -m app.check"""
import openai

from app import config
from app.jira import Jira


def main():
    jira = Jira()
    me = jira.call("GET", "/rest/api/2/myself")
    print(f"OK  Jira login: {me['displayName']}")

    project = jira.call("GET", f"/rest/api/2/project/{jira.project}")
    types = [t["name"] for t in project["issueTypes"]]
    print(f"OK  Project {project['key']} ({project['name']}), issue types: {types}")
    if config.get("JIRA_ISSUE_TYPE", "Task") not in types:
        print("!!  JIRA_ISSUE_TYPE is not one of these; change it in .env")

    imported = jira.search(jira.in_scope(), limit=1)
    print(f"OK  Search works ({'dataset already imported' if imported else 'nothing imported yet'})")

    client = openai.OpenAI(api_key=config.get("LLM_API_KEY"), base_url=config.get("LLM_BASE_URL"))
    model = config.get("LLM_MODEL")
    try:
        reply = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Reply with the word OK."}])
        print(f"OK  Model {model} answered: {reply.choices[0].message.content!r}")
    except openai.APIError as e:
        print(f"!!  Model {model} failed: {e}")
        try:
            names = sorted(m.id for m in client.models.list())
            print("    Models your key can use:", ", ".join(names))
        except openai.APIError:
            pass


if __name__ == "__main__":
    main()
