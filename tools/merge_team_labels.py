"""Turn the team's blind-labelling log (ilies/labels/all_label_log.jsonl) into labels/team_all.json in the usual
layout, one verdict per (camera, photo, area): the latest answer of each person, majority across people when
several answered (a split vote is kept with uncertain = true).

    .venv/bin/python tools/merge_team_labels.py
"""
import collections, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelfcheck as sc

LOG = sc.ROOT / "ilies/labels/all_label_log.jsonl"
OUT = sc.ROOT / "labels/team_all.json"


def main():
    latest = {}   # (person, cam, photo, area) -> last real answer
    for line in open(LOG):
        l = json.loads(line)
        if not l.get("stock"):
            continue
        latest[(l["name"], l["camera"], l["photo"], l["area_id"])] = l
    by_area = collections.defaultdict(list)
    for (person, cam, photo, aid), l in latest.items():
        by_area[(cam, photo, aid)].append(l)
    out, n_split, people = {}, 0, collections.Counter()
    for (cam, photo, aid), ls in by_area.items():
        stocks = collections.Counter(l["stock"] for l in ls)
        prods = collections.Counter("wrong" if l.get("product") == "wrong" else "right" for l in ls)
        stock, ns = stocks.most_common(1)[0]
        prod, np_ = prods.most_common(1)[0]
        split = (ns * 2 <= len(ls) and len(ls) > 1) or (np_ * 2 <= len(ls) and len(ls) > 1)
        if split:
            n_split += 1
        for l in ls:
            people[l["name"]] += 1
        out.setdefault(cam, {}).setdefault(photo, {})[aid] = {
            "planogram_compliant": prod != "wrong", "stock_level": stock, "stock_value": sc.STOCK_VALUE[stock],
            "uncertain": bool(split), "labellers": sorted({l["name"] for l in ls}), "n_answers": len(ls)}
    OUT.parent.mkdir(exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1)
    n = sum(len(p) for c in out.values() for p in c.values())
    print(f"{n} team-labelled areas -> {OUT.relative_to(sc.ROOT)} (answers per person {dict(people)}; {n_split} split votes marked uncertain)")


if __name__ == "__main__":
    main()
