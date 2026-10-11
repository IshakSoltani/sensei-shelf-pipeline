// Same logic as the web version: results -> tasks -> round plan. No screens in here.
import areas from '../data/areas.json';
import locations from '../data/locations.json';
import products from '../data/products.json';
import planogram from '../data/planogram.json';
import resultsDemo from '../data/results_demo.json';

export const CONFIG = {
  trolleyCapacityCrates: 12, // a stock trolley with 3 tiers of 4 standard crates; the worker can change it on the pick list
  defaultPackCapacity: 8,
  packsPerCrate: 16,
  minutesPerStop: 1,
  minutesPerTask: 0.75,
  heavyMinKg: 3,
  walkingOrder: ['1A', '1B', '1C', '2A', '2B', '2C', '3A', '3B', '3C', '4A', '4B', '4C', '5A', '5B'],
};

// Results come from the demo file, or from the bridge (drop page) once it has analyzed pictures.
let results = resultsDemo;
let source = { live: false, base: null };
export const dataInfo = () => ({ live: source.live, photos: Object.values(results).reduce((n, p) => n + Object.keys(p).length, 0) });

// newest photo name per camera
export function newestPhotos(res = results) {
  const out = {};
  for (const [cam, photos] of Object.entries(res)) {
    const newest = Object.keys(photos).sort((a, b) => parseInt(b) - parseInt(a))[0];
    if (newest) out[cam] = newest;
  }
  return out;
}
// base = bridge address (for photo urls); null goes back to the demo data
export function setResults(obj, base) {
  if (obj && (Object.keys(obj).length || base)) { results = obj; source = { live: true, base }; }   // the bridge answered (even with no pictures yet)
  else { results = resultsDemo; source = { live: false, base: null }; }                             // no bridge: demo data
}

const productById = Object.fromEntries(products.map((p) => [String(p.product_id), p]));
export const bayByCode = Object.fromEntries(locations.map((b) => [b.location_code, b]));
export const bayRank = (code) => {
  const i = CONFIG.walkingOrder.indexOf(code);
  return i < 0 ? 999 : i;
};

export const WORDS = {
  empty: { label: 'Empty', sub: 'Restock now' },
  wrong: { label: 'Wrong product', sub: 'Put back in place' },
  half: { label: 'Half', sub: 'Top up' },
  damaged: { label: 'Damaged fruit', sub: '' },
};
export const colorOf = (type) => (type === 'damaged' ? 'wrong' : type);
const URGENT = new Set(['empty', 'wrong', 'damaged']);

function areaStatus(r) {
  if (!r) return 'unknown';
  if (r.planogram_compliant === false) return 'wrong';
  if (r.stock_value === 0 || r.stock_level === 'empty') return 'empty';
  if (r.stock_value === 0.5 || r.stock_level === 'half') return 'half';
  return 'ok';
}

function isHeavy(p) {
  if (!p) return false;
  if (p.heavy === true || /melon/i.test(p.name_en || '')) return true;
  const kg = /(\d+(?:\.\d+)?)\s?kg/i.exec(p.name_en || '');
  return !!kg && parseFloat(kg[1]) >= CONFIG.heavyMinKg;
}

export function fmtCrates(n) {
  const whole = Math.floor(n);
  const txt = (whole ? whole : '') + (n - whole >= 0.5 ? '½' : '') || '0';
  return txt + (n > 1 ? ' crates' : ' crate');
}

// How many packs fit in ONE shelf area. Smaller packs fit more; the numbers are estimates that staff can correct
// (CAPACITY_OVERRIDES wins). Example from the shop floor: organic red apples 500g only fit about 4 per area.
const CAPACITY_OVERRIDES = { '30491': 4 };
function packsPerArea(p) {
  if (CAPACITY_OVERRIDES[String(p.product_id)]) return CAPACITY_OVERRIDES[String(p.product_id)];
  const m = /(\d+(?:\.\d+)?)\s?(kg|g)\b/i.exec(p.name_en || '');
  if (!m) return p.pack_capacity || CONFIG.defaultPackCapacity;
  const grams = parseFloat(m[1]) * (m[2].toLowerCase() === 'kg' ? 1000 : 1);
  if (grams <= 250) return 10;
  if (grams <= 1000) return 6;
  if (grams <= 2000) return 5;
  if (grams <= 3000) return 4;
  return 3;
}

