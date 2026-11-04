"""Paired, predeclared text-SAM versus Muse-box/SAM fixture evaluation."""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from autoqa.agent.grounder import Grounder, bearing
from autoqa.io import write_json
from autoqa.secrets import model_key


def iou(a, b):
    return float(np.logical_and(a, b).sum() / max(1, np.logical_or(a, b).sum()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=Path("runs/m2-fixtures/fixtures.json"))
    parser.add_argument("--out", type=Path, default=Path("runs/m2-grounding-experiment"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    fixtures = json.loads(args.fixtures.read_text())
    cases = []
    for index, frame in enumerate(fixtures["frames"]):
        concept = frame["labels"][0]["concept"]
        cases.append(dict(frame=frame, concept=concept, positive=True))
        if index % 3 == 0:
            absent = "medkit" if frame["scenario"] == "my_way_home" else "green armor"
            assert not any(a["concept"] == absent for a in frame["labels"])
            cases.append(dict(frame=frame, concept=absent, positive=False))
    write_json(args.out / "cases.json", cases)
    rows = []
    key = model_key(Path.cwd())
    for mode in ("sam_text", "muse_box_sam"):
        for index, case in enumerate(cases):
            frame = case["frame"]
            grounder = Grounder(key, mode=mode, directory=args.out / f"{mode}_{index:02d}")
            row = dict(
                mode=mode,
                index=index,
                scenario=frame["scenario"],
                frame=frame["path"],
                concept=case["concept"],
                positive=case["positive"],
            )
            try:
                public, matches = grounder.locate(Path(frame["path"]).read_bytes(), case["concept"])
                truth = [a for a in frame["labels"] if a["concept"] == case["concept"]]
                masks = [np.array(Image.open(a["mask"])) > 0 for a in truth]
                scores = [iou(m["mask"] > 0, t) for m in matches for t in masks]
                box_scores = []
                for proposal in public["proposals"]:
                    if proposal is not None:
                        boxmask = np.zeros((480, 640), dtype=bool)
                        x1, y1, x2, y2 = proposal["box"]
                        boxmask[y1:y2, x1:x2] = True
                        for label in truth:
                            target = np.zeros_like(boxmask)
                            x1, y1, x2, y2 = label["box"]
                            target[y1:y2, x1:x2] = True
                            box_scores.append(iou(boxmask, target))
                errors = []
                for match in matches:
                    if masks:
                        best = max(
                            range(len(masks)), key=lambda i: iou(match["mask"] > 0, masks[i])
                        )
                        if iou(match["mask"] > 0, masks[best]) >= 0.5:
                            errors.append(abs(bearing(match["box"]) - truth[best]["gt_bearing"]))
                row.update(
                    status="completed",
                    result=public,
                    mask_iou=max(scores, default=0.0) if truth else None,
                    muse_box_iou=max(box_scores, default=0.0)
                    if truth and mode == "muse_box_sam"
                    else None,
                    hit=bool(scores and max(scores) >= 0.5),
                    false_positive=not case["positive"] and public["found"],
                    bearing_errors_deg=errors,
                    requests=grounder.calls,
                )
            except Exception as error:
                row.update(
                    status="could not verify",
                    error_type=type(error).__name__,
                    cost_usd=grounder.cost_usd,
                    requests=grounder.calls,
                )
            rows.append(row)
            write_json(args.out / "results.json", rows)
            print(
                f"{mode} {index + 1}/{len(cases)} {row['status']} hit={row.get('hit')} cost={grounder.cost_usd:.6f}",
                flush=True,
            )
    summary = {}
    for mode in ("sam_text", "muse_box_sam"):
        subset = [r for r in rows if r["mode"] == mode]
        positives = [r for r in subset if r["positive"]]
        negatives = [r for r in subset if not r["positive"]]
        errors = [e for r in subset for e in r.get("bearing_errors_deg", [])]
        summary[mode] = dict(
            positive_cases=len(positives),
            hits=sum(r.get("hit", False) for r in positives),
            negative_cases=len(negatives),
            false_positives=sum(r.get("false_positive", False) for r in negatives),
            incomplete=sum(r["status"] != "completed" for r in subset),
            cost_usd=sum(c["cost_usd"] for r in subset for c in r["requests"]),
            matched_bearing_mae_deg=sum(errors) / len(errors) if errors else None,
        )
    write_json(args.out / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
