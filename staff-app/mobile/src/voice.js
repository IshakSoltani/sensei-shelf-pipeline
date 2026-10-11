// Hands-free voice: the phone talks (ElevenLabs through the bridge, the iPhone's own voice if that fails)
// and listens for a few words (ElevenLabs speech-to-text through the bridge).
import { AudioModule, RecordingPresets, createAudioPlayer, requestRecordingPermissionsAsync, setAudioModeAsync } from 'expo-audio';
import * as Speech from 'expo-speech';

let base = null;   // bridge address, set once by the app
let on = false;    // the speaker button
let status = 'idle'; // idle | speaking | listening | thinking
let token = 0;     // bumped by cancel(), so old flows stop quietly
let holding = null; // an announcement (new alert) is being spoken: everything else waits
let abortHear = null;
const subs = new Set();
const dbg = (m) => { try { if (base) fetch(`${base}/api/log?m=${encodeURIComponent(m)}`).catch(() => {}); } catch (e) { /* no log */ } };

export const setBridge = (b) => { base = b; };
export const isOn = () => on;
export const setOn = (v) => { on = v; if (!v) cancel(); };
export const onStatus = (fn) => { subs.add(fn); return () => subs.delete(fn); };
const setStatus = (s) => { status = s; subs.forEach((f) => f(s)); };

export function cancel() {
  token++;
  if (abortHear) abortHear();
  try { Speech.stop(); } catch (e) { /* nothing is speaking */ }
  if (player) { try { player.pause(); } catch (e) { /* already gone */ } }
  setStatus('idle');
}

let player = null;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// speak through ElevenLabs; resolves when the audio has finished (or throws, so the caller can fall back)
async function playTts(text, lang) {
  const url = `${base}/api/speak?lang=${lang}&text=${encodeURIComponent(text)}`;
  const ping = await fetch(url);
  if (!ping.ok) throw new Error(`speak ${ping.status}`);
  await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false });
  await new Promise((resolve, reject) => {
    if (player) { try { player.remove(); } catch (e) { /* ignore */ } }
    const p = createAudioPlayer({ uri: url });
    player = p;
    const timer = setTimeout(() => { sub.remove(); resolve(); }, 20000);
    const sub = p.addListener('playbackStatusUpdate', (st) => {
      if (st.didJustFinish) { clearTimeout(timer); sub.remove(); resolve(); }
    });
    try { p.play(); } catch (e) { clearTimeout(timer); sub.remove(); reject(e); }
  });
}
const playPhone = (text) => new Promise((resolve) => {
  try { Speech.speak(text, { language: 'en-GB', rate: 0.98, onDone: resolve, onStopped: resolve, onError: resolve }); } catch (e) { resolve(); }
});

async function speakNow(text, lang = 'en') {
  setStatus('speaking');
  try { await playTts(text, lang); } catch (e) { dbg(`voice fell back to phone voice: ${e && e.message}`); await playPhone(text); }
}

// Say something. Resolves false if it was interrupted (cancel or a newer flow).
export async function say(text, lang = 'en') {
  if (!on || !base) return false;
  if (holding) await holding;
  const mine = token;
  await speakNow(text, lang);
  if (mine !== token) return false;
  setStatus('idle');
  return true;
}

// Something that must be heard now (a new camera alert): pauses the current flow, then lets it carry on.
export async function announce(text, lang = 'en') {
  if (!on || !base) return;
  if (abortHear) abortHear();
  try { Speech.stop(); } catch (e) { /* ignore */ }
  if (player) { try { player.pause(); } catch (e) { /* ignore */ } }
  const run = (async () => { await speakNow(text, lang); setStatus('idle'); })();
  holding = run;
  try { await run; } finally { if (holding === run) holding = null; }
}

// Listen until the worker stops talking (or `ms` at most). Returns the words (lowercase),
// '' if nothing was said or understood, null if interrupted.
export async function hear(ms = 9000) {
  if (!on || !base) return null;
  if (holding) { await holding; }
  const mine = token;
  let aborted = false;
  abortHear = () => { aborted = true; };
  try {
    const perm = await requestRecordingPermissionsAsync();
    if (!perm.granted) { dbg('microphone permission not granted'); return ''; }
    await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: true });
    const rec = new AudioModule.AudioRecorder({ ...RecordingPresets.HIGH_QUALITY, isMeteringEnabled: true });
    await rec.prepareToRecordAsync();
    setStatus('listening');
    rec.record();
    const t0 = Date.now();
    let floor = null, samples = [], spoke = false, lastLoud = 0;
    while (Date.now() - t0 < ms && !aborted && mine === token) {
      await sleep(80);
      const m = rec.getStatus().metering;
      if (typeof m !== 'number') continue;
      const age = Date.now() - t0;
      if (age < 450) { samples.push(m); continue; } // the first moments tell us how noisy the room is
      if (floor === null) floor = samples.length ? samples.reduce((x, y) => x + y, 0) / samples.length : -50;
      if (m > floor + 10) { spoke = true; lastLoud = age; }
      if (spoke && age - lastLoud > 1000) break;       // spoke, then a second of quiet: done
      if (!spoke && age > 5500) break;                  // nobody is talking
    }
    await rec.stop();
    await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false });
    if (aborted || mine !== token) { setStatus('idle'); return null; }
    if (!spoke && floor !== null) { setStatus('idle'); return ''; }
    dbg(`sending audio, ${Math.round((Date.now() - t0) / 100) / 10}s`);
    setStatus('thinking');
    const blob = await (await fetch(rec.uri)).blob();
    const r = await fetch(`${base}/api/listen`, { method: 'POST', body: blob });
    const j = await r.json();
    setStatus('idle');
    return mine !== token ? null : String(j.text || '').toLowerCase().trim();
  } catch (e) {
    dbg(`hear failed: ${e && e.message}`);
    setStatus('idle');
    return '';
  } finally {
    abortHear = null;
  }
}

