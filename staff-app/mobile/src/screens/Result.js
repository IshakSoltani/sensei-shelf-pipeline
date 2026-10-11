// A photo sent from the manager dashboard (Settings > Send to phone): the camera picture with every shelf area
// coloured by the model's verdict, the score against the hand labels when the photo has them, and each area's status.
import { Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, font, radius } from '../theme';
import areas from '../data/areas.json';
import planogram from '../data/planogram.json';
import ShelfPicture, { FILL } from '../ui/ShelfPicture';
import { tap } from '../feel';

const ORDER = ['empty', 'wrong', 'half', 'ok'];
const WORD = { empty: 'Empty', wrong: 'Wrong product', half: 'Half', ok: 'Full' };
const statusOf = (v) => (!v ? 'ok' : v.planogram_compliant === false ? 'wrong' : v.stock_level === 'empty' ? 'empty' : v.stock_level === 'half' ? 'half' : 'ok');

export default function Result({ focus, base, onClose }) {
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const here = Object.entries(areas).filter(([, a]) => a.camera === focus.camera)
    .map(([id, a]) => ({ id, name: a.name_en, emoji: a.emoji, bay: a.location_code, bayName: a.bay_name, pts: (planogram[focus.camera] || {})[id], status: statusOf(focus.verdicts[id]) }))
    .sort((x, y) => ORDER.indexOf(x.status) - ORDER.indexOf(y.status) || x.bay.localeCompare(y.bay));
  const counts = Object.fromEntries(ORDER.map((k) => [k, here.filter((a) => a.status === k).length]));
  const ts = parseInt(focus.name, 10);
  const when = ts > 1e9 ? new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null;
  const bays = [...new Set(here.map((a) => a.bay))].sort();
  const sc = focus.score;

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={{ paddingTop: insets.top + 20, paddingBottom: insets.bottom + 130 }}>
        <View style={s.pad}>
          <Text style={s.eyebrow}>New picture{when ? ` · ${when}` : ''}</Text>
          <Text style={s.h1}>{(here[0] || {}).bayName || `Camera ${focus.camera}`}</Text>
          <Text style={s.codes}>{bays.join(' · ')}</Text>
        </View>

        <View style={[s.pad, { marginTop: 20 }]}>
          <ShelfPicture areas={here} w={width - 48} photo={base ? `${base}/photos/${focus.camera}/${focus.name}` : null} />
          <View style={s.legend}>
            {ORDER.map((k) => (
              <View key={k} style={s.legendItem}><View style={[s.dot, { backgroundColor: FILL[k] }]} /><Text style={s.legendText}>{counts[k]} {WORD[k]}</Text></View>
            ))}
          </View>
        </View>

        <View style={[s.pad, { marginTop: 20 }]}>
          <View style={s.card}>
            {sc ? (
              <>
                <Text style={s.big}>{sc.points}<Text style={s.of}>{'  '}of {sc.max} points</Text></Text>
                <Text style={s.sub}>{sc.counts.stock_ok} of {sc.max / 2} stock levels and {sc.counts.id_ok} of {sc.max / 2} products right</Text>
              </>
            ) : (
              <Text style={s.sub}>No labels for this photo yet, so no score.</Text>
            )}
          </View>
        </View>

        <View style={s.pad}>
          <Text style={s.section}>On this picture</Text>
          {here.map((a) => (
            <View key={a.id} style={s.row}>
              <Text style={s.emoji}>{a.emoji}</Text>
              <View style={{ flex: 1 }}><Text style={s.name}>{a.name}</Text><Text style={s.bay}>{a.bay}</Text></View>
              <View style={[s.dot, { backgroundColor: FILL[a.status] }]} />
              <Text style={s.status}>{WORD[a.status]}</Text>
            </View>
          ))}
        </View>
      </ScrollView>

      <View style={[s.wrap, { bottom: insets.bottom + 16 }]} pointerEvents="box-none">
        <Pressable onPress={() => { tap(); onClose(); }} accessibilityRole="button" accessibilityLabel="Back to the shelves"
          style={({ pressed }) => [s.btn, pressed && { opacity: 0.88 }]}>
          <Text style={[s.btnText, { flex: 1 }]}>Back to the shelves</Text>
          <View style={s.btnRound}><Ionicons name="checkmark" size={24} color={colors.dark} /></View>
        </Pressable>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  pad: { paddingHorizontal: 24 },
  eyebrow: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginBottom: 14, marginTop: 8 },
  h1: { fontFamily: font.regular, fontSize: 36, lineHeight: 42, color: colors.ink, letterSpacing: -1.1, paddingRight: 60 },
  codes: { fontFamily: font.medium, fontSize: 15, color: colors.ink2, marginTop: 8 },
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: 14, marginTop: 16 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  legendText: { fontFamily: font.regular, fontSize: 14, color: colors.ink },
  dot: { width: 12, height: 12, borderRadius: 6 },
  card: { backgroundColor: colors.surface, borderRadius: radius, padding: 22 },
  big: { fontFamily: font.light, fontSize: 52, lineHeight: 58, color: colors.ink, letterSpacing: -1.6 },
  of: { fontFamily: font.regular, fontSize: 18, letterSpacing: 0, color: colors.ink2 },
  sub: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginTop: 4 },
  section: { fontFamily: font.medium, fontSize: 13, color: colors.ink2, letterSpacing: 1.2, textTransform: 'uppercase', marginTop: 28, marginBottom: 12 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: colors.surface, borderRadius: 18, paddingVertical: 14, paddingHorizontal: 16, marginBottom: 10 },
  emoji: { fontSize: 24 },
  name: { fontFamily: font.medium, fontSize: 16, color: colors.ink },
  bay: { fontFamily: font.regular, fontSize: 13, color: colors.ink2, marginTop: 1 },
  status: { fontFamily: font.regular, fontSize: 14, color: colors.ink2, minWidth: 96, textAlign: 'right' },
  wrap: { position: 'absolute', left: 24, right: 24 },
  btn: { flexDirection: 'row', alignItems: 'center', gap: 16, backgroundColor: colors.dark, borderRadius: radius, paddingVertical: 20, paddingLeft: 26, paddingRight: 20 },
  btnText: { fontFamily: font.medium, fontSize: 20, color: colors.onDark, letterSpacing: -0.4 },
  btnRound: { width: 56, height: 56, borderRadius: 28, backgroundColor: '#ffffff', alignItems: 'center', justifyContent: 'center' },
});
