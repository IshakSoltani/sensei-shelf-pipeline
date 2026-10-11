import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Animated, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import Constants from 'expo-constants';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import { useFonts, Figtree_300Light, Figtree_400Regular, Figtree_500Medium } from '@expo-google-fonts/figtree';
import { Feather, Ionicons } from '@expo/vector-icons';

import { colors, font, radius } from './src/theme';
import Pick from './src/screens/Pick';
import Route from './src/screens/Route';
import Complete from './src/screens/Complete';
import GoTo from './src/ui/GoTo';
import ProductThumb from './src/ui/ProductThumb';
import PeekCard from './src/ui/PeekCard';
import Live from './src/screens/Live';
import Report from './src/screens/Report';
import Result from './src/screens/Result';
import { announce, ask, cancel, converse, isOn, onStatus, register, registerDefault, setBridge, setOn } from './src/voice';
import { alertBuzz, choose, confirm, menuLayout, smoothLayout, soft, success, tap } from './src/feel';
import { CONFIG, WORDS, newAlerts, bayByCode, dataInfo, newestPhotos, setResults, autoTrips, oneTrip, bayRank, buildTasks, colorOf, fmtCrates, planRound, splitTrips } from './src/logic/tasks';

// The bridge runs on the same computer as the Expo server (port 8787)
const FALLBACK_HOST = '194.210.234.128'; // this Mac's address on the wifi, used only if it can't be found out
const bridgeBase = () => {
  const port = ':8787';
  try { if (Platform.OS === 'web') return `http://${window.location.hostname}${port}`; } catch (e) { /* not web */ }
  try { // the address Expo Go loaded the app from
    const host = Constants.expoConfig && Constants.expoConfig.hostUri;
    if (host) return `http://${host.split(':')[0]}${port}`;
  } catch (e) { /* try the next way */ }
  try {
    const url = require('react-native/Libraries/NativeModules/specs/NativeSourceCode').default.getConstants().scriptURL;
    const m = /\/\/([^:/]+)/.exec(url);
    if (m) return `http://${m[1]}${port}`;
  } catch (e) { /* use the fallback */ }
  return `http://${FALLBACK_HOST}${port}`;
};

// Voice assistant (ElevenLabs): paused for now. Set to true, and rename bridge/.env.paused back to .env, to bring it back.
const VOICE_FEATURE = false;

const TABS = [
  { key: 'round', label: 'Round', icon: 'clipboard' },
  { key: 'live', label: 'Live shelves', icon: 'camera' },
  { key: 'report', label: 'Report', icon: 'flag' },
];

export default function App() {
  const [loaded] = useFonts({ Figtree_300Light, Figtree_400Regular, Figtree_500Medium });
  if (!loaded) return null;
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <Shell />
    </SafeAreaProvider>
  );
}

