"""Sanitized SAM wire evidence and explicit empty-output/parser checks."""

import base64
import hashlib
import io
import json
from pathlib import Path

from PIL import Image


def sanitize(value, secret=""):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower()
            in {
                "authorization",
                "api_key",
                "encrypted_content",
                "headers",
                "image_url",
            }
            else sanitize(item, secret)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item, secret) for item in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, "[REDACTED]")
    return value


def request_evidence(payload):
    url = payload["input"][0]["content"][1]["image_url"]
    prefix, encoded = url.split(",", 1)
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        decoded = dict(format=image.format, mode=image.mode, size=list(image.size))
    return dict(
        schema=sanitize(payload),
        image=dict(
            data_url_prefix=prefix,
            base64_characters=len(encoded),
            decoded_bytes=len(raw),
            decoded_sha256=hashlib.sha256(raw).hexdigest(),
            jpeg_magic=raw[:3].hex(),
            **decoded,
        ),
    )


class SamAudit:
    def __init__(self, payload, secret="", path: Path | None = None):
        self.secret, self.path = secret, path
        self.data = dict(request=request_evidence(payload), events=[])

    def observe(self, event):
        self.data["events"].append(sanitize(event, self.secret))

    def finish(self, parsed):
        events = self.data["events"]
        delta = "".join(
            e.get("delta", "") for e in events if e["type"] == "response.output_text.delta"
        )
        terminal = next(
            (e["response"] for e in reversed(events) if e["type"] == "response.completed"), {}
        )
        output = terminal.get("output", [])
        final_text = "".join(
            part.get("text", "")
            for item in output
            for part in item.get("content", [])
            if part["type"] == "output_text"
        )
        summary = dict(
            finish_status=terminal.get("status"),
            output_item_types=[i["type"] for i in output],
            delta_events=sum(e["type"] == "response.output_text.delta" for e in events),
            delta_characters=len(delta),
            terminal_text_characters=len(final_text),
            parser_text_characters=len(parsed.raw_output),
            parser_record_count=len(parsed.records),
            parser_status=parsed.outcome.status,
            parser_diagnostics=[
                dict(severity=d.severity, code=d.code, message=d.message)
                for d in parsed.diagnostics
            ],
            raw_text_matches_parser=(delta or final_text) == parsed.raw_output,
            terminal_text_matches_parser=final_text == parsed.raw_output,
            genuinely_empty_model_output=not delta and not final_text,
        )
        self.data["summary"] = summary
        if not summary["raw_text_matches_parser"] or not summary["terminal_text_matches_parser"]:
            raise ValueError(
                "SAM wire text differs from parser output; mask decoding is not verified"
            )
        if summary["finish_status"] != "completed":
            raise ValueError("No completed SAM terminal response was observed")
        return summary

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(sanitize(self.data, self.secret), indent=2) + "\n")
