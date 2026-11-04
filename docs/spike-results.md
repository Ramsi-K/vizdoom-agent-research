# M0 feasibility findings

Executed: 20261104T064312.342534Z (UTC).
Evidence: `runs/m0_spike_20261104T064312.342534Z/summary.json` (local, ignored).

`passed` means the stated probe ran successfully. `partial` and `could not verify` do not satisfy the full acceptance criterion.

| Section 2.5 item | Result | Limitation |
| --- | --- | --- |
| 1. Model access, credits, rate limits | partial | Credits remaining could not be verified; inspect measured model access and rate headers. |
| 2. Stateless image/tool/reasoning round trip | passed | — |
| 3. Reasoning-effort latency and token usage | passed | — |
| 4. Bundled IWAD and headless MAP01 | passed | — |
| 5. -nomonsters and replay synchronization | passed | — |
| 6. Turn, movement and FOV calibration | passed | — |
| 7. Exact actor and label names | passed | — |
| 8. 1280x720 HUD demo replay and H.264 | passed | — |
| 9. SAM streaming and 20-frame perception benchmark | partial | Door/switch masks require manual semantic annotation; absent actor masks are unscored, never successes. |
| 10. Files upload and video understanding | passed | — |

## Findings

Measured camera FOV: **90.0°**. Binary left/right turns take **[29, 29] tics** to pass 90° from rest. Positive delta angles turn right; a configured 30° button maximum clips larger turns.
Eighth-tic forward displacement: `{'walk': 4.5419464111328125, 'run': 9.083831787109375}` map units/tic, including acceleration. The full traces are in `configs/calibration.json`.
SAM text concepts: **100** completed requests on **20** Doom frames, **0** masks. IoU hit rate on **20** actor-annotated requests: **0.0** at threshold 0.5; unannotated door/switch cases are excluded.
Separate box control: live decoding verified = **True**, foreground pixels = **[2061]**, IoU = **0.9248582230623819**. This proves decoder operation, not text-guided object recognition.

## Measurements

The JSON below is generated from this execution, not illustrative specification values.
Secrets, request headers, encrypted reasoning and image payloads are excluded.

