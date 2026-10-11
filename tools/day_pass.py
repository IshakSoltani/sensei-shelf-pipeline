"""Day pass: judge every area of every shared photo once with Haiku, without references, and build the
reference store the validation run needs.

    .venv/bin/python tools/day_pass.py

Writes data/day_pass.json (one answer per photo and area) and data/refs/<area>/{empty,full,last}.jpg with
data/refs/index.json (timestamps, fill percentages and the last photo's verdict), so predict.py needs no
dataset on disk. Answers are cached under cache/judge, so re-runs are free.
"""
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelfcheck as sc

MODEL = "claude-haiku-5-5"


async def main():
    assert sc.load_env(), "ANTHROPIC_API_KEY missing (.env)"
    photos = sc.shared_photos()
    judge = sc.Judge(model=MODEL, concurrency=32)
    items = [(ph, aid) for cam, phs in photos.items() for ph in phs for aid in sc.CAM_AREAS[cam]]
    print(f"{len(items)} (photo, area) pairs on {len(photos)} cameras")
    day = await judge.judge_many(items, lambda ph, aid, s: sc.build_content(ph, aid), samples=(0,), version="v1", label="day pass")
    errors = [k for k, v in day.items() if "error" in v]
    if errors:
        print(f"retrying {len(errors)} errors once")
        retry = await judge.judge_many([(ph, aid) for ph, aid in items if (ph.cam, ph.name, aid, 0) in set(errors)],
                                       lambda ph, aid, s: sc.build_content(ph, aid), samples=(0,), version="v1", label="retry")
        day.update({k: v for k, v in retry.items() if "error" not in v})
    json.dump({"|".join(k[:3]): v for k, v in day.items()}, open(sc.DATA / "day_pass.json", "w"))

    store = {}
    for cam, aids in sc.CAM_AREAS.items():
        for aid in aids:
            d = sc.DATA / "refs" / aid; d.mkdir(parents=True, exist_ok=True)
            entry = {"cam": cam}
            refs = sc.build_refs(cam, aid, None, day, photos)
            for kind in ("empty", "full"):
                r = refs.get(kind) if refs else None
                if r:
                    (d / f"{kind}.jpg").write_bytes(r["jpg"])
                    entry[kind] = {"ts": r["ts"], "name": r["name"], "fill": r["fill"]}
            rows = [(ph, day.get((cam, ph.name, aid, 0))) for ph in photos[cam]]
            rows = [(ph, r) for ph, r in rows if sc.usable(r)]
            if rows:
                ph, r = max(rows, key=lambda x: x[0].ts)
                (d / "last.jpg").write_bytes(sc.jpg(sc.rectify(sc.load_image(ph), sc.AREAS[aid])))
                entry["last"] = {"ts": ph.ts, "name": ph.name, "fill": r["fill_percent"], "matches": r["matches_planned"],
                                 "stock": sc.decide_stock(r["p_empty"], r["p_half"], r["p_full"]),
                                 "p": [round(r["p_empty"], 3), round(r["p_half"], 3), round(r["p_full"], 3)]}
            entry["timeline"] = [{"ts": ph.ts, "fill": r["fill_percent"], "matches": r["matches_planned"], "usable": r["image_usable"]} for ph, r in rows]
            store[aid] = entry
    json.dump(store, open(sc.DATA / "refs/index.json", "w"), indent=0)
    no_empty = [a for a, e in store.items() if "empty" not in e]
    print(f"reference store: {len(store)} areas, {len(no_empty)} without an empty reference: {' '.join(no_empty)}")
    print("usage:", judge.usage_summary())


if __name__ == "__main__":
    asyncio.run(main())
