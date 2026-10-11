// Round complete: what was fixed, how long it took, and what happens next.
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, font, radius } from '../theme';
import ProductThumb from '../ui/ProductThumb';
import { tap } from '../feel';
import { WORDS, bayByCode } from '../logic/tasks';

const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;

export default function Complete({ done, seconds, log = [], total = 0, nextTripNo, onNextTrip, onHome, onReset }) {
  const insets = useSafeAreaInsets();
  const several = log.length > 1;
  const finished = !nextTripNo;
  const jobsAll = log.length ? log.reduce((n, r) => n + r.jobs, 0) : done.length;
  const last = log[log.length - 1];
  // the big number: this trip while more are coming, the whole round once the last trip is done
  const bigSeconds = finished ? (total || seconds) : last ? last.loadSeconds + last.seconds : seconds;
  const bigLabel = finished ? (several ? 'Total time · all trips' : 'Round time') : `Trip ${last ? last.no : 1} time`;
  return (
    <View style={{ flex: 1 }}>
    <ScrollView contentContainerStyle={{ paddingTop: insets.top + 68, paddingHorizontal: 24, paddingBottom: insets.bottom + 150 }}>
      <View style={s.badge}><Feather name="check" size={44} color={colors.dark} /></View>
      <Text style={s.h1}>{nextTripNo ? 'Trip done.' : 'Round complete.'}</Text>

      <View style={s.stats}>
        <View style={[s.stat, { backgroundColor: '#a9d68f' }]}>
          <Text style={[s.statValue, { color: colors.dark }]} numberOfLines={1}>{jobsAll}</Text>
          <Text style={[s.statLabel, { color: colors.dark }]}>{finished ? 'Jobs fixed' : 'Jobs fixed so far'}</Text>
        </View>
        <View style={[s.stat, { backgroundColor: colors.dark }]}>
          <Text style={s.statValue} numberOfLines={1} adjustsFontSizeToFit>{mmss(bigSeconds)}</Text>
          <Text style={s.statLabel}>{bigLabel}</Text>
        </View>
      </View>

      {/* trip by trip: loading and walking the route */}
      {log.length > 0 && (
        <View style={s.times}>
          {log.map((r) => (
            <View key={r.no} style={s.timeRow}>
              <Text style={s.timeTrip}>{log.length > 1 || nextTripNo ? `Trip ${r.no}` : 'This round'}</Text>
              <View style={{ flex: 1 }}>
                <Text style={s.timeMain}>{mmss(r.loadSeconds + r.seconds)}</Text>
                <Text style={s.timeSub}>Loading {mmss(r.loadSeconds)} · Route {mmss(r.seconds)} · {r.jobs} {r.jobs === 1 ? 'job' : 'jobs'}</Text>
              </View>
            </View>
          ))}
          {finished && several && (
            <View style={[s.timeRow, s.timeTotal]}>
              <Text style={s.timeTrip}>Total</Text>
              <View style={{ flex: 1 }}>
                <Text style={s.timeMain}>{mmss(total)}</Text>
                <Text style={s.timeSub}>From the first loading to the last Done, breaks included</Text>
              </View>
            </View>
          )}
        </View>
      )}

      <View style={{ marginTop: 24 }}>
        {(finished && several ? log.flatMap((r) => r.tasks || []) : done).map((t) => (
          <View key={t.key} style={s.row}>
            <ProductThumb productId={t.productId} emoji={t.emoji} size={44} radius={12} />
            <View style={{ flex: 1 }}>
              <Text style={s.name}>{t.name}{t.count > 1 ? `  ×${t.count}` : ''}</Text>
              <Text style={s.meta}>{t.bay} · {(bayByCode[t.bay] || {}).bay_name} · {WORDS[t.type].label}</Text>
            </View>
            <Text style={s.wait}>Waiting for{'\n'}camera ✓</Text>
          </View>
        ))}
      </View>

      <Pressable onPress={() => { tap(); onReset(); }} style={s.reset}><Text style={s.resetText}>Restart demo</Text></Pressable>
    </ScrollView>

    {/* always in view, whatever the length of the list */}
    <View style={[s.footer, { bottom: insets.bottom + 16 }]} pointerEvents="box-none">
      {nextTripNo ? (
        <>
          <Pressable onPress={() => { tap(); onNextTrip(); }} accessibilityRole="button" style={({ pressed }) => [s.btn, pressed && { opacity: 0.88 }]}>
            <Text style={s.btnText}>Load trolley for trip {nextTripNo}</Text>
            <View style={s.btnIcon}><Feather name="arrow-right" size={24} color={colors.dark} /></View>
          </Pressable>
          <Pressable onPress={() => { tap(); onHome(); }} accessibilityRole="button" style={s.homeLink}>
            <Text style={s.homeLinkText}>Back to home</Text>
          </Pressable>
        </>
      ) : (
        <Pressable onPress={() => { tap(); onHome(); }} accessibilityRole="button" style={({ pressed }) => [s.btn, pressed && { opacity: 0.88 }]}>
          <Text style={s.btnText}>Back to home</Text>
          <View style={s.btnIcon}><Feather name="home" size={22} color={colors.dark} /></View>
        </Pressable>
      )}
    </View>
    </View>
  );
}

const s = StyleSheet.create({
  badge: { width: 96, height: 96, borderRadius: 48, backgroundColor: '#a9d68f', alignItems: 'center', justifyContent: 'center' },
  h1: { fontFamily: font.regular, fontSize: 38, lineHeight: 43, color: colors.ink, letterSpacing: -1.1, marginTop: 24 },
  stats: { flexDirection: 'row', gap: 12, marginTop: 24 },
  stat: { flex: 1, borderRadius: radius, padding: 20 },
  statValue: { fontFamily: font.light, fontSize: 44, lineHeight: 52, color: '#fff', letterSpacing: -1.5 },
  statLabel: { fontFamily: font.regular, fontSize: 14, color: 'rgba(255,255,255,0.8)', marginTop: 2 },
  times: { backgroundColor: colors.surface, borderRadius: 22, paddingHorizontal: 18, paddingVertical: 6, marginTop: 16 },
  timeRow: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 12 },
  timeTotal: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  timeTrip: { fontFamily: font.medium, fontSize: 15, color: colors.ink2, width: 62 },
  timeMain: { fontFamily: font.medium, fontSize: 20, color: colors.ink, letterSpacing: -0.3 },
  timeSub: { fontFamily: font.regular, fontSize: 13, color: colors.ink2, marginTop: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: colors.surface, borderRadius: 20, padding: 14, marginBottom: 10 },
  emoji: { fontSize: 28 },
  name: { fontFamily: font.medium, fontSize: 17, color: colors.ink },
  meta: { fontFamily: font.regular, fontSize: 13, color: colors.ink2, marginTop: 1 },
  wait: { fontFamily: font.medium, fontSize: 12, lineHeight: 15, color: '#4a9b72', textAlign: 'right' },
  footer: { position: 'absolute', left: 24, right: 24 },
  btn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', minHeight: 72, borderRadius: 22, backgroundColor: colors.dark, paddingLeft: 26, paddingRight: 12 },
  homeLink: { alignItems: 'center', paddingTop: 12 },
  homeLinkText: { fontFamily: font.medium, fontSize: 15, color: colors.dark, textDecorationLine: 'underline' },
  btnText: { flex: 1, textAlign: 'left', fontFamily: font.medium, fontSize: 20, color: colors.onDark },
  btnIcon: { width: 50, height: 50, borderRadius: 25, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center' },
  reset: { alignItems: 'center', paddingVertical: 20 },
  resetText: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, textDecorationLine: 'underline' },
});