function makeTask(type, a, areaId) {
  const p = productById[String(a.product_id)] || {};
  const loose = a.sold_as === 'loose';
  const frac = type === 'half' ? 0.5 : 1;
  const packs = Math.max(1, Math.round(packsPerArea(p) * frac));
  const heavy = isHeavy(p);
  let text;
  if (type === 'wrong') text = `Wrong product here. Move it to its own spot, then fill with ${a.name_en}.`;
  else if (type === 'empty') text = `Refill completely · ${loose ? '1 crate' : `~${packs} packs`}.`;
  else text = `Top up halfway · ${loose ? '½ crate' : `~${packs} packs`}.`;
  return {
    key: areaId, type, bay: a.location_code, productId: String(a.product_id),
    name: a.name_en, emoji: a.emoji, heavy, text,
    crates: loose ? frac : 0, packs: loose ? 0 : packs,
    load: heavy ? 0 : loose ? frac : packs / CONFIG.packsPerCrate,
  };
}

// A problem reported by staff becomes a task for the next round
export function reportTask(r) {
  const bay = bayByCode[r.bay] || {};
  const w = WORDS[r.type];
  return {
    key: r.id, type: r.type, bay: r.bay, productId: r.id, reported: true,
    name: r.name || `Reported: ${w.label.toLowerCase()}`, emoji: r.emoji || '📣', heavy: false,
    text: `A colleague reported this in ${bay.bay_name || r.bay}. Have a look and fix it.`,
    crates: 0, packs: 0, load: 0,
  };
}

// Areas that are empty or hold the wrong product in the newest picture of each camera (key = area id)
function urgentAreas(res) {
  const out = {};
  for (const photos of Object.values(res || {})) {
    const newest = Object.keys(photos).sort((a, b) => parseInt(b) - parseInt(a))[0];
    if (!newest) continue;
    for (const [id, r] of Object.entries(photos[newest])) {
      const a = areas[id], st = areaStatus(r);
      if (a && (st === 'empty' || st === 'wrong')) out[id] = { type: st, bay: a.location_code, name: a.name_en, emoji: a.emoji };
    }
  }
  return out;
}

// What got worse between two sets of results: a short list for a notification, grouped by bay + product + type.
export function newAlerts(prev, next) {
  const before = urgentAreas(prev), after = urgentAreas(next);
  const groups = new Map();
  for (const [id, u] of Object.entries(after)) {
    if (before[id] && before[id].type === u.type) continue;
    const k = `${u.bay}|${u.name}|${u.type}`;
    const g = groups.get(k);
    if (g) g.count++; else groups.set(k, { ...u, count: 1, aisle: String((bayByCode[u.bay] || {}).aisle || '') });
  }
  return [...groups.values()].sort((a, b) => (a.type === b.type ? bayRank(a.bay) - bayRank(b.bay) : a.type === 'empty' ? -1 : 1));
}

export function buildTasks(fixed = new Set(), reports = []) {
  const tasks = [];
  for (const photos of Object.values(results)) {
    const newest = Object.keys(photos).sort((a, b) => parseInt(b) - parseInt(a))[0];
    for (const [id, r] of Object.entries(photos[newest])) {
      const a = areas[id];
      const st = areaStatus(r);
      if (a && st !== 'ok' && st !== 'unknown') tasks.push(makeTask(st, a, id));
    }
  }
  for (const r of reports) tasks.push(reportTask(r));
  return mergeTasks(tasks.filter((t) => !fixed.has(t.key)));
}

// The same product in several shelf areas of one bay is ONE job ("Banana, 4 empty areas, 4 boxes"), not four.
function mergeTasks(list) {
  const groups = new Map();
  for (const t of list) {
    const k = t.reported ? t.key : `${t.bay}|${t.productId}|${t.type}`;
    const g = groups.get(k);
    if (!g) { groups.set(k, { ...t, keys: [t.key], count: 1 }); continue; }
    g.keys.push(t.key); g.count++; g.crates += t.crates; g.packs += t.packs; g.load += t.load;
  }
  return [...groups.values()].map((g) => {
    if (g.count < 2) return g;
    const total = g.crates ? fmtCrates(g.crates) : `~${g.packs} packs`;
    const text = g.type === 'wrong' ? `Wrong product in ${g.count} areas. Move it to its own spot, then fill with ${g.name}.`
      : g.type === 'empty' ? `Refill ${g.count} empty areas completely · ${total} in total.`
      : `Top up ${g.count} areas halfway · ${total} in total.`;
    return { ...g, text };
  });
}

