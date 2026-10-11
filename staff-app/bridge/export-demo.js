// Makes ONE self-contained file of the manager dashboard (data and pictures inside), so it can be
// opened by double-clicking, e-mailed, or put on any web page. No server needed.
//   node bridge/export-demo.js     ->  bridge/demo/manager-demo.html
const fs = require('fs');
const path = require('path');

const root = __dirname;
const read = (p, d) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return d; } };
const results = read(path.join(root, 'data', 'results.json'), {});
const rounds = read(path.join(root, 'data', 'rounds.json'), []);
const appData = path.join(root, '..', 'mobile', 'src', 'data');
const planogram = read(path.join(appData, 'planogram.json'), {});
const areas = read(path.join(appData, 'areas.json'), {});
const staffing = read(path.join(root, 'data', 'staffing.json'), {});

// only the newest picture of each camera is shown, so only those go in
const photos = {};
for (const cam of Object.keys(results)) {
  const newest = Object.keys(results[cam]).sort((a, b) => parseInt(b) - parseInt(a))[0];
  const file = path.join(root, 'data', 'photos', cam, newest || '');
  if (newest && fs.existsSync(file)) photos[`${cam}/${newest}`] = 'data:image/jpeg;base64,' + fs.readFileSync(file).toString('base64');
}

// product pictures (small), so the dashboard shows them without any server
const product = {};
const pdir = path.join(root, 'public', 'product-images');
if (fs.existsSync(pdir)) for (const f of fs.readdirSync(pdir)) if (f.endsWith('.jpg')) product[f.slice(0, -4)] = 'data:image/jpeg;base64,' + fs.readFileSync(path.join(pdir, f)).toString('base64');

const snapshot = { results, rounds, staffing, planogram: { planogram, areas }, photos, product, made: new Date().toISOString() };
const shim = `<script>
// Snapshot mode: answer the dashboard's data requests from inside this file.
window.__SNAPSHOT = ${JSON.stringify(snapshot)};
const __realFetch = window.fetch.bind(window);
window.fetch = (url, opts) => {
  const u = String(url), S = window.__SNAPSHOT, ok = (d) => Promise.resolve(new Response(JSON.stringify(d), { headers: { 'Content-Type': 'application/json' } }));
  if (u.startsWith('/api/results')) return ok(S.results);
  if (u.startsWith('/api/planogram')) return ok(S.planogram);
  if (u.startsWith('/api/rounds')) return ok(S.rounds);
  if (u.startsWith('/api/staffing')) return ok(S.staffing || {});
  if (u.startsWith('/api/')) return ok({ ok: true });
  return __realFetch(url, opts);
};
</script>`;

let html = fs.readFileSync(path.join(root, 'public', 'manager.html'), 'utf8');
html = html.replace('<script>\nconst $ =', shim + '\n<script>\nconst $ =');
html = html.replace('im.src = `/photos/${c.cam}/${c.name}`;', 'im.src = (window.__SNAPSHOT && window.__SNAPSHOT.photos[`${c.cam}/${c.name}`]) || `/photos/${c.cam}/${c.name}`;');
html = html.replace('setInterval(load, 15000);', ''); // nothing changes in a snapshot
html = html.replace('<title>Shelf Round · Store manager</title>', '<title>Shelf Round · Store manager (snapshot)</title>');

fs.mkdirSync(path.join(root, 'demo'), { recursive: true });
const out = path.join(root, 'demo', 'manager-demo.html');
fs.writeFileSync(out, html);
console.log(`Saved ${out} (${(fs.statSync(out).size / 1e6).toFixed(1)} MB, ${Object.keys(photos).length} pictures, ${rounds.length} rounds)`);