// A few words are enough. English and Portuguese.
const WORDS = {
  done: /\b(done|finish(ed)?|complete(d)?|ready|feito|pronto|acabei|terminei|conclu[ií]do)\b/,
  here: /\b(here|arrived|i'?m here|aqui|cheguei|estou aqui)\b/,
  next: /\b(next|continue|go on|pr[oó]ximo|seguinte)\b/,
  repeat: /\b(repeat|again|say that|repete|outra vez)\b/,
  yes: /\b(yes|yeah|yep|sure|ok(ay)?|sim|claro)\b/,
  no: /\b(no|nope|n[aã]o)\b/,
};
export function intent(text) {
  if (!text) return null;
  for (const k of ['repeat', 'done', 'here', 'next', 'yes', 'no']) if (WORDS[k].test(text)) return k;
  return null;
}

// Listen up to `tries` times until one of the wanted words comes up. Returns that word, or null.
export async function listenFor(wanted, tries = 3, ms = 4000) {
  const mine = token;
  for (let n = 0; n < tries; n++) {
    const text = await hear(ms);
    if (mine !== token) return null;
    if (!on) return null;
    if (text === null) { n--; continue; } // interrupted by an announcement: does not count
    const k = intent(text);
    if (k && wanted.includes(k)) return k;
  }
  return null;
}

// ---- the assistant: each screen says what is going on and what "done" means there ----
const NOTHING = { context: () => ({}), actions: {} };
let screenVoice = null, defaultVoice = NOTHING;
const current = () => screenVoice || defaultVoice;
export const registerDefault = (v) => { defaultVoice = v; return () => { if (defaultVoice === v) defaultVoice = NOTHING; }; };
export const register = (v) => { screenVoice = v; return () => { if (screenVoice === v) screenVoice = null; }; };
let history = [];

// product names the AI gives back -> items on the screen
const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9\u00c0-\u017f ]/g, ' ').replace(/\s+/g, ' ').trim();
export function matchNames(names, list, nameOf = (x) => x.name) {
  const out = new Set();
  for (const n of names || []) {
    const q = norm(n);
    if (!q) continue;
    let hit = list.find((x) => norm(nameOf(x)) === q) || list.find((x) => norm(nameOf(x)).includes(q) || q.includes(norm(nameOf(x))));
    if (!hit) { const w = q.split(' ').filter((x) => x.length > 3); hit = list.find((x) => w.some((y) => norm(nameOf(x)).includes(y))); }
    if (hit) out.add(hit);
  }
  return [...out];
}

async function askBrain(message) {
  const c = current();
  const r = await fetch(`${base}/api/assistant`, {
    method: 'POST', body: JSON.stringify({ message, context: { ...c.context(), available_actions: Object.keys(c.actions) } }),
  });
  if (!r.ok) throw new Error(`brain ${r.status}`);
  return r.json();
}

// The assistant goes along with the worker: optionally speaks first, then keeps listening, answers,
// and presses the app's buttons for them, until the screen changes or voice is switched off.
export async function converse(opener) {
  if (!on || !base) return;
  cancel();                // only one conversation at a time: a new screen takes over
  const mine = token;
  if (opener && !(await say(opener))) return;
  while (on && mine === token) {
    const text = await hear();
    if (mine !== token || !on) return;
    if (text === null) continue;           // an alert was read out meanwhile
    if (!text) continue;                    // silence: keep listening
    let reply = null;
    try { reply = await askBrain(text); } catch (e) { dbg(`assistant failed: ${e && e.message}`); }
    if (!reply) {                           // no brain: simple words still work
      const k = intent(text);
      const act = k === 'repeat' ? 'repeat' : k === 'done' || k === 'here' || k === 'next' ? 'done' : null;
      reply = { say: act ? '' : '', do: act ? [{ action: act }] : [] };
    }
    const c = current();
    const ends = c.ends || [];
    const now = (reply.do || []).filter((d) => c.actions[d.action] && !ends.includes(d.action));
    const last = (reply.do || []).find((d) => c.actions[d.action] && ends.includes(d.action));
    now.forEach((d) => { try { c.actions[d.action](d); } catch (e) { dbg(`action ${d.action} failed: ${e && e.message}`); } });
    if (reply.say && !(await say(reply.say))) return;
    if (last) { c.actions[last.action](last); return; } // moves to another screen, which starts its own conversation
  }
}

// the Ask button: wake her up when the microphone is idle
export const ask = () => converse(null);
