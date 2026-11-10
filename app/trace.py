"""Writes trace.jsonl (one line per model call / tool call) and keeps totals."""
import json
import time
from collections import Counter


class Trace:
    def __init__(self, path):
        self.file = open(path, "w", encoding="utf-8")
        self.start = time.time()
        self.totals = Counter()

    def write(self, **event):
        event = {"t": round(time.time() - self.start, 1), **event}
        self.file.write(json.dumps(event, default=str) + "\n")
        self.file.flush()

    def model(self, phase, usage, seconds):
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        self.totals.update(model_calls=1, input_tokens=tokens_in, output_tokens=tokens_out)
        self.write(type="model_call", phase=phase, input_tokens=tokens_in,
                   output_tokens=tokens_out, seconds=round(seconds, 1))

    def tool(self, phase, name, args, ok, result, seconds):
        self.totals.update(tool_calls=1, tool_errors=0 if ok else 1)
        self.write(type="tool_call", phase=phase, tool=name, input=args, ok=ok,
                   seconds=round(seconds, 2), result=result[:300])

    def seconds(self):
        return round(time.time() - self.start)
