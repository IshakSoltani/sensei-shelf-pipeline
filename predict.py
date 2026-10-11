"""Shelf Check, the one-shot run: photos in, Sensei's annotations file out, never incomplete.

    .venv/bin/python predict.py <images_dir> --out submission.json [--time-budget 720] [--fresh]

<images_dir> holds the photos as <cam>/<file>.jpg (one folder per camera, like the dataset) or flat <cam>_<file>.jpg.

What happens, in order:
  0. pre-flight: planogram, reference store, API key (one tiny Haiku call), plan and cost estimate
  1. a complete fallback file is written immediately (nearest earlier photo's verdict, else half/compliant)
  2. sample 0 for every area with Opus, then samples 1-2 (reference order swapped on sample 1), concurrency ramped
  3. areas with no answer are retried on the fallback models; no new call starts after --stop-new-calls seconds
  4. the file is rewritten atomically every 30 s and at the end; every area of every photo is asserted present
Logs: <out>.run/run_log.json (per call tokens, model, latency, cost per image, fallbacks), answers.jsonl, predictions per photo.
"""
import argparse, asyncio, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shelfcheck as sc

STORE = sc.DATA / "refs"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def write_atomic(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


class Run:
    def __init__(self, args):
        self.args = args
        self.cfg = json.load(open(args.config))
        self.t0 = time.time()
        self.photos = sc.index_photos(args.images_dir)
        self.store = json.load(open(STORE / "index.json")) if (STORE / "index.json").exists() else {}
        self.targets = [(ph, aid) for cam, phs in self.photos.items() for ph in phs for aid in sc.CAM_AREAS[cam]]
        self.results = {}       # (cam, name, aid, sample) -> answer
        self.decided = {}       # (cam, name) -> {aid: (stock, compliant)} for photos already judged (better fallbacks)
        self.notes = {}
        self.images = {}
        self.refs = {}
        self.workdir = Path(str(args.out).replace(".json", "") + ".run"); self.workdir.mkdir(parents=True, exist_ok=True)
        cache = self.workdir / "cache" if args.fresh else sc.CACHE
        self.judge = sc.Judge(model=self.cfg["model"], concurrency=self.cfg["concurrency"], ramp_start=self.cfg["ramp_start"],
                              ramp_seconds=self.cfg["ramp_seconds"], cache=cache)
        self.judge.deadline = self.t0 + args.stop_new_calls

    # ------------------------------------------------------------ images, references, fallbacks
    def image(self, ph):
        if ph.key() not in self.images:
            self.images[ph.key()] = sc.load_image(ph)
        return self.images[ph.key()]

    def ref_from_store(self, aid, kind):
        e = self.store.get(aid, {}).get(kind)
        p = STORE / aid / f"{kind}.jpg"
        return {"ts": e["ts"], "name": e["name"], "fill": e.get("fill"), "jpg": p.read_bytes()} if e and p.exists() else None

    def refs_for(self, ph, aid):
        """EMPTY/FULL from the store (shared training day); PREV = the store's last shared photo, or (only if the config
        allows it) the latest earlier photo of this run. None when the config says to judge the image alone."""
        if (ph.key(), aid) in self.refs:
            return self.refs[(ph.key(), aid)]
        if not self.cfg.get("use_references", True):
            self.refs[(ph.key(), aid)] = None
            return None
        r = {"empty": self.ref_from_store(aid, "empty"), "full": self.ref_from_store(aid, "full"), "prev": None}
        earlier = [p for p in self.photos[ph.cam] if p is not ph and p.ts is not None and (ph.ts is None or p.ts < ph.ts)] \
            if self.cfg.get("prev_from_run_photos", False) else []
        if earlier:
            prev = max(earlier, key=lambda p: p.ts)
            r["prev"] = {"ts": prev.ts, "name": prev.name, "fill": None, "jpg": sc.jpg(sc.rectify(self.image(prev), sc.AREAS[aid]))}
        else:
            last = self.ref_from_store(aid, "last")
            if last and (ph.ts is None or last["ts"] < ph.ts):
                r["prev"] = last
        self.refs[(ph.key(), aid)] = r
        return r

    def fallback(self, ph, aid):
        """Nearest earlier photo already decided in this run, else the store's last shared verdict, else half/compliant."""
        earlier = [p for p in self.photos[ph.cam] if p.key() in self.decided and p is not ph and p.ts is not None and (ph.ts is None or p.ts < ph.ts)]
        if earlier:
            d = self.decided[max(earlier, key=lambda p: p.ts).key()].get(aid)
            if d:
                return d
        last = self.store.get(aid, {}).get("last")
        return (last["stock"], True) if last else ("half", True)

    # ------------------------------------------------------------ output
    def predictions(self):
        fb = {(ph.cam, ph.name, aid): self.fallback(ph, aid) for ph, aid in self.targets}
        samples = tuple(range(self.cfg["samples"])) + (10, 11, 12, 13)
        pred, notes = sc.predictions(self.targets, self.results, samples, self.cfg["stock_rule"], self.cfg["flag_rule"], self.cfg.get("alpha", 1.0), fb)
        self.notes = notes
        return pred

    def write(self, final=False):
        pred = self.predictions()
        assert sc.complete(pred, self.photos), "output incomplete"
        write_atomic(self.args.out, pred)
        if final:
            for cam, phs in self.photos.items():
                for ph in phs:
                    self.decided[ph.key()] = {aid: (v["stock_level"], v["planogram_compliant"]) for aid, v in pred[cam][ph.name].items()}
        return pred

    async def writer(self):
        while True:
            await asyncio.sleep(30)
            try:
                self.write()
                log(f"checkpoint written ({len(self.results)} answers so far, ${self.judge.cost():.2f})")
            except Exception as e:  # never let the writer kill the run
                log(f"checkpoint failed: {e!r}")

    # ------------------------------------------------------------ judging
    async def judge_items(self, items, samples, model=None, label=""):
        """Judge (photo, area) pairs for the given samples, storing answers as they arrive; returns the number of good answers."""
        done, good, t0 = 0, 0, time.time()

        async def one(ph, aid, s):
            key = (ph.cam, ph.name, aid, s)
            if key in self.results and "error" not in self.results[key]:
                return key, self.results[key]
            content = sc.build_content(ph, aid, refs=self.refs_for(ph, aid), swap=(s % 10 == 1), img=self.image(ph), with_time=self.cfg.get("use_time", True))
            return key, await self.judge.judge(content, s, self.cfg["effort"], self.cfg["version"], model, tag=f"{ph.cam}/{ph.name}")

        for coro in asyncio.as_completed([one(ph, aid, s) for ph, aid in items for s in samples]):
            key, r = await coro
            self.results[key] = r
            done += 1; good += "error" not in r
            if done % 50 == 0 or done == len(items) * len(samples):
                log(f"{label}: {done}/{len(items) * len(samples)} answers, {good} good, {time.time() - t0:.0f}s, ${self.judge.cost():.2f}")
        return good

    def unanswered(self):
        return [(ph, aid) for ph, aid in self.targets
                if not any("error" not in self.results.get((ph.cam, ph.name, aid, s), {"error": 1}) for s in list(range(self.cfg["samples"])) + [10, 11, 12])]

    # ------------------------------------------------------------ cascade: local model first, Opus where unsure
    def local_stage(self, stop_at):
        """Judge every area with the local model (one answer each) until stop_at; runs in a worker thread."""
        sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
        from local_eval import LocalJudge
        cas = self.cfg["cascade"]
        judge = LocalJudge(cas["first"].replace("local:", ""), adapter=cas.get("adapter"))
        scratch = self.workdir / "local"; scratch.mkdir(exist_ok=True)
        done = 0
        for ph, aid in self.targets:
            if time.time() > stop_at:
                log(f"local stage stopped at the time limit after {done} areas"); break
            r = judge.judge(ph, aid, self.image(ph), scratch)
            self.results[(ph.cam, ph.name, aid, 0)] = r
            done += 1
            if done % 25 == 0:
                log(f"local: {done}/{len(self.targets)} areas, {time.time() - self.t0:.0f}s")
        log(f"local stage: {done} areas answered, mean {sum(judge.seconds) / max(len(judge.seconds), 1):.1f}s per area")

    def escalation_set(self):
        """Areas the local model is unsure about (or did not answer), per the cascade thresholds."""
        from cascade_tune import first_stage_verdict
        t = self.cfg["cascade"]["escalate"]
        out = []
        for ph, aid in self.targets:
            r = self.results.get((ph.cam, ph.name, aid, 0))
            stock, escalate = first_stage_verdict([r], t)
            if escalate:
                out.append((ph, aid))
            elif r and "error" not in r:
                r["matches_planned"] = "yes"   # a confident local "yes" is compliant by construction
        return out

    async def run_cascade(self):
        a, cfg = self.args, self.cfg
        cas = cfg["cascade"]
        writer = asyncio.create_task(self.writer())
        try:
            stop_at = self.t0 + cas.get("local_time_budget_seconds", 2400)
            await asyncio.get_running_loop().run_in_executor(None, self.local_stage, stop_at)
            self.write(final=True)
            esc = self.escalation_set()
            log(f"{len(esc)} of {len(self.targets)} areas go to {cfg['model']} ({len(esc) / len(self.targets):.0%})")
            for ph, aid in esc:   # the local answer must not be averaged with Opus's
                self.results.pop((ph.cam, ph.name, aid, 0), None)
            if esc and time.time() - self.t0 < a.stop_new_calls:
                await self.judge_items(esc, (10,), label="escalated")
                self.write(final=True)
            for i, m in enumerate(cfg.get("fallback_models", [])):
                left = self.unanswered()
                if not left or time.time() - self.t0 > a.stop_new_calls:
                    break
                log(f"{len(left)} areas without an answer: trying {m}")
                await self.judge_items(left, (11 + i,), model=m, label=m)
        finally:
            writer.cancel()
        self.finish()

    async def run(self):
        a, cfg = self.args, self.cfg
        n_photos = sum(len(v) for v in self.photos.values())
        log(f"{n_photos} photos on {len(self.photos)} cameras, {len(self.targets)} areas; model {cfg['model']} x{cfg['samples']} samples; "
            f"budget {a.time_budget}s, no new calls after {a.stop_new_calls}s")
        for cam, phs in sorted(self.photos.items()):
            log(f"  {cam}: " + ", ".join(f"{p.name} ({sc.hhmm(p.ts)})" for p in phs))
        missing_refs = [aid for ph, aid in self.targets if aid not in self.store]
        if missing_refs:
            log(f"WARNING: no reference store for {len(set(missing_refs))} areas (run tools/day_pass.py); judging them without references")
        pred = self.write()
        log(f"fallback file written: {a.out} (complete, {sum(len(v) for c in pred.values() for v in c.values())} areas)")
        if a.no_api:
            self.finish(); return

        probe = await self.judge.judge(sc.build_content(self.targets[0][0], self.targets[0][1], img=self.image(self.targets[0][0])),
                                       0, "low", "v1", "claude-haiku-5-5", tag="probe")
        if "error" in probe:
            log(f"API probe failed: {probe['error']}. Continuing; the fallback file stands if every call fails.")
        else:
            log("API key OK")
        if cfg.get("mode") == "cascade":
            log(f"cascade mode: {cfg['cascade']['first']} first (adapter {cfg['cascade'].get('adapter')}), {cfg['model']} where unsure")
            await self.run_cascade(); return

        writer = asyncio.create_task(self.writer())
        try:
            good = await self.judge_items(self.targets, (0,), label="sample 0")
            self.write(final=True)
            log(f"sample 0 done: {good}/{len(self.targets)} areas answered at {time.time() - self.t0:.0f}s")
            if cfg["samples"] > 1 and time.time() - self.t0 < a.stop_new_calls:
                await self.judge_items(self.targets, tuple(range(1, cfg["samples"])), label="samples 1+")
                self.write(final=True)
            for i, m in enumerate(cfg.get("fallback_models", [])):
                left = self.unanswered()
                if not left or time.time() - self.t0 > a.stop_new_calls:
                    break
                log(f"{len(left)} areas without an answer: trying {m}")
                await self.judge_items(left, (10 + i,), model=m, label=m)
        finally:
            writer.cancel()
        self.finish()

    def finish(self):
        pred = self.write(final=True)
        n = sum(len(v) for c in pred.values() for v in c.values())
        flat = [v for c in pred.values() for p in c.values() for v in p.values()]
        empty = sum(v["stock_level"] == "empty" for v in flat) / max(n, 1)
        flagged = sum(not v["planogram_compliant"] for v in flat) / max(n, 1)
        if empty > 0.6: log(f"WARNING: {empty:.0%} of areas empty; check the camera/photo mapping")
        if flagged > 0.4: log(f"WARNING: {flagged:.0%} of areas flagged as wrong product; check the mapping and references")
        u = self.judge.usage_summary()
        n_photos = sum(len(v) for v in self.photos.values())
        answered = sum(1 for ph, aid in self.targets if any("error" not in self.results.get((ph.cam, ph.name, aid, s), {"error": 1}) for s in list(range(self.cfg["samples"])) + [10, 11, 12, 13]))
        by_local = sum(1 for ph, aid in self.targets if "error" not in self.results.get((ph.cam, ph.name, aid, 0), {"error": 1}) and not str(self.results[(ph.cam, ph.name, aid, 0)].get("_model", "")).startswith("claude"))
        run_log = {"out": str(self.args.out), "images_dir": str(self.args.images_dir), "photos": n_photos, "areas": n,
                   "wall_seconds": round(time.time() - self.t0, 1), "config": self.cfg, "usage": u,
                   "cost_per_image_usd": round(u["cost"] / max(n_photos, 1), 4), "areas_answered_by_model": answered, "areas_answered_locally": by_local,
                   "mode": self.cfg.get("mode", "opus"),
                   "areas_from_fallback": len(self.notes), "fallback_notes": {"|".join(k): v for k, v in self.notes.items()},
                   "answers_per_model": {m: sum(1 for c in self.judge.calls if c.get("model") == m and "cost" in c) for m in {c.get("model") for c in self.judge.calls}},
                   "calls": self.judge.calls}
        write_atomic(self.workdir / "run_log.json", run_log)
        with open(self.workdir / "answers.jsonl", "w") as f:
            for k, v in self.results.items():
                f.write(json.dumps({"cam": k[0], "file": k[1], "area": k[2], "sample": k[3], **v}) + "\n")
        log(f"DONE: {n} areas in {n_photos} photos -> {self.args.out}; {answered} judged by a model, {len(self.notes)} from fallbacks; "
            f"{u['calls']} calls, ${u['cost']:.2f} (${run_log['cost_per_image_usd']:.3f} per image), {run_log['wall_seconds']:.0f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images_dir")
    ap.add_argument("--out", default="submission.json")
    ap.add_argument("--config", default=str(sc.ROOT / "config.json"))
    ap.add_argument("--time-budget", type=int, default=None, help="seconds; the file is final by then (default from config)")
    ap.add_argument("--stop-new-calls", type=int, default=None, help="seconds after which no new API call starts (default from config)")
    ap.add_argument("--fresh", action="store_true", help="ignore the shared answer cache (rehearsals)")
    ap.add_argument("--no-api", action="store_true", help="write the fallback file only")
    args = ap.parse_args()
    cfg = json.load(open(args.config))
    args.time_budget = args.time_budget or cfg["time_budget_seconds"]
    args.stop_new_calls = min(args.stop_new_calls or cfg["stop_new_calls_seconds"], max(args.time_budget - 120, 10))
    args.out = Path(args.out)
    if not sc.load_env() and not args.no_api:
        log("WARNING: ANTHROPIC_API_KEY missing: only the fallback file will be written")
        args.no_api = True
    if not Path(args.images_dir).exists():
        sys.exit(f"images folder not found: {args.images_dir}")
    run = Run(args)
    if not run.targets:
        sys.exit(f"no photos for any planogram camera found under {args.images_dir} (expected <cam>/<file>.jpg or <cam>_<file>.jpg)")
    asyncio.run(run.run())


if __name__ == "__main__":
    main()
