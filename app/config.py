import os

from dotenv import load_dotenv

load_dotenv()


def get(name, default=None):
    value = os.getenv(name, "").strip()
    if value:
        return value
    if default is not None:
        return default
    raise SystemExit(f"{name} is missing in .env (see .env.example)")
