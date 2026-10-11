// Live shelves. Level 1: the whole store split into aisles. Level 2: one aisle, swipe or use the
// arrows to move along its cameras (starts in the middle), with the picture and shelf areas coloured by status.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import Svg, { Image as SvgImage, Polygon, Rect, Text as SvgText } from 'react-native-svg';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, font, radius } from '../theme';
import { choose, soft, tap, smoothLayout } from '../feel';
import { PHOTO, liveData } from '../logic/tasks';
import { viewBoxOf } from '../ui/ShelfPicture';

const FILL = { ok: colors.ok, half: colors.half, wrong: colors.wrong, empty: colors.empty, unknown: '#8f9aa3' };
const WORD = { ok: 'Full', half: 'Half', wrong: 'Wrong product', empty: 'Empty', unknown: 'Not checked yet' };
const ORDER = ['empty', 'wrong', 'half', 'ok'];

export default function Live({ fixed, onReport }) {
  const { sections, photos, times } = useMemo(() => liveData(fixed), [fixed]);
  const [open, setOpen] = useState(null);
  const sec = sections.find((x) => x.n === open);
  if (sec) return <Area key={sec.n} section={sec} photos={photos} times={times} onReport={onReport} onBack={() => { soft(); setOpen(null); }} />;
  return <Overview sections={sections} photos={photos} onOpen={(n) => { tap(); setOpen(n); }} />;
}

const countAreas = (areas) => {
  const c = { empty: 0, wrong: 0, half: 0, ok: 0, unknown: 0 };
  for (const a of areas) c[a.status] += 1;
  return c;
};