function Shell() {
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState('round');
  const [open, setOpen] = useState(false);
  const [screen, setScreen] = useState('home'); // home -> pick -> route (inside the Round tab)
  const [round, setRound] = useState(null); // { trips, t, start }
  const [fixed, setFixed] = useState(() => new Set()); // tasks already done (hidden from Home)
  const [presetBay, setPresetBay] = useState(null); // bay chosen from Live shelves, to prefill Report
  const [employee, setEmployee] = useState('Worker 1'); // who is doing the round, shown to the manager
  const [version, setVersion] = useState(0); // bumps when new camera results are applied
  const latest = useRef(null);
  const lastJ = useRef(undefined); // the previous results, to see what is new
  const [voiceOn, setVoiceOn] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState('idle');
  const [alert, setAlert] = useState(null); // the "new on your shelves" banner
  const applied = useRef('');
  const [poll, setPoll] = useState(0);
  const [bridgeNote, setBridgeNote] = useState('');
  const [go, setGo] = useState(null); // full-screen "Go to Aisle N"
  const [peek, setPeek] = useState(null); // product held on the pick list
  const [reports, setReports] = useState([]); // problems reported by staff, joined to the next round
  const [lastDone, setLastDone] = useState({ tasks: [], seconds: 0 });
  const [focusView, setFocusView] = useState(null); // a photo sent from the dashboard (Settings > Send to phone)
  const [pendingFocus, setPendingFocus] = useState(null);
  const lastFocus = useRef(undefined);
  useEffect(() => { setBridge(bridgeBase()); return onStatus(setVoiceStatus); }, []);
  const toggleVoice = () => {
    const v = !isOn();
    setOn(v); setVoiceOn(v); choose();
    if (v) { setOn(true); converse("Hi, I'm Mia. I'll go along with you and press the buttons for you."); }
  };
  // what the assistant knows when no screen has said anything more specific
  useEffect(() => registerDefault({
    context: () => {
      const tasks = buildTasks(fixed, reports);
      return {
        screen: `${tab} tab, ${screen}`,
        shelves_needing_attention: tasks.map((x) => ({ product: x.name, problem: WORDS[x.type].label, aisle: (bayByCode[x.bay] || {}).aisle, bay_name: (bayByCode[x.bay] || {}).bay_name, areas: x.count || 1 })),
      };
    },
    actions: {},
  }), [fixed, reports, tab, screen, version]);
  const showAlert = (list) => {
    const lines = list.slice(0, 2).map((a) => (a.type === 'empty' ? `${a.emoji || '🛒'} ${a.name} ran out in ${a.bay}` : `↔️ Wrong product in ${a.bay}`));
    if (list.length > 2) lines.push(`and ${list.length - 2} more`);
    const aisles = [...new Set(list.map((a) => a.aisle).filter(Boolean))];
    const title = list.some((a) => a.type === 'empty')
      ? (aisles.length === 1 ? `Aisle ${aisles[0]} needs you` : 'Shelves need you')
      : 'Something is out of place';
    alertBuzz();
    const spoken = list.slice(0, 3).map((a) => (a.type === 'empty' ? `${a.name} ran out in ${a.bay}` : `Wrong product in ${a.bay}`));
    announce(`${title}. ${spoken.join('. ')}.`);
    smoothLayout();
    setAlert({ id: Date.now(), title, body: lines.join('\n') });
  };
  useEffect(() => {
    if (!alert) return;
    const id = setTimeout(() => { smoothLayout(); setAlert(null); }, 8000);
    return () => clearTimeout(id);
  }, [alert]);

  // Ask the bridge for results every few seconds (silently does nothing if it is not running)
  useEffect(() => {
    const base = bridgeBase();
    if (!base) return;
    let stop = false;
    const tick = async () => {
      try {
        // a photo sent from the dashboard opens its result; the first look is only the starting point
        let sent = false;
        try {
          const f = await (await fetch(`${base}/api/focus`)).json();
          if (lastFocus.current === undefined) lastFocus.current = (f && f.at) || 0;
          else if (!stop && f && f.at > lastFocus.current) { lastFocus.current = f.at; sent = true; setPendingFocus(f); }
        } catch (e) { /* an older bridge without /api/focus */ }
        const r = await fetch(`${base}/api/results`);
        const j = await r.json();
        if (!stop && Object.keys(j).length) {
          // first look is only the starting point; after that, anything newly empty or wrong buzzes the phone
          const fresh = lastJ.current === undefined ? [] : newAlerts(lastJ.current, j);
          lastJ.current = j;
          if (fresh.length && !sent) showAlert(fresh);   // a sent photo shows its own result instead
        }
        if (!stop) { latest.current = { j, base }; setBridgeNote(''); setPoll((n) => n + 1); }
      } catch (e) {
        if (!stop) setBridgeNote(`Bridge not reachable (${base})`);
        if (!stop && latest.current && latest.current.j !== null) { latest.current = { j: null, base }; setPoll((n) => n + 1); } // bridge gone: back to demo
      }
    };
    tick();
    const id = setInterval(tick, 4000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  // Apply new results, but never in the middle of a round (the plan is made once)
  useEffect(() => {
    const l = latest.current;
    if (!l || screen === 'pick' || screen === 'route') return;
    const key = JSON.stringify(l.j);
    if (key === applied.current) return;
    const before = newestPhotos();
    applied.current = key;
    setResults(l.j, l.base);
    const after = newestPhotos();
    // a camera that sent a new picture is the truth again: forget what we ticked off there
    const changed = Object.keys(after).filter((c) => after[c] !== before[c]);
    if (changed.length) setFixed((f) => new Set([...f].filter((k) => !changed.some((c) => k.startsWith(`${c}_`)))));
    setVersion((v) => v + 1);
  }, [poll, screen]);

  // open a sent photo's result, but never in the middle of a round (it waits until the worker is back home)
  useEffect(() => {
    if (!pendingFocus || screen === 'pick' || screen === 'route') return;
    success(); smoothLayout();
    setFocusView(pendingFocus); setPendingFocus(null); setOpen(false); setTab('result');
  }, [pendingFocus, screen]);

  const goHome = () => { smoothLayout(); setRound(null); setScreen('home'); };
  const setMenu = (v) => { menuLayout(); setOpen(v); };
  const pick = (key) => { setPresetBay(null); choose(); menuLayout(); setTab(key); setOpen(false); };
  return (
    <View style={s.root}>
      <View style={s.screen}>
        {tab === 'result' && focusView ? <Result focus={focusView} base={bridgeBase()} onClose={() => { smoothLayout(); setTab('round'); }} />
          : tab === 'live' ? <Live key={`live-${version}`} fixed={fixed} onReport={(bay) => { setPresetBay(bay); setTab('report'); }} />
          : tab === 'report' ? <Report key={presetBay || 'none'} preset={presetBay} reports={reports} onSend={(r) => setReports((x) => [...x, r])} />
          : screen === 'pick' ? (
            <Pick key={round.t} fixed={fixed} onPeek={setPeek} trips={round.trips} t={round.t} mode={round.mode}
              onSwap={(outBay, inBay) => {
                const [a, b] = round.trips;
                const out = a.stops.find((st) => st.bay === outBay), inn = b.stops.find((st) => st.bay === inBay);
                const make = (list) => ({ stops: list.sort((x, y) => bayRank(x.bay) - bayRank(y.bay)), load: list.reduce((n, st) => n + st.load, 0) });
                setRound({ ...round, trips: [make(a.stops.filter((st) => st !== out).concat(inn)), make(b.stops.filter((st) => st !== inn).concat(out))] });
              }}
              onCart={(crates) => {
                // a different cart size changes how many trips are needed: plan again (nothing is loaded yet at this point)
                CONFIG.trolleyCapacityCrates = crates;
                const stops = round.stops || round.trips.flatMap((tr) => tr.stops);
                setRound({ ...round, stops, mode: 'auto', trips: autoTrips(stops) });
              }}
              onMoveItem={(productId) => {
                // bring one product (all its jobs) from the later trips into this trip
                const dest = round.t;
                const mk = (stops) => ({ stops, load: stops.reduce((n, st) => n + st.load, 0) });
                const taken = [];
                const rest = round.trips.map((tr, idx) => {
                  if (idx <= dest) return tr;
                  const stops = tr.stops.map((st) => {
                    const mine = st.tasks.filter((x) => !x.reported && x.productId === productId);
                    if (!mine.length) return st;
                    taken.push({ st, mine });
                    const tasks = st.tasks.filter((x) => !mine.includes(x));
                    return { ...st, tasks, load: tasks.reduce((n, x) => n + x.load, 0) };
                  }).filter((st) => st.tasks.length);
                  return mk(stops);
                }).filter((tr, idx) => idx <= dest || tr.stops.length);
                if (!taken.length) return;
                let stops = rest[dest].stops.slice();
                for (const { st, mine } of taken) {
                  const at = stops.findIndex((x) => x.bay === st.bay);
                  if (at >= 0) { const tasks = [...stops[at].tasks, ...mine]; stops[at] = { ...stops[at], tasks, load: tasks.reduce((n, x) => n + x.load, 0) }; }
                  else stops.push({ ...st, tasks: mine, load: mine.reduce((n, x) => n + x.load, 0) });
                }
                stops.sort((a, b) => bayRank(a.bay) - bayRank(b.bay));
                rest[dest] = mk(stops);
                setRound({ ...round, trips: rest });
              }}
              onMove={(bay) => {
                const from = round.trips.findIndex((tr, i) => i > 0 && tr.stops.some((st) => st.bay === bay));
                if (from < 0) return;
                const stop = round.trips[from].stops.find((st) => st.bay === bay);
                const trips = round.trips.map((tr, i) => {
                  const list = i === 0 ? [...tr.stops, stop].sort((a, b) => bayRank(a.bay) - bayRank(b.bay))
                    : i === from ? tr.stops.filter((st) => st !== stop) : tr.stops;
                  return { stops: list, load: list.reduce((n, st) => n + st.load, 0) };
                }).filter((tr) => tr.stops.length);
                setRound({ ...round, trips });
              }}
              onMode={(mode, loaded) => {
                const stops = round.stops || round.trips.flatMap((tr) => tr.stops);
                setRound({ ...round, stops, mode, trips: mode === 'auto' ? autoTrips(stops) : mode === 'one' ? oneTrip(stops) : splitTrips(stops, mode, loaded) });
              }}
              onStart={(loaded) => {
                let trips = round.trips;
                if (loaded) {
                  // Start with only what was loaded: the rest becomes the next trip
                  const trip = round.trips[round.t];
                  const mk = (stops) => ({ stops, load: stops.reduce((n, st) => n + st.load, 0) });
                  const part = (keep) => trip.stops
                    .map((st) => { const tasks = st.tasks.filter((x) => keep(x)); return { ...st, tasks, load: tasks.reduce((n, x) => n + x.load, 0) }; })
                    .filter((st) => st.tasks.length);
                  const now = part((x) => x.reported || loaded.has(x.productId));
                  const later = part((x) => !(x.reported || loaded.has(x.productId)));
                  trips = [...round.trips.slice(0, round.t), mk(now), ...(later.length ? [mk(later)] : []), ...round.trips.slice(round.t + 1)];
                }
                setRound({ ...round, trips, start: Date.now() });
                setScreen('route');
              }} />
          ) : screen === 'route' ? (
            <Route goOpen={!!go} key={round.t} onGo={(g) => setGo({ ...g, id: Date.now() })} trip={round.trips[round.t]}
              tripLabel={round.trips.length > 1 ? `Trip ${round.t + 1} of ${round.trips.length} · ` : ''}
              onFinish={(tasks, stops) => {
                setFixed((f) => new Set([...f, ...tasks.flatMap((t) => t.keys || [t.key])]));
                const nowMs = Date.now();
                const seconds = Math.round((nowMs - round.start) / 1000);
                const rec = { no: round.t + 1, loadSeconds: Math.max(0, Math.round((round.start - (round.opened || round.start)) / 1000)), seconds, tasks, jobs: tasks.length, areas: tasks.reduce((n, x) => n + (x.count || 1), 0) };
                const log = [...(round.log || []), rec];
                setRound((r) => ({ ...r, log }));
                setLastDone({ tasks, seconds, log, total: Math.round((nowMs - (round.began || round.start)) / 1000) });
                // tell the manager's dashboard (silently skipped if the bridge is not running)
                try {
                  fetch(`${bridgeBase()}/api/rounds`, { method: 'POST', body: JSON.stringify({
                    employee, trip: round.t + 1, loadSeconds: rec.loadSeconds, start: Math.round(round.start / 1000), end: Math.round(Date.now() / 1000), seconds, stops,
                    tasks: tasks.map((t) => ({ key: t.key, name: t.name, emoji: t.emoji, type: t.type, bay: t.bay, aisle: String((bayByCode[t.bay] || {}).aisle), productId: t.productId })),
                  }) }).catch(() => {});
                } catch (e) { /* no bridge */ }
                setScreen('complete');
              }} />
          ) : screen === 'complete' ? (
            <Complete done={lastDone.tasks} seconds={lastDone.seconds} log={lastDone.log || []} total={lastDone.total || 0}
              nextTripNo={round.t + 1 < round.trips.length ? round.t + 2 : null}
              onNextTrip={() => { setRound({ ...round, t: round.t + 1, opened: Date.now() }); setScreen('pick'); }}
              onHome={goHome}
              onReset={() => { setFixed(new Set()); setReports([]); goHome(); }} />
          ) : <Home key={`home-${version}`} note={bridgeNote} employee={employee} onName={setEmployee} fixed={fixed} reports={reports} onReset={() => { setFixed(new Set()); setReports([]); }}
              onStart={(plan) => { const now = Date.now(); setRound({ stops: plan.stops, trips: plan.trips, mode: 'auto', t: 0, start: 0, began: now, opened: now, log: [] }); setScreen('pick'); }} />}
      </View>

      {tab === 'round' && screen !== 'home' && <View style={[s.topBar, { height: insets.top + 60 }]} pointerEvents="none" />}
      {tab === 'round' && (screen === 'pick' || screen === 'route') && (
        <Pressable style={[s.backBtn, { top: insets.top + 8 }]} accessibilityRole="button" accessibilityLabel="Back"
          onPress={() => { soft(); smoothLayout(); setGo(null); if (screen === 'route') setScreen('pick'); else goHome(); }}>
          <Feather name="chevron-left" size={30} color={colors.dark} />
        </Pressable>
      )}

      {VOICE_FEATURE && <Pressable onPress={toggleVoice} accessibilityRole="switch" accessibilityState={{ checked: voiceOn }} accessibilityLabel="Voice assistant"
        style={[s.voiceBtn, { top: insets.top + 8, left: screen === 'pick' || screen === 'route' ? 64 : 16 }, voiceOn && s.voiceBtnOn]}>
        <Feather name={voiceOn ? 'volume-2' : 'volume-x'} size={20} color={voiceOn ? colors.onDark : colors.ink2} />
      </Pressable>}
      {VOICE_FEATURE && voiceOn && (
        <Pressable onPress={() => { tap(); ask(); }} accessibilityRole="button" accessibilityLabel="Ask the assistant"
          style={[s.voicePill, { bottom: insets.bottom + (screen === 'home' ? 124 : screen === 'pick' ? 100 : 14) }]}>
          <Feather name={voiceStatus === 'speaking' ? 'volume-2' : voiceStatus === 'thinking' ? 'more-horizontal' : 'mic'} size={16} color={colors.onDark} />
          <Text style={s.voicePillText}>{voiceStatus === 'listening' ? 'Listening…' : voiceStatus === 'speaking' ? 'Speaking' : voiceStatus === 'thinking' ? 'One moment' : 'Tap and ask me'}</Text>
        </Pressable>
      )}

      {alert && (
        <Pressable style={[s.alert, { top: insets.top + 64 }]} accessibilityRole="alert" onPress={() => {
          tap(); smoothLayout(); setAlert(null);
          if (screen !== 'pick' && screen !== 'route') { setTab('round'); setOpen(false); if (screen !== 'home') goHome(); }
        }}>
          <View style={s.alertDot}><Feather name="bell" size={18} color={colors.dark} /></View>
          <View style={{ flex: 1 }}>
            <Text style={s.alertTitle}>{alert.title}</Text>
            <Text style={s.alertBody}>{alert.body}</Text>
            {(screen === 'pick' || screen === 'route') && <Text style={s.alertNote}>Added to your next round</Text>}
          </View>
        </Pressable>
      )}

      {peek && <PeekCard peek={peek} />}
      {go && <GoTo key={go.id} {...go} onDone={() => setGo(null)} />}

      {/* tap anywhere outside the menu to close it */}
      {open && <Pressable style={s.catcher} onPress={() => setMenu(false)} accessibilityLabel="Close menu" />}

      {/* menu button and the icon pill live in one container, so they always sit together */}
      <View style={[s.menuWrap, { top: insets.top + 8 }]} pointerEvents="box-none">
        <Pressable style={s.menuBtn} onPress={() => { soft(); setMenu(!open); }}
          accessibilityRole="button" accessibilityLabel={open ? 'Close menu' : 'Open menu'}>
          {open ? (
            <>
              <View style={[s.bar, s.barX, { transform: [{ rotate: '45deg' }] }]} />
              <View style={[s.bar, s.barX, { transform: [{ rotate: '-45deg' }] }]} />
            </>
          ) : (
            <><View style={s.bar} /><View style={s.bar} /><View style={s.bar} /></>
          )}
        </Pressable>

        {open && (
          <View style={s.vpill}>
            {TABS.map((t) => {
              const on = tab === t.key;
              return (
                <Pressable key={t.key} onPress={() => pick(t.key)} accessibilityRole="menuitem" accessibilityLabel={t.label}
                  style={[s.vpillItem, on && s.vpillItemOn]}>
                  <Feather name={t.icon} size={22} color={on ? colors.dark : '#c9d3ce'} />
                </Pressable>
              );
            })}
          </View>
        )}
      </View>
    </View>
  );
}

function Home({ fixed, reports, note, employee, onName, onStart, onReset }) {
  const insets = useSafeAreaInsets();
  const tasks = useMemo(() => buildTasks(fixed, reports), [fixed, reports]);
  const plan = useMemo(() => planRound(tasks), [tasks]);
  const n = tasks.reduce((sum, t) => sum + (t.count || 1), 0); // shelf areas, not jobs
  const [expanded, setExpanded] = useState({});
  const toggle = (type) => {
    tap();
    smoothLayout();
    setExpanded((e) => ({ ...e, [type]: !e[type] }));
  };

  // the assistant on the home screen: "let's start" starts the round
  useEffect(() => register({
    context: () => ({
      screen: 'home', shelves_needing_attention: n,
      by_problem: { empty: tasks.filter((x) => colorOf(x.type) === 'empty').length, wrong: tasks.filter((x) => colorOf(x.type) === 'wrong').length, half: tasks.filter((x) => colorOf(x.type) === 'half').length },
      round_ready: plan.trips.length > 0, stops: plan.stops.length, minutes: plan.minutes, trips: plan.trips.length,
    }),
    actions: { start_round: () => { confirm(); onStart(plan); } },
    ends: ['start_round'],
  }), [tasks, plan]);
  useEffect(() => {
    if (!isOn()) return;
    converse(n === 0 ? "All the shelves look fine right now." : `${n} ${n === 1 ? 'shelf needs' : 'shelves need'} your attention. ${plan.stops.length} stops, about ${plan.minutes} minutes. Say let's start when you're ready.`);
    return () => cancel();
  }, []);

  const rows = ['empty', 'wrong', 'half'].map((type) => {
    const list = tasks.filter((t) => colorOf(t.type) === type);
    const bays = [...new Set(list.map((t) => t.bay))].sort((a, b) => bayRank(a) - bayRank(b));
    const items = list.slice().sort((x, y) => bayRank(x.bay) - bayRank(y.bay));
    return { type, count: list.reduce((sum, t) => sum + (t.count || 1), 0), bays, items };
  });

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={{ paddingTop: insets.top + 24, paddingHorizontal: 24, paddingBottom: 130 }}>
        <View style={s.hero}>
          {!dataInfo().live && !!note && <Text style={s.note}>{note}</Text>}
          <Text style={s.eyebrow}>Shelf status{dataInfo().live ? ` · live, ${dataInfo().photos} ${dataInfo().photos === 1 ? 'picture' : 'pictures'}` : ' · demo data'}</Text>
          {n === 0 ? (
            <Text style={s.h1}>All shelves{'\n'}are fine.</Text>
          ) : (
            <Text style={s.h1}>{n} {n === 1 ? 'shelf needs' : 'shelves need'}{'\n'}your attention.</Text>
          )}
        </View>

        {n > 0 && rows.map((r) => {
          const isOpen = !!expanded[r.type] && r.count > 0;
          return (
            <Pressable key={r.type} disabled={r.count === 0} onPress={() => toggle(r.type)}
              accessibilityRole="button" accessibilityState={{ expanded: isOpen }}
              style={[s.row, r.count === 0 && { opacity: 0.6 }]}>
              <View style={s.rowTop}>
                <View style={[s.num, { backgroundColor: r.count ? colors[r.type] : colors.surface2 }]}>
                  <Text style={s.numText}>{r.count}</Text>
                </View>
                <View style={s.meta}>
                  <Text style={s.rowTitle}>{WORDS[r.type].label}</Text>
                  <Text style={s.rowSub}>{WORDS[r.type].sub}</Text>
                  {!isOpen && r.bays.length > 0 && (
                    <View style={s.bays}>
                      {r.bays.slice(0, 4).map((b) => <Text key={b} style={s.bay}>{b}</Text>)}
                      {r.bays.length > 4 && <Text style={[s.bay, s.bayMore]}>+{r.bays.length - 4}</Text>}
                    </View>
                  )}
                </View>
                {r.count > 0 && <Feather name={isOpen ? 'chevron-up' : 'chevron-down'} size={22} color={colors.ink2} style={{ marginTop: 4 }} />}
              </View>

              {isOpen && (
                <View style={s.items}>
                  {r.items.map((t) => (
                    <View key={t.key} style={s.item}>
                      <ProductThumb productId={t.productId} emoji={t.emoji} size={46} radius={12} />
                      <View style={{ flex: 1 }}>
                        <Text style={s.itemName}>{t.name}{t.count > 1 ? `  ×${t.count}` : ''}</Text>
                        <Text style={s.itemQty}>{t.reported ? 'Reported by staff' : t.crates ? fmtCrates(t.crates) : `~${t.packs} packs`}</Text>
                      </View>
                      <Text style={s.bay}>{t.bay}</Text>
                    </View>
                  ))}
                </View>
              )}
            </Pressable>
          );
        })}

        {(fixed.size > 0 || reports.length > 0) && (
          <Pressable onPress={() => { tap(); onReset(); }} style={s.restart}>
            <Text style={s.restartText}>Restart demo</Text>
          </Pressable>
        )}
      </ScrollView>

      {plan.trips.length > 0 && (
        <View style={[s.startWrap, { bottom: insets.bottom + 16 }]} pointerEvents="box-none">
          <Pressable onPress={() => { confirm(); onStart(plan); }} accessibilityRole="button" accessibilityLabel="Start restock round"
            style={({ pressed }) => [s.start, pressed && { opacity: 0.88 }]}>
            <View style={{ flex: 1 }}>
              <Text style={s.startTitle}>Start round</Text>
              <Text style={s.startSub}>{plan.stops.length} stops · about {plan.minutes} min{plan.trips.length > 1 ? ` · ${plan.trips.length} trips` : ''}</Text>
            </View>
            <View style={s.startPlay}>
              <Ionicons name="play" size={24} color={colors.dark} style={{ marginLeft: 3 }} />
            </View>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  screen: { flex: 1 },
  hero: { paddingTop: 8, paddingBottom: 22 },
  start: { flexDirection: 'row', alignItems: 'center', gap: 16, backgroundColor: colors.dark, borderRadius: radius, paddingVertical: 20, paddingLeft: 26, paddingRight: 20 },
  startWrap: { position: 'absolute', left: 24, right: 24 },
  startTitle: { fontFamily: font.medium, fontSize: 22, color: colors.onDark, letterSpacing: -0.4 },
  startSub: { fontFamily: font.regular, fontSize: 15, color: 'rgba(255,255,255,0.7)', marginTop: 2 },
  startPlay: { width: 56, height: 56, borderRadius: 28, backgroundColor: '#ffffff', alignItems: 'center', justifyContent: 'center' },
  topBar: { position: 'absolute', top: 0, left: 0, right: 0, backgroundColor: colors.bg, zIndex: 4 },
  backBtn: { position: 'absolute', left: 8, width: 52, height: 52, alignItems: 'center', justifyContent: 'center', zIndex: 10 },
  catcher: { ...StyleSheet.absoluteFill, zIndex: 5 },
  menuWrap: { position: 'absolute', right: 8, alignItems: 'center', zIndex: 10 },
  menuBtn: { width: 64, height: 52, alignItems: 'center', justifyContent: 'center', gap: 6 },
  vpill: { width: 64, padding: 6, gap: 6, marginTop: 6, borderRadius: 32, backgroundColor: colors.dark, shadowColor: '#1d2b25', shadowOpacity: 0.2, shadowRadius: 18, shadowOffset: { width: 0, height: 8 } },
  bar: { width: 26, height: 2.5, borderRadius: 2, backgroundColor: colors.dark },
  barX: { position: 'absolute' },
  vpillItem: { width: 52, height: 52, borderRadius: 26, alignItems: 'center', justifyContent: 'center' },
  vpillItemOn: { backgroundColor: '#ffffff' },
  voiceBtn: { position: 'absolute', zIndex: 11, width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center' },
  voiceBtnOn: { backgroundColor: colors.dark },
  voicePill: { position: 'absolute', alignSelf: 'center', zIndex: 30, flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: colors.dark, borderRadius: 99, paddingHorizontal: 16, paddingVertical: 9 },
  voicePillText: { fontFamily: font.medium, fontSize: 14, color: colors.onDark },
  alert: { position: 'absolute', left: 16, right: 16, zIndex: 20, flexDirection: 'row', gap: 14, alignItems: 'center', backgroundColor: colors.dark, borderRadius: radius, padding: 16, shadowColor: '#1d2b25', shadowOpacity: 0.25, shadowRadius: 18, shadowOffset: { width: 0, height: 8 } },
  alertDot: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center' },
  alertTitle: { fontFamily: font.medium, fontSize: 17, color: colors.onDark },
  alertBody: { fontFamily: font.regular, fontSize: 14, lineHeight: 20, color: 'rgba(255,255,255,0.75)', marginTop: 2 },
  alertNote: { fontFamily: font.regular, fontSize: 12, color: 'rgba(255,255,255,0.55)', marginTop: 4 },
  who: { flexDirection: 'row', alignItems: 'center', gap: 8, alignSelf: 'flex-start', marginBottom: 18, paddingVertical: 8, paddingHorizontal: 14, borderRadius: 99, backgroundColor: colors.surface },
  whoText: { fontFamily: font.medium, fontSize: 14, color: colors.ink },
  note: { fontFamily: font.regular, fontSize: 13, color: '#c0553f', marginBottom: 6 },
  eyebrow: { marginBottom: 14, fontFamily: font.regular, fontSize: 15, color: colors.ink2 },
  h1: { fontFamily: font.regular, fontSize: 38, lineHeight: 43, color: colors.ink, letterSpacing: -1.1 },
  lead: { fontFamily: font.regular, fontSize: 18, color: colors.ink2, marginTop: 16 },
  restart: { alignItems: 'center', paddingVertical: 14 },
  restartText: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, textDecorationLine: 'underline' },
  rowTop: { flexDirection: 'row', gap: 20 },
  items: { marginTop: 18, paddingTop: 6, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  item: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 12 },
  itemEmoji: { fontSize: 30, width: 38, textAlign: 'center' },
  itemName: { fontFamily: font.medium, fontSize: 18, color: colors.ink, letterSpacing: -0.2 },
  itemQty: { fontFamily: font.regular, fontSize: 14, color: colors.ink2 },
  row: { backgroundColor: colors.surface, borderRadius: radius, padding: 22, paddingLeft: 20, marginBottom: 14 },
  num: { width: 64, height: 64, borderRadius: 32, alignItems: 'center', justifyContent: 'center' },
  numText: { fontFamily: font.regular, fontSize: 30, color: colors.ink },
  meta: { flex: 1, paddingTop: 4 },
  rowTitle: { fontFamily: font.medium, fontSize: 21, color: colors.ink, letterSpacing: -0.4 },
  rowSub: { fontFamily: font.regular, fontSize: 15, color: colors.ink2 },
  bays: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 12 },
  bayMore: { backgroundColor: 'transparent', color: colors.ink2 },
  bay: { fontFamily: font.medium, fontSize: 15, color: colors.ink, backgroundColor: colors.surface2, paddingHorizontal: 13, paddingVertical: 5, borderRadius: 99, overflow: 'hidden' },
});
