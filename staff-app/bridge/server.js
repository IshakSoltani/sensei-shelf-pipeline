// Shelf Round bridge: drop camera photos on a web page, get results the phone app reads.
// No dependencies. Run:  node bridge/server.js
// Analysis: every new photo goes to the shelf-check model (the repo's app/server.py, default http://127.0.0.1:8790,
// set SHELF_MODEL_URL to change it): code rules first, the fine-tuned model / Opus for the rest, code decision rules.
// If the model is not running, the bridge falls back to one Claude call per photo (needs ANTHROPIC_API_KEY).
const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PORT = process.env.PORT || 8787;
const DATA = path.join(__dirname, 'data');
const PHOTOS = path.join(DATA, 'photos');
const RESULTS = path.join(DATA, 'results.json');
const ROUNDS = path.join(DATA, 'rounds.json'); // finished restock rounds sent by the phone app (and demo shifts)
const CONTRIB = path.join(DATA, 'contributions.json');
const FOCUS = path.join(DATA, 'focus.json'); // the photo sent to the phone from Settings > Send to phone // who dropped which photo on the Settings page (training playground)
const MILESTONES = [25, 50, 100, 250, 500, 1000, 2500]; // photos collected before each training round of the open model
const APP_DATA = path.join(__dirname, '..', 'mobile', 'src', 'data');
const planogram = JSON.parse(fs.readFileSync(path.join(APP_DATA, 'planogram.json'), 'utf8')); // camera -> area -> 4 corners
const areas = JSON.parse(fs.readFileSync(path.join(APP_DATA, 'areas.json'), 'utf8'));       // area -> product info
// optional bridge/.env with ELEVENLABS_API_KEY=... (kept on this computer, never sent to the phone)
try {
  for (const line of fs.readFileSync(path.join(__dirname, '.env'), 'utf8').split('\n')) {
    const m = /^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
} catch (e) { /* no .env file */ }
const KEY = process.env.ANTHROPIC_API_KEY || '';
const EL_KEY = process.env.ELEVENLABS_API_KEY || '';
const EL_VOICE = process.env.ELEVENLABS_VOICE || 'EXAVITQu4vr4xnSDxMaL'; // Sarah: soft, clear female voice. Others: Alice Xb7hH8MSUJpSbSDYk0k2, Matilda XrExE9yKIg1WjnnlVkGX
const AGENT_ID = process.env.ELEVENLABS_AGENT_ID || '';
const AGENT_KEY = process.env.ELEVENLABS_AGENT_KEY || '';
const MIA_PROMPT = `You are Mia, the voice assistant who accompanies a supermarket fruit & vegetable worker through a restocking round, and who operates the worker's app for them because their hands are full. You are heard through the phone, never read.
Style: warm, calm, quick, like a friendly colleague. 1 short sentence, 2 at most. Plain spoken words, no lists, no emojis, no markdown, no codes like "1B": say the aisle number and the bay name. Say "two crates", not "2x".
Each message starts with ROUND STATE (what the app shows right now, and available_actions) then WORKER SAYS. Use only that state and what was said earlier. If you don't know, say so briefly.
Reply in the language the worker spoke (English or Portuguese). The microphone is always open, so the worker may be talking to someone else or the speech may be garbled: then answer with "say": "" and no actions, stay silent.
Return ONLY JSON, nothing else: {"say": "<what you say aloud, or empty>", "do": [ {"action": "<name>", ...} ]}
You can only use actions listed in available_actions:
- start_round: the worker is ready to begin the round (from the home screen). Moves to loading the trolley.
- load {"items": ["<product name exactly as in the state>", ...]}: the worker says they put these on the trolley. Use the exact product names from the state; match "bananas" to the right item. Then say what you marked and what is still left to load, naming the next item with its amount.
- load_all: everything is on the trolley.
- start_route: the worker finished loading ("that's it", "let's go") and wants to walk to the shelves. Act right away. If some items are not loaded, say which ones stay for a later trip ("Going with bananas. Oranges wait for the next trip.").
- complete {"items": ["<product name>", ...]}: at a shelf, the worker finished only those products. Say which one(s) are done and what is still left at this shelf.
- done: at a shelf, the whole stop is finished; or on the arrival screen, the worker says they are here.
- repeat: say the instructions of the current stop again.
Always end by guiding the next step when the worker would need it ("Bananas loaded. Next, two crates of apples.").`;

// One open connection to the ElevenLabs agent (text mode), kept warm between questions so answers come fast.
let agentWs = null, agentOpening = null, agentWaiting = null, agentQueue = Promise.resolve();
function openAgent() {
  if (agentWs && agentWs.readyState === 1) return Promise.resolve(agentWs);
  if (agentOpening) return agentOpening;
  agentOpening = (async () => {
    const r = await fetch(`https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=${AGENT_ID}`, { headers: { 'xi-api-key': AGENT_KEY } });
    if (!r.ok) throw new Error(`signed url ${r.status}`);
    const ws = new WebSocket((await r.json()).signed_url);
    await new Promise((ok, no) => {
      ws.onopen = () => ws.send(JSON.stringify({ type: 'conversation_initiation_client_data', conversation_config_override: { conversation: { text_only: true }, agent: { first_message: '', language: 'en', prompt: { prompt: MIA_PROMPT } } } }));
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === 'ping') ws.send(JSON.stringify({ type: 'pong', event_id: m.ping_event.event_id }));
        else if (m.type === 'conversation_initiation_metadata') { agentWs = ws; ok(); }
        else if (m.type === 'agent_response' && agentWaiting) { const w = agentWaiting; agentWaiting = null; w.ok(m.agent_response_event.agent_response); }
      };
      ws.onerror = () => no(new Error('agent socket error'));
      ws.onclose = () => { if (agentWs === ws) agentWs = null; if (agentWaiting) { const w = agentWaiting; agentWaiting = null; w.no(new Error('agent closed')); } no(new Error('agent closed')); };
    });
    return ws;
  })().finally(() => { agentOpening = null; });
  return agentOpening;
}
function askAgent(text) {
  const turn = agentQueue.then(async () => {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const ws = await openAgent();
        return await new Promise((ok, no) => {
          const timer = setTimeout(() => { agentWaiting = null; no(new Error('agent timeout')); }, 12000);
          agentWaiting = { ok: (v) => { clearTimeout(timer); ok(v); }, no: (e) => { clearTimeout(timer); no(e); } };
          ws.send(JSON.stringify({ type: 'user_message', text }));
        });
      } catch (e) { try { agentWs && agentWs.close(); } catch (x) { /* ignore */ } agentWs = null; if (attempt) throw e; }
    }
  });
  agentQueue = turn.catch(() => {});
  return turn;
}
const EL_TTS_MODEL = process.env.ELEVENLABS_TTS_MODEL || 'eleven_flash_v2_5';
const TTS_DIR = path.join(__dirname, 'data', 'tts'); // every phrase is made once, then replayed from here (fast, and free)
const MODEL = process.env.CLAUDE_MODEL || 'claude-sonnet-5-5';
const SHELF_MODEL = (process.env.SHELF_MODEL_URL || 'http://127.0.0.1:8790').replace(/\/$/, '');
let shelfModelOnline = false; // checked at start and every 15 s
async function checkShelfModel() {
  try { const r = await fetch(`${SHELF_MODEL}/samples`, { signal: AbortSignal.timeout(2000) }); shelfModelOnline = r.ok; }
  catch (e) { shelfModelOnline = false; }
  return shelfModelOnline;
}

