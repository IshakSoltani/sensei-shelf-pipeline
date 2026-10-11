"""Shelf Check: planogram compliance and stock level from shelf-camera photos, judged by Claude.

One module shared by the notebook, the tools and predict.py. The functions are the ones from the
prototype notebook (tools/build_notebook.py), with four changes decided on 10 Oct evening:
prompt v4 (mentor thresholds), causal references (never a later frame), the leftover-wrong-items
compliance rule, and a model/cost log per call.
"""
import asyncio, base64, csv, datetime, hashlib, json, os, random, re, time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATASET = DATA / "raw/extracted/new_dataset_hackathon_junctionx_10_oct_2026_v2"
SHARED_IMAGES = DATASET / "images/Frutas e Legumes (train & test)"
CACHE = ROOT / "cache/judge"
EXPERIMENTS = ROOT / "out/experiments.jsonl"
STOCK_VALUE = {"empty": 0, "half": 0.5, "full": 1}


def load_env(path=ROOT / ".env"):
    """Put the variables of .env into the environment (existing variables win)."""
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------- planogram and photos

def load_planogram(path=None):
    """Returns (AREAS, CAM_AREAS): area_id -> area dict (with 'cam'), camera -> [area_id]."""
    path = Path(path) if path else (DATA / "planogram.json" if (DATA / "planogram.json").exists() else DATASET / "planogram.json")
    plan = json.load(open(path))["Frutas e Legumes"]["cameras"]
    areas = {a["area_id"]: dict(a, cam=cam) for cam, c in plan.items() for a in c["products"]}
    cam_areas = {cam: [a["area_id"] for a in c["products"]] for cam, c in plan.items()}
    return areas, cam_areas


AREAS, CAM_AREAS = load_planogram()

CAMERA_NAMES = {  # what each camera looks at, for people who do not know the camera ids
    "6215": "Bananas and oranges", "6371": "Melons and watermelons", "6373": "Apples and pears",
    "6376": "Citrus, kiwi and melons", "6383": "Vegetables in baskets", "6385": "Apples",
    "6386": "Cabbages and broccoli", "6443": "Tomatoes", "6462": "Grapes, cherries and mangoes",
    "6468": "Tropical fruit", "6491": "Stone fruit", "6591": "Potatoes and ginger",
    "6594": "Onions, garlic and pumpkins", "6781": "Onions and potatoes"}


def camera_name(cam):
    return CAMERA_NAMES.get(cam, f"camera {cam}")


def hhmm(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%H:%M") if ts else "??:??"


def parse_ts(name):
    """Unix timestamp from a file name like 1751045755.jpg; None when the name has no 10-digit number."""
    m = re.search(r"(?<!\d)(\d{10})(?!\d)", name)
    return int(m.group(1)) if m else None


class Photo:
    """One photo of one camera: cam, name (file name with extension), ts (unix, may be None), path."""
    __slots__ = ("cam", "name", "ts", "path")

    def __init__(self, cam, name, ts, path):
        self.cam, self.name, self.ts, self.path = cam, name, ts, Path(path)

    def __repr__(self):
        return f"Photo({self.cam}/{self.name} {hhmm(self.ts)})"

    def key(self):
        return (self.cam, self.name)


def index_photos(root, cams=None):
    """All photos under root, as {cam: [Photo sorted by time]}.

    Accepts <root>/<cam>/<file>.jpg and flat <root>/<cam>_<file>.jpg; skips __MACOSX and ._ files.
    Photos without a parsable timestamp sort last (treated as the newest)."""
    root = Path(root)
    cams = set(cams or CAM_AREAS)
    out = {}
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".jpg", ".jpeg") or "__MACOSX" in p.parts or p.name.startswith("._"):
            continue
        cam = p.parent.name if p.parent.name in cams else next((c for c in cams if p.name.startswith(c + "_")), None)
        if cam is None:
            continue
        out.setdefault(cam, []).append(Photo(cam, p.name, parse_ts(p.name), p))
    for cam in out:
        out[cam].sort(key=lambda ph: (ph.ts is None, ph.ts or 0, ph.name))
    return out


def shared_photos():
    return index_photos(SHARED_IMAGES) if SHARED_IMAGES.exists() else {}


def load_image(photo_or_path):
    img = cv2.imread(str(getattr(photo_or_path, "path", photo_or_path)))
    if img is None:
        raise FileNotFoundError(f"cannot read image {photo_or_path}")
    return img


# ---------------------------------------------------------------- crops

PAD = 300
MAGENTA = (255, 0, 255)


def quad(a):
    ic = a["image_coordinates"]
    return np.float32([ic["top_left"], ic["top_right"], ic["bottom_right"], ic["bottom_left"]])


def padded(img):
    return cv2.copyMakeBorder(img, PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT, value=(70, 70, 70))


def rectify(img, a, min_side=320, max_long=1000):
    """The area warped to a rectangle that keeps its aspect ratio, short side >= min_side px."""
    q = quad(a)
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    s = min(max(min_side / min(w, h), 1.0), max_long / max(w, h))
    W, H = int(round(w * s)), int(round(h * s))
    M = cv2.getPerspectiveTransform(q + PAD, np.float32([[0, 0], [W, 0], [W, H], [0, H]]))
    return cv2.warpPerspective(padded(img), M, (W, H), flags=cv2.INTER_CUBIC)