// "Latest picture · 14:32" and, when it is much older than the newest picture of the shop, how much older
function pictureAge(times, camera) {
  const t = times[camera];
  if (!t) return 'Latest camera picture.';
  let at;
  try { at = new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Lisbon' }); }
  catch (e) { at = new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
  const gap = Math.max(...Object.values(times)) - t;
  const hours = Math.round(gap / 3600);
  return `Latest picture · ${at}` + (gap >= 1800 ? ` · ${hours >= 1 ? `${hours} h` : `${Math.round(gap / 60)} min`} older than the newest` : '');
}

const bayNames = (cams) => cams.flatMap((c) => c.bays.map((b) => b.code)).join(' · ');

// Tiny version of a camera picture: the shelf areas in their status colours
function Thumb({ cam, w, photo }) {
  const h = (w * PHOTO.h) / PHOTO.w;
  return (
    <View style={{ width: w, height: h, borderRadius: 12, overflow: 'hidden', backgroundColor: '#56655d' }}>
      <Svg width={w} height={h} viewBox={viewBoxOf(cam.areas)}>
        <Rect x={-2000} y={-2000} width={6000} height={6000} fill="#56655d" />
        {photo && <SvgImage x={0} y={0} width={PHOTO.w} height={PHOTO.h} href={{ uri: photo }} preserveAspectRatio="xMidYMid slice" />}
        {cam.areas.filter((a) => a.pts).map((a) => (
          <Polygon key={a.id} points={a.pts.map((p) => p.join(',')).join(' ')} fill={FILL[a.status]} fillOpacity={photo ? 0.3 : 0.85} stroke={photo ? FILL[a.status] : 'none'} strokeWidth={photo ? 6 : 0} />
        ))}
      </Svg>
    </View>
  );
}

function Overview({ sections, photos, onOpen }) {
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const tw = Math.floor((width - 48 - 40 - 16) / 3); // three thumbnails per card
  const all = sections.flatMap((x) => x.cameras.flatMap((c) => c.areas));
  const total = countAreas(all);
  const need = total.empty + total.wrong + total.half;

  return (
    <ScrollView contentContainerStyle={{ paddingTop: insets.top + 24, paddingHorizontal: 24, paddingBottom: insets.bottom + 40 }}>
      <Text style={s.eyebrow}>Live shelves</Text>
      <Text style={s.h1}>{need === 0 ? 'Every shelf\nlooks full.' : `${need} ${need === 1 ? 'shelf needs' : 'shelves need'}\nyour attention.`}</Text>

      <View style={s.overall}>
        <View style={s.stack}>
          {[...ORDER, 'unknown'].map((k) => total[k] > 0 && <View key={k} style={{ flex: total[k], backgroundColor: FILL[k] }} />)}
        </View>
        <View style={s.legend}>
          {[...ORDER, 'unknown'].filter((k) => k !== 'unknown' || total.unknown > 0).map((k) => (
            <View key={k} style={s.legendItem}>
              <View style={[s.dot, { backgroundColor: FILL[k] }]} />
              <Text style={s.legendText}>{total[k]} {WORD[k]}</Text>
            </View>
          ))}
        </View>
      </View>

      <Text style={s.section}>The shop in {sections.length} aisles</Text>
      {sections.map((sec) => {
        const c = countAreas(sec.cameras.flatMap((x) => x.areas));
        const bad = c.empty + c.wrong + c.half;
        return (
          <Pressable key={sec.n} onPress={() => onOpen(sec.n)} accessibilityRole="button" accessibilityLabel={`Aisle ${sec.n}`} style={s.card}>
            <View style={s.cardTop}>
              <View style={{ flex: 1 }}>
                <Text style={s.cardTitle}>Aisle {sec.n}</Text>
                <Text style={s.cardSub}>{bayNames(sec.cameras)}</Text>
              </View>
              {bad > 0
                ? <View style={[s.count, { backgroundColor: FILL[ORDER.find((k) => c[k] > 0)] }]}><Text style={s.countText}>{bad}</Text></View>
                : c.ok === 0
                  ? <View style={[s.count, { backgroundColor: colors.surface2 }]}><Feather name="minus" size={18} color={colors.ink2} /></View>
                  : <View style={[s.count, { backgroundColor: '#e6f1ea' }]}><Feather name="check" size={18} color={colors.dark} /></View>}
            </View>
            <View style={s.thumbs}>
              {sec.cameras.map((cam) => <Thumb key={cam.camera} cam={cam} w={tw} photo={photos[cam.camera]} />)}
            </View>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

function Area({ section, photos, times, onBack, onReport }) {
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const w = width - 48;
  const h = (w * PHOTO.h) / PHOTO.w;
  const cams = section.cameras;
  const n = cams.length;
  const pager = useRef(null);
  const start = useRef(Math.floor(n / 2)).current; // start in the middle camera
  const [idx, setIdx] = useState(start);
  const [areaId, setAreaId] = useState(null);
  const cam = cams[idx];

  useEffect(() => { pager.current && pager.current.scrollTo({ x: start * width, animated: false }); }, []);

  // An arrow tap moves exactly one side. While that slide runs, the pager's own scroll events are ignored,
  // so they can't push the page further (this was jumping to the last side).
  const lock = useRef(0);
  const go = (i) => {
    if (i < 0 || i >= n || i === idx) return;
    choose();
    lock.current = Date.now() + 600;
    pager.current && pager.current.scrollTo({ x: i * width, animated: true });
    setIdx(i); setAreaId(null);
  };
  // Only a finger swipe updates the page from the scroll position
  const onEnd = (e) => {
    if (Date.now() < lock.current) return;
    const i = Math.round(e.nativeEvent.contentOffset.x / width);
    if (i !== idx && i >= 0 && i < n) { choose(); setIdx(i); setAreaId(null); }
  };
  const pickArea = (id) => { tap(); smoothLayout(); setAreaId(id === areaId ? null : id); };
  const chosen = cam.areas.find((a) => a.id === areaId);
  const counts = countAreas(cam.areas);

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={{ paddingTop: insets.top + 68, paddingBottom: insets.bottom + 40 }}>
        <View style={s.pad}>
          <Text style={s.eyebrow}>Aisle {section.n} · Side {idx + 1} of {n}</Text>
          <Text style={s.h1}>{cam.bays.map((b) => b.name).join(' · ')}</Text>
          <Text style={s.codes}>{cam.bays.map((b) => b.code).join(' · ')}</Text>
        </View>

        {/* swipe sideways, or use the arrows */}
        <View style={{ marginTop: 20 }}>
          <ScrollView ref={pager} horizontal pagingEnabled showsHorizontalScrollIndicator={false}
            onMomentumScrollEnd={onEnd} contentOffset={{ x: start * width, y: 0 }}>
            {cams.map((c) => (
              <View key={c.camera} style={{ width, paddingHorizontal: 24 }}>
                <View style={[s.photo, { width: w, height: h }]}>
                  <Svg width={w} height={h} viewBox={viewBoxOf(c.areas)}>
                    <Rect x={-2000} y={-2000} width={6000} height={6000} fill="#56655d" />
                    {photos[c.camera] && <SvgImage x={0} y={0} width={PHOTO.w} height={PHOTO.h} href={{ uri: photos[c.camera] }} preserveAspectRatio="xMidYMid slice" />}
                    {c.areas.filter((a) => a.pts).map((a) => {
                      const cx = a.pts.reduce((t, p) => t + p[0], 0) / a.pts.length;
                      const cy = a.pts.reduce((t, p) => t + p[1], 0) / a.pts.length;
                      const sel = a.id === areaId;
                      const xs = a.pts.map((q) => q[0]), ys = a.pts.map((q) => q[1]);
                      const size = Math.max(34, Math.min(100, Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) * 0.55));
                      return (
                        <React.Fragment key={a.id}>
                          <Polygon points={a.pts.map((p) => p.join(',')).join(' ')} fill={FILL[a.status]} fillOpacity={photos[c.camera] ? 0.3 : 0.62}
                            stroke={sel ? '#ffffff' : FILL[a.status]} strokeWidth={sel ? 9 : 5} onPress={() => pickArea(a.id)} />
                          {!photos[c.camera] && <SvgText x={cx} y={cy + size * 0.34} fontSize={size} textAnchor="middle" onPress={() => pickArea(a.id)}>{a.emoji}</SvgText>}
                        </React.Fragment>
                      );
                    })}
                  </Svg>
                </View>
              </View>
            ))}
          </ScrollView>
          {idx > 0 && (
            <Pressable onPress={() => go(idx - 1)} accessibilityRole="button" accessibilityLabel="Previous side" style={[s.arrow, { left: 34, top: h / 2 - 22 }]}>
              <Feather name="chevron-left" size={26} color={colors.dark} />
            </Pressable>
          )}
          {idx < n - 1 && (
            <Pressable onPress={() => go(idx + 1)} accessibilityRole="button" accessibilityLabel="Next side" style={[s.arrow, { right: 34, top: h / 2 - 22 }]}>
              <Feather name="chevron-right" size={26} color={colors.dark} />
            </Pressable>
          )}
        </View>

        {/* the whole area at a glance: one dot per side, current one stretched */}
        <View style={s.dots}>
          {cams.map((c, i) => (
            <Pressable key={c.camera} onPress={() => go(i)} hitSlop={10} accessibilityLabel={`Side ${i + 1}`}
              style={[s.pip, { backgroundColor: FILL[c.status] }, i === idx && s.pipOn]} />
          ))}
        </View>

        <View style={s.pad}>
          <Text style={s.sample}>{photos[cam.camera] ? pictureAge(times, cam.camera) : 'Sample camera picture. Real photos appear here once the cameras are connected.'}</Text>
          <View style={s.legend}>
            {ORDER.map((k) => (
              <View key={k} style={s.legendItem}>
                <View style={[s.dot, { backgroundColor: FILL[k] }]} />
                <Text style={s.legendText}>{counts[k]} {WORD[k]}</Text>
              </View>
            ))}
          </View>

          {chosen ? (
            <View style={s.info}>
              <Text style={s.infoEmoji}>{chosen.emoji}</Text>
              <View style={{ flex: 1 }}>
                <Text style={s.infoName}>{chosen.name}</Text>
                <Text style={s.infoBay}>Bay {chosen.bay}</Text>
              </View>
              <View style={[s.badge, { backgroundColor: FILL[chosen.status] }]}><Text style={s.badgeText}>{WORD[chosen.status]}</Text></View>
            </View>
          ) : (
            <Text style={s.hint}>Tap a shelf area on the picture to see what it is.</Text>
          )}

          <Pressable onPress={() => { tap(); onReport && onReport(cam.bays[0].code); }} accessibilityRole="button" style={s.report}>
            <Feather name="flag" size={18} color={colors.dark} />
            <Text style={s.reportText}>Spot a problem? Report it here</Text>
          </Pressable>

          <Text style={s.section}>On this side</Text>
          {cam.areas.map((a) => (
            <Pressable key={a.id} onPress={() => pickArea(a.id)} style={[s.row, a.id === areaId && s.rowOn]}>
              <Text style={s.rowEmoji}>{a.emoji}</Text>
              <Text style={s.rowName} numberOfLines={1}>{a.name}</Text>
              <View style={[s.dot, { backgroundColor: FILL[a.status] }]} />
              <Text style={s.rowStatus}>{WORD[a.status]}</Text>
            </Pressable>
          ))}
        </View>
      </ScrollView>

      <View style={[s.topBar, { height: insets.top + 60 }]} pointerEvents="none" />
      <Pressable onPress={onBack} accessibilityRole="button" accessibilityLabel="Back to all aisles" style={[s.back, { top: insets.top + 8 }]}>
        <Feather name="chevron-left" size={30} color={colors.dark} />
      </Pressable>
    </View>
  );
}

const s = StyleSheet.create({
  pad: { paddingHorizontal: 24 },
  eyebrow: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginBottom: 14, marginTop: 8 },
  h1: { fontFamily: font.regular, fontSize: 36, lineHeight: 42, color: colors.ink, letterSpacing: -1.1, paddingRight: 60 },
  overall: { backgroundColor: colors.surface, borderRadius: radius, padding: 20, marginTop: 24 },
  stack: { flexDirection: 'row', height: 14, borderRadius: 7, overflow: 'hidden', gap: 2 },
  card: { backgroundColor: colors.surface, borderRadius: radius, padding: 20, marginBottom: 12 },
  cardTop: { flexDirection: 'row', alignItems: 'center' },
  cardTitle: { fontFamily: font.medium, fontSize: 24, color: colors.ink, letterSpacing: -0.6 },
  cardSub: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginTop: 2 },
  bayRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 16 },
  bayChip: { flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: colors.surface2, borderRadius: 99, paddingHorizontal: 12, paddingVertical: 6 },
  bayChipText: { fontFamily: font.medium, fontSize: 14, color: colors.ink },
  report: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10, height: 56, borderRadius: 20, borderWidth: 1.5, borderColor: colors.line, marginTop: 24 },
  reportText: { fontFamily: font.medium, fontSize: 16, color: colors.dark },
  codes: { fontFamily: font.medium, fontSize: 15, color: colors.ink2, marginTop: 8 },
  count: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  countText: { fontFamily: font.medium, fontSize: 17, color: colors.ink },
  thumbs: { flexDirection: 'row', gap: 8, marginTop: 16 },
  dots: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 8, marginTop: 16 },
  pip: { width: 10, height: 10, borderRadius: 5 },
  pipOn: { width: 28 },
  chips: { paddingHorizontal: 24, gap: 10, paddingTop: 22 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 8, height: 46, paddingHorizontal: 16, borderRadius: 23, backgroundColor: colors.surface },
  chipOn: { backgroundColor: colors.dark },
  chipText: { fontFamily: font.medium, fontSize: 16, color: colors.ink },
  dot: { width: 12, height: 12, borderRadius: 6 },
  photo: { borderRadius: radius, overflow: 'hidden', backgroundColor: '#56655d' },
  arrow: { position: 'absolute', width: 44, height: 44, borderRadius: 22, backgroundColor: 'rgba(255,255,255,0.92)', alignItems: 'center', justifyContent: 'center' },
  sample: { fontFamily: font.regular, fontSize: 13, color: colors.ink2, marginTop: 10 },
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: 14, marginTop: 16 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  legendText: { fontFamily: font.regular, fontSize: 14, color: colors.ink },
  info: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: colors.surface, borderRadius: radius, padding: 18, marginTop: 20 },
  infoEmoji: { fontSize: 38 },
  infoName: { fontFamily: font.medium, fontSize: 19, color: colors.ink, letterSpacing: -0.3 },
  infoBay: { fontFamily: font.regular, fontSize: 14, color: colors.ink2 },
  badge: { borderRadius: 99, paddingHorizontal: 14, paddingVertical: 7 },
  badgeText: { fontFamily: font.medium, fontSize: 14, color: colors.ink },
  hint: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginTop: 20 },
  section: { fontFamily: font.medium, fontSize: 13, color: colors.ink2, letterSpacing: 1.2, textTransform: 'uppercase', marginTop: 28, marginBottom: 12 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: colors.surface, borderRadius: 20, padding: 14, marginBottom: 10 },
  rowOn: { backgroundColor: '#e6f1ea' },
  rowEmoji: { fontSize: 26 },
  rowName: { flex: 1, fontFamily: font.medium, fontSize: 16, color: colors.ink },
  rowStatus: { fontFamily: font.regular, fontSize: 14, color: colors.ink2, minWidth: 40 },
  topBar: { position: 'absolute', top: 0, left: 0, right: 0, backgroundColor: colors.bg },
  back: { position: 'absolute', left: 8, width: 52, height: 52, alignItems: 'center', justifyContent: 'center' },
});
