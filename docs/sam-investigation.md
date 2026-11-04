# SAM 3.1 empty-response investigation

Execution: 2026-11-04T06:40:10.502319+00:00. Local evidence: `runs/m0_sam_20261104T064002Z/summary.json`.

| Case | Check | Raw characters | Parsed records | Decoded masks | Foreground pixels | Wire equals parser |
| --- | --- | --- | --- | --- | --- | --- |
| red-square | passed | 150 | 2 | 1 | [66564] | True |
| square | passed | 166 | 2 | 1 | [66613] | True |
| absent-yellow-square | passed | 0 | 0 | 0 | [] | True |
| doom-green-armor | passed | 0 | 0 | 0 | [] | True |
| doom-armor-box | passed | 188 | 2 | 1 | [2061] | True |

## Findings

The Doom concept request returned genuinely empty model output: zero text-delta events and an empty terminal `output_text`, with `status=completed`. The parser matched the wire exactly; it did not discard a mask.

Both square prompts and the Doom box prompt produced nonempty `one_bit` masks decoded successfully. The Doom box mask contained 2,061 foreground pixels with confidence 0.921875. Live mask decoding is verified by these nonempty controls. Text-guided Doom armor detection failed on this fixture; these controls do not establish the underlying cause.

The synthetic control is a large red square on white, saved as a real 640×480 RGB JPEG at quality 85. The negative control names an absent yellow square.
The Doom box control supplies the engine's visible armor bounding box. It tests the wire decoder independently of concept recognition and is not a gameplay policy.

## Schema and encoding verification

Every trace records the request schema with the data URL redacted, image MIME prefix, base64 validation, decoded byte count, SHA-256, JPEG magic, RGB mode and dimensions.
Requests use `model=sam-3.1`, one user message with `type=message`, one `input_text`, one JPEG `input_image`, `stream=true`, and string metadata `mask_encoding=one_bit`, `include_confidence=true`.
Concepts are single noun phrases; box prompts use the official `build_object_prompt` helper and are never combined with concept text.

## Raw response versus parser

Sanitized traces under `docs/sam-traces/` include every server event before adaptation, the complete terminal response structure, output item types, finish status, raw text, parser diagnostics, and decoded mask counts.
Meta's documented no-match response completes with empty `output_text` and no delta events. An empty completed stream verifies transport only. It never verifies live mask decoding.
The adapter finalizes a lane on `response.content_part.done` when `response.output_text.done` is absent, as Meta's Python cookbook demonstrates. Each trace compares unadapted wire text with parser output and rejects a mismatch.
`live_mask_decoding_verified` is true only after decoding a mask with nonzero foreground pixels. Synthetic offline decoder tests are separate from live evidence.

## Official references

- [segmenting](https://dev.meta.ai/docs/sam/segmenting)
- [reading-segmentation](https://dev.meta.ai/docs/sam/reading-segmentation)
- [client-libraries](https://dev.meta.ai/docs/sam/client-libraries)
- [sam_api.py](https://github.com/meta-models/meta-model-cookbook/blob/main/07_sam/01_api_basics/python/sam_api.py)
