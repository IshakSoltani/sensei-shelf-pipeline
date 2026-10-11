// Small touch feedback and soft motion, in one place so the feel is easy to tune.
import { LayoutAnimation } from 'react-native';
import * as Haptics from 'expo-haptics';

const safe = (fn) => { try { fn().catch(() => {}); } catch (e) { /* no haptics on this device */ } };

export const tap = () => safe(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light));      // buttons, cards
export const soft = () => safe(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Soft));      // opening / closing
export const choose = () => safe(() => Haptics.selectionAsync());                                 // picking an option
export const success = () => safe(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)); // task done
export const confirm = () => safe(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)); // starting the round

// Cards sliding open and closed
export const smoothLayout = () => LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);

// Menu pill gently fading in and out (done by iOS itself, so it can't get stuck)
export const menuLayout = () =>
  LayoutAnimation.configureNext({
    duration: 240,
    create: { type: LayoutAnimation.Types.easeInEaseOut, property: LayoutAnimation.Properties.opacity },
    delete: { type: LayoutAnimation.Types.easeInEaseOut, property: LayoutAnimation.Properties.opacity },
    update: { type: LayoutAnimation.Types.easeInEaseOut },
  });

// New problem on the shelves: two firm buzzes, hard to miss in a pocket
export const alertBuzz = () => {
  safe(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning));
  setTimeout(() => safe(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning)), 380);
};