export const CART_SIZES = [{ label: 'Small', crates: 8 }, { label: 'Standard', crates: 12 }, { label: 'Large', crates: 16 }];

// How long a stop takes (minutes): walking to it plus every job
const effort = (st) => CONFIG.minutesPerStop + st.tasks.length * CONFIG.minutesPerTask;
const sumLoad = (list) => list.reduce((n, st) => n + st.load, 0);

// Plan the trips. Rule number one: every EMPTY job is in the first trip(s). Then Wrong product jobs go in
// the first trip when they fit and are on the way; Half top-ups fill whatever room is left, so the red
// shelves are never left for the second round.
export function autoTrips(stops) {
  const cap = CONFIG.trolleyCapacityCrates;
  const total = sumLoad(stops);
  if (total <= cap * 1.15) return [asTrip(stops)]; // a little over the cart is better than a whole extra trip for a few items

  const info = Object.fromEntries(stops.map((st) => [st.bay, st]));
  const cls = (t) => (t.type === 'empty' ? 0 : t.type === 'half' ? 2 : 1); // damaged counts as wrong
  const tasks = stops.flatMap((st) => st.tasks).sort((a, b) => cls(a) - cls(b) || bayRank(a.bay) - bayRank(b.bay));
  const trips = [{ tasks: [], load: 0, lo: Infinity, hi: -Infinity, mins: 0 }];
  const roomFor = (tr, t) => !tr.tasks.length || tr.load + t.load <= cap + 1e-9;
  const away = (tr, t) => { // how many bays outside the walk this trip already has
    if (!tr.tasks.length) return 0;
    const r = bayRank(t.bay);
    return r < tr.lo ? tr.lo - r : r > tr.hi ? r - tr.hi : 0;
  };
  for (const t of tasks) {
    const options = trips.filter((tr) => roomFor(tr, t));
    let tr;
    if (!options.length) { tr = { tasks: [], load: 0, lo: Infinity, hi: -Infinity, mins: 0 }; trips.push(tr); }
    else if (cls(t) === 0) tr = options[0]; // empty: the earliest trip with room
    else tr = options.slice().sort((a, b) => away(a, t) - away(b, t) || (cls(t) === 1 ? 0 : a.mins - b.mins))[0];
    tr.tasks.push(t); tr.load += t.load; tr.mins += CONFIG.minutesPerTask;
    tr.lo = Math.min(tr.lo, bayRank(t.bay)); tr.hi = Math.max(tr.hi, bayRank(t.bay));
  }
  return trips.filter((tr) => tr.tasks.length).map((tr) => {
    const byBay = {};
    for (const t of tr.tasks) (byBay[t.bay] = byBay[t.bay] || []).push(t);
    const list = Object.keys(byBay).sort((a, b) => bayRank(a) - bayRank(b)).map((bay) => {
      const ts = byBay[bay];
      return { ...info[bay], tasks: ts, load: sumLoad(ts), hasMix: ts.some((x) => URGENT.has(x.type)) && ts.some((x) => x.type === 'half') };
    });
    return asTrip(list);
  });
}

const asTrip = (list) => ({ stops: list, load: list.reduce((n, s) => n + s.load, 0) });

export const oneTrip = (stops) => [asTrip(stops)]; // the worker takes everything in one go

// The worker asked for two trips. 'position': cut the walking order where the loads balance.
// 'priority': urgent bays first, bays with only Half tasks second.
// `loaded` = product ids already ticked on the trolley: their stops always stay in trip 1.
export function splitTrips(stops, mode, loaded = new Set()) {
  if (stops.length < 2) return [asTrip(stops)];
  const pinned = (st) => st.tasks.some((t) => !t.reported && loaded.has(t.productId));
  const urgent = (st) => st.tasks.some((t) => URGENT.has(t.type));
  const make = (inFirst) => {
    const first = stops.filter(inFirst);
    const second = stops.filter((st) => !inFirst(st));
    return first.length && second.length ? [asTrip(first), asTrip(second)] : null;
  };
  if (mode === 'priority') {
    // Split by JOB, not by stop: every Empty and Wrong job (plus anything already loaded) goes in trip 1,
    // the Half top-ups in trip 2. Both trips keep the walking order of the aisles.
    const goesFirst = (t) => URGENT.has(t.type) || (!t.reported && loaded.has(t.productId));
    const cut = (keep) => stops.map((st) => {
      const tasks = st.tasks.filter(keep);
      return { ...st, tasks, load: tasks.reduce((n, x) => n + x.load, 0), hasMix: tasks.some((x) => URGENT.has(x.type)) && tasks.some((x) => x.type === 'half') };
    }).filter((st) => st.tasks.length);
    const first = cut(goesFirst), second = cut((t) => !goesFirst(t));
    if (first.length && second.length) return [asTrip(first), asTrip(second)];
  }
  let best = null, bestGap = Infinity;
  for (let k = 0; k <= stops.length; k++) {
    const r = make((st) => stops.indexOf(st) < k || pinned(st));
    if (!r) continue;
    const gap = Math.abs(r[0].load - r[1].load);
    if (gap < bestGap) { bestGap = gap; best = r; }
  }
  return best || [asTrip(stops)];
}

