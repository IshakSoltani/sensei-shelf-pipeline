"""Build the LoRA training set: the 1,247 areas from photos without hand labels, each with two crops and Opus's
strict one-answer verdict as the target (teacher labels).

    .venv/bin/python tools/build_lora_dataset.py            # -> out/lora/data/train.parquet (+ val.parquet)
    .venv/bin/python tools/build_lora_dataset.py --with-collected
        # also every photo collected through the staff app (the manager's training playground and the camera pictures
        # the bridge has judged): the teacher's saved answers on them become new training examples (the learning loop)

The dev photos (hand labels) are excluded, so evaluation on labels/dev.csv stays clean.
"""
import io, json, sys, random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelfcheck as sc
from local_eval import SHORT

OUT = sc.ROOT / "out/lora/data"
MAX_SIDE = 448


def pil(img_bgr):
    h, w = img_bgr.shape[:2]
    s = min(1.0, MAX_SIDE / max(h, w))
    if s < 1.0:
        img_bgr = cv2.resize(img_bgr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))


def human_target(lab, r, planned_en):
    """A training target built from a person's label (stock + compliant); Opus's answer supplies the product name."""
    stock = next(k for k, v in sc.STOCK_VALUE.items() if v == lab["stock_value"])
    p = {"empty": 0, "half": 0, "full": 0}; p[stock] = 90
    for nb in {"empty": ["half"], "half": ["empty", "full"], "full": ["half"]}[stock]:
        p[nb] = 10 if stock != "half" else 5
    fill = {"empty": 10, "half": 45, "full": 85}[stock]
    if lab["compliant"]:
        return {"visible_product": planned_en, "matches_planned": "yes" if stock != "empty" or (r and r.get("matches_planned") != "no_product") else "no_product",
                "p_match": 95, "fill_percent": fill, "p_empty": p["empty"], "p_half": p["half"], "p_full": p["full"]}
    seen = r["visible_product"] if r and r.get("matches_planned") == "no" else "other produce"
    return {"visible_product": seen, "matches_planned": "no", "p_match": 5, "fill_percent": fill, "p_empty": p["empty"], "p_half": p["half"], "p_full": p["full"]}


COLLECTED = sc.ROOT / "staff-app/bridge/data/photos"


def collected_photos(skip):
    """Photos the staff app has collected (staff-app/bridge/data/photos/<camera>/<file>), minus those in skip."""
    out = []
    if COLLECTED.exists():
        for cam_dir in sorted(p for p in COLLECTED.iterdir() if p.is_dir() and p.name in sc.CAM_AREAS):
            for f in sorted(cam_dir.glob("*.jp*g")):
                if (cam_dir.name, f.name) not in skip:
                    out.append(sc.Photo(cam_dir.name, f.name, sc.parse_ts(f.name), str(f)))
    return out


def main():
    from datasets import Dataset, Features, Sequence, Image as HFImage, Value
    human_first = "--human-first" in sys.argv   # people's labels as targets where they exist (the team's relabelling), Opus elsewhere
    global OUT
    if human_first:
        OUT = sc.ROOT / "out/lora/data_human"      # a separate folder: run 1 is still reading out/lora/data
    cfg = json.load(open(sc.ROOT / "config.json")); labels = sc.load_labels(); photos = sc.shared_photos()
    # the clean evaluation photos: labelled by Claude/Ilies before the relabelling (source != team_all); never in training
    dev_photos = {(c, f) for (c, f, a), v in labels.items() if v["source"] != "team_all"}
    j = sc.Judge(model="claude-opus-5-5")
    rows, n_human = [], 0
    todo = [ph for cam, phs in sorted(photos.items()) for ph in phs]
    if "--with-collected" in sys.argv:
        extra = collected_photos(dev_photos | {(ph.cam, ph.name) for ph in todo})
        print(f"{len(extra)} photos collected through the staff app join the training set")
        todo += extra
    for ph in todo:
        cam = ph.cam
        if (cam, ph.name) in dev_photos:
            continue
        img = sc.load_image(ph)
        for aid in sc.CAM_AREAS[cam]:
            r = j.cached(sc.build_content(ph, aid, refs=None, img=img, with_time=False), 0, cfg["effort"], cfg["version"], "claude-opus-5-5")
            if not r or "error" in r:
                continue
            a, p = sc.AREAS[aid], sc.planned(aid)
            lab = labels.get((cam, ph.name, aid))
            if human_first and lab and lab["source"] == "team_all" and not lab["uncertain"]:
                target = human_target(lab, r, p["en"]); n_human += 1
            else:
                target = {"visible_product": r["visible_product"], "matches_planned": r["matches_planned"], "p_match": int(r["p_match"]),
                          "fill_percent": int(r["fill_percent"]), "p_empty": int(round(r["p_empty"] * 100)), "p_half": int(round(r["p_half"] * 100)),
                          "p_full": int(round(r["p_full"] * 100))}
            text = SHORT + f"Planned product: {p['en']} ({p['unit']}). Candidates: {'; '.join(sc.candidates(cam) + ['empty container', 'other produce'])}."
            rows.append({"images": [pil(sc.context_crop(img, a)), pil(sc.rectify(img, a))],
                         "messages": [{"role": "user", "content": text}, {"role": "assistant", "content": json.dumps(target)}],
                         "area": aid, "photo": ph.name})
    random.Random(0).shuffle(rows)
    n_val = 60
    # the training photos are daytime, so the teacher says "full" 86 % of the time; over-sample the rarer answers
    # (half x3, empty x6, wrong product x3) so the student does not learn to say "full" even more than Qwen already does
    def weight(r):
        t = json.loads(r["messages"][1]["content"])
        lvl = max(("empty", "half", "full"), key=lambda k: t["p_" + k])
        w = {"full": 1, "half": 3, "empty": 6}[lvl]
        return max(w, 3) if t["matches_planned"] == "no" else w
    val, train = rows[:n_val], rows[n_val:]
    train = [r for r in train for _ in range(weight(r))]
    random.Random(1).shuffle(train)
    rows = val + train
    feats = Features({"images": Sequence(HFImage()), "messages": [{"role": Value("string"), "content": Value("string")}], "area": Value("string"), "photo": Value("string")})
    OUT.mkdir(parents=True, exist_ok=True)
    Dataset.from_list(rows[n_val:], features=feats).to_parquet(str(OUT / "train.parquet"))
    Dataset.from_list(rows[:n_val], features=feats).to_parquet(str(OUT / "val.parquet"))
    stocks = {}
    for r in rows:
        t = json.loads(r["messages"][1]["content"]); lvl = max(("empty", "half", "full"), key=lambda k: t["p_" + k]); stocks[lvl] = stocks.get(lvl, 0) + 1
    print(f"{len(rows) - n_val} training (after over-sampling) and {n_val} validation examples -> {OUT}; {n_human} targets from people's labels; stock levels in the file: {stocks}; "
          f"non-matching products: {sum(1 for r in rows if json.loads(r['messages'][1]['content'])['matches_planned'] == 'no')}")


if __name__ == "__main__":
    main()
