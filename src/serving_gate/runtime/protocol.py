"""Normalize two deployed worker wire dialects into lifecycle events."""
import json

from .errors import WorkerError


def count(value, positive=False):
    if type(value) is not int or value < int(positive):
        raise WorkerError("protocol_error")
    return value


class WorkerProtocol:
    def __init__(self, version, max_tokens):
        if version not in {"v1", "v2"}:
            raise WorkerError("protocol_error")
        self.version = version
        self.max_tokens = max_tokens
        self.seen = {}
        self.tokens = 0
        self.complete = False

    def decode(self, event, raw):
        try:
            value = json.loads(raw)
            if not isinstance(value, dict): raise ValueError()
            if self.version == "v1":
                kind = {"token": "delta", "done": "complete", "error": "failure"}.get(event)
                normalized = ({"seq": value["seq"], "text": value["text"], "tokens": value["tokens"]}
                              if kind == "delta" else value)
            else:
                if event != "message": raise ValueError()
                kind = value["type"]
                if kind == "delta":
                    normalized = {"seq": value["index"], "text": value["content"], "tokens": value["token_count"]}
                elif kind == "complete": normalized = value["usage"]
                else: normalized = {"code": value["code"], "retryable": value["can_retry"]}
            if kind == "delta":
                seq = count(normalized["seq"])
                tokens = count(normalized["tokens"], True)
                if not isinstance(normalized["text"], str): raise ValueError()
                if seq in self.seen:
                    if self.seen[seq] != normalized: raise ValueError()
                    return ("delta", {"text": normalized["text"], "token_count": tokens})
                if seq != len(self.seen): raise ValueError()
                if self.tokens + tokens > self.max_tokens: raise ValueError()
                self.seen[seq] = normalized
                self.tokens += tokens
                return ("delta", {"text": normalized["text"], "token_count": tokens})
            if kind == "complete":
                usage = {key: count(normalized[key]) for key in ("input_tokens", "output_tokens")}
                if usage["output_tokens"] != self.tokens: raise ValueError()
                self.complete = True
                return ("complete", usage)
            if kind == "failure":
                if not isinstance(normalized["code"], str) or type(normalized["retryable"]) is not bool:
                    raise ValueError()
                raise WorkerError("worker_failure", normalized["retryable"])
            raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise WorkerError("protocol_error") from exc