fs.mkdirSync(PHOTOS, { recursive: true });
fs.mkdirSync(TTS_DIR, { recursive: true });
const readResults = () => { try { return JSON.parse(fs.readFileSync(RESULTS, 'utf8')); } catch (e) { return {}; } };
const writeResults = (r) => fs.writeFileSync(RESULTS, JSON.stringify(r, null, 1));
const readRounds = () => { try { return JSON.parse(fs.readFileSync(ROUNDS, 'utf8')); } catch (e) { return []; } };
const writeRounds = (r) => fs.writeFileSync(ROUNDS, JSON.stringify(r, null, 1));

// ---- demo shifts, so the manager dashboard is alive before real rounds exist ----
function seedDemoRounds() {
  let seed = 7;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  const crew = [['Ana Ribeiro', 0.8], ['Bruno Costa', 1.0], ['Carla Mendes', 1.25], ['Diego Santos', 0.9], ['Eva Pires', 1.5]];
  const slowAisle = { 1: 1.0, 2: 1.05, 3: 1.1, 4: 1.45, 5: 0.95 }; // aisle 4 (heavy potatoes, bags) takes longer
  const ids = Object.keys(areas);
  const times = Object.values(readResults()).flatMap((p) => Object.keys(p)).map((n) => parseInt(n, 10)).filter((n) => n > 1e9);
  const t0 = times.length ? Math.min(...times) : 1751000000, t1 = times.length ? Math.max(...times) : 1751046000;
  const kind = () => { const x = rnd(); return x < 0.25 ? 'empty' : x < 0.4 ? 'wrong' : 'half'; };
  const out = [];
  crew.forEach(([employee, skill], ci) => {
    const n = 2 + Math.floor(rnd() * 3);
    for (let r = 0; r < n; r++) {
      const picks = new Set();
      const want = 5 + Math.floor(rnd() * 8);
      while (picks.size < want) picks.add(ids[Math.floor(rnd() * ids.length)]);
      const tasks = [...picks].map((id) => ({ key: id, name: areas[id].name_en, emoji: areas[id].emoji, type: kind(), bay: areas[id].location_code, aisle: String(areas[id].aisle), productId: String(areas[id].product_id) }))
        .sort((a, b) => a.bay.localeCompare(b.bay));
      const stops = [];
      for (const t of tasks) {
        let st = stops.find((x) => x.bay === t.bay);
        if (!st) { st = { bay: t.bay, aisle: t.aisle, seconds: 0, tasks: 0 }; stops.push(st); }
        st.tasks++; st.seconds += Math.round((40 + rnd() * 50) * skill * (slowAisle[t.aisle] || 1));
      }
      stops.forEach((st) => { st.seconds += Math.round((50 + rnd() * 40) * skill); }); // walking to the stop
      const seconds = stops.reduce((a, st) => a + st.seconds, 0);
      const loadSeconds = Math.round((50 + rnd() * 80) * skill * Math.min(1.6, Math.max(0.6, tasks.length / 8))); // time spent loading the trolley
      const start = Math.round(t0 + (t1 - t0) * ((ci * 0.13 + r * 0.27 + rnd() * 0.1) % 0.95));
      out.push({ id: `demo-${ci}-${r}`, demo: true, employee, start, end: start + seconds, seconds, loadSeconds, tasks, stops });
    }
  });
  return out;
}

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.json': 'application/json', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.css': 'text/css' };
const send = (res, code, body, type = 'application/json') => {
  res.writeHead(code, { 'Content-Type': type, 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' });
  res.end(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body));
};
const body = (req, limit = 30e6) => new Promise((ok, no) => {
  const parts = []; let n = 0;
  req.on('data', (c) => { n += c.length; if (n > limit) { no(new Error('too big')); req.destroy(); } else parts.push(c); });
  req.on('end', () => ok(Buffer.concat(parts)));
  req.on('error', no);
});
const safeName = (s) => /^[\w.-]+\.(jpe?g|png)$/i.test(s || '') && !s.includes('..');

function summary(areasOfPhoto) {
  const c = { empty: 0, half: 0, wrong: 0, full: 0 };
  for (const r of Object.values(areasOfPhoto || {})) {
    if (r.planogram_compliant === false) c.wrong++;
    else if (r.stock_level === 'empty') c.empty++;
    else if (r.stock_level === 'half') c.half++;
    else c.full++;
  }
  return c;
}

function status() {
  const results = readResults();
  const photos = [];
  for (const cam of fs.readdirSync(PHOTOS)) {
    const dir = path.join(PHOTOS, cam);
    if (!fs.statSync(dir).isDirectory()) continue;
    for (const name of fs.readdirSync(dir).filter(safeName)) {
      const r = results[cam] && results[cam][name];
      photos.push({ camera: cam, name, analyzed: !!r, summary: r ? summary(r) : null, at: fs.statSync(path.join(dir, name)).mtimeMs });
    }
  }
  photos.sort((a, b) => b.at - a.at);
  const ips = Object.values(os.networkInterfaces()).flat().filter((i) => i.family === 'IPv4' && !i.internal).map((i) => i.address);
  return { photos, cameras: Object.keys(planogram), ips, port: PORT, autoAnalyze: shelfModelOnline || !!KEY, shelfModel: shelfModelOnline };
}

// ---- analysis with the shelf-check model: the photo goes to the model service, the verdicts come back per area ----
const DETAILS = path.join(DATA, 'details.json'); // per area: who decided (code rule / model), what was seen, the fill
const readDetails = () => { try { return JSON.parse(fs.readFileSync(DETAILS, 'utf8')); } catch (e) { return {}; } };
async function askShelfModel(bytes, camera, name) {   // camera may be 'auto': the model recognises it from the photo
  const r = await fetch(`${SHELF_MODEL}/analyze_gates?cam=${camera}&name=${encodeURIComponent(name)}&api=1`, {
    method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: bytes, signal: AbortSignal.timeout(180000),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || `model ${r.status}`);
  return j;
}
async function analyzeWithShelfModel(camera, name) {
  const j = await askShelfModel(fs.readFileSync(path.join(PHOTOS, camera, name)), camera, name);
  return keepModelAnswer(j, camera, name);
}
// judge a photo with the model (camera may be 'auto'), keep it with the other pictures and write its results
async function judgeAndKeep(bytes, cam, name) {
  const j = await askShelfModel(bytes, cam, name), camera = j.camera;
  fs.mkdirSync(path.join(PHOTOS, camera), { recursive: true });
  fs.writeFileSync(path.join(PHOTOS, camera, name), bytes);
  const out = keepModelAnswer(j, camera, name);
  const results = readResults(); (results[camera] = results[camera] || {})[name] = out; writeResults(results);
  return { camera, out };
}
function keepModelAnswer(j, camera, name) {
  const out = j.submission[j.camera][j.file];
  const det = readDetails();
  (det[camera] = det[camera] || {})[name] = Object.fromEntries(j.areas.map((a) => [a.area_id, {
    decided_by: a.decided_by, seen: a.seen || null, fill: a.fill ?? null, why: a.gate ? a.gate.why : null, observation: a.observation || null }]));
  fs.writeFileSync(DETAILS, JSON.stringify(det, null, 1));
  const by = j.summary.by;
  console.log(`  model: ${by.gate} areas by code rules, ${by.opus} by the model, ${by.fallback} fallback, $${(j.summary.api_cost_usd || 0).toFixed(3)}`);
  return out;
}

// ---- fallback: one Claude call per photo (needs ANTHROPIC_API_KEY), only when the shelf-check model is not running ----
async function analyzeWithClaude(camera, name) {
  const file = path.join(PHOTOS, camera, name);
  const media = /\.png$/i.test(name) ? 'image/png' : 'image/jpeg';
  const list = Object.entries(planogram[camera] || {}).map(([id, pts]) => {
    const a = areas[id] || {};
    return `- ${id}: expected "${a.name_en}" ${a.emoji || ''}; corners (x,y) ${pts.map((p) => `(${p[0]},${p[1]})`).join(' ')}`;
  }).join('\n');
  const prompt = `This is a ceiling-camera photo (1280x720) of a fruit & vegetable gondola. The planogram says which product should be in each area (a 4-corner shape, pixel coordinates from the top-left):
${list}

For EVERY area say:
- "planogram_compliant": true if the product actually shown there is the expected one, false if it is a different product;
- "stock_level": "empty", "half" or "full" (how full the area is).
If unsure between two stock levels, choose the LOWER one (overstating stock costs the store more than a false alarm).
Reply with ONLY JSON: {"<area_id>": {"planogram_compliant": true, "stock_level": "full"}, ...}`;
  const r = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-api-key': KEY, 'anthropic-version': '2023-06-01' },
    body: JSON.stringify({
      model: MODEL, max_tokens: 4000,
      messages: [{ role: 'user', content: [
        { type: 'image', source: { type: 'base64', media_type: media, data: fs.readFileSync(file).toString('base64') } },
        { type: 'text', text: prompt },
      ] }],
    }),
  });
  const j = await r.json();
  if (!r.ok) throw new Error((j.error && j.error.message) || `API ${r.status}`);
  const text = (j.content || []).map((c) => c.text || '').join('');
  const found = JSON.parse(text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1));
  const out = {};
  for (const id of Object.keys(planogram[camera])) {
    const f = found[id] || {};
    const lvl = ['empty', 'half', 'full'].includes(f.stock_level) ? f.stock_level : 'full';
    out[id] = { planogram_compliant: f.planogram_compliant !== false, stock_level: lvl, stock_value: lvl === 'empty' ? 0 : lvl === 'half' ? 0.5 : 1 };
  }
  return out;
}
const analyzing = new Set();
async function autoAnalyze(camera, name) {
  const id = `${camera}/${name}`;
  if (analyzing.has(id)) return;
  if (!shelfModelOnline) await checkShelfModel();
  if (!shelfModelOnline && !KEY) return;
  analyzing.add(id);
  try {
    const out = shelfModelOnline ? await analyzeWithShelfModel(camera, name) : await analyzeWithClaude(camera, name);
    const res = readResults();
    (res[camera] = res[camera] || {})[name] = out;
    writeResults(res);
    console.log('analyzed', id);
  } catch (e) { console.log('analysis failed', id, e.message); }
  analyzing.delete(id);
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://x');
    const p = decodeURIComponent(url.pathname);
    if (req.method === 'OPTIONS') return send(res, 204, '');

    // the model's validation page (test run, score, tuning, download), served through the bridge for the Settings tab
    const MODEL_ROUTES = ['/gates', '/settings', '/samples', '/analyze_gates', '/score', '/redecide'];
    if (MODEL_ROUTES.includes(p) || p.startsWith('/sample/')) {
      try {
        const r = await fetch(SHELF_MODEL + req.url, {
          method: req.method, headers: req.headers['content-type'] ? { 'Content-Type': req.headers['content-type'] } : {},
          body: ['POST', 'PUT'].includes(req.method) ? await body(req) : undefined, signal: AbortSignal.timeout(300000),
        });
        res.writeHead(r.status, { 'Content-Type': r.headers.get('content-type') || 'application/octet-stream', 'Cache-Control': 'no-store' });
        return res.end(Buffer.from(await r.arrayBuffer()));
      } catch (e) { return send(res, 502, { error: `The shelf-check model is not reachable at ${SHELF_MODEL}. Start it with ./start.sh` }); }
    }
    if (p === '/api/status') return send(res, 200, status());
    if (p === '/api/results' && req.method === 'GET') return send(res, 200, readResults());
    if (p === '/api/planogram') return send(res, 200, { planogram, areas });
    // staffing plan by hour, worked out from a day of camera verdicts (tools/staffing_forecast.py)
    if (p === '/api/staffing') { try { return send(res, 200, fs.readFileSync(path.join(DATA, 'staffing.json'), 'utf8')); } catch (e) { return send(res, 200, {}); } }

    // ---- voice: text -> speech, speech -> text (ElevenLabs). The phone falls back to its own voice if this fails ----
    if (p === '/api/log') { console.log(`  [phone] ${url.searchParams.get('m')}`); return send(res, 200, { ok: true }); }
    if (p === '/api/voice') return send(res, 200, { enabled: !!EL_KEY, brain: !!(AGENT_ID && AGENT_KEY) });
    // the assistant's brain: the worker said something, with the state of the round attached
    if (p === '/api/assistant' && req.method === 'POST') {
      if (!AGENT_ID || !AGENT_KEY) return send(res, 503, { error: 'no ElevenLabs agent configured' });
      const { message, context } = JSON.parse((await body(req)).toString('utf8'));
      const t0 = Date.now();
      const raw = await askAgent(`ROUND STATE: ${JSON.stringify(context || {})}\nWORKER SAYS: ${String(message || '').slice(0, 500)}`);
      let out;
      try { out = JSON.parse(/\{[\s\S]*\}/.exec(raw)[0]); } catch (e) { out = { say: raw.trim().slice(0, 300), do: [] }; }
      const todo = (Array.isArray(out.do) ? out.do : []).filter((d) => d && typeof d.action === 'string');
      console.log(`assistant ${Date.now() - t0}ms: "${message}" -> "${String(out.say).slice(0, 80)}" ${JSON.stringify(todo)}`);
      return send(res, 200, { say: String(out.say || ''), do: todo });
    }
    if (p === '/api/speak' && req.method === 'GET') {
      const text = (url.searchParams.get('text') || '').slice(0, 400), lang = url.searchParams.get('lang') || 'en';
      if (!EL_KEY) return send(res, 503, { error: 'no ELEVENLABS_API_KEY' });
      if (!text.trim()) return send(res, 400, { error: 'no text' });
      const file = path.join(TTS_DIR, require('crypto').createHash('sha1').update(`${EL_VOICE}|${EL_TTS_MODEL}|${lang}|${text}`).digest('hex') + '.mp3');
      if (!fs.existsSync(file)) {
        const r = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${EL_VOICE}?output_format=mp3_44100_64`, {
          method: 'POST', headers: { 'xi-api-key': EL_KEY, 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, model_id: EL_TTS_MODEL, language_code: lang }),
        });
        if (!r.ok) return send(res, 502, { error: `elevenlabs ${r.status}: ${(await r.text()).slice(0, 200)}` });
        fs.writeFileSync(file, Buffer.from(await r.arrayBuffer()));
      }
      return send(res, 200, fs.readFileSync(file), 'audio/mpeg');
    }
    if (p === '/api/listen' && req.method === 'POST') {
      if (!EL_KEY) return send(res, 503, { error: 'no ELEVENLABS_API_KEY' });
      const audio = await body(req);
      const form = new FormData();
      form.append('model_id', 'scribe_v2');
      if (url.searchParams.get('lang')) form.append('language_code', url.searchParams.get('lang'));
      form.append('file', new Blob([audio], { type: 'audio/mp4' }), 'clip.m4a');
      const r = await fetch('https://api.elevenlabs.io/v1/speech-to-text', { method: 'POST', headers: { 'xi-api-key': EL_KEY }, body: form });
      if (!r.ok) return send(res, 502, { error: `elevenlabs ${r.status}: ${(await r.text()).slice(0, 200)}` });
      const j = await r.json();
      console.log(`  heard (${audio.length} bytes): "${j.text || ''}"`);
      return send(res, 200, { text: j.text || '' });
    }

    // training playground: what has been collected so far, for the open model's next training round
    if (p === '/api/training') {
      const results = readResults(), contrib = (() => { try { return JSON.parse(fs.readFileSync(CONTRIB, 'utf8')); } catch (e) { return []; } })();
      const cams = {}; let photos = 0, areasDone = 0;
      for (const cam of Object.keys(planogram)) {
        const dir = path.join(PHOTOS, cam), n = fs.existsSync(dir) ? fs.readdirSync(dir).filter(safeName).length : 0;
        cams[cam] = n; photos += n;
        for (const r of Object.values(results[cam] || {})) areasDone += Object.keys(r).length;
      }
      const by = {}; for (const c of contrib) by[c.by] = (by[c.by] || 0) + 1;
      const next = MILESTONES.find((m) => m > photos) || photos + 1000, level = MILESTONES.filter((m) => m <= photos).length + 1;
      return send(res, 200, { photos, areas: areasDone, cameras: cams, contributors: Object.entries(by).map(([name, n]) => ({ name, photos: n })).sort((a, b) => b.photos - a.photos),
        level, next, prev: MILESTONES.filter((m) => m <= photos).pop() || 0, milestones: MILESTONES });
    }
    // Settings > Send to phone: one photo, judged now, scored against the hand labels when it has them; the phone opens it
    if (p === '/api/focus') { try { return send(res, 200, fs.readFileSync(FOCUS, 'utf8')); } catch (e) { return send(res, 200, {}); } }
    if (p === '/api/show' && req.method === 'POST') {
      const name = url.searchParams.get('name'), cam = url.searchParams.get('camera') || 'auto';
      if (!safeName(name)) return send(res, 400, { error: 'bad file name' });
      if (cam !== 'auto' && !planogram[cam]) return send(res, 400, { error: `unknown camera ${cam}` });
      if (!shelfModelOnline && !(await checkShelfModel())) return send(res, 503, { error: 'The shelf-check model is not running. Start it with ./start.sh' });
      try {
        const { camera, out } = await judgeAndKeep(await body(req), cam, name);
        let score = null;
        try {
          const sr = await fetch(`${SHELF_MODEL}/score`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ submission: { [camera]: { [name]: out } } }) });
          const sj = await sr.json(); if (sj.areas) score = { points: sj.points, max: sj.max, counts: sj.counts };
        } catch (e) { /* no score */ }
        const focus = { camera, name, at: Date.now(), verdicts: out, score };
        fs.writeFileSync(FOCUS, JSON.stringify(focus));
        console.log(`sent to the phone: ${camera}/${name}` + (score ? ` (${score.points} of ${score.max} points)` : ' (no labels, no score)'));
        return send(res, 200, focus);
      } catch (e) { return send(res, 500, { error: e.message }); }
    }
    // Settings > Update shelves: one photo of an uploaded folder, judged now and kept; the phone picks it up on its next look
    if (p === '/api/photo' && req.method === 'POST') {
      const name = url.searchParams.get('name'), cam = url.searchParams.get('camera') || 'auto';
      if (!safeName(name)) return send(res, 400, { error: 'bad file name' });
      if (cam !== 'auto' && !planogram[cam]) return send(res, 400, { error: `unknown camera ${cam}` });
      if (!shelfModelOnline && !(await checkShelfModel())) return send(res, 503, { error: 'The shelf-check model is not running. Start it with ./start.sh' });
      try { const { camera } = await judgeAndKeep(await body(req), cam, name); return send(res, 200, { ok: true, camera, name }); }
      catch (e) { return send(res, 500, { error: e.message }); }
    }
    // start fresh: remove the shelf pictures and their results (the training contributors and the rounds stay)
    if (p === '/api/clear-shelves' && req.method === 'POST') {
      fs.rmSync(PHOTOS, { recursive: true, force: true }); fs.mkdirSync(PHOTOS, { recursive: true });
      writeResults({}); fs.rmSync(DETAILS, { force: true }); fs.rmSync(FOCUS, { force: true });
      return send(res, 200, { ok: true });
    }
    if (p === '/api/upload' && req.method === 'POST') {
      const camera = url.searchParams.get('camera'), name = url.searchParams.get('name');
      if (!planogram[camera]) return send(res, 400, { error: `unknown camera ${camera}` });
      if (!safeName(name)) return send(res, 400, { error: 'bad file name' });
      fs.mkdirSync(path.join(PHOTOS, camera), { recursive: true });
      fs.writeFileSync(path.join(PHOTOS, camera, name), await body(req));
      const by = (url.searchParams.get('by') || '').trim().slice(0, 40);
      if (by) {
        let c = []; try { c = JSON.parse(fs.readFileSync(CONTRIB, 'utf8')); } catch (e) { /* first one */ }
        if (!c.some((x) => x.camera === camera && x.name === name)) { c.push({ camera, name, by, at: Date.now() }); fs.writeFileSync(CONTRIB, JSON.stringify(c, null, 1)); }
      }
      autoAnalyze(camera, name);
      return send(res, 200, { ok: true });
    }
    // anyone (me, or the friends' model) can post results: { camera: { photo: { area: {planogram_compliant, stock_level, stock_value} } } }
    if (p === '/api/results' && req.method === 'POST') {
      const incoming = JSON.parse((await body(req)).toString('utf8'));
      const res0 = readResults();
      for (const [cam, photos] of Object.entries(incoming)) for (const [name, a] of Object.entries(photos)) (res0[cam] = res0[cam] || {})[name] = a;
      writeResults(res0);
      return send(res, 200, { ok: true });
    }
    if (p === '/api/details' && req.method === 'GET') return send(res, 200, readDetails());
    if (p === '/api/analyze' && req.method === 'POST') {
      if (!shelfModelOnline && !(await checkShelfModel()) && !KEY) return send(res, 400, { error: 'The shelf-check model is not running and no ANTHROPIC_API_KEY is set' });
      autoAnalyze(url.searchParams.get('camera'), url.searchParams.get('name'));
      return send(res, 200, { ok: true });
    }
    if (p === '/api/rounds' && req.method === 'GET') return send(res, 200, readRounds());
    if (p === '/api/rounds' && req.method === 'POST') {
      const r = JSON.parse((await body(req)).toString('utf8'));
      const all = readRounds();
      all.push({ id: `r-${Date.now()}`, demo: false, ...r });
      writeRounds(all);
      return send(res, 200, { ok: true });
    }
    if (p === '/api/seed-demo' && req.method === 'POST') {
      writeRounds([...readRounds().filter((r) => !r.demo), ...seedDemoRounds()]);
      return send(res, 200, { ok: true });
    }
    if (p === '/api/clear-demo' && req.method === 'POST') { writeRounds(readRounds().filter((r) => !r.demo)); return send(res, 200, { ok: true }); }
    if (p === '/api/clear' && req.method === 'POST') {
      fs.rmSync(PHOTOS, { recursive: true, force: true }); fs.mkdirSync(PHOTOS, { recursive: true }); writeResults({}); fs.rmSync(DETAILS, { force: true }); fs.rmSync(CONTRIB, { force: true });
      return send(res, 200, { ok: true });
    }

    // photos and the web pages
    let file = null;
    if (p.startsWith('/photos/')) file = path.join(PHOTOS, p.slice(8));
    else file = path.join(__dirname, 'public', p === '/' ? 'index.html' : p);
    const base = p.startsWith('/photos/') ? PHOTOS : path.join(__dirname, 'public');
    if (!path.resolve(file).startsWith(base) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, { error: 'not found' });
    return send(res, 200, fs.readFileSync(file), TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream');
  } catch (e) {
    send(res, 500, { error: e.message });
  }
});

server.listen(PORT, '0.0.0.0', () => {
  const ips = status().ips;
  console.log(`Shelf Round bridge on http://localhost:${PORT}`);
  ips.forEach((ip) => console.log(`  phone / other devices: http://${ip}:${PORT}`));
  console.log(AGENT_ID && AGENT_KEY ? 'Assistant brain (ElevenLabs agent Mia): ON' : 'Assistant brain: off (needs ELEVENLABS_AGENT_ID and ELEVENLABS_AGENT_KEY in bridge/.env)');
  if (AGENT_ID && AGENT_KEY) openAgent().catch(() => {}); // warm up
  console.log(EL_KEY ? 'Voice (ElevenLabs): ON' : 'Voice (ElevenLabs): off (put ELEVENLABS_API_KEY in bridge/.env)');
  checkShelfModel().then((on) => console.log(on
    ? `Analysis: shelf-check model at ${SHELF_MODEL}`
    : `Analysis: shelf-check model not reachable at ${SHELF_MODEL} (start it: python app/server.py --port 8790)` + (KEY ? `; falling back to ${MODEL}` : '')));
  setInterval(checkShelfModel, 15000);
});
