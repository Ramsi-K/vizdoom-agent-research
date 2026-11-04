"""Only model-written notes and player-observable action feedback."""

import json
from collections import deque


class Memory:
    def __init__(self):
        self.scratchpad = ""
        self.history = deque(maxlen=20)

    def note(self, text, mode):
        self.scratchpad = (text if mode == "replace" else self.scratchpad + "\n" + text)[-1500:]
        return {"scratchpad": self.scratchpad}

    def record(self, decision, text):
        """One bounded row per decision, including tool evidence before its act."""
        prefix = f"Decision {decision}: "
        if self.history and self.history[-1].startswith(prefix):
            body = self.history[-1][len(prefix) :] + "; " + text
            self.history[-1] = prefix + body[-(1600 - len(prefix)) :]
        else:
            self.history.append((prefix + text)[:1600])

    def record_locate(self, decision, concept, result):
        # Preserve uncertainty across decisions without creating a third memory store.
        summary = dict(
            found=result.get("found", False),
            status=result.get("status", "completed"),
            candidates=[
                {key: match[key] for key in ("bearing_deg", "confidence", "vertical_band")}
                for match in result.get("matches", [])[:3]
            ],
            identity_verified=False,
        )
        self.record(decision, f"locate {concept!r}: {json.dumps(summary)}")