def context_crop(img, a, grow=0.6, scale=2):
    """The area's neighbourhood (bounding box grown by 60 %), outlined, enlarged 2x."""
    q = quad(a) + PAD
    x0, y0 = q.min(0); x1, y1 = q.max(0); w, h = x1 - x0, y1 - y0
    x0, x1 = int(max(0, x0 - grow * w / 2)), int(x1 + grow * w / 2)
    y0, y1 = int(max(0, y0 - grow * h / 2)), int(y1 + grow * h / 2)
    im = padded(img); cv2.polylines(im, [q.astype(np.int32)], True, MAGENTA, 2)
    crop = im[y0:y1, x0:x1]
    return cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def outlined_frame(img, a):
    im = img.copy(); cv2.polylines(im, [quad(a).astype(np.int32)], True, MAGENTA, 3)
    return im


def jpg(img, q=90):
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()


def unjpg(b):
    return cv2.imdecode(np.frombuffer(b, np.uint8), 1)


# ---------------------------------------------------------------- products

PRODUCTS_FILE = DATA / "products_en.json"
PRODUCTS = json.load(open(PRODUCTS_FILE)) if PRODUCTS_FILE.exists() else {}


def planned(aid):
    a = AREAS[aid]
    return PRODUCTS.get(a["product_id"], {"en": a["product_name"], "unit": "loose" if a["product_name"].endswith("KG") else "packaged", "cues": "", "pt": a["product_name"]})


def candidates(cam):
    seen, out = set(), []
    for aid in CAM_AREAS[cam]:
        en = planned(aid)["en"]
        if en not in seen:
            seen.add(en); out.append(en)
    return out


# ---------------------------------------------------------------- prompts and schema

SYSTEM_V1 = """You are a shelf auditor for the fruit and vegetable section of a supermarket. Fixed ceiling cameras photograph the shelves about once an hour. A planogram defines, for each camera, four-cornered areas of the shelf (one container, or one part of a shared container) and the product planned for each area. You judge ONE target area per request.

IMAGES. "full camera frame": the whole photo with the target area outlined in magenta. "context crop": the neighbourhood of the target, outlined again and enlarged 2x. "rectified zoom": the target area alone, warped to a rectangle. Grey borders are outside the camera's view. Reference images, when present, show the SAME area at other times of the same day: when it was EMPTY, when it was FULL, and one hour earlier. The references may come in either order; read their labels.

HOW TO JUDGE
- Judge only what rests inside the outlined container on this tier. Ignore produce hanging down from the tier above (banana bunches on hooks often overhang the tray below), the price rail and labels, and the neighbouring containers.
- If a container is shared with a neighbouring area, judge the part inside the outline.
- Empty cups of a moulded pulp tray, cardboard, plastic liners, a crate floor, wicker and a black tray are NOT produce.
- Products are often misplaced. Do not assume the planned product is there: identify what you actually see, choosing from the candidate list; use "other produce" if it is none of them and "empty container" if nothing is there.
- A fabric blind, a cart or a person covering the area makes the image unusable: say so in image_usable and still give your best probabilities.

STOCK LEVEL, judged from above as the share of the container's floor area covered by product:
- empty: the floor is mostly visible, roughly under 15% covered;
- half: anything in between, roughly 15-70%;
- full: 70% or more covered, product piled or faced.
With references, fill_percent is relative to them: 0 = like the EMPTY reference, 100 = like the FULL reference. Without references, estimate it in absolute terms.

OUTPUT: integers with p_empty + p_half + p_full = 100. p_match (0-100) is the probability that the visible product is the planned one; 0 when there is no product. Use 90 or more only when the case is unambiguous. observations: at most 25 words of visible evidence, no step-by-step reasoning."""

# v4 (10 Oct evening): the mentors' definitions, the organisers' answers, truthful reference labels.
SYSTEM_V4 = """You are a shelf auditor for the fruit and vegetable section of a supermarket. Fixed ceiling cameras photograph the shelves about once an hour. A planogram defines, for each camera, four-cornered areas of the shelf (one container, or one part of a shared container) and the product planned for each area. You judge ONE target area per request.

IMAGES. "full camera frame": the whole photo with the target area outlined in magenta. "context crop": the neighbourhood of the target, outlined again and enlarged 2x. "rectified zoom": the target area alone, warped to a rectangle. Grey borders are outside the camera's view. Reference images, when present, show the SAME area earlier on the same day: at its emptiest moment, at its fullest moment, and in the most recent earlier photo. Each reference label states the time and the approximate share of the floor that was covered then. The fullest reference may itself not be completely full, and in rare cases shows a different product than the one planned: always trust what the target photo shows. The references may come in either order; read their labels.

HOW TO JUDGE
- Judge only what rests inside the outlined container on this tier. Ignore produce hanging down from the tier above (banana bunches on hooks often overhang the tray below), the price rail and labels, and the neighbouring containers.
- If a container is shared with a neighbouring area, judge the part inside the outline.
- Empty cups of a moulded pulp tray, cardboard, paper, plastic liners, a crate floor, wicker and a black tray are NOT produce: a container showing only these is empty.
- Products are often misplaced. Do not assume the planned product is there: identify what you actually see, choosing from the candidate list; use "other produce" if it is none of them and "empty container" if nothing is there.
- THE PRODUCT CHECK IS GENERIC. The planogram is checked at the level of the kind of produce, never the variety, size grade, brand, organic label or pack weight. Any banana variety is a banana; oranges and clementines are both orange citrus; any pack of red tomatoes is a tomato pack; cantaloupe, Galia and honeydew are all melons; white cabbage and cauliflower are different kinds. Judge the product that occupies the area: a container of the planned product with a few stray items of something else still matches ("yes"). Answer "no" when what is there is a different kind of produce (oranges in a banana tray, aubergines in a chayote basket, lemons in a kiwi slot) or clearly a different form (loose fruit where bags are planned, or bags where loose is planned), INCLUDING when only one or two leftover items of the wrong kind remain in an otherwise empty container. Answer "no_product" only when nothing at all is in the container. Use "cannot_tell" when the crop is too small or blurred to be sure.
- A fabric blind, a cart or a person covering the area makes the image unusable: say so in image_usable and still give your best probabilities.

STOCK LEVEL, judged from above as the share of the container's floor area covered by product (whatever the product, even a wrong one). The store is relaxed about fullness:
- full: more than about 80% covered. A tray missing a few items, with small gaps, is still full.
- half: roughly 20-80% covered, the gap is big enough that staff should be sent to restock.
- empty: under about 20% covered, or only one or two small items left (one or two grapes, bananas or peppers left is empty). One or two LARGE items (melons, pineapples, papayas, big cabbages) left in a container is half, not empty.
Coverage counts, not pile height. fill_percent is your estimate of the covered share of the floor (0-100), consistent with the reference percentages when references are given.

OUTPUT: integers with p_empty + p_half + p_full = 100. p_match (0-100) is the probability that the visible product is the planned kind; 0 when there is no product. Use 90 or more only when the case is unambiguous. observations: at most 25 words of visible evidence, no step-by-step reasoning."""

