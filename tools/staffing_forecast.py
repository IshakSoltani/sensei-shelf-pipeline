"""How many people to put on restocking, hour by hour, from a day of camera verdicts.

    .venv/bin/python tools/staffing_forecast.py        -> staff-app/bridge/data/staffing.json (the manager dashboard reads it)

Source: data/day_pass.json, the model's answer for every area of every shared training-day photo (14 cameras, about one
photo an hour each), turned into verdicts with the same decision rules as the submission (gatepipe.opus_decision).

Demand in an hour = new problems: areas that got worse since that camera's previous photo (full -> half, anything ->
empty, or a wrong product appearing). Hours where not every camera was photographed are scaled up to all 152 areas.
The first photos of the day show the overnight backlog (every area not full); it is cleared over the first two hours.
Capacity: about 2 1/4 minutes per job, walking included (the dashboard's own average), at 75 % productive time, so one
person clears about 20 jobs an hour. People = jobs / 20, rounded up, at least one.
"""
import collections, datetime, json, math, sys, zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelfcheck as sc
import gatepipe as gp

OUT = sc.ROOT / "staff-app/bridge/data/staffing.json"
TZ = zoneinfo.ZoneInfo("Europe/Lisbon")
MINUTES_PER_JOB, PRODUCTIVE = 2.25, 0.75
JOBS_PER_PERSON_HOUR = round(60 * PRODUCTIVE / MINUTES_PER_JOB)   # 20
OPENING_HOURS = 2
WORSE = {"full": 0, "half": 1, "empty": 2, "wrong": 2}


def verdicts():
    """{camera: [(time, {area: 'full' | 'half' | 'empty' | 'wrong'})]} in time order."""
    by = collections.defaultdict(dict)
    for k, r in json.load(open(sc.DATA / "day_pass.json")).items():
        cam, name, aid = k.split("|")
        if "error" in r or r.get("image_usable") == "fully_occluded":
            continue
        stock, ok = gp.opus_decision(r, aid)
        by[(cam, name)][aid] = stock if ok else "wrong"
    out = collections.defaultdict(list)
    for (cam, name), v in by.items():
        out[cam].append((datetime.datetime.fromtimestamp(int(name[:10]), TZ), v))
    return {c: sorted(v, key=lambda x: x[0]) for c, v in out.items()}


def main():
    days = verdicts()
    total = len(sc.AREAS)
    new, kinds, seen = collections.Counter(), collections.defaultdict(collections.Counter), collections.Counter()
    first_open, first_kinds, day = 0, collections.Counter(), None
    for cam, photos in days.items():
        prev = None
        for t, v in photos:
            day = day or t
            seen[t.hour] += len(v)
            if prev is None:   # the first picture of the day: what the night left behind
                for s in v.values():
                    if s != "full":
                        first_open += 1; first_kinds[s] += 1
            else:
                for aid, s in v.items():
                    p = prev.get(aid)
                    if p and s != p and (WORSE[s] > WORSE[p] or (s == "wrong" and p != "wrong")):
                        new[t.hour] += 1; kinds[t.hour][s] += 1
            prev = v
    hours = sorted(h for h in seen if seen[h] >= total / 2)   # leave out an hour with only a few last pictures
    start = hours[0]
    rows = []
    for h in hours:
        scale = total / seen[h]
        jobs = new[h] * scale
        if h < start + OPENING_HOURS:
            jobs += first_open / OPENING_HOURS
        rows.append({"hour": h, "label": f"{h:02d}:00", "new_problems": round(new[h] * scale),
                     "kinds": {k: round(n * scale) for k, n in kinds[h].items()},
                     "opening_backlog": round(first_open / OPENING_HOURS) if h < start + OPENING_HOURS else 0,
                     "jobs": round(jobs), "people": max(1, math.ceil(jobs / JOBS_PER_PERSON_HOUR))})

    def blocks(pred):
        out, cur = [], None
        for r in rows:
            if pred(r):
                cur = cur or {"from": r["hour"], "to": r["hour"] + 1, "rows": []}
                cur["to"] = r["hour"] + 1; cur["rows"].append(r)
            elif cur:
                out.append(cur); cur = None
        if cur:
            out.append(cur)
        return out

    def summary(b, why):
        return {"from": f"{b['from']:02d}:00", "to": f"{b['to']:02d}:00", "people": max(r["people"] for r in b["rows"]),
                "jobs_per_hour": round(sum(r["jobs"] for r in b["rows"]) / len(b["rows"])), "why": why}

    after = [r for r in rows if r["hour"] >= start + OPENING_HOURS]
    peak_jobs = 0.85 * max(r["new_problems"] for r in after)   # busy = within 15 % of the day's busiest hour
    busy = blocks(lambda r: r["hour"] >= start + OPENING_HOURS and r["new_problems"] >= peak_jobs)
    quiet = blocks(lambda r: r["hour"] >= start + OPENING_HOURS and r["people"] == 1)
    plan = {
        "source": f"{len(days)} cameras, {sum(len(p) for p in days.values())} pictures, {day:%A %d %B %Y} (Lisbon time)",
        "method": f"New problems per hour (shelves that got worse since the camera's previous picture), about "
                  f"{MINUTES_PER_JOB:g} min per job with walking, so one person clears about {JOBS_PER_PERSON_HOUR} jobs an hour.",
        "jobs_per_person_hour": JOBS_PER_PERSON_HOUR,
        "hours": rows,
        "opening": {"from": f"{start:02d}:00", "to": f"{start + OPENING_HOURS:02d}:00", "backlog": first_open,
                    "kinds": dict(first_kinds), "people": max(r["people"] for r in rows if r["hour"] < start + OPENING_HOURS)},
        "busiest": sorted((summary(b, "most shelves running empty or getting the wrong product") for b in busy),
                          key=lambda b: -b["jobs_per_hour"]),
        "quiet": [summary(b, "few new problems") for b in quiet],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(plan, open(OUT, "w"), indent=1)
    print(plan["source"])
    for r in rows:
        print(f"  {r['label']}  {r['jobs']:3d} jobs  {r['people']} {'person' if r['people'] == 1 else 'people'}  {r['kinds']}"
              + (f"  (+{r['opening_backlog']} from the night)" if r["opening_backlog"] else ""))
    print("opening:", plan["opening"]); print("busiest:", plan["busiest"]); print("quiet:", plan["quiet"])
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