```json
{
  "timestamp_utc": "20261104T064312.342534Z",
  "artifacts": "runs/m0_spike_20261104T064312.342534Z",
  "wall_seconds": 208.99092547699547,
  "mode": "spike",
  "checks": {
    "1": {
      "status": "partial",
      "reason": "Credits remaining could not be verified; inspect measured model access and rate headers.",
      "measured": {
        "model_ids": [
          "muse-image-1.0",
          "muse-spark-1.1",
          "muse-spark-1.2",
          "muse-spark-1.2-contributor",
          "muse-spark-1.3",
          "muse-spark-1.3-contributor",
          "muse-voice-transcribe-1.0",
          "muse-wonder-1.0",
          "sam-3.1"
        ],
        "missing": [],
        "rate_limits": {},
        "credits_remaining": null,
        "credits_note": "No documented balance endpoint; dashboard balance could not be verified."
      }
    },
    "2": {
      "status": "passed",
      "reason": null,
      "measured": {
        "first": {
          "latency_ms": 5951.400334997743,
          "usage": {
            "input_tokens": 1086,
            "input_tokens_details": {
              "cache_write_tokens": null,
              "cached_tokens": 1009,
              "cache_write_tokens_details": null
            },
            "output_tokens": 517,
            "output_tokens_details": {
              "reasoning_tokens": 380
            },
            "total_tokens": 1603
          },
          "rate_limits": {
            "x-ratelimit-limit-requests": "3000",
            "x-ratelimit-remaining-requests": "2991",
            "x-ratelimit-limit-tokens": "4000000",
            "x-ratelimit-remaining-tokens": "3997553"
          }
        },
        "second": {
          "latency_ms": 2389.799636999669,
          "usage": {
            "input_tokens": 1635,
            "input_tokens_details": {
              "cache_write_tokens": null,
              "cached_tokens": 1009,
              "cache_write_tokens_details": null
            },
            "output_tokens": 66,
            "output_tokens_details": {
              "reasoning_tokens": 41
            },
            "total_tokens": 1701
          },
          "rate_limits": {
            "x-ratelimit-limit-requests": "3000",
            "x-ratelimit-remaining-requests": "2990",
            "x-ratelimit-limit-tokens": "4000000",
            "x-ratelimit-remaining-tokens": "3996959"
          }
        },
        "second_status": "completed",
        "output_types": [
          "reasoning",
          "message",
          "function_call"
        ],
        "function_calls": 1,
        "encrypted_reasoning_replayed": true,
        "game_actions_executed": 0
      }
    },
    "3": {
      "status": "passed",
      "reason": null,
      "measured": {
        "efforts": {
          "minimal": {
            "status": "passed",
            "reason": null,
            "measured": {
              "latency_ms": 2563.069171999814,
              "usage": {
                "input_tokens": 407,
                "input_tokens_details": {
                  "cache_write_tokens": null,
                  "cached_tokens": 0,
                  "cache_write_tokens_details": null
                },
                "output_tokens": 172,
                "output_tokens_details": {
                  "reasoning_tokens": 72
                },
                "total_tokens": 579
              },
              "rate_limits": {
                "x-ratelimit-limit-requests": "3000",
                "x-ratelimit-remaining-requests": "2989",
                "x-ratelimit-limit-tokens": "4000000",
                "x-ratelimit-remaining-tokens": "3996267"
              }
            }
          },
          "low": {
            "status": "passed",
            "reason": null,
            "measured": {
              "latency_ms": 5625.93964899861,
              "usage": {
                "input_tokens": 407,
                "input_tokens_details": {
                  "cache_write_tokens": null,
                  "cached_tokens": 0,
                  "cache_write_tokens_details": null
                },
                "output_tokens": 450,
                "output_tokens_details": {
                  "reasoning_tokens": 382
                },
                "total_tokens": 857
              },
              "rate_limits": {
                "x-ratelimit-limit-requests": "3000",
                "x-ratelimit-remaining-requests": "2989",
                "x-ratelimit-limit-tokens": "4000000",
                "x-ratelimit-remaining-tokens": "3995688"
              }
            }
          },
          "medium": {
            "status": "passed",
            "reason": null,
            "measured": {
              "latency_ms": 6797.316494004917,
              "usage": {
                "input_tokens": 407,
                "input_tokens_details": {
                  "cache_write_tokens": null,
                  "cached_tokens": 0,
                  "cache_write_tokens_details": null
                },
                "output_tokens": 591,
                "output_tokens_details": {
                  "reasoning_tokens": 524
                },
                "total_tokens": 998
              },
              "rate_limits": {
                "x-ratelimit-limit-requests": "3000",
                "x-ratelimit-remaining-requests": "2989",
                "x-ratelimit-limit-tokens": "4000000",
                "x-ratelimit-remaining-tokens": "3995243"
              }
            }
          }
        }
      }
    },
    "4": {
      "status": "passed",
      "reason": null,
      "measured": {
        "freedoom2_path": "/home/ramsi/projects/vizdoom-agent-research/.venv/lib/python3.12/site-packages/vizdoom/freedoom2.wad",
        "freedoom2_sha256": "a8772e088847032510d97ba2312406a6998f21cbab44d4ff10696faa9c0ecd4b",
        "freedoom1_installed": true
      }
    },
    "5": {
      "status": "passed",
      "reason": null,
      "measured": {
        "scenario": "freedoom2",
        "nomonsters": true,
        "original": {
          "final_tic": 72,
          "reward": 0.0
        },
        "replay": {
          "final_tic": 72,
          "reward": 0.0
        },
        "matches": true,
        "frames": 71,
        "replay_shape": [
          720,
          1280,
          3
        ],
        "codec": "h264",
        "lmp": "/home/ramsi/projects/vizdoom-agent-research/runs/m0_spike_20261104T064312.342534Z/freedoom2_True.lmp",
        "lmp_sha256": "17928f521e2fee06849c0f41ec8ce2260c260549d0acfd0f366f6c73f4c34a72",
        "video": "/home/ramsi/projects/vizdoom-agent-research/runs/m0_spike_20261104T064312.342534Z/freedoom2_True.mp4"
      }
    },
    "6": {
      "status": "passed",
      "reason": null,
      "measured": {
        "camera_fov_deg": 90.0,
        "resolution": [
          640,
          480
        ],
        "delta_default_max": 0.0,
        "delta_turn_samples": [
          {
            "requested_deg": 5.0,
            "max_value": 0.0,
            "applied_signed_deg": -4.998779298038812
          },
          {
            "requested_deg": 45.0,
            "max_value": 0.0,
            "applied_signed_deg": -45.00000001047738
          },
          {
            "requested_deg": 90.0,
            "max_value": 0.0,
            "applied_signed_deg": -90.00000002095476
          },
          {
            "requested_deg": 180.0,
            "max_value": 0.0,
            "applied_signed_deg": -179.99999995809048
          },
          {
            "requested_deg": -45.0,
            "max_value": 0.0,
            "applied_signed_deg": 45.00000001047738
          },
          {
            "requested_deg": 5.0,
            "max_value": 30.0,
            "applied_signed_deg": -4.998779298038869
          },
          {
            "requested_deg": 45.0,
            "max_value": 30.0,
            "applied_signed_deg": -30.003662116360772
          },
          {
            "requested_deg": 90.0,
            "max_value": 30.0,
            "applied_signed_deg": -30.003662116360772
          },
          {
            "requested_deg": 180.0,
            "max_value": 30.0,
            "applied_signed_deg": -30.003662116360772
          },
          {
            "requested_deg": -45.0,
            "max_value": 30.0,
            "applied_signed_deg": 29.99816895229702
          }
        ],
        "binary_turns": {
          "left": {
            "tics_per_90": 29
          },
          "right": {
            "tics_per_90": 29
          }
        },
        "forward_units_per_tic": {
          "walk": 4.5419464111328125,
          "run": 9.083831787109375
        },
        "movement_method": "Maximum eighth-tic displacement across four headings; inspect raw traces for collisions. Not a steady-state estimate."
      }
    },
    "7": {
      "status": "passed",
      "reason": null,
      "measured": {
        "actor_names": {
          "my_way_home": {
            "labels": [
              "GreenArmor"
            ],
            "objects": [
              "DoomPlayer",
              "GreenArmor"
            ]
          },
          "health_gathering_supreme": {
            "labels": [
              "CustomMedikit",
              "Poison"
            ],
            "objects": [
              "CustomMedikit",
              "DoomPlayer",
              "Poison"
            ]
          },
          "freedoom2": {
            "labels": [
              "DoomPlayer"
            ],
            "objects": [
              "ArmorBonus",
              "BigTree",
              "BlueArmor",
              "Chainsaw",
              "Clip",
              "Column",
              "DeadMarine",
              "DoomPlayer",
              "ExplosiveBarrel",
              "GreenArmor",
              "HealthBonus",
              "Medikit",
              "Shell",
              "Shotgun",
              "Soulsphere",
              "Stalagtite",
              "Stimpack",
              "TechPillar",
              "TorchTree"
            ]
          }
        },
        "scenario_sha256": {
          "my_way_home": "3dc0eb5e3557df7918484f91bdc4bc13b78cf67c6a6eaf2052e8ad66cfccbe46",
          "health_gathering_supreme": "d53069631aacb542bd9bf5349787f5d4f5bf61a9f720cd23ae9cc3d89d2f4fe7",
          "freedoom2": "349a7fd32da0964ee0605569b2befa8edc3cc4380ec4e3cfa492e0941fefa74f"
        }
      }
    },
    "8": {
      "status": "passed",
      "reason": null,
      "measured": {
        "runs": [
          {
            "scenario": "my_way_home",
            "nomonsters": false,
            "original": {
              "final_tic": 81,
              "reward": -0.007100000000000006
            },
            "replay": {
              "final_tic": 81,
              "reward": -0.007100000000000006
            },
            "matches": true,
            "frames": 71,
            "replay_shape": [
              720,
              1280,
              3
            ],
            "codec": "h264",
            "lmp": "/home/ramsi/projects/vizdoom-agent-research/runs/m0_spike_20261104T064312.342534Z/my_way_home_False.lmp",
            "lmp_sha256": "b9258df570bc1617b58a527a31337a04afc79cfb7ce54ac99c4ed352e91857eb",
            "video": "/home/ramsi/projects/vizdoom-agent-research/runs/m0_spike_20261104T064312.342534Z/my_way_home_False.mp4"
          },
          {
            "scenario": "freedoom2",
            "nomonsters": false,
            "original": {
              "final_tic": 72,
              "reward": 0.0
            },
            "replay": {
              "final_tic": 72,
              "reward": 0.0
            },
            "matches": true,
            "frames": 71,
            "replay_shape": [
              720,
              1280,
              3
            ],
            "codec": "h264",
            "lmp": "/home/ramsi/projects/vizdoom-agent-research/runs/m0_spike_20261104T064312.342534Z/freedoom2_False.lmp",
            "lmp_sha256": "280e9cb98d1334b508a1d02cf20bfbccb7339829d512de332432357c11bca067",
            "video": "/home/ramsi/projects/vizdoom-agent-research/runs/m0_spike_20261104T064312.342534Z/freedoom2_False.mp4"
          }
        ]
      }
    },
    "9": {
      "status": "partial",
      "reason": "Door/switch masks require manual semantic annotation; absent actor masks are unscored, never successes.",
      "measured": {
        "requested_frames": 20,
        "completed_requests": 100,
        "complete": false,
        "scored_requests": 20,
        "iou_threshold": 0.5,
        "iou_hit_rate": 0.0,
        "confidence_distribution": [],
        "decoded_masks": 0,
        "latency_ms": {
          "min": 664.6703049991629,
          "median": 1485.0030084999162,
          "max": 2606.7827209990355
        },
        "reason": "Door/switch masks require manual semantic annotation; absent actor masks are unscored, never successes."
      }
    },
    "10": {
      "status": "passed",
      "reason": null,
      "measured": {
        "upload_ms": 4046.5787920038565,
        "inference": {
          "latency_ms": 11534.845941998356,
          "usage": {
            "input_tokens": 539,
            "input_tokens_details": {
              "cache_write_tokens": null,
              "cached_tokens": 0,
              "cache_write_tokens_details": null
            },
            "output_tokens": 603,
            "output_tokens_details": {
              "reasoning_tokens": 563
            },
            "total_tokens": 1142
          },
          "rate_limits": {
            "x-ratelimit-limit-requests": "3000",
            "x-ratelimit-remaining-requests": "2999",
            "x-ratelimit-limit-tokens": "4000000",
            "x-ratelimit-remaining-tokens": "4000000"
          }
        },
        "bytes": 753411
      }
    }
  },
  "auxiliary": {
    "environment": {
      "status": "passed",
      "reason": null,
      "measured": {
        "python": "3.12.3",
        "platform": "Linux-5.15.146.1-microsoft-standard-WSL2-x86_64-with-glibc2.39",
        "python_312": true,
        "key_present": true,
        "env_ignored": true,
        "ffmpeg": "ffmpeg version n9.0.2-17-g2a571b6068-20260930 Copyright (c) 2000-2026 the FFmpeg developers",
        "vizdoom_version": "1.3.2"
      }
    },
    "iwad": {
      "status": "passed",
      "reason": null,
      "measured": {
        "freedoom2_path": "/home/ramsi/projects/vizdoom-agent-research/.venv/lib/python3.12/site-packages/vizdoom/freedoom2.wad",
        "freedoom2_sha256": "a8772e088847032510d97ba2312406a6998f21cbab44d4ff10696faa9c0ecd4b",
        "freedoom1_installed": true
      }
    },
    "minimal_response": {
      "status": "passed",
      "reason": null,
      "measured": {
        "response_status": "incomplete",
        "latency_ms": 1459.4743139969069,
        "usage": {
          "input_tokens": 11,
          "input_tokens_details": {
            "cache_write_tokens": null,
            "cached_tokens": 0,
            "cache_write_tokens_details": null
          },
          "output_tokens": 16,
          "output_tokens_details": {
            "reasoning_tokens": 9
          },
          "total_tokens": 27
        },
        "rate_limits": {
          "x-ratelimit-limit-requests": "3000",
          "x-ratelimit-remaining-requests": "2997",
          "x-ratelimit-limit-tokens": "4000000",
          "x-ratelimit-remaining-tokens": "3998683"
        }
      }
    },
    "image_token_counter": {
      "status": "passed",
      "reason": null,
      "measured": {
        "object": "response.input_tokens",
        "input_tokens": 585
      }
    },
    "sam_box_control": {
      "status": "passed",
      "reason": null,
      "measured": {
        "latency_ms": 1656.247897000867,
        "concept": "<0f>1<|box;x1=288;y1=228;x2=352;y2=286;w=640;h=480|>",
        "confidences": [
          0.921875
        ],
        "encodings": [
          "one_bit"
        ],
        "mask_count": 1,
        "parser": "parse_responses_stream(image_segmentation_format())",
        "completed": true,
        "raw_output_chars": 188,
        "foreground_pixels": [
          2061
        ],
        "live_mask_decoding_verified": true,
        "wire": {
          "finish_status": "completed",
          "output_item_types": [
            "message"
          ],
          "delta_events": 1,
          "delta_characters": 188,
          "terminal_text_characters": 188,
          "parser_text_characters": 188,
          "parser_record_count": 2,
          "parser_status": "completed",
          "parser_diagnostics": [],
          "raw_text_matches_parser": true,
          "terminal_text_matches_parser": true,
          "genuinely_empty_model_output": false
        },
        "iou": 0.9248582230623819,
        "note": "Ground-truth box supplied for M0 decoder validation only; not text-guided detection performance"
      }
    }
  },
  "fixture_count": 20,
  "fixture_manifest": "/home/ramsi/projects/vizdoom-agent-research/runs/m0_spike_20261104T064312.342534Z/frames.json",
  "api_base_url": "https://api.meta.ai/v1"
}
```

