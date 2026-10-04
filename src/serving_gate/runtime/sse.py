"""Incremental SSE framing, independent of transport reads and worker dialect."""
import codecs
import json

from .errors import WorkerError


class SSEDecoder:
    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.buffer = ""
        self.event = "message"
        self.data = []
        self.identifier = None

    def feed(self, chunk, final=False):
        try:
            self.buffer += chunk.decode("utf-8", errors="replace")
        except UnicodeDecodeError as exc:
            raise WorkerError("protocol_error") from exc
        frames = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.removesuffix("\r")
            if line == "":
                if self.data:
                    frames.append((self.event, "\n".join(self.data), self.identifier))
                self.event, self.data, self.identifier = "message", [], None
            elif not line.startswith(":"):
                key, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if key == "data": self.data.append(value)
                elif key == "event": self.event = value
                elif key == "id": self.identifier = value
        # An unterminated final frame is not a complete business event.
        return frames


def frame(event, data, identifier=None):
    prefix = "" if identifier is None else f"id: {identifier}\n"
    return (prefix + "event: " + event + "\ndata: " +
            json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")
