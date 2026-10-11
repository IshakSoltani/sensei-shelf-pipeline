// Pretend a camera just sent a new picture where one shelf area is empty. The phone should buzz.
//   node bridge/test-alert.js          sends it (a backup of results.json is saved first)
//   node bridge/test-alert.js undo     puts results.json back as it was
const fs = require('fs');
const path = require('path');
const FILE = path.join(__dirname, 'data', 'results.json');
const BACKUP = FILE + '.before-test';
const BASE = `http://localhost:${process.env.PORT || 8787}`;

(async () => {
  if (process.argv[2] === 'undo') {
    if (!fs.existsSync(BACKUP)) return console.log('Nothing to undo.');
    fs.copyFileSync(BACKUP, FILE); fs.unlinkSync(BACKUP);
    return console.log('Restored. The bridge reads the file each time, so the phone catches up in a few seconds.');
  }
  const res = JSON.parse(fs.readFileSync(FILE, 'utf8'));
  const cam = Object.keys(res)[0];
  const newest = Object.keys(res[cam]).sort((a, b) => parseInt(b) - parseInt(a))[0];
  const areas = Object.entries(res[cam][newest]);
  const target = areas.find(([, r]) => r.stock_level !== 'empty' && r.planogram_compliant !== false) || areas[0];
  const copy = JSON.parse(JSON.stringify(res[cam][newest]));
  copy[target[0]] = { ...copy[target[0]], stock_level: 'empty', stock_value: 0, planogram_compliant: true };
  if (!fs.existsSync(BACKUP)) fs.copyFileSync(FILE, BACKUP);
  const name = `${Math.floor(Date.now() / 1000) + 1000000000}.jpg`; // sorts as the newest
  await fetch(`${BASE}/api/results`, { method: 'POST', body: JSON.stringify({ [cam]: { [name]: copy } }) });
  console.log(`Sent: area ${target[0]} on camera ${cam} is now empty. Run "node bridge/test-alert.js undo" afterwards.`);
})();
