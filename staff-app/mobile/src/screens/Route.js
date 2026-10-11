// The route: one stop (bay) at a time, tasks in priority order, a Done button on each.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, font, radius } from '../theme';
import ProductThumb from '../ui/ProductThumb';
import { smoothLayout, success, choose } from '../feel';
import { WORDS, bayByCode, colorOf, fmtCrates } from '../logic/tasks';
import { cancel, converse, isOn, matchNames, register } from '../voice';

const PRIORITY = { empty: 0, wrong: 1, damaged: 1, half: 2 };
const aisleOf = (bay) => (bayByCode[bay] || {}).aisle;
const aisleLabel = (a) => (String(a).toLowerCase() === 'island' ? 'Island' : `Aisle ${a}`);

// How to get from the previous stop to this one
function directions(prev, stop) {
  const a = aisleOf(stop.bay);
  if (!prev) return { icon: 'map-pin', text: `Start at ${aisleLabel(a)}` };
  if (aisleOf(prev.bay) === a) return { icon: 'arrow-down', text: 'Same aisle, keep going' };
  return { icon: 'navigation', text: `Move on to ${aisleLabel(a)}` };
}

export default function Route({ trip, tripLabel, goOpen, onFinish, onGo }) {
  const insets = useSafeAreaInsets();
  const scroller = useRef(null);
  const [i, setI] = useState(0);
  const [doneKeys, setDoneKeys] = useState(new Set());
  const stopStart = useRef(Date.now());
  const stopLog = useRef([]); // how long each stop took, walking included
  const stop = trip.stops[i];
  const last = i === trip.stops.length - 1;

  const tasks = useMemo(() => stop.tasks.slice().sort((a, b) => PRIORITY[a.type] - PRIORITY[b.type]), [stop]);
  const aisles = useMemo(() => {
    const out = [];
    for (const st of trip.stops) { const a = aisleOf(st.bay); if (out[out.length - 1] !== a) out.push(a); }
    return out;
  }, [trip]);
  const curAisleIdx = useMemo(() => {
    let idx = -1, prev;
    for (let k = 0; k <= i; k++) { const a = aisleOf(trip.stops[k].bay); if (a !== prev) { idx++; prev = a; } }
    return idx;
  }, [i, trip]);

  // New aisle (or first stop of the trip): announce it full-screen, with what is waiting there
  useEffect(() => {
    const a = aisleOf(stop.bay);
    if (i > 0 && aisleOf(trip.stops[i - 1].bay) === a) return;
    const here = [];
    for (let k = i; k < trip.stops.length && aisleOf(trip.stops[k].bay) === a; k++) here.push(trip.stops[k]);
    const emojis = [...new Set(here.flatMap((st) => st.tasks.slice().sort((x, y) => PRIORITY[x.type] - PRIORITY[y.type]).map((t) => t.emoji)))];
    const island = String(a).toLowerCase() === 'island';
    onGo && onGo({
      title: i === 0 ? 'Start here' : 'Next',
      number: island ? 'Go to the Island' : `Go to Aisle ${a}`,
      emojis: emojis.slice(0, 8),
      note: `${here.length} ${here.length === 1 ? 'stop' : 'stops'} · ${here.map((st) => st.bay).join(', ')}`,
      speech: `${i === 0 ? 'Start at' : 'Next, go to'} ${island ? 'the island' : `aisle ${a}`}. ${[...new Set(here.flatMap((st) => st.tasks.map((t) => t.name)))].slice(0, 3).join(', ')}.`,
    });
  }, [i]);

  useEffect(() => { stopStart.current = Date.now(); }, [i]);
  const logStop = () => stopLog.current.push({ bay: stop.bay, aisle: String(aisleOf(stop.bay)), seconds: Math.round((Date.now() - stopStart.current) / 1000), tasks: stop.tasks.length });

  // at the shelf: the assistant tells you what to do, answers, and ticks things for you
  const qty = (t) => (t.type === 'wrong' ? 'wrong product, move it' : t.crates ? fmtCrates(t.crates) : `about ${t.packs} packs`);
  const line = `${stop.bayName}. ${tasks.map((t) => `${t.name}, ${qty(t)}`).join('. ')}.`;
  useEffect(() => {
    if (goOpen) return;
    return register({
      context: () => ({
        screen: 'restocking_shelf', stop_number: i + 1, total_stops: trip.stops.length,
        aisle: aisleOf(stop.bay), bay_name: stop.bayName,
        tasks_here: tasks.map((t) => ({ product: t.name, what: WORDS[t.type].label, amount: qty(t), done: doneKeys.has(t.key) })),
        next_stops: trip.stops.slice(i + 1, i + 4).map((st) => ({ aisle: aisleOf(st.bay), bay_name: st.bayName, products: st.tasks.map((t) => t.name) })),
      }),
      actions: {
        repeat: () => { cancel(); converse(line); },
        done: () => { success(); smoothLayout(); setDoneKeys((d) => { const n = new Set(d); tasks.forEach((t) => n.add(t.key)); return n; }); },
        // "bananas are done": only those products, the rest stays open
        complete: ({ items }) => {
          const hit = items && items.length ? matchNames(items, tasks, (x) => x.name) : tasks;
          if (!hit.length) return;
          success(); smoothLayout();
          setDoneKeys((d) => { const n = new Set(d); hit.forEach((t) => n.add(t.key)); return n; });
        },
      },
    });
  }, [i, goOpen, doneKeys]);
  useEffect(() => {
    if (goOpen || !isOn()) return;
    converse(line);
    return () => cancel();
  }, [i, goOpen]);

  const pending = tasks.filter((t) => !doneKeys.has(t.key));
  const mix = pending.some((t) => t.type !== 'half') && pending.some((t) => t.type === 'half'); // label only while red/orange and half are both still open
  const dir = directions(trip.stops[i - 1], stop);

  const complete = (task) => {
    success();
    smoothLayout();
    setDoneKeys((d) => new Set(d).add(task.key));
  };

  // When the last task of a stop is ticked, move on after a short pause
  useEffect(() => {
    if (pending.length > 0) return;
    const id = setTimeout(() => {
      logStop();
      if (last) {
        onFinish(trip.stops.flatMap((st) => st.tasks), stopLog.current);
      } else {
        smoothLayout();
        setI((n) => n + 1);
        scroller.current && scroller.current.scrollTo({ y: 0, animated: false });
      }
    }, 650);
    return () => clearTimeout(id);
  }, [pending.length, i]);

  const next = trip.stops[i + 1];
  let shownHalfLabel = false;

  return (
    <ScrollView ref={scroller} contentContainerStyle={{ paddingTop: insets.top + 68, paddingHorizontal: 24, paddingBottom: 60 }}>
      {/* progress: one segment per stop */}
      <View style={s.segs}>
        {trip.stops.map((st, k) => <View key={st.bay} style={[s.seg, k < i && s.segDone, k === i && s.segNow]} />)}
      </View>
      <Text style={s.eyebrow}>{tripLabel}Stop {i + 1} of {trip.stops.length}</Text>

      {/* where to go */}
      <Text style={s.h1}>{aisleLabel(aisleOf(stop.bay))}</Text>
      <Text style={s.lead}>Bay {stop.bay} · {stop.bayName}</Text>

      <View style={s.dir}>
        <Feather name={dir.icon} size={18} color={colors.dark} />
        <Text style={s.dirText}>{dir.text}</Text>
      </View>

      {/* the path through the store */}
      <View style={s.trail}>
        {aisles.map((a, k) => (
          <View key={`${a}-${k}`} style={s.trailItem}>
            <View style={[s.trailChip, k === curAisleIdx && s.trailNow, k < curAisleIdx && s.trailPast]}>
              {k < curAisleIdx
                ? <Feather name="check" size={13} color={colors.ink2} />
                : <Text style={[s.trailText, k === curAisleIdx && { color: '#fff' }]}>{String(a).toLowerCase() === 'island' ? 'Island' : a}</Text>}
            </View>
            {k < aisles.length - 1 && <Feather name="chevron-right" size={14} color="#b9c2cc" />}
          </View>
        ))}
      </View>

      {/* open jobs first, most urgent on top. A finished job drops to the bottom, so the next one slides up
          into the same spot and its Done button is exactly where you just pressed. */}
      <View style={{ marginTop: 28 }}>
        {pending.map((t) => {
          let label = null;
          if (mix && t.type === 'half' && !shownHalfLabel) { shownHalfLabel = true; label = <Text style={s.section}>While you're here</Text>; }
          return (
            <View key={t.key}>
              {label}
              <View style={s.card}>
                <View style={[s.badge, { backgroundColor: colors[colorOf(t.type)] }]}>
                  <Text style={s.badgeText}>{WORDS[t.type].label}</Text>
                </View>
                <View style={s.prodRow}>
                  <ProductThumb productId={t.productId} emoji={t.emoji} size={68} radius={18} />
                  <Text style={s.name} numberOfLines={2}>{t.name}{t.count > 1 ? `  ×${t.count}` : ''}</Text>
                </View>
                <Text style={s.instr} numberOfLines={3}>{t.text}</Text>
                <Pressable onPress={() => complete(t)} onPressIn={choose} accessibilityRole="button" accessibilityLabel={`Done: ${t.name}`}
                  style={({ pressed }) => [s.doneBtn, pressed && s.doneBtnPressed]}>
                  <Feather name="check" size={26} color="#fff" />
                  <Text style={s.doneBtnText}>Done</Text>
                </Pressable>
              </View>
            </View>
          );
        })}

        {tasks.filter((t) => doneKeys.has(t.key)).map((t) => (
          <View key={t.key} style={s.doneRow}>
            <View style={s.doneCheck}><Feather name="check" size={18} color="#fff" /></View>
            <ProductThumb productId={t.productId} emoji={t.emoji} size={34} radius={10} />
            <Text style={s.doneName} numberOfLines={1}>{t.name}{t.count > 1 ? `  ×${t.count}` : ''}</Text>
            <Text style={s.doneWord}>Done</Text>
          </View>
        ))}
      </View>

      {next && pending.length > 0 && (
        <Text style={s.next}>Next: {next.bay} · {next.bayName}</Text>
      )}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  segs: { flexDirection: 'row', gap: 5 },
  seg: { flex: 1, height: 5, borderRadius: 3, backgroundColor: colors.surface2 },
  segDone: { backgroundColor: '#a9d68f' },
  segNow: { backgroundColor: colors.dark },
  eyebrow: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginTop: 18, marginBottom: 12 },
  h1: { fontFamily: font.regular, fontSize: 44, lineHeight: 48, color: colors.ink, letterSpacing: -1.4 },
  lead: { fontFamily: font.regular, fontSize: 19, color: colors.ink2, marginTop: 6 },
  dir: { flexDirection: 'row', alignItems: 'center', gap: 10, alignSelf: 'flex-start', backgroundColor: colors.surface, borderRadius: 99, paddingVertical: 10, paddingHorizontal: 16, marginTop: 18 },
  dirText: { fontFamily: font.medium, fontSize: 15, color: colors.dark },
  trail: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', rowGap: 8, marginTop: 14 },
  trailItem: { flexDirection: 'row', alignItems: 'center' },
  trailChip: { minWidth: 34, height: 30, paddingHorizontal: 11, borderRadius: 15, borderWidth: 1.5, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  trailNow: { backgroundColor: colors.dark, borderColor: colors.dark },
  trailPast: { backgroundColor: colors.surface2, borderColor: colors.surface2 },
  trailText: { fontFamily: font.medium, fontSize: 14, color: colors.ink2 },
  section: { fontFamily: font.medium, fontSize: 13, color: colors.ink2, letterSpacing: 1.2, textTransform: 'uppercase', marginTop: 12, marginBottom: 12 },
  card: { backgroundColor: colors.surface, borderRadius: radius, padding: 20, marginBottom: 14 },
  badge: { alignSelf: 'flex-start', borderRadius: 99, paddingHorizontal: 14, paddingVertical: 7 },
  badgeText: { fontFamily: font.medium, fontSize: 14, color: colors.ink },
  prodRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 16, minHeight: 68 },
  emoji: { fontSize: 36 },
  name: { flex: 1, fontFamily: font.medium, fontSize: 24, color: colors.ink, letterSpacing: -0.5 },
  instr: { fontFamily: font.regular, fontSize: 18, lineHeight: 25, color: colors.ink2, marginTop: 10, minHeight: 75 }, // same height for every card, so Done always lands in the same place
  doneBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10, minHeight: 68, borderRadius: 22, backgroundColor: colors.dark, marginTop: 20 },
  doneBtnPressed: { transform: [{ scale: 0.97 }], backgroundColor: '#2a3a34' },
  doneBtnText: { fontFamily: font.medium, fontSize: 22, color: colors.onDark, letterSpacing: -0.3 },
  doneRow: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: '#e6f1ea', borderRadius: 20, padding: 14, marginBottom: 12 },
  doneCheck: { width: 32, height: 32, borderRadius: 16, backgroundColor: colors.dark, alignItems: 'center', justifyContent: 'center' },
  doneEmoji: { fontSize: 22 },
  doneName: { flex: 1, fontFamily: font.medium, fontSize: 17, color: colors.ink2 },
  doneWord: { fontFamily: font.medium, fontSize: 15, color: '#4a9b72' },
  next: { fontFamily: font.regular, fontSize: 16, color: colors.ink2, textAlign: 'center', marginTop: 20 },
});