// Walking distance of a trip, in bays between its first and last stop
const spanOf = (list) => list.length ? Math.max(...list.map((st) => bayRank(st.bay))) - Math.min(...list.map((st) => bayRank(st.bay))) : 0;

// With two trips: is there one stop in trip 1 (not yet on the trolley) worth swapping with one in trip 2?
// It must shorten the total walk and keep both trolleys within capacity.
export function suggestSwap(trips, loaded = new Set()) {
  if (trips.length !== 2) return null;
  const [a, b] = trips;
  const pinned = (st) => st.tasks.some((t) => !t.reported && loaded.has(t.productId));
  const cap = CONFIG.trolleyCapacityCrates;
  const load = (list) => list.reduce((n, st) => n + st.load, 0);
  const now = spanOf(a.stops) + spanOf(b.stops);
  let best = null;
  for (const out of a.stops.filter((st) => !pinned(st))) {
    for (const inn of b.stops) {
      const na = a.stops.filter((st) => st !== out).concat(inn);
      const nb = b.stops.filter((st) => st !== inn).concat(out);
      if (load(na) > Math.max(cap, load(a.stops)) || load(nb) > Math.max(cap, load(b.stops))) continue;
      const saves = now - (spanOf(na) + spanOf(nb));
      if (saves > 0 && (!best || saves > best.saves)) best = { out, inn, saves };
    }
  }
  return best;
}

export function planRound(tasks) {
  const byBay = {};
  for (const t of tasks) (byBay[t.bay] = byBay[t.bay] || []).push(t);
  const codes = Object.keys(byBay).sort((a, b) => bayRank(a) - bayRank(b));
  const urgent = codes.filter((c) => byBay[c].some((t) => URGENT.has(t.type)));
  if (!urgent.length) return { trips: [], stops: [], taskCount: 0, minutes: 0, leftOver: codes };

  const lo = bayRank(urgent[0]);
  const hi = bayRank(urgent[urgent.length - 1]);
  const inRound = codes.filter((c) => urgent.includes(c) || (bayRank(c) > lo && bayRank(c) < hi));
  const leftOver = codes.filter((c) => !inRound.includes(c));

  const stops = inRound.map((code) => {
    const list = byBay[code].slice().sort((a, b) => (URGENT.has(b.type) ? 1 : 0) - (URGENT.has(a.type) ? 1 : 0));
    return {
      bay: code, bayName: (bayByCode[code] || {}).bay_name || '', tasks: list,
      load: list.reduce((s, t) => s + t.load, 0),
      hasMix: list.some((t) => URGENT.has(t.type)) && list.some((t) => t.type === 'half'),
    };
  });

  const trips = autoTrips(stops);

  const taskCount = stops.reduce((n, s) => n + s.tasks.length, 0);
  const minutes = Math.round(stops.length * CONFIG.minutesPerStop + taskCount * CONFIG.minutesPerTask);
  return { trips, stops, taskCount, minutes, leftOver };
}

const URGENCY = { empty: 0, wrong: 1, half: 2 };

// One line per product for the pick list ("Load your trolley")
export function pickList(trip) {
  const lines = {};
  for (const stop of trip.stops) {
    for (const t of stop.tasks) {
      if (t.reported) continue; // staff reports need no loading
      const l = (lines[t.productId] = lines[t.productId] || {
        id: t.productId, name: t.name, emoji: t.emoji, heavy: t.heavy, bays: [], boxes: 0, packs: 0, type: t.type,
      });
      if (URGENCY[colorOf(t.type)] < URGENCY[colorOf(l.type)]) l.type = t.type; // most urgent task decides the colour
      if (!l.bays.includes(t.bay)) l.bays.push(t.bay);
      l.boxes += t.crates;
      l.packs += t.packs;
    }
  }
  // aisle by aisle in walking order; inside the same bay the most urgent (red) first
  const urgency = (l) => ({ empty: 0, wrong: 1, damaged: 1, half: 2 }[l.type] ?? 3);
  const firstRank = (l) => Math.min(...l.bays.map((b) => bayRank(b)));
  return Object.values(lines).sort((a, b) => firstRank(a) - firstRank(b) || urgency(a) - urgency(b));
}