# v5: the "relaxed store" boundary where both labellers actually put it (about 70 %), with the gaps that still count as full named.
SYSTEM_V5 = SYSTEM_V4.replace(
    """- full: more than about 80% covered. A tray missing a few items, with small gaps, is still full.
- half: roughly 20-80% covered, the gap is big enough that staff should be sent to restock.""",
    """- full: the container is stocked, roughly 70% or more of the floor covered. A container missing a few items is still full: small gaps between items, a few empty cups of a pulp tray, an uncovered back row or a thin bare strip along one edge do not make it half.
- half: roughly 20-70% covered: a clear open part of the floor, big enough that staff should be sent to refill it.""")
assert SYSTEM_V5 != SYSTEM_V4

# v6: the organisers' own definition (10 Oct, via Ishak): empty = a shopper would not buy what is left; half = staff would take the
# time to refill it; full = they would not bother; in doubt, under-estimate. v6b = the same definitions without the "lean lower" line.
_i, _j = SYSTEM_V5.find("STOCK LEVEL"), SYSTEM_V5.find("\n\nOUTPUT")
assert 0 < _i < _j
_V6_NEW = 'STOCK LEVEL, judged from above (whatever the product, even a wrong one). The organisers define the three levels by what a shopper and the staff would do, not by a percentage:\n- empty: a shopper would NOT be willing to buy what is left. Nothing there, or only one or two small leftover items (one or two grapes, bananas or peppers), or just the bare liner, pulp cups, cardboard or crate floor. One or two LARGE items (melons, pineapples, papayas, big cabbages) left in a container is half, not empty.\n- half: a shopper would still buy from it, but a staff member would take the time to refill it now: a clear open part of the floor, a container visibly down from stocked.\n- full: stocked; a staff member would not bother refilling it. Small gaps between items, a few empty cups of a pulp tray, an uncovered back row or a thin bare strip along one edge are still full.\nCoverage counts, not pile height. fill_percent is your estimate of the covered share of the floor (0-100).{LEAN}'
_V6_LEAN = ' WHEN IN DOUBT BETWEEN TWO LEVELS, CHOOSE THE LOWER ONE: the organisers prefer an under-estimate to an over-estimate, and put your probability mass accordingly.'
SYSTEM_V6 = SYSTEM_V5[:_i] + _V6_NEW.replace("{LEAN}", _V6_LEAN) + SYSTEM_V5[_j:]
SYSTEM_V6B = SYSTEM_V5[:_i] + _V6_NEW.replace("{LEAN}", "") + SYSTEM_V5[_j:]
# v7: the mentor's rule only, no coverage (Ishak, 11 Oct 00:55): decide by the two questions, in doubt go lower.
_V7_STOCK = """STOCK LEVEL. Decide it ONLY with the mentors' two questions, never by measuring how much of the floor is covered:
1. Would a shopper be willing to buy what is left in this container? If not, it is EMPTY (nothing there, the bare liner, cups, cardboard or crate floor, or only one or two leftover items nobody would pick).
2. If a shopper would still buy from it: would a staff member take the time to replenish it now? If yes, it is HALF. If they would not bother yet, it is FULL.
One or two LARGE items (melons, pineapples, papayas, big cabbages) left are still worth buying: half, not empty. Whatever the product, even a wrong one, counts as stock.
IN CASE OF DOUBT BETWEEN TWO LEVELS, CHOOSE THE LOWER ONE: under-estimating is better than over-estimating. Put your probability mass accordingly.
fill_percent: a rough estimate of the covered share of the floor (0-100), for the record only; it does not decide the level."""
SYSTEM_V7 = SYSTEM_V5[:_i] + _V7_STOCK + SYSTEM_V5[_j:]
# v8: v5 unchanged + the mentors' customer/staff view as an extra cross-check (Ishak, 11 Oct 01:10). v8b adds "in doubt, lower".
_V8_EXTRA = """
CROSS-CHECK FROM THE STORE'S POINT OF VIEW (the mentors' way of seeing it). After judging the floor, also ask:
- Would a shopper be willing to buy what is left? If not, the area is empty. One or two small leftover items (a couple of bananas, a few loose peppers) on a bare liner are not what a shopper picks from: empty.
- Would a staff member take the time to replenish it now? If yes, it is half; if they would not bother yet, it is full. A crate holding a few LARGE items (melons, watermelons, pineapples) that covers its floor reasonably is full even with floor visible between them.
The rules above still hold; this view settles the borderline cases.{LEAN}"""
_V8_LEAN = " In case of doubt between two levels, choose the lower one: an under-estimate costs less than an over-estimate."
SYSTEM_V8 = SYSTEM_V5[:_j] + _V8_EXTRA.replace("{LEAN}", "") + SYSTEM_V5[_j:]
SYSTEM_V8B = SYSTEM_V5[:_j] + _V8_EXTRA.replace("{LEAN}", _V8_LEAN) + SYSTEM_V5[_j:]
# v9 (11 Oct, Ishak): v5 plus the two-customer test, asked as its own question (customers_served).
_V9_TEST = """
THE TWO-CUSTOMER TEST decides between empty and the other levels. Ask: could two customers, one after the other, each still buy a normal purchase of the planned product from what is left, without anyone refilling? A normal purchase is one pack or bag of a packaged product; one melon, pineapple, papaya, cabbage, cauliflower or pumpkin; one bunch of bananas or grapes; about 1 kg of other loose fruit or vegetables (6 to 8 apples, oranges or tomatoes).
- customers_served "none": nothing of the planned product is left (or only scraps nobody would buy).
- customers_served "one": a single pack, bag or large item, or loose produce for only one purchase. The area is EMPTY: staff must be sent to refill it.
- customers_served "two_or_more": at least two purchases are left. The area is NOT empty, however little of the floor it covers: two 1 kg bags in a large crate are half. Whether it is half or full still follows the coverage rules above.
Count only the planned product (or the same kind); leftovers of a wrong product serve nobody. If you cannot tell whether one or two purchases are left, answer "one"."""
_k = SYSTEM_V5.index("OUTPUT:")
SYSTEM_V9 = SYSTEM_V5[:_k] + _V9_TEST.strip() + "\n\n" + SYSTEM_V5[_k:].replace(
    "OUTPUT: integers", "OUTPUT: p_empty must agree with customers_served (high when it is none or one, low when two_or_more); integers")
