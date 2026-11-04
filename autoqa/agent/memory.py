"""Only model-written notes and player-observable action feedback."""

from collections import deque


class Memory:
    def __init__(self):
        self.scratchpad = ""
        self.history = deque(maxlen=20)

    def note(self, text, mode):
        self.scratchpad = (text if mode == "replace" else self.scratchpad + "\n" + text)[-1500:]
        return {"scratchpad": self.scratchpad}
