"""Raw-wire evidence must distinguish empty output from parser data loss."""

from types import SimpleNamespace

import pytest
from PIL import Image

from autoqa.m0.api import image_input
from autoqa.m0.sam_audit import SamAudit, request_evidence, sanitize


@pytest.fixture
def audit(tmp_path):
    path = tmp_path / "input.jpg"
    Image.new("RGB", (640, 480), "red").save(path, quality=85)
    payload = dict(model="sam-3.1", input=image_input(path, "red square"), stream=True)
    return SamAudit(payload, secret="dummy-secret", path=tmp_path / "trace.json")


def test_request_encoding_is_validated_and_image_payload_redacted(audit):
    request = audit.data["request"]
    assert request["image"]["jpeg_magic"] == "ffd8ff"
    assert request["image"]["size"] == [640, 480]
    assert request["image"]["mode"] == "RGB"
    assert request["schema"]["input"][0]["content"][1]["image_url"] == "[REDACTED]"


def test_invalid_base64_is_rejected():
    payload = dict(input=[dict(content=[{}, dict(image_url="data:image/jpeg;base64,!!!")])])
    with pytest.raises(ValueError):
        request_evidence(payload)


def complete(text):
    return dict(
        type="response.completed",
        response=dict(
            status="completed",
            output=[dict(type="message", content=[dict(type="output_text", text=text)])],
        ),
    )


def test_empty_server_output_is_recorded_as_empty_not_live_decode(audit):
    audit.observe(complete(""))
    summary = audit.finish(
        SimpleNamespace(
            raw_output="", records=[], diagnostics=[], outcome=SimpleNamespace(status="completed")
        )
    )
    assert summary["genuinely_empty_model_output"]
    assert summary["raw_text_matches_parser"]
    assert summary["delta_events"] == 0
    assert "live_mask_decoding_verified" not in summary


def test_parser_discarding_nonempty_wire_is_rejected(audit):
    audit.observe(dict(type="response.output_text.delta", delta="mask wire"))
    audit.observe(complete("mask wire"))
    with pytest.raises(ValueError, match="differs from parser"):
        audit.finish(
            SimpleNamespace(
                raw_output="",
                records=[],
                diagnostics=[],
                outcome=SimpleNamespace(status="completed"),
            )
        )
    audit.save()
    assert "mask wire" in audit.path.read_text()


def test_sanitization_removes_secrets_from_nested_events():
    event = {
        "headers": {"Authorization": "dummy-secret"},
        "error": {"message": "echo dummy-secret"},
    }
    assert "dummy-secret" not in str(sanitize(event, "dummy-secret"))
