// Full-screen "Go to Aisle N" moment, shown when a trip starts and whenever the next stop is in another aisle.
// It stays until the worker taps "I'm here".
import { useEffect, useRef } from 'react';
import { Animated, Easing, Pressable, StyleSheet, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, font } from '../theme';
import { confirm } from '../feel';
import { cancel, converse, isOn, register } from '../voice';

export default function GoTo({ title, number, emojis, note, speech, onDone }) {
  const insets = useSafeAreaInsets();
  const fade = useRef(new Animated.Value(0)).current;
  const rise = useRef(new Animated.Value(24)).current;
  const closing = useRef(false);

  const close = () => {
    if (closing.current) return;
    closing.current = true;
    Animated.timing(fade, { toValue: 0, duration: 280, easing: Easing.out(Easing.quad), useNativeDriver: false }).start(onDone);
  };

  // hands full? it says where to go and listens ("I'm here" closes this screen)
  useEffect(() => {
    if (!isOn() || !speech) return;
    const unregister = register({
      context: () => ({ screen: 'walking_to_next_aisle', headline: number, waiting_there: note, tip: 'if the worker says they are here or arrived, use action done' }),
      actions: { done: close },
      ends: ['done'],
    });
    converse(speech);
    return () => { unregister(); cancel(); };
  }, []);

  useEffect(() => {
    confirm();
    Animated.parallel([
      Animated.timing(fade, { toValue: 1, duration: 320, easing: Easing.out(Easing.quad), useNativeDriver: false }),
      Animated.timing(rise, { toValue: 0, duration: 420, easing: Easing.out(Easing.cubic), useNativeDriver: false }),
    ]).start();
  }, []);

  return (
    <Animated.View style={[s.screen, { opacity: fade }]}>
      <View style={[s.press, { paddingTop: insets.top, paddingBottom: insets.bottom }]}>
        <Animated.View style={[s.center, { transform: [{ translateY: rise }] }]}>
          <View style={s.badge}><Feather name="navigation" size={30} color={colors.dark} /></View>
          <Text style={s.small}>{title}</Text>
          <Text style={s.big}>{number}</Text>
          <View style={s.emojis}>
            {emojis.map((e, k) => <Text key={k} style={s.emoji}>{e}</Text>)}
          </View>
          <Text style={s.note}>{note}</Text>
        </Animated.View>
        <View style={[s.footer, { bottom: insets.bottom + 16 }]}>
          <Pressable onPress={close} accessibilityRole="button" accessibilityLabel="I'm here"
            style={({ pressed }) => [s.btn, pressed && { opacity: 0.88 }]}>
            <Text style={s.btnText}>I'm here</Text>
            <View style={s.btnIcon}><Feather name="check" size={24} color={colors.dark} /></View>
          </Pressable>
        </View>
      </View>
    </Animated.View>
  );
}

const s = StyleSheet.create({
  screen: { ...StyleSheet.absoluteFill, backgroundColor: colors.bg, zIndex: 40 },
  press: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 },
  center: { alignItems: 'center' },
  badge: { width: 76, height: 76, borderRadius: 38, backgroundColor: '#e6f1ea', alignItems: 'center', justifyContent: 'center', marginBottom: 28 },
  small: { fontFamily: font.regular, fontSize: 24, color: colors.ink2, letterSpacing: -0.4 },
  big: { fontFamily: font.light, fontSize: 84, lineHeight: 92, color: colors.ink, letterSpacing: -3, textAlign: 'center', marginTop: 4 },
  emojis: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: 14, marginTop: 30 },
  emoji: { fontSize: 38 },
  note: { fontFamily: font.regular, fontSize: 16, color: colors.ink2, marginTop: 18, textAlign: 'center' },
  footer: { position: 'absolute', left: 24, right: 24 },
  btn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: 72, borderRadius: 22, backgroundColor: colors.dark, paddingLeft: 26, paddingRight: 12 },
  btnText: { fontFamily: font.medium, fontSize: 20, color: colors.onDark, letterSpacing: -0.2 },
  btnIcon: { width: 50, height: 50, borderRadius: 25, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center' },
});
