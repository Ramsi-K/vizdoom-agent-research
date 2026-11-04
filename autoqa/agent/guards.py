"""Guards emit text or stop; they never choose or modify a game action."""

import time

STUCK_WARNING = (
    "Your last three identical actions changed nothing on screen. Try something different."
)


class Guards:
    def __init__(self, config):
        self.config = config
        self.started = time.monotonic()
        self.total_usd = 0.0
        self.empty_outputs = 0
        self.idle_decisions = 0
        self.last_signature = None
        self.repeats = 0
        self.warning = None

    def remaining_seconds(self):
        return max(0.0, self.config.wall_seconds - (time.monotonic() - self.started))

    def budget_exhausted(self):
        return self.total_usd >= self.config.max_usd or self.remaining_seconds() <= 0

    def observe(self, action, feedback):
        signature = tuple((m.type, m.amount) for m in action.moves), action.run
        self.repeats = (
            self.repeats + 1
            if signature == self.last_signature and feedback.frame_change_pct < 2
            else int(feedback.frame_change_pct < 2)
        )
        self.last_signature = signature
        self.warning = STUCK_WARNING if self.repeats >= 3 else None
        return self.warning