SYSTEMS = {"v1": SYSTEM_V1, "v4": SYSTEM_V4, "v5": SYSTEM_V5, "v6": SYSTEM_V6, "v6b": SYSTEM_V6B, "v7": SYSTEM_V7, "v8": SYSTEM_V8, "v8b": SYSTEM_V8B,
           "v9": SYSTEM_V9}

SCHEMA = {"type": "object", "properties": {
    "image_usable": {"type": "string", "enum": ["clear", "partly_occluded", "fully_occluded", "blur_or_glare"]},
    "observations": {"type": "string"},
    "visible_product": {"type": "string"},
    "matches_planned": {"type": "string", "enum": ["yes", "no", "cannot_tell", "no_product"]},
    "p_match": {"type": "integer"},
    "fill_percent": {"type": "integer"},
    "p_empty": {"type": "integer"}, "p_half": {"type": "integer"}, "p_full": {"type": "integer"}},
    "required": ["image_usable", "observations", "visible_product", "matches_planned", "p_match",
                 "fill_percent", "p_empty", "p_half", "p_full"], "additionalProperties": False}


SCHEMA_V9 = json.loads(json.dumps(SCHEMA))
SCHEMA_V9["properties"]["customers_served"] = {"type": "string", "enum": ["none", "one", "two_or_more"]}
SCHEMA_V9["required"] = SCHEMA["required"] + ["customers_served"]
SCHEMAS = {"v9": SCHEMA_V9}   # every other version keeps SCHEMA, so its requests (and saved answers) are unchanged


def img_block(b):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(b).decode()}}


def build_content(photo, aid, refs=None, swap=False, img=None, with_time=True):
    """The user content for one (photo, area). Without refs (and with_time) it is byte-identical to the prototype's
    v1 content, so the cached day pass is reused.

    refs = {"empty": Ref|None, "full": Ref|None, "prev": Ref|None}; a Ref is a dict with jpg (bytes), ts, fill (or None).
    with_time=False leaves every clock time out (the organisers: judge the image, not the time of day).
    """
    img = load_image(photo) if img is None else img
    a, p, cam = AREAS[aid], planned(aid), photo.cam
    blocks, n = [], 0

    def add(label, b):
        nonlocal n; n += 1
        blocks.append({"type": "text", "text": f"Image {n}: {label}"}); blocks.append(img_block(b))

    if refs:
        order = [("empty", refs.get("empty")), ("full", refs.get("full"))]
        if swap: order.reverse()
        for kind, ref in order:
            if ref:
                about = f", roughly {ref['fill']}% of the floor covered" if ref.get("fill") is not None else ""
                when = f" ({hhmm(ref['ts'])} UTC{about}; rectified zoom)" if with_time else f" (from another photo of the same day{about}; rectified zoom)"
                add(f"reference: this same area at its {'emptiest' if kind == 'empty' else 'fullest'} moment earlier today{when}", ref["jpg"])
        if refs.get("prev"):
            when = f" ({hhmm(refs['prev']['ts'])} UTC, rectified zoom)" if with_time else " (rectified zoom)"
            add(f"reference: this same area in the most recent earlier photo{when}", refs["prev"]["jpg"])
    add("full camera frame; the target area is outlined in magenta", jpg(outlined_frame(img, a), 85))
    add("context crop around the target (outlined), enlarged 2x", jpg(context_crop(img, a)))
    add("rectified zoom of the target area", jpg(rectify(img, a)))
    cands = "; ".join(candidates(cam) + ["empty container", "other produce"])
    text = (f"Target area {aid} on camera {cam}, photo taken at {hhmm(photo.ts)} UTC.\n" if with_time else f"Target area {aid} on camera {cam}.\n") + \
           (f"Planned product: {p['en']} ({p['unit']}; {p['cues']}).\n"
            f"Candidate products on this camera: {cands}.\n")
    if refs and not refs.get("empty"):
        text += "No photo of this area empty exists from earlier today: an empty container shows its floor (tray, crate, liner or wicker).\n"
    blocks.append({"type": "text", "text": text + "Return the JSON."})
    return blocks


# ---------------------------------------------------------------- the judge

PRICES = {  # $ per million tokens: input, output, cache read, cache write
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.5},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0},
}


