// "Hold a product" card: what the cameras saw, pinned to the top of the screen while a finger is down.
import { StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, font, radius } from '../theme';
import { WORDS, bayByCode } from '../logic/tasks';
import ShelfPicture, { FILL } from './ShelfPicture';

export default function PeekCard({ peek }) {
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const { item, areas, cameraAreas, photos = {} } = peek;
  return (
    <View style={s.dim} pointerEvents="none">
      <View style={[s.card, { marginTop: insets.top + 8 }]}>
        <Text style={s.title}>{item.emoji}  {item.name}</Text>
        {areas.map((a) => (
          <View key={a.id} style={{ marginTop: 14, alignItems: 'center' }}>
            <ShelfPicture areas={cameraAreas[a.camera]} photo={photos[a.camera]} focus={a.id} w={width - 32 - 32} radius={18} />
            <View style={s.meta}>
              <View style={[s.dot, { backgroundColor: FILL[a.status] }]} />
              <Text style={s.text}>{WORDS[a.status] ? WORDS[a.status].label : a.status === 'unknown' ? 'Not checked yet' : 'Full'} · {a.bay} {(bayByCode[a.bay] || {}).bay_name}</Text>
            </View>
          </View>
        ))}
        <Text style={s.note}>{Object.keys(photos).length ? 'Latest camera picture' : 'Sample camera picture'} · release to close</Text>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  dim: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(52,70,63,0.55)', alignItems: 'center', paddingHorizontal: 16, zIndex: 30 },
  card: { alignSelf: 'stretch', alignItems: 'center', backgroundColor: colors.bg, borderRadius: radius, padding: 16, shadowColor: '#1d2b25', shadowOpacity: 0.25, shadowRadius: 24, shadowOffset: { width: 0, height: 10 } },
  title: { textAlign: 'center', fontFamily: font.medium, fontSize: 22, color: colors.ink, letterSpacing: -0.3 },
  meta: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, marginTop: 10 },
  dot: { width: 12, height: 12, borderRadius: 6 },
  text: { fontFamily: font.medium, fontSize: 15, color: colors.ink },
  note: { fontFamily: font.regular, fontSize: 13, color: colors.ink2, marginTop: 14, textAlign: 'center' },
});