// Live shelves: every bay with the status of each of its areas, plus the camera picture outlines
export const AREAS_IN_SHOP = 5; // the shop is shown as 5 areas, each a few cameras side by side
export const PHOTO = { w: 1280, h: 720 };
const RANK = { unknown: -1, ok: 0, half: 1, wrong: 2, empty: 3 };

export function liveData(fixed = new Set()) {
  const status = {};
  const newest = newestPhotos();
  const photos = {}; // camera -> picture url (only when the bridge has the real photo)
  const times = {}; // camera -> when the newest picture was taken (unix seconds, from the file name)
  for (const [cam, name] of Object.entries(newest)) {
    for (const [id, r] of Object.entries(results[cam][name])) status[id] = areaStatus(r);
    if (parseInt(name, 10) > 1e9) times[cam] = parseInt(name, 10);
    if (source.live && source.base) photos[cam] = `${source.base}/photos/${cam}/${name}`;
  }
  const cameraAreas = {}; // camera -> [{id, pts, ...}]
  const bays = {};
  for (const [id, a] of Object.entries(areas)) {
    const st = fixed.has(id) ? 'ok' : !status[id] ? (source.live ? 'unknown' : 'ok') : status[id] === 'unknown' ? 'ok' : status[id];
    const area = { id, camera: a.camera, productId: String(a.product_id), name: a.name_en, emoji: a.emoji, bay: a.location_code, status: st, pts: (planogram[a.camera] || {})[id] };
    (cameraAreas[a.camera] = cameraAreas[a.camera] || []).push(area);
    const b = (bays[a.location_code] = bays[a.location_code] || { code: a.location_code, name: a.bay_name, aisle: a.aisle, camera: a.camera, status: 'unknown' });
    if (RANK[st] > RANK[b.status]) b.status = st;
  }
  const list = Object.values(bays).sort((x, y) => bayRank(x.code) - bayRank(y.code));

  // One entry per camera ("side"), in walking order, then the cameras cut into 5 areas of the shop
  const cameras = Object.entries(cameraAreas).map(([camera, list2]) => {
    const own = list.filter((b) => b.camera === camera);
    const worst = list2.reduce((w, a) => (RANK[a.status] > RANK[w] ? a.status : w), 'unknown');
    return { camera, bays: own, areas: list2, status: worst, first: Math.min(...own.map((b) => bayRank(b.code))) };
  }).sort((x, y) => x.first - y.first);
  const per = Math.ceil(cameras.length / AREAS_IN_SHOP);
  const sections = [];
  for (let i = 0; i < cameras.length; i += per) sections.push({ n: sections.length + 1, cameras: cameras.slice(i, i + per) });
  return { bays: list, cameraAreas, cameras, sections, photos, times };
}

// A real picture of a product, cut out of the newest camera photo that shows it properly
// (right product, not empty). Returns null when there is no such picture, so screens fall back to the emoji.
export function productCrop(productId) {
  if (!(source.live && source.base)) return null;
  const newest = newestPhotos();
  let best = null;
  for (const [id, a] of Object.entries(areas)) {
    if (String(a.product_id) !== String(productId)) continue;
    const name = newest[a.camera];
    const r = name && results[a.camera][name][id];
    const pts = (planogram[a.camera] || {})[id];
    if (!r || !pts || r.planogram_compliant === false || r.stock_value === 0) continue;
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    const w = Math.max(...xs) - Math.min(...xs), h = Math.max(...ys) - Math.min(...ys);
    const score = r.stock_value * 1e6 + w * h; // fuller first, then the bigger area
    if (!best || score > best.score) best = { score, uri: `${source.base}/photos/${a.camera}/${name}`, cx: (Math.max(...xs) + Math.min(...xs)) / 2, cy: (Math.max(...ys) + Math.min(...ys)) / 2, side: Math.max(w, h) * 1.1 };
  }
  if (!best) return null;
  const side = Math.max(best.side, 120);
  return { uri: best.uri, viewBox: `${Math.round(best.cx - side / 2)} ${Math.round(best.cy - side / 2)} ${Math.round(side)} ${Math.round(side)}` };
}
