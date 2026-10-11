// A product's picture: the official catalogue photo when we have one, else a real crop from the camera
// photo, else its emoji.
import { Image, Text, View } from 'react-native';
import Svg, { Image as SvgImage } from 'react-native-svg';

import { PHOTO, productCrop } from '../logic/tasks';
import productImages from '../data/productImages';

export default function ProductThumb({ productId, emoji, size = 48, radius = 14 }) {
  const official = productImages[String(productId)];
  if (official) {
    return (
      <View style={{ width: size, height: size, borderRadius: radius, overflow: 'hidden', backgroundColor: '#ffffff' }}>
        <Image source={official} style={{ width: size, height: size }} resizeMode="contain" />
      </View>
    );
  }
  const crop = productCrop(productId);
  if (!crop) return <Text style={{ fontSize: size * 0.62, width: size, textAlign: 'center' }}>{emoji}</Text>;
  return (
    <View style={{ width: size, height: size, borderRadius: radius, overflow: 'hidden', backgroundColor: '#56655d' }}>
      <Svg width={size} height={size} viewBox={crop.viewBox}>
        <SvgImage x={0} y={0} width={PHOTO.w} height={PHOTO.h} href={{ uri: crop.uri }} preserveAspectRatio="xMidYMid slice" />
      </Svg>
    </View>
  );
}
