// "Load your trolley": one line per product, tick each one as it goes on the trolley.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, font, radius } from '../theme';
import ProductThumb from '../ui/ProductThumb';
import { choose, confirm, smoothLayout, soft } from '../feel';
import { cancel, converse, isOn, matchNames, register } from '../voice';
import { CART_SIZES, CONFIG, colorOf, liveData, pickList, suggestSwap } from '../logic/tasks';

// Big number + small unit, with the box emoji
function Amount({ item }) {
  const value = item.boxes ? fmt(item.boxes) : `~${item.packs}`;
  const unit = item.boxes ? (item.boxes > 1 ? 'crates' : 'crate') : 'packs';
  return (
    <View style={s.amount}>
      <View>
        <Text style={s.amountValue}>{value}</Text>
        <Text style={s.amountUnit}>{unit}</Text>
      </View>
    </View>
  );
}
const fmt = (n) => { const w = Math.floor(n); return (w ? w : '') + (n - w >= 0.5 ? '½' : '') || '0'; };

export default function Pick({ trips, t = 0, mode = 'auto', fixed, onPeek, onMode, onMove, onMoveItem, onSwap, onCart, onStart }) {
  const trip = trips[t];
  const later = trips.slice(t + 1);
  const insets = useSafeAreaInsets();
  const items = useMemo(() => pickList(trip), [trip]);
  const [picked, setPicked] = useState(new Set());
  const [openLater, setOpenLater] = useState({}); // which dotted trip cards are opened
  const [planOpen, setPlanOpen] = useState(false); // cart size and trip options, folded away by default
  const live = useMemo(() => liveData(fixed), [fixed]);
  const peekAreas = (item) => {
    const byId = {};
    for (const list of Object.values(live.cameraAreas)) for (const a of list) byId[a.id] = a;
    const ids = trip.stops.flatMap((st) => st.tasks).filter((x) => !x.reported && x.productId === item.id).flatMap((x) => x.keys || [x.key]);
    return ids.map((id) => byId[id]).filter(Boolean).slice(0, 3);
  };
  const swap = useMemo(() => (mode === 'position' && t === 0 ? suggestSwap(trips, picked) : null), [trips, mode, t, picked]);
  const done = items.filter((i) => picked.has(i.id)).length;
  const all = items.length > 0 && done === items.length;

  // the assistant: knows the list, ticks what you say you loaded, starts the route when you say that's it
  const pickedRef = useRef(picked);
  pickedRef.current = picked;
  useEffect(() => register({
    context: () => ({
      screen: 'loading_trolley', trip: `${t + 1} of ${trips.length}`, crates_total: Math.round(trip.load * 2) / 2, trolley_capacity_crates: CONFIG.trolleyCapacityCrates,
      items: items.map((x) => ({ product: x.name, amount: x.boxes ? `${fmt(x.boxes)} ${x.boxes > 1 ? 'crates' : 'crate'}` : `about ${x.packs} packs`, heavy: !!x.heavy, loaded: picked.has(x.id) })),
      loaded_count: done, total_count: items.length,
    }),
    actions: {
      load: ({ items: names }) => {
        const hit = matchNames(names, items);
        if (!hit.length) return;
        choose(); smoothLayout();
        setPicked((p) => { const n = new Set(p); hit.forEach((x) => n.add(x.id)); return n; });
      },
      load_all: () => { choose(); smoothLayout(); setPicked(new Set(items.map((x) => x.id))); },
      start_route: () => {
        const p = pickedRef.current;
        confirm();
        if (p.size === 0 || p.size === items.length) onStart(); else onStart(p);
      },
    },
    ends: ['start_route'],
  }), [items, picked, trip]);
  useEffect(() => {
    if (!isOn()) return;
    const list = items.slice(0, 6).map((x) => `${x.name}, ${x.boxes ? `${fmt(x.boxes)} ${x.boxes > 1 ? 'crates' : 'crate'}` : `about ${x.packs} packs`}`).join('. ');
    converse(`Let's load the trolley. ${items.length} items. ${list}.${items.length > 6 ? ' And more, ask me for the rest.' : ''} Tell me as you load them.`);
    return () => cancel();
  }, [t]);

  const tickAll = () => {
    choose(); smoothLayout();
    setPicked(done === items.length ? new Set() : new Set(items.map((x) => x.id)));
  };
  // "Everything in one trip" means taking it all: no need to tick a long list one by one
  useEffect(() => { if (mode === 'one') { smoothLayout(); setPicked(new Set(items.map((x) => x.id))); } }, [mode]);

  const toggle = (id) => {
    choose();
    smoothLayout();
    setPicked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  };

  const row = (i) => {
    const on = picked.has(i.id);
    return (
      <Pressable key={i.id} onPress={() => toggle(i.id)} delayLongPress={350}
        onLongPress={() => { soft(); onPeek && onPeek({ item: i, areas: peekAreas(i), cameraAreas: live.cameraAreas, photos: live.photos }); }} onPressOut={() => onPeek && onPeek(null)} accessibilityRole="checkbox" accessibilityState={{ checked: on }}
        style={[s.row, { borderColor: colors[colorOf(i.type)] }, on && s.rowOn]}>
        <View style={[s.check, on && s.checkOn]}>{on && <Feather name="check" size={22} color="#fff" />}</View>
        <ProductThumb productId={i.id} emoji={i.emoji} size={52} />
        <View style={{ flex: 1 }}>
          <Text style={[s.name, on && { color: colors.ink2 }]} numberOfLines={2}>{i.name}</Text>
          <Text style={s.bays}>For {i.bays.join(', ')}</Text>
        </View>
        <Amount item={i} />
      </Pressable>
    );
  };

  const light = items.filter((i) => !i.heavy);
  const heavy = items.filter((i) => i.heavy);

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={{ paddingTop: insets.top + 68, paddingHorizontal: 24, paddingBottom: all || done > 0 ? 140 : 48 }}>
        {trips.length > 1
          ? <View style={s.tripPill}><Text style={s.tripPillText}>Trip {t + 1} of {trips.length}</Text></View>
          : <Text style={s.eyebrow}>Your round</Text>}
        <Text style={s.h1}>Load the trolley 📦</Text>

        {/* one calm line: how much is loaded, how full the cart is */}
        <View style={s.track}><View style={[s.fill, { width: `${items.length ? (done / items.length) * 100 : 0}%` }]} /></View>
        <View style={s.leadRow}>
          <View style={{ flex: 1 }}>
            <Text style={s.lead}>{done} of {items.length} loaded</Text>
            <Text style={s.load}>
              {fmt(Math.round(trip.load * 2) / 2)} {Math.round(trip.load * 2) / 2 === 1 ? 'crate' : 'crates'}
              {trip.load > CONFIG.trolleyCapacityCrates ? (trips.length === 1 && trip.load <= CONFIG.trolleyCapacityCrates * 1.15 ? ` · a little over your cart (${CONFIG.trolleyCapacityCrates})` : ` · more than your cart holds (${CONFIG.trolleyCapacityCrates})`) : ` of ${CONFIG.trolleyCapacityCrates}`}
            </Text>
          </View>
          {items.length > 1 && (
            <Pressable onPress={tickAll} accessibilityRole="button" style={s.tickAll}>
              <Feather name={done === items.length ? 'rotate-ccw' : 'check-circle'} size={16} color={colors.dark} />
              <Text style={s.tickAllText}>{done === items.length ? 'Untick all' : 'Tick all'}</Text>
            </Pressable>
          )}
        </View>

        {/* cart size and how to split the round: tucked away until you need them */}
        {t === 0 && (onCart || onMode) && (
          <View style={s.plan}>
            <Pressable onPress={() => { choose(); smoothLayout(); setPlanOpen((v) => !v); }} accessibilityRole="button" accessibilityState={{ expanded: planOpen }} style={s.planHead}>
              <Feather name="sliders" size={18} color={colors.dark} />
              <Text style={s.planSummary} numberOfLines={1}>
                {(CART_SIZES.find((c) => c.crates === CONFIG.trolleyCapacityCrates) || { label: 'Custom' }).label} cart · {mode === 'one' ? 'one trip' : trips.length > 1 ? `${trips.length} trips${mode === 'position' ? ' · by location' : mode === 'priority' ? ' · by priority' : ''}` : '1 trip'}
              </Text>
              <Feather name={planOpen ? 'chevron-up' : 'chevron-down'} size={20} color={colors.ink2} />
            </Pressable>
            {planOpen && (
              <View style={s.planBody}>

        {t === 0 && onCart && (
          <View style={s.cartRow}>
            {CART_SIZES.map((c) => {
              const on = CONFIG.trolleyCapacityCrates === c.crates;
              return (
                <Pressable key={c.crates} onPress={() => { if (!on) { choose(); smoothLayout(); onCart(c.crates); } }} accessibilityRole="button" accessibilityState={{ selected: on }}
                  style={[s.cartChip, on && s.cartChipOn]}>
                  <Text style={[s.cartChipText, on && { color: colors.onDark }]}>{c.label} · {c.crates}</Text>
                </Pressable>
              );
            })}
          </View>
        )}

        {t === 0 && onMode && (trips.length > 1 || mode !== 'auto' || trips[0].stops.length > 1) && (
          <View style={s.split}>
            <View style={s.splitTop}>
              <View style={{ flex: 1 }}>
                <Text style={s.splitTitle}>{mode === 'one' ? 'One trip' : 'Two trips'}</Text>
                <Text style={s.splitSub}>
                  {mode === 'one' ? 'Everything goes on the trolley at once.'
                    : mode === 'auto' ? (trips.length > 1 ? `Too much for one trolley: ${trips.length} trips` : "Doesn't all fit on the trolley? Split it in two.")
                    : 'Split the load in two'}
                </Text>
              </View>
              {mode !== 'one' && (
                <Switch value={mode !== 'auto'} trackColor={{ true: colors.dark, false: colors.surface2 }}
                  onValueChange={(v) => { choose(); smoothLayout(); onMode(v ? 'position' : 'auto', picked); }} />
              )}
            </View>
            {mode === 'one' && (
              <Pressable onPress={() => { choose(); smoothLayout(); onMode('auto', picked); }} style={s.oneLink}>
                <Text style={s.oneLinkText}>Back to the suggested trips</Text>
              </Pressable>
            )}
            {mode === 'auto' && trips.length > 1 && (
              <Pressable onPress={() => { choose(); smoothLayout(); onMode('one', picked); }} style={s.oneLink}>
                <Text style={s.oneLinkText}>Take everything in one trip</Text>
              </Pressable>
            )}
            {mode !== 'auto' && mode !== 'one' && (
              <>
                <View style={s.seg}>
                  {[['position', 'By location'], ['priority', 'By priority']].map(([k, label]) => (
                    <Pressable key={k} onPress={() => { choose(); smoothLayout(); onMode(k, picked); }} style={[s.segItem, mode === k && s.segOn]}>
                      <Text style={[s.segText, mode === k && { color: colors.onDark }]}>{label}</Text>
                    </Pressable>
                  ))}
                </View>
                <Text style={s.splitNote}>
                  {mode === 'position'
                    ? 'Trip 1 covers the first part of the store, trip 2 the rest. Less walking back and forth.'
                    : 'Trip 1 fixes the Empty and Wrong shelves. Trip 2 tops up the Half ones.'}
                </Text>
                {done > 0 && <Text style={s.splitNote}>Items you already ticked stay in trip 1.</Text>}
                <Pressable onPress={() => { choose(); smoothLayout(); onMode('one', picked); }} style={s.oneLink}>
                  <Text style={s.oneLinkText}>Take everything in one trip</Text>
                </Pressable>
              </>
            )}
          </View>
        )}


              </View>
            )}
          </View>
        )}

        {swap && onSwap && (
          <View style={s.swap}>
            <View style={s.swapHead}>
              <Feather name="repeat" size={18} color={colors.dark} />
              <Text style={s.swapTitle}>Better route</Text>
            </View>
            <Text style={s.swapText}>
              Swap <Text style={s.swapBold}>{swap.out.bay} · {swap.out.bayName}</Text> for <Text style={s.swapBold}>{swap.inn.bay} · {swap.inn.bayName}</Text>.
              {' '}Saves about {swap.saves} {swap.saves === 1 ? 'bay' : 'bays'} of walking.
            </Text>
            <Pressable onPress={() => { confirm(); smoothLayout(); onSwap(swap.out.bay, swap.inn.bay); }} accessibilityRole="button" style={s.swapBtn}>
              <Text style={s.swapBtnText}>Swap</Text>
            </Pressable>
          </View>
        )}

        <View style={{ marginTop: 22 }}>{light.map(row)}</View>

        {heavy.length > 0 && (
          <>
            <Text style={s.section}>Heavy · pallet truck</Text>
            {heavy.map(row)}
          </>
        )}

        {later.map((tr, i) => {
          const items2 = pickList(tr);
          const open = !!openLater[i];
          const tripNo = t + i + 2;
          return (
            <View key={i} style={{ marginTop: 22 }}>
              {/* the dotted card: tap to see what is planned for this trip */}
              <Pressable onPress={() => { choose(); smoothLayout(); setOpenLater((o) => ({ ...o, [i]: !o[i] })); }} accessibilityRole="button" accessibilityState={{ expanded: open }}
                style={s.later}>
                <View style={s.laterHead}>
                  <Text style={s.laterTitle}>Trip {tripNo}</Text>
                  <View style={s.laterRight}>
                    <Text style={s.laterTag}>Planned for after</Text>
                    <Feather name={open ? 'chevron-up' : 'chevron-down'} size={20} color={colors.ink2} />
                  </View>
                </View>
                <Text style={s.laterText}>{items2.length} {items2.length === 1 ? 'item' : 'items'} · {tr.stops.length} {tr.stops.length === 1 ? 'stop' : 'stops'}</Text>
                {!open && <View style={s.laterThumbs}>{items2.slice(0, 7).map((x) => <ProductThumb key={x.id} productId={x.id} emoji={x.emoji} size={34} radius={10} />)}</View>}
              </Pressable>

              {open && (
                <View>
                  {/* the line between this trip and the next one */}
                  <View style={s.divider}>
                    <View style={s.dividerLine} />
                    <Text style={s.dividerText}>Trip {tripNo} · tap to bring one into this trip</Text>
                    <View style={s.dividerLine} />
                  </View>
                  {items2.map((x) => (
                    <Pressable key={x.id} onPress={() => { choose(); smoothLayout(); onMoveItem && onMoveItem(x.id); }} accessibilityRole="button"
                      accessibilityLabel={`Add ${x.name} to this trip`} style={[s.row, s.rowLater, { borderColor: colors[colorOf(x.type)] }]}>
                      <View style={s.plus}><Feather name="plus" size={22} color={colors.dark} /></View>
                      <ProductThumb productId={x.id} emoji={x.emoji} size={52} />
                      <View style={{ flex: 1 }}>
                        <Text style={s.name} numberOfLines={2}>{x.name}</Text>
                        <Text style={s.bays}>For {x.bays.join(', ')}</Text>
                      </View>
                      <Amount item={x} />
                    </Pressable>
                  ))}
                </View>
              )}
            </View>
          );
        })}
      </ScrollView>

      {!all && done > 0 && (
        <View style={[s.footer, s.miniWrap, { bottom: insets.bottom + 16 }]} pointerEvents="box-none">
          <Pressable onPress={() => { confirm(); onStart(picked); }} accessibilityRole="button" accessibilityLabel={`Start the route with ${done} loaded items`}
            style={({ pressed }) => [s.mini, pressed && { opacity: 0.88 }]}>
            <Text style={s.miniText}>Start with {done}</Text>
            <View style={s.miniIcon}><Feather name="arrow-right" size={20} color={colors.dark} /></View>
          </Pressable>
        </View>
      )}
      {all && (
        <View style={[s.footer, { bottom: insets.bottom + 16 }]} pointerEvents="box-none">
          <Pressable onPress={() => { confirm(); onStart(); }} accessibilityRole="button"
            style={({ pressed }) => [s.go, pressed && { opacity: 0.88 }]}>
            <Text style={s.goText}>Start route</Text>
            <View style={s.goIcon}><Feather name="arrow-right" size={24} color={colors.dark} /></View>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  eyebrow: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginBottom: 14 },
  h1: { fontFamily: font.regular, fontSize: 34, lineHeight: 40, color: colors.ink, letterSpacing: -1 },
  lead: { fontFamily: font.regular, fontSize: 18, color: colors.ink2 },
  leadRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 12 },
  tickAll: { flexDirection: 'row', alignItems: 'center', gap: 7, height: 36, paddingHorizontal: 14, borderRadius: 18, backgroundColor: colors.surface },
  tickAllText: { fontFamily: font.medium, fontSize: 14, color: colors.dark },
  load: { fontFamily: font.regular, fontSize: 14, color: colors.ink2, marginTop: 2 },
  cartRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' },
  cartLabel: { fontFamily: font.medium, fontSize: 14, color: colors.ink2, marginRight: 4 },
  cartChip: { height: 34, paddingHorizontal: 13, borderRadius: 17, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center' },
  cartChipOn: { backgroundColor: colors.dark },
  cartChipText: { fontFamily: font.medium, fontSize: 13, color: colors.ink },
  track: { height: 6, borderRadius: 3, backgroundColor: colors.surface2, marginTop: 18, overflow: 'hidden' },
  fill: { height: 6, borderRadius: 3, backgroundColor: colors.dark },
  split: { marginTop: 18, paddingTop: 16, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  plan: { backgroundColor: colors.surface, borderRadius: 22, marginTop: 18 },
  planHead: { flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 54, paddingHorizontal: 18 },
  planSummary: { flex: 1, fontFamily: font.medium, fontSize: 15, color: colors.ink },
  planBody: { paddingHorizontal: 18, paddingBottom: 18 },
  splitTop: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  splitTitle: { fontFamily: font.medium, fontSize: 18, color: colors.ink },
  splitSub: { fontFamily: font.regular, fontSize: 14, color: colors.ink2, marginTop: 1 },
  seg: { flexDirection: 'row', backgroundColor: colors.surface2, borderRadius: 99, padding: 4, marginTop: 16 },
  segItem: { flex: 1, alignItems: 'center', justifyContent: 'center', height: 42, borderRadius: 99 },
  segOn: { backgroundColor: colors.dark },
  segText: { fontFamily: font.medium, fontSize: 15, color: colors.ink2 },
  oneLink: { alignSelf: 'flex-start', marginTop: 12 },
  oneLinkText: { fontFamily: font.medium, fontSize: 15, color: colors.dark, textDecorationLine: 'underline' },
  splitNote: { fontFamily: font.regular, fontSize: 14, lineHeight: 20, color: colors.ink2, marginTop: 12 },
  swap: { backgroundColor: '#e6f1ea', borderRadius: radius, padding: 18, marginTop: 14 },
  swapHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  swapTitle: { fontFamily: font.medium, fontSize: 16, color: colors.dark },
  swapText: { fontFamily: font.regular, fontSize: 15, lineHeight: 22, color: colors.ink, marginTop: 8 },
  swapBold: { fontFamily: font.medium },
  swapBtn: { alignSelf: 'flex-start', height: 42, paddingHorizontal: 24, borderRadius: 21, backgroundColor: colors.dark, alignItems: 'center', justifyContent: 'center', marginTop: 14 },
  swapBtnText: { fontFamily: font.medium, fontSize: 16, color: colors.onDark },
  tripPill: { alignSelf: 'flex-start', backgroundColor: colors.dark, borderRadius: 99, paddingHorizontal: 14, paddingVertical: 6, marginBottom: 14 },
  tripPillText: { fontFamily: font.medium, fontSize: 14, color: colors.onDark },
  later: { borderRadius: radius, borderWidth: 1.5, borderColor: colors.line, borderStyle: 'dashed', padding: 18 },
  laterHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  laterTitle: { fontFamily: font.medium, fontSize: 18, color: colors.ink2 },
  laterRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  laterThumbs: { flexDirection: 'row', gap: 8, marginTop: 14, opacity: 0.85 },
  divider: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 20, marginBottom: 14 },
  dividerLine: { flex: 1, height: 1.5, backgroundColor: colors.line },
  dividerText: { fontFamily: font.medium, fontSize: 12, color: colors.ink2, letterSpacing: 0.4 },
  rowLater: { opacity: 0.92, borderStyle: 'dashed' },
  plus: { width: 40, height: 40, borderRadius: 20, borderWidth: 2, borderColor: '#b9c2cc', borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center' },
  laterTag: { fontFamily: font.regular, fontSize: 13, color: colors.ink2 },
  laterStop: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 14 },
  laterBay: { fontFamily: font.medium, fontSize: 16, color: colors.ink },
  laterNote: { fontFamily: font.regular, fontSize: 13, color: colors.ink2, marginTop: 14 },
  addBtn: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 38, paddingHorizontal: 14, borderRadius: 19, backgroundColor: colors.surface2 },
  addText: { fontFamily: font.medium, fontSize: 15, color: colors.dark },
  laterText: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginTop: 6 },
  laterEmoji: { fontSize: 22, minWidth: 52, opacity: 0.8 },
  section: { fontFamily: font.medium, fontSize: 13, color: colors.ink2, letterSpacing: 1.2, textTransform: 'uppercase', marginTop: 28, marginBottom: 12 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: colors.surface, borderRadius: radius, padding: 16, paddingRight: 18, marginBottom: 12, minHeight: 88, borderWidth: 3 },
  rowOn: { backgroundColor: '#e6f1ea' },
  check: { width: 40, height: 40, borderRadius: 20, borderWidth: 2, borderColor: '#b9c2cc', alignItems: 'center', justifyContent: 'center' },
  checkOn: { backgroundColor: colors.dark, borderColor: colors.dark },
  emoji: { fontSize: 30 },
  name: { fontFamily: font.medium, fontSize: 18, color: colors.ink, letterSpacing: -0.2 },
  bays: { fontFamily: font.regular, fontSize: 14, color: colors.ink2, marginTop: 1 },
  amount: { minWidth: 64 },
  amountValue: { fontFamily: font.light, fontSize: 28, lineHeight: 30, color: colors.ink, letterSpacing: -0.8, textAlign: 'right' },
  amountUnit: { fontFamily: font.regular, fontSize: 13, color: colors.ink2, textAlign: 'right' },
  partial: { fontFamily: font.regular, fontSize: 14, lineHeight: 20, color: colors.ink2, marginTop: 12 },
  miniWrap: { alignItems: 'flex-end', left: 24, right: 24 },
  mini: { flexDirection: 'row', alignItems: 'center', gap: 12, height: 58, borderRadius: 29, backgroundColor: colors.dark, paddingLeft: 22, paddingRight: 8 },
  miniText: { fontFamily: font.medium, fontSize: 17, color: colors.onDark },
  miniIcon: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center' },
  footer: { position: 'absolute', left: 24, right: 24 },
  go: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: 72, borderRadius: 22, backgroundColor: colors.dark, paddingLeft: 26, paddingRight: 12 },
  goOff: { backgroundColor: colors.surface2, justifyContent: 'center', paddingRight: 26 },
  goText: { fontFamily: font.medium, fontSize: 20, color: colors.onDark, letterSpacing: -0.2 },
  goIcon: { width: 50, height: 50, borderRadius: 25, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center' },
});