def call_cost(model, u):
    pr = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (u.get("in", 0) * pr["input"] + u.get("out", 0) * pr["output"] + u.get("cache_read", 0) * pr["cache_read"]
            + u.get("cache_write", 0) * pr["cache_write"]) / 1e6


def _clean(d):
    for k in ("p_match", "fill_percent", "p_empty", "p_half", "p_full"):
        d[k] = int(min(100, max(0, d.get(k, 0))))
    s = d["p_empty"] + d["p_half"] + d["p_full"]
    if s == 0:
        d["p_half"], s = 100, 100
    for k in ("p_empty", "p_half", "p_full"):
        d[k] = d[k] / s
    return d


class Judge:
    """Async Claude judge with a disk cache, a ramped concurrency limit and a per-call log.

    judge(content, sample, effort, version, model) -> dict (the schema fields, '_tokens', '_model') or {'error': ...}.
    Cache key: model | version | sample | effort | sha256 of the content (text and image bytes).
    """

    def __init__(self, model="claude-opus-5-5", concurrency=32, ramp_start=8, ramp_seconds=30, timeout=90, max_retries=4, cache=CACHE):
        import anthropic
        self.model = model
        self.client = anthropic.AsyncAnthropic(timeout=timeout, max_retries=max_retries)
        self.cache = Path(cache); self.cache.mkdir(parents=True, exist_ok=True)
        self.concurrency, self.ramp_start, self.ramp_seconds = concurrency, ramp_start, ramp_seconds
        self.sem = None; self._ramp_task = None
        self.deadline = None  # unix time after which no new API call is started (predict.py time guard)
        self.calls = []  # one dict per API call: model, in, out, cache_read, cache_write, seconds, cost, tag

    def _ensure_sem(self):
        if self.sem is None:
            self.sem = asyncio.Semaphore(min(self.ramp_start, self.concurrency))
            if self.concurrency > self.ramp_start:
                self._ramp_task = asyncio.create_task(self._ramp())

    async def _ramp(self):
        """Release extra permits gradually so the request rate climbs over ramp_seconds (acceleration limits)."""
        extra = self.concurrency - self.ramp_start
        for i in range(extra):
            await asyncio.sleep(self.ramp_seconds / extra)
            self.sem.release()

    def key(self, content, sample, effort, version, model):
        h = hashlib.sha256(f"{model}|{version}|{sample}|{effort}".encode())
        for b in content:
            h.update(b["text"].encode() if b["type"] == "text" else base64.b64decode(b["source"]["data"]))
        return h.hexdigest()

    def cached(self, content, sample=0, effort="low", version="v4", model=None):
        path = self.cache / f"{self.key(content, sample, effort, version, model or self.model)}.json"
        return json.load(open(path)) if path.exists() else None

    async def judge(self, content, sample=0, effort="low", version="v4", model=None, tag=None):
        model = model or self.model
        path = self.cache / f"{self.key(content, sample, effort, version, model)}.json"
        if path.exists():
            return json.load(open(path))
        self._ensure_sem()
        t0 = time.time()
        async with self.sem:
            if self.deadline and time.time() > self.deadline:
                return {"error": "deadline", "_model": model}
            try:
                r = await self.client.messages.create(
                    model=model, max_tokens=4000,
                    system=[{"type": "text", "text": SYSTEMS[version], "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": content}],
                    output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMAS.get(version, SCHEMA)}})
            except Exception as e:
                self.calls.append({"model": model, "error": repr(e)[:200], "seconds": round(time.time() - t0, 1), "tag": tag})
                return {"error": repr(e)[:300], "_model": model}
        u = r.usage
        tok = {"in": u.input_tokens, "out": u.output_tokens, "cache_read": u.cache_read_input_tokens or 0,
               "cache_write": u.cache_creation_input_tokens or 0}
        self.calls.append(dict(tok, model=model, seconds=round(time.time() - t0, 1), cost=call_cost(model, tok), tag=tag))
        if r.stop_reason != "end_turn":
            return {"error": f"stop_reason={r.stop_reason}", "_model": model}
        try:
            d = _clean(json.loads(next(b.text for b in r.content if b.type == "text")))
        except Exception as e:
            return {"error": f"bad json: {e!r}"[:300], "_model": model}
        d["_tokens"] = tok; d["_model"] = model
        path.write_text(json.dumps(d))
        return d

    async def judge_many(self, items, make_content, samples=(0,), effort="low", version="v4", model=None, label="", quiet=False):
        """items: list of (photo, aid). make_content(photo, aid, sample) -> content. Returns {(cam, name, aid, sample): result}."""
        t0 = time.time()

        async def one(photo, aid, s):
            content = make_content(photo, aid, s)
            return ((photo.cam, photo.name, aid, s), await self.judge(content, s, effort, version, model, tag=f"{photo.cam}/{photo.name}"))

        results = dict(await asyncio.gather(*[one(ph, aid, s) for ph, aid in items for s in samples]))
        errs = sum(1 for r in results.values() if "error" in r)
        if not quiet:
            print(f"{label or 'judge'}: {len(results)} answers ({errs} errors) in {time.time() - t0:.0f}s; "
                  f"{len(self.calls)} API calls so far, ${self.cost():.2f}")
        return results

    def cost(self, tag_prefix=None):
        return sum(c.get("cost", 0) for c in self.calls if tag_prefix is None or (c.get("tag") or "").startswith(tag_prefix))

    def usage_summary(self):
        ok = [c for c in self.calls if "cost" in c]
        return {"calls": len(self.calls), "errors": len(self.calls) - len(ok), "cost": round(self.cost(), 4),
                "input_tokens": sum(c["in"] for c in ok), "output_tokens": sum(c["out"] for c in ok),
                "cache_read_tokens": sum(c["cache_read"] for c in ok),
                "mean_seconds": round(float(np.mean([c["seconds"] for c in ok])), 1) if ok else None}