## Reproduce

Run in WSL with Python 3.12 and `uv`: `make setup`, `make test`, `make doctor`, `make spike`.
`autoqa doctor --offline` and `autoqa spike --offline` skip all network checks explicitly.
The default spike issues at most 101 SAM requests (20 frames × 5 concepts, plus one box decoder control), three effort samples, a tool round trip and one video upload, with no automatic retries.
Use `MODEL_API_KEY` in the environment or ignored `.env`; the existing `META_MODEL_API_KEY` name is accepted as a compatibility alias.
A nonzero command exit means at least one required check is incomplete. No M1–M5 commands or gameplay agent are implemented.

## Interpretation limits

Calibration actions are scripted diagnostic probes, isolated in `autoqa/m0/`; they are not gameplay performance results.
The perception fixtures intentionally seek a visible armor object, log the selected episode, and rotate at spawn. They do not measure completion or agent navigation.
IoU is scored only against visible engine actor masks. Doors and switches have no actor mask and remain unverified without manual annotations.
Movement measurements include the raw acceleration traces and collisions. The reported eighth-tic speeds are not steady-state speeds.
The video test uploads a short real replay, not a two-minute stress test. Credits and missing rate-limit headers cannot be inferred from catalog access.
See [SAM investigation](sam-investigation.md) and its sanitized raw event traces for empty-output controls. An empty completed stream never establishes live mask decoding; the box control must decode nonzero foreground pixels.
Raw artifacts stay local under `runs/`; keep them for inspection. This document and `configs/calibration.json` preserve sanitized findings.
