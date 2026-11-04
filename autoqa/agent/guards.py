"""Guards emit text or stop; they never choose or modify a game action."""

import io
import time
from collections import deque

import numpy as np
from PIL import Image

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
        self.repeat_warning = None
        self.cycle_warning = None
        self.views = deque(maxlen=48)
        self.last_cycle_warning = -4

    @property
    def warning(self):
        return "\n".join(w for w in (self.repeat_warning, self.cycle_warning) if w) or None

    def observe_view(self, frame_jpeg, decision):
        """Compare visible pixels only, never infer position or select an action."""
        if self.views and decision <= self.views[-1][0]:
            return None
        with Image.open(io.BytesIO(frame_jpeg)) as image:
            # Exclude the bottom quarter (HUD/most weapon motion).
            view = (
                np.array(
                    image.convert("RGB")
                    .crop((0, 0, image.width, image.height * 3 // 4))
                    .resize((32, 24), Image.Resampling.BILINEAR),
                    dtype=float,
                )
                / 255
            )
        matches = []
        for old_decision, old_view in reversed(self.views):
            distance = float(np.abs(view - old_view).mean())
            if (
                decision - old_decision >= 6
                and distance <= 0.04
                and all(abs(old_decision - d) >= 3 for d, _ in matches)
            ):
                matches.append((old_decision, distance))
                if len(matches) == 2:
                    break
        self.views.append((decision, view))
        self.cycle_warning = None
        if len(matches) < 2 or decision - self.last_cycle_warning < 4:
            return None
        self.last_cycle_warning = decision
        self.cycle_warning = (
            f"Visual-cycle warning: this view resembles decisions {matches[0][0]} and "
            f"{matches[1][0]} despite intervening actions. Similar-looking places can differ; "
            "this is not proof of the same location or a blocked path. Reconsider your "
            "landmark/outcome ledger and record what the visible results support before "
            "choosing your next action."
        )
        return dict(
            text=self.cycle_warning,
            matched_decisions=[d for d, _ in matches],
            pixel_mae=[distance for _, distance in matches],
            threshold=0.04,
            min_gap=6,
            source="visible_rgb_only",
        )

    def remaining_seconds(self):
        return max(0.0, self.config.wall_seconds - (time.monotonic() - self.started))

    def budget_exhausted(self):
        return self.total_usd >= self.config.max_usd or self.remaining_seconds() <= 0

    def observe(self, action, feedback):
        signature = (
            tuple((m.type, m.amount, getattr(m, "fire", False)) for m in action.moves),
            action.run,
        )
        self.repeats = (
            self.repeats + 1
            if signature == self.last_signature and feedback.frame_change_pct < 2
            else int(feedback.frame_change_pct < 2)
        )
        self.last_signature = signature
        self.repeat_warning = STUCK_WARNING if self.repeats >= 3 else None
        return self.repeat_warning