# ---------------------------------------------------------------- references (causal)

def usable(r):
    return bool(r) and "error" not in r and r.get("image_usable") != "fully_occluded"


def build_refs(cam, aid, before_ts, day, photos, min_empty_fill=30):
    """EMPTY / FULL references for an area from the day pass, using only photos strictly earlier than before_ts.

    day: {(cam, name, aid, 0): day-pass result}. photos: {cam: [Photo]}. Returns {"empty": Ref|None, "full": Ref|None, "note": str}
    where Ref = {"ts", "name", "fill", "jpg"}; or None when no usable earlier photo exists.
    """
    rows = [(ph, day.get((cam, ph.name, aid, 0))) for ph in photos.get(cam, []) if ph.ts is not None and (before_ts is None or ph.ts < before_ts)]
    rows = [(ph, r) for ph, r in rows if usable(r)]
    if not rows:
        return None
    emp = min(rows, key=lambda x: (x[1]["fill_percent"], x[1]["p_full"]))
    ok = [x for x in rows if x[1]["matches_planned"] == "yes"] or rows
    ful = max(ok, key=lambda x: (x[1]["fill_percent"], x[1]["p_full"]))
    a = AREAS[aid]

    def ref(ph, r):
        return {"ts": ph.ts, "name": ph.name, "fill": int(r["fill_percent"]), "jpg": jpg(rectify(load_image(ph), a))}

    return {"empty": ref(*emp) if emp[1]["fill_percent"] <= min_empty_fill else None, "full": ref(*ful),
            "note": f"emptiest {hhmm(emp[0].ts)} fill {emp[1]['fill_percent']}, fullest {hhmm(ful[0].ts)} fill {ful[1]['fill_percent']}"}


def prev_ref(cam, aid, before_ts, photos, exclude_name=None):
    """The most recent photo of the camera strictly earlier than before_ts (any source), as a Ref; None if there is none."""
    cands = [ph for ph in photos.get(cam, []) if ph.ts is not None and before_ts is not None and ph.ts < before_ts and ph.name != exclude_name]
    if not cands:
        return None
    ph = max(cands, key=lambda p: p.ts)
    return {"ts": ph.ts, "name": ph.name, "fill": None, "jpg": jpg(rectify(load_image(ph), AREAS[aid]))}


def nearest_estimate(cam, aid, ts, day, photos):
    """Fallback for a blocked or missing answer: the day-pass verdict of the nearest usable earlier photo (else the nearest later)."""
    rows = [(ph, day.get((cam, ph.name, aid, 0))) for ph in photos.get(cam, []) if ph.ts is not None and ts is not None and ph.ts != ts]
    rows = [(ph, r) for ph, r in rows if usable(r)]
    if not rows:
        return None
    ph, r = min(rows, key=lambda x: (x[0].ts > ts, abs(x[0].ts - ts)))
    return decide_stock(r["p_empty"], r["p_half"], r["p_full"]), True


# ---------------------------------------------------------------- decisions

def decide_stock(p0, ph, p1, rule="ev", alpha=1.0):
    """Stock level from averaged probabilities. 'ev' = best expected points (under -2, over -3); 'argmax' = most likely."""
    p1 = p1 * alpha; s = p0 + ph + p1; p0, ph, p1 = p0 / s, ph / s, p1 / s
    if rule == "argmax":
        return max((("empty", p0), ("half", ph), ("full", p1)), key=lambda x: x[1])[0]
    ev = {"empty": p0 - 2 * (ph + p1), "half": ph - 3 * p0 - 2 * p1, "full": p1 - 3 * (p0 + ph)}
    return max(ev, key=ev.get)


def decide(rs, stock_rule="ev", flag_rule="unanimous_no", alpha=1.0, fallback=None):
    """The sample answers for one area -> (stock, compliant, note).

    Flag rules for a wrong product: unanimous_no = every sample says "no" and mean p_match <= 30;
    majority_no = more than half say "no"; soft = mean of (100 - p_match) over "no" and "cannot_tell" > 0.5; never.
    Compliance: an area where every sample sees no product at all is compliant; otherwise the flag rule decides,
    also when the stock is empty (one or two leftover wrong items are non-compliant, per the organisers)."""
    rs = [r for r in rs if r and "error" not in r]
    occluded = bool(rs) and sum(r["image_usable"] == "fully_occluded" for r in rs) * 2 > len(rs)
    if not rs or occluded:
        stock, comp = fallback if fallback else ("half", True)
        return stock, comp, "occluded" if occluded else "no answer"
    p0, ph, p1 = (float(np.mean([r[k] for r in rs])) for k in ("p_empty", "p_half", "p_full"))
    fills = [r["fill_percent"] for r in rs if isinstance(r.get("fill_percent"), (int, float))]
    if stock_rule.startswith("fill") and fills:
        # "fill:35:65": the level from the model's own fill estimate (empty below 35 %, half below 65 %, else full).
        # Chosen by grouped cross-validation on the 901 hand labels (docs/decisions.md, 11 Oct 00:40).
        _, fe, fh = (stock_rule.split(":") + ["35", "65"])[:3]
        f = float(np.mean(fills)); stock = "empty" if f < float(fe) else ("half" if f < float(fh) else "full")
    else:
        stock = decide_stock(p0, ph, p1, "argmax" if stock_rule.startswith("fill") else stock_rule, alpha)
    if all(r["matches_planned"] == "no_product" for r in rs):
        return stock, True, ""
    no = [r["matches_planned"] == "no" for r in rs]
    soft = [(100 - r["p_match"]) / 100 if r["matches_planned"] in ("no", "cannot_tell") else 0.0 for r in rs]
    flag = {"soft": np.mean(soft) > 0.5, "majority_no": sum(no) * 2 > len(no),
            "unanimous_no": all(no) and np.mean([r["p_match"] for r in rs]) <= 30, "never": False}[flag_rule]
    return stock, not flag, ""


