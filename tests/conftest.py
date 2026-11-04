import io

import pytest
from PIL import Image

from autoqa.doom.types import AgentObservation


@pytest.fixture
def observation():
    out = io.BytesIO()
    Image.new("RGB", (640, 480), "black").save(out, format="JPEG")
    return AgentObservation(0, 0, 2100, out.getvalue(), (), 0.0, None, "fixture")
