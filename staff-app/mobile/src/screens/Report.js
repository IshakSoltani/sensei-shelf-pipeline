// Report a problem: which product, what's wrong, optional photo. Sent reports join the next restock round.
import { useMemo, useState } from 'react';
import { Alert, Image, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, font, radius } from '../theme';
import ProductThumb from '../ui/ProductThumb';
import { choose, success, tap, smoothLayout } from '../feel';
import { WORDS, colorOf, liveData } from '../logic/tasks';

const TYPES = ['empty', 'wrong', 'half', 'damaged'];
const hhmm = (d) => new Date(d).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const plain = (t) => t.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');

export default function Report({ reports, onSend, preset }) {
  const insets = useSafeAreaInsets();
  const { bays, cameraAreas, sections } = useMemo(() => liveData(), []);
  const areaOfBay = (code) => (sections.find((x) => x.cameras.some((c) => c.bays.some((b) => b.code === code))) || {}).n || null;
  // one row per product per bay
  const products = useMemo(() => {
    const seen = new Set(), out = [];
    for (const a of Object.values(cameraAreas).flat()) {
      const key = `${a.productId}|${a.bay}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ key, productId: a.productId, name: a.name, emoji: a.emoji, bay: a.bay });
    }
    return out;
  }, [cameraAreas]);
  const bayName = (code) => (bays.find((b) => b.code === code) || {}).name || '';

  const [section, setSection] = useState(preset || null); // a bay chip
  const [area, setArea] = useState(preset ? areaOfBay(preset) : null); // optional filter: one of the 5 aisles
  const [query, setQuery] = useState('');
  const [product, setProduct] = useState(null);
  const [type, setType] = useState(null);
  const [photo, setPhoto] = useState(null);
  const [sent, setSent] = useState(false);
  const ready = product && type;

  const q = plain(query.trim());
  const list = q
    ? products.filter((p) => plain(p.name).includes(q)).slice(0, 12)
    : section ? products.filter((p) => p.bay === section) : [];

  const getPhoto = async (camera) => {
    tap();
    try {
      let res;
      if (camera) {
        const perm = await ImagePicker.requestCameraPermissionsAsync();
        if (!perm.granted) { Alert.alert('Camera is off', 'Allow the camera in Settings, or choose a photo instead.'); return; }
        res = await ImagePicker.launchCameraAsync({ quality: 0.6 });
      } else {
        res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], quality: 0.6 });
      }
      if (!res.canceled && res.assets && res.assets[0]) { smoothLayout(); setPhoto(res.assets[0].uri); }
    } catch (e) {
      Alert.alert('No photo', 'The camera is not available here. You can still send the report.');
    }
  };

  const send = () => {
    success();
    smoothLayout();
    onSend({ id: `report-${Date.now()}`, bay: product.bay, productId: product.productId, name: product.name, emoji: product.emoji, type, photo, at: Date.now() });
    setProduct(null); setType(null); setPhoto(null); setQuery(''); setSent(true);
  };

  return (
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingTop: insets.top + 24, paddingHorizontal: 24, paddingBottom: insets.bottom + 40 }}>
      <Text style={s.eyebrow}>Report</Text>
      <Text style={s.h1}>What's wrong{'\n'}on the shelf?</Text>

      {sent && (
        <View style={s.sent}>
          <View style={s.sentCheck}><Feather name="check" size={20} color="#fff" /></View>
          <Text style={s.sentText}>Sent. It's in the next restock round, and it helps the cameras learn.</Text>
        </View>
      )}

      <Text style={s.section}>Which product</Text>
      {product ? (
        <View style={s.picked}>
          <ProductThumb productId={product.productId} emoji={product.emoji} size={56} radius={16} />
          <View style={{ flex: 1 }}>
            <Text style={s.pickedName}>{product.name}</Text>
            <Text style={s.pickedBay}>{product.bay} · {bayName(product.bay)}</Text>
          </View>
          <Pressable onPress={() => { tap(); smoothLayout(); setProduct(null); }} hitSlop={10} accessibilityLabel="Change product" style={s.change}>
            <Feather name="x" size={20} color={colors.ink2} />
          </Pressable>
        </View>
      ) : (
        <>
          <View style={s.search}>
            <Feather name="search" size={20} color={colors.ink2} />
            <TextInput value={query} onChangeText={(t) => { setQuery(t); setSent(false); }} placeholder="Search a product" placeholderTextColor="#a3adb8"
              style={s.searchInput} autoCorrect={false} returnKeyType="search" />
            {query.length > 0 && <Pressable onPress={() => setQuery('')} hitSlop={10}><Feather name="x-circle" size={20} color={colors.ink2} /></Pressable>}
          </View>

          {!q && (
            <View style={s.areas}>
              {sections.map((sec) => {
                const on = area === sec.n;
                return (
                  <Pressable key={sec.n} onPress={() => { choose(); smoothLayout(); setArea(on ? null : sec.n); setSection(null); }} accessibilityRole="button" accessibilityState={{ selected: on }}
                    style={[s.area, on && s.areaOn]}>
                    <Text style={[s.areaText, on && { color: colors.onDark }]}>Aisle {sec.n}</Text>
                  </Pressable>
                );
              })}
            </View>
          )}
          {!q && (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}>
              {bays.filter((b) => !area || areaOfBay(b.code) === area).map((b) => {
                const on = section === b.code;
                return (
                  <Pressable key={b.code} onPress={() => { choose(); smoothLayout(); setSection(on ? null : b.code); }} accessibilityRole="button" accessibilityState={{ selected: on }}
                    style={[s.chip, on && s.chipOn]}>
                    <Text style={[s.chipText, on && { color: colors.onDark }]}>{b.name}</Text>
                  </Pressable>
                );
              })}
            </ScrollView>
          )}

          {list.length > 0 ? (
            <View style={s.options}>
              {list.map((p) => (
                <Pressable key={p.key} onPress={() => { choose(); smoothLayout(); setProduct(p); setSent(false); }} accessibilityRole="button" style={s.option}>
                  <ProductThumb productId={p.productId} emoji={p.emoji} size={46} radius={12} />
                  <View style={{ flex: 1 }}>
                    <Text style={s.optionName}>{p.name}</Text>
                    <Text style={s.optionBay}>{p.bay} · {bayName(p.bay)}</Text>
                  </View>
                </Pressable>
              ))}
            </View>
          ) : (
            <Text style={s.hint}>{q ? 'No product found.' : 'Search, or pick an aisle and a section above.'}</Text>
          )}
        </>
      )}

      <Text style={s.section}>What's wrong</Text>
      <View style={s.grid}>
        {TYPES.map((k) => {
          const on = type === k;
          return (
            <Pressable key={k} onPress={() => { choose(); setType(k); setSent(false); }} accessibilityRole="button" accessibilityState={{ selected: on }}
              style={[s.type, on && s.typeOn]}>
              <View style={[s.typeDot, { backgroundColor: colors[colorOf(k)] }]} />
              <Text style={s.typeText}>{WORDS[k].label}</Text>
            </Pressable>
          );
        })}
      </View>

      <Text style={s.section}>Photo <Text style={s.optional}>· optional</Text></Text>
      {photo ? (
        <View>
          <Image source={{ uri: photo }} style={s.preview} />
          <Pressable onPress={() => { tap(); smoothLayout(); setPhoto(null); }} style={s.remove}><Text style={s.removeText}>Remove photo</Text></Pressable>
        </View>
      ) : (
        <View style={s.photoRow}>
          <Pressable onPress={() => getPhoto(true)} style={s.photoBtn}><Feather name="camera" size={22} color={colors.dark} /><Text style={s.photoText}>Take photo</Text></Pressable>
          <Pressable onPress={() => getPhoto(false)} style={s.photoBtn}><Feather name="image" size={22} color={colors.dark} /><Text style={s.photoText}>Choose photo</Text></Pressable>
        </View>
      )}

      <Pressable disabled={!ready} onPress={send} accessibilityRole="button"
        style={({ pressed }) => [s.go, !ready && s.goOff, pressed && { opacity: 0.88 }]}>
        <Text style={[s.goText, !ready && { color: colors.ink2 }]}>{ready ? 'Send report' : 'Choose a product and a problem'}</Text>
      </Pressable>

      {reports.length > 0 && (
        <>
          <Text style={s.section}>Sent today</Text>
          {reports.slice().reverse().map((r) => (
            <View key={r.id} style={s.sentRow}>
              {r.photo ? <Image source={{ uri: r.photo }} style={s.thumb} /> : <View style={[s.thumb, s.thumbEmpty]}><Text style={{ fontSize: 24 }}>{r.emoji || '📣'}</Text></View>}
              <View style={{ flex: 1 }}>
                <Text style={s.sentName}>{r.name || r.bay} · {WORDS[r.type].label}</Text>
                <Text style={s.sentMeta}>{r.bay} · {hhmm(r.at)} · in the next round</Text>
              </View>
              <View style={[s.dotBig, { backgroundColor: colors[colorOf(r.type)] }]} />
            </View>
          ))}
        </>
      )}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  eyebrow: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginBottom: 14, marginTop: 8 },
  h1: { fontFamily: font.regular, fontSize: 38, lineHeight: 43, color: colors.ink, letterSpacing: -1.1 },
  section: { fontFamily: font.medium, fontSize: 13, color: colors.ink2, letterSpacing: 1.2, textTransform: 'uppercase', marginTop: 30, marginBottom: 12 },
  optional: { fontFamily: font.regular, textTransform: 'none', letterSpacing: 0 },
  sent: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: '#e6f1ea', borderRadius: 20, padding: 16, marginTop: 24 },
  sentCheck: { width: 34, height: 34, borderRadius: 17, backgroundColor: colors.dark, alignItems: 'center', justifyContent: 'center' },
  sentText: { flex: 1, fontFamily: font.regular, fontSize: 15, lineHeight: 21, color: colors.dark },
  search: { flexDirection: 'row', alignItems: 'center', gap: 12, height: 60, backgroundColor: colors.surface, borderRadius: 22, paddingHorizontal: 20 },
  searchInput: { flex: 1, fontFamily: font.regular, fontSize: 18, color: colors.ink, height: 60 },
  areas: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 14 },
  area: { height: 38, paddingHorizontal: 15, borderRadius: 19, borderWidth: 1.5, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  areaOn: { backgroundColor: colors.dark, borderColor: colors.dark },
  areaText: { fontFamily: font.medium, fontSize: 14, color: colors.ink2 },
  chips: { gap: 10, paddingTop: 14, paddingRight: 8 },
  chip: { height: 42, paddingHorizontal: 16, borderRadius: 21, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center' },
  chipOn: { backgroundColor: colors.dark },
  chipText: { fontFamily: font.medium, fontSize: 15, color: colors.ink },
  options: { backgroundColor: colors.surface, borderRadius: 22, marginTop: 14, paddingVertical: 6 },
  option: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 12, paddingHorizontal: 18 },
  optionEmoji: { fontSize: 28, width: 36, textAlign: 'center' },
  optionName: { fontFamily: font.medium, fontSize: 17, color: colors.ink, letterSpacing: -0.2 },
  optionBay: { fontFamily: font.regular, fontSize: 13, color: colors.ink2, marginTop: 1 },
  hint: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, marginTop: 14 },
  picked: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: '#e6f1ea', borderRadius: 22, padding: 16 },
  pickedEmoji: { fontSize: 34 },
  pickedName: { fontFamily: font.medium, fontSize: 19, color: colors.ink, letterSpacing: -0.3 },
  pickedBay: { fontFamily: font.regular, fontSize: 14, color: colors.ink2 },
  change: { width: 36, height: 36, borderRadius: 18, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  type: { width: '48%', flexGrow: 1, flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 72, backgroundColor: colors.surface, borderRadius: 22, paddingHorizontal: 18, borderWidth: 2, borderColor: 'transparent' },
  typeOn: { borderColor: colors.dark, backgroundColor: '#f1f6f3' },
  typeDot: { width: 22, height: 22, borderRadius: 11 },
  typeText: { flex: 1, fontFamily: font.medium, fontSize: 17, color: colors.ink },
  photoRow: { flexDirection: 'row', gap: 12 },
  photoBtn: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10, minHeight: 64, borderRadius: 22, borderWidth: 1.5, borderColor: colors.line, borderStyle: 'dashed' },
  photoText: { fontFamily: font.medium, fontSize: 16, color: colors.dark },
  preview: { width: '100%', height: 220, borderRadius: radius },
  remove: { alignItems: 'center', paddingVertical: 12 },
  removeText: { fontFamily: font.regular, fontSize: 15, color: colors.ink2, textDecorationLine: 'underline' },
  go: { alignItems: 'center', justifyContent: 'center', minHeight: 72, borderRadius: 22, backgroundColor: colors.dark, marginTop: 30 },
  goOff: { backgroundColor: colors.surface2 },
  goText: { fontFamily: font.medium, fontSize: 20, color: colors.onDark },
  sentRow: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: colors.surface, borderRadius: 20, padding: 12, marginBottom: 10 },
  thumb: { width: 52, height: 52, borderRadius: 14 },
  thumbEmpty: { backgroundColor: colors.surface2, alignItems: 'center', justifyContent: 'center' },
  sentName: { fontFamily: font.medium, fontSize: 17, color: colors.ink },
  sentMeta: { fontFamily: font.regular, fontSize: 13, color: colors.ink2 },
  dotBig: { width: 16, height: 16, borderRadius: 8, marginRight: 6 },
});
