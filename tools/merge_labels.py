"""Merge the hand labels into labels/dev.csv.

Sources: out/proto/labels.csv (Claude, 10 photos, with an "uncertain" flag) and ilies/labels/my_labels.json
(Ilies, 120 areas; 'covered' entries carry no truth and are skipped). Later label files in the same JSON
format can be added on the command line.

    .venv/bin/python tools/merge_labels.py [more_labels.json ...]

Columns: cam, ts, area, stock, compliant (1/0), source (ours|ilies|both|conflict|<file>), uncertain (1/0),
last2 (1 when the photo is one of the camera's last two shared photos), stock_alt (the other labeller's
stock on a conflict), ilies_split (tune|holdout|'').
"""
import csv, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelfcheck as sc

OUT = sc.ROOT / "labels/dev.csv"
VAL2STOCK = {0: "empty", 0.5: "half", 1: "full"}


def read_ours(path):
    rows = {}
    for r in csv.DictReader(open(path)):
        ts = int(r["file"].replace(".jpg", ""))
        rows[(r["cam"], ts, r["area"])] = {"stock": r["stock"], "compliant": r["compliant"] in ("1", "True", "true"),
                                           "uncertain": r["uncertain"] not in ("", "0", "False", "false")}
    return rows


def read_json(path):
    """Ilies' format: {cam: {"<ts>.jpg": {area: {planogram_compliant, stock_level, ...} | {covered: true}}}}."""
    rows, covered = {}, 0
    for cam, photos in json.load(open(path)).items():
        for name, areas in photos.items():
            for aid, e in areas.items():
                if e.get("covered") or "stock_level" not in e:
                    covered += 1; continue
                rows[(cam, sc.parse_ts(name), aid)] = {"stock": e["stock_level"], "compliant": bool(e["planogram_compliant"]),
                                                       "uncertain": bool(e.get("uncertain", False))}
    return rows, covered


def main(extra):
    ours = read_ours(sc.ROOT / "out/proto/labels.csv")
    ilies, covered = read_json(sc.ROOT / "ilies/labels/my_labels.json")
    split = {}
    for name in ("tune", "holdout"):
        p = sc.ROOT / f"ilies/labels/labels_{name}.json"
        if p.exists():
            for k in read_json(p)[0]:
                split[k] = name
    others = {}
    for f in extra:
        rows, _ = read_json(f)
        for k, v in rows.items():
            others.setdefault(k, []).append((Path(f).stem, v))

    photos = sc.shared_photos()
    last2 = {(cam, ph.ts) for cam, phs in photos.items() for ph in phs[-2:]}

    merged, conflicts = [], []
    for key in sorted(set(ours) | set(ilies) | set(others)):
        cam, ts, aid = key
        a, b = ours.get(key), ilies.get(key)
        extra_rows = others.get(key, [])
        # priority: the team's blind labels (people), then Ilies, then Claude's own labels; the first vote is kept on a conflict
        votes = [v for v in extra_rows if v[0] == "team_all"]
        votes += [("ilies", b)] if b else []
        votes += [("ours", a)] if a else []
        votes += [v for v in extra_rows if v[0] != "team_all"]
        stocks = {v["stock"] for _, v in votes}; comps = {v["compliant"] for _, v in votes}
        src = votes[0][0] if len(votes) == 1 else ("both" if len(stocks) == 1 and len(comps) == 1 else "conflict")
        # on a conflict keep the first labeller's values (ours first) and record the alternative
        stock, comp = votes[0][1]["stock"], votes[0][1]["compliant"]
        alt = next((v["stock"] for _, v in votes[1:] if v["stock"] != stock), "")
        unc = any(v["uncertain"] for _, v in votes) or src == "conflict"
        if src == "conflict":
            conflicts.append((key, [(s, v["stock"], int(v["compliant"])) for s, v in votes]))
        merged.append({"cam": cam, "ts": ts, "area": aid, "stock": stock, "compliant": int(comp), "source": src,
                       "uncertain": int(unc), "last2": int((cam, ts) in last2), "stock_alt": alt, "ilies_split": split.get(key, "")})

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(merged[0]))
        w.writeheader(); w.writerows(merged)

    n = len(merged)
    by_src = {s: sum(r["source"] == s for r in merged) for s in sorted({r["source"] for r in merged})}
    print(f"{n} labelled areas -> {OUT.relative_to(sc.ROOT)}  (Ilies' covered entries skipped: {covered})")
    print("sources:", by_src, "| uncertain:", sum(r["uncertain"] for r in merged), "| last-two-frames rows:", sum(r["last2"] for r in merged))
    print("stock:", {s: sum(r["stock"] == s for r in merged) for s in ("empty", "half", "full")},
          "| non-compliant:", sum(1 - r["compliant"] for r in merged))
    print("\nlast shared photo per camera: labelled areas / areas")
    for cam, phs in sorted(photos.items()):
        last = phs[-1]
        done = sum(1 for r in merged if r["cam"] == cam and r["ts"] == last.ts)
        print(f"  {cam} {last.name} {sc.hhmm(last.ts)}: {done}/{len(sc.CAM_AREAS[cam])}")
    if conflicts:
        print("\nconflicts (decide by eye; the first value is in the file):")
        for key, vs in conflicts:
            print("  ", key, vs)


if __name__ == "__main__":
    main(sys.argv[1:])