# Kinds of produce whose varieties the organisers count as the same product ("judge the kind of fruit, not the variety").
# Kiwi is left out on purpose: the labels treat gold and green kiwi as different products.
KINDS = {"apple": ("apple",), "onion": ("onion",), "potato": ("potato",), "tomato": ("tomato",), "orange": ("orange", "clementine", "tangerine"),
         "banana": ("banana",), "melon": ("melon",), "pear": ("pear",)}
KIND_EXCLUDE = ("plantain", "banana-pão", "banana-pao", "watermelon")


SAME_KIND_OVERRIDE = True

def kind_of(name):
    n = (name or "").lower()
    if any(x in n for x in KIND_EXCLUDE):
        return None
    for k, words in KINDS.items():
        if any(w in n for w in words):
            return k
    return None


def same_kind(planned_name, seen_name):
    k = kind_of(planned_name)
    return k is not None and k == kind_of(seen_name)


# THE TWO-CUSTOMER TEST (Ishak, 11 Oct): "can we still serve two customers in a row without refilling?"
#   no  -> empty: someone is sent to replenish. One pack or bag left, or a portion for only one customer, is empty.
#   yes -> at least half, however little of the crate it covers (two 1 kg bags of apples are half, not empty).
# A unit is one pack or bag for packaged products (a pack weight in the name: 500 G, 1 KG, EMB.; a calibre such as
# "75/85 KG" or "R0 KG" is not one) and one item for large loose produce (a melon, pineapple, papaya, cabbage...).
# Prompt v9 asks Opus the question directly (customers_served); for older answers the count is read from Opus's own
# observation ("Single 1 kg Carioca bag", "Two cardboard packs"). Unknown count -> the coverage reading stands.
STOCK_TEST = json.load(open(ROOT / "config.json")).get("stock_test", "coverage")
_PACK = re.compile(r"(?<![/\d.,A-Za-z])\d+(?:[.,]\d+)?\s?(?:KG|G|GR)\b|\bEMB\b", re.I)
LARGE_ITEMS = ("melon", "watermelon", "pineapple", "papaya", "cabbage", "cauliflower", "pumpkin", "squash")
_UNITS = r"(?:bags?|packs?|packages?|punnets?|nets?|trays?|boxe?s?|clamshells?|cartons?|sacks?|" + "|".join(f"{w}s?" for w in LARGE_ITEMS) + ")"
_NUMBER = {"a single": 1, "single": 1, "only one": 1, "one": 1, "one or two": 1, "a couple of": 2, "couple of": 2, "two": 2,
           "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
           "several": 3, "a few": 3, "few": 3, "multiple": 3, "many": 6}
_COUNT = re.compile(r"\b(" + "|".join(sorted(_NUMBER, key=len, reverse=True)) + r")\b(?:\s+[\w'-]+){0,4}?\s+" + _UNITS + r"\b", re.I)
# "a bag of ...", "an orange net bag", or an observation that opens with "Net bag of ..." = one unit
_ONE = re.compile(r"(?:\b(?:a|an)\s+(?:[\w'-]+\s+){0,3}?|^(?:[\w'-]+\s+){0,2}?)(?:bag|pack|package|punnet|tray|box|clamshell|carton|sack|"
                  + "|".join(LARGE_ITEMS) + r")\b(?!s)(?!\s+(?:heads|curds|leaves|halves|slices|pieces))", re.I)


def is_packaged(aid):
    return bool(_PACK.search(AREAS[aid]["product_name"]))


def counts_units(aid):
    """True when stock of this area is counted in units (packs, bags or large items) rather than coverage."""
    return is_packaged(aid) or any(w in planned(aid)["en"].lower() for w in LARGE_ITEMS)


def units_seen(text):
    """Number of packs / bags / large items Opus describes, or None when its observation gives no count."""
    m = _COUNT.search(text or "")
    if m:
        return _NUMBER[m.group(1).lower()]
    if _ONE.search(text or "") and not re.search(_UNITS.replace("s?", "s"), text or "", re.I):
        return 1
    return None


def two_customer_test(stock, rs, aid):
    """Apply the two-customer test to a stock level decided from coverage. Returns the new level."""
    if STOCK_TEST != "two_customers":
        return stock
    ok = [r for r in rs if r and "error" not in r]
    if not ok or any(r.get("image_usable") == "fully_occluded" for r in ok):
        return stock
    served = [r.get("customers_served") for r in ok if r.get("customers_served")]
    if served:   # prompt v9: Opus answered the question itself
        if all(x in ("none", "one") for x in served):
            return "empty"
        return "half" if stock == "empty" else stock
    if not counts_units(aid):
        return stock
    right = all(r.get("matches_planned") == "yes" or same_kind(planned(aid)["en"], r.get("visible_product", "")) for r in ok)
    n = [units_seen(r.get("observations")) for r in ok]
    if not right or any(x is None for x in n):
        return stock
    n = min(n)
    if n <= 1:
        return "empty"
    return "half" if stock == "empty" else stock


def predictions(targets, results, samples=(0, 1, 2), stock_rule="ev", flag_rule="unanimous_no", alpha=1.0, fallbacks=None, quiet=True):
    """targets: list of (photo, aid); results: {(cam, name, aid, sample): answer}; fallbacks: {(cam, name, aid): (stock, compliant)}.
    Returns (official dict, notes {(cam, name, aid): note}). Asserts one entry per target."""
    out, notes = {}, {}
    for ph, aid in targets:
        rs = [results.get((ph.cam, ph.name, aid, s)) for s in samples]
        fb = (fallbacks or {}).get((ph.cam, ph.name, aid))
        stock, compliant, note = decide(rs, stock_rule, flag_rule, alpha, fb)
        if not note:
            stock = two_customer_test(stock, rs, aid)
        if not compliant and SAME_KIND_OVERRIDE:
            seen = [r.get("visible_product", "") for r in rs if r and "error" not in r]
            if seen and all(same_kind(planned(aid)["en"], x) for x in seen):
                compliant = True   # a variety of the planned kind (Gala vs Golden apples): the organisers count it as compliant
        if note:
            notes[(ph.cam, ph.name, aid)] = note + (" -> nearest photo" if fb else " -> default half/compliant")
        out.setdefault(ph.cam, {}).setdefault(ph.name, {})[aid] = {
            "planogram_compliant": bool(compliant), "stock_level": stock, "stock_value": STOCK_VALUE[stock]}
    n = sum(len(v) for c in out.values() for v in c.values())
    assert n == len(targets), (n, len(targets))
    if not quiet:
        print(f"{n} areas written; fallbacks: {len(notes)}")
    return out, notes


def complete(pred, photos_by_cam):
    """True when pred has every area of every photo given as {cam: [Photo]} (or {cam: [names]})."""
    for cam, phs in photos_by_cam.items():
        for ph in phs:
            name = getattr(ph, "name", ph)
            d = pred.get(cam, {}).get(name, {})
            for aid in CAM_AREAS[cam]:
                v = d.get(aid)
                if not v or not isinstance(v.get("planogram_compliant"), bool) or v.get("stock_value") not in (0, 0.5, 1):
                    return False
    return True


# ---------------------------------------------------------------- labels, scoring, experiment log

def load_labels(path=ROOT / "labels/dev.csv"):
    """labels/dev.csv -> {(cam, file, area): {"stock_value", "compliant", "uncertain", "last2", "source"}}."""
    out = {}
    for r in csv.DictReader(open(path)):
        ts = r.get("ts") or r.get("file", "").replace(".jpg", "")
        out[(r["cam"], f"{ts}.jpg", r["area"])] = {
            "stock_value": STOCK_VALUE[r["stock"]], "compliant": r["compliant"] in ("1", "True", "true"),
            "uncertain": r.get("uncertain", "0") not in ("", "0", "False", "false"),
            "last2": r.get("last2", "0") in ("1", "True", "true"), "source": r.get("source", "")}
    return out


def score(pred, labels):
    """Official points: +1 / -2 compliance, +1 / -2 under / -3 over stock, -5 missing. Returns (total, counts)."""
    c = {"id_ok": 0, "id_wrong": 0, "stock_ok": 0, "under": 0, "over": 0, "missing": 0, "false_flag": 0, "missed_nc": 0}
    for (cam, file, aid), lab in labels.items():
        p = pred.get(cam, {}).get(file, {}).get(aid)
        if not p or not isinstance(p.get("planogram_compliant"), bool) or p.get("stock_value") not in (0, 0.5, 1):
            c["missing"] += 1; continue
        if p["planogram_compliant"] == lab["compliant"]:
            c["id_ok"] += 1
        else:
            c["id_wrong"] += 1
            c["false_flag" if lab["compliant"] else "missed_nc"] += 1
        d = p["stock_value"] - lab["stock_value"]
        c["stock_ok" if d == 0 else ("under" if d < 0 else "over")] += 1
    total = c["id_ok"] + c["stock_ok"] - 2 * c["id_wrong"] - 2 * c["under"] - 3 * c["over"] - 5 * c["missing"]
    return total, c


def bootstrap(pred, labels, n=2000, seed=0):
    """Bootstrap over labelled areas: (mean, 2.5th, 97.5th percentile) of the total score."""
    keys = list(labels); rng = random.Random(seed); tots = []
    per = {k: score(pred, {k: labels[k]})[0] for k in keys}
    for _ in range(n):
        tots.append(sum(per[rng.choice(keys)] for _ in keys))
    tots.sort()
    return float(np.mean(tots)), tots[int(0.025 * n)], tots[int(0.975 * n) - 1]


def check_score():
    """The brief's worked example: 289 areas, 19 wrong ids, 35 under, 24 over -> 320 of 578."""
    labels, pred = {}, {"c": {"i.jpg": {}}}
    for i in range(289):
        labels[("c", "i.jpg", f"a{i}")] = {"stock_value": 0.5, "compliant": True}
        p = {"planogram_compliant": i >= 19, "stock_level": "half", "stock_value": 0.5}
        if i < 35: p["stock_value"] = 0
        elif i < 59: p["stock_value"] = 1
        pred["c"]["i.jpg"][f"a{i}"] = p
    t, _ = score(pred, labels)
    assert t == 320 and 2 * len(labels) == 578, t
    return t


def log_experiment(**fields):
    """Append one experiment to out/experiments.jsonl (time added). Returns the entry."""
    EXPERIMENTS.parent.mkdir(parents=True, exist_ok=True)
    entry = {"time": datetime.datetime.now().astimezone().isoformat(timespec="minutes"), **fields}
    with open(EXPERIMENTS, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_experiments():
    return [json.loads(l) for l in open(EXPERIMENTS)] if EXPERIMENTS.exists() else []


if __name__ == "__main__":
    import sys
    if "--check-score" in sys.argv:
        print("worked example reproduced:", check_score(), "/ 578")
    else:
        print(f"{len(AREAS)} areas on {len(CAM_AREAS)} cameras; shared photos: {sum(len(v) for v in shared_photos().values())}; "
              f"API key: {'found' if load_env() else 'missing'}")
