// Stand-in for a camera picture: the camera's shelf areas, coloured by status, zoomed to where the shelves are.
import React from 'react';
import { View } from 'react-native';
import Svg, { Image as SvgImage, Polygon, Rect, Text as SvgText } from 'react-native-svg';

import { colors, radius as defaultRadius } from '../theme';
import { PHOTO } from '../logic/tasks';

export const FILL = { ok: colors.ok, half: colors.half, wrong: colors.wrong, empty: colors.empty, unknown: '#8f9aa3' };

// bounding box of the areas, with a margin, kept 16:9 and centred on the shelves
export function viewBoxOf(areas) {
  const pts = areas.flatMap((a) => a.pts || []);
  if (!pts.length) return `0 0 ${PHOTO.w} ${PHOTO.h}`;
  const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
  const m = 50;
  let x0 = Math.max(0, Math.min(...xs) - m), x1 = Math.min(PHOTO.w, Math.max(...xs) + m);
  let y0 = Math.max(0, Math.min(...ys) - m), y1 = Math.min(PHOTO.h, Math.max(...ys) + m);
  let w = x1 - x0, h = y1 - y0;
  if (w / h > PHOTO.w / PHOTO.h) { const nh = (w * PHOTO.h) / PHOTO.w; y0 -= (nh - h) / 2; h = nh; }
  else { const nw = (h * PHOTO.w) / PHOTO.h; x0 -= (nw - w) / 2; w = nw; }
  return `${Math.round(x0)} ${Math.round(y0)} ${Math.round(w)} ${Math.round(h)}`;
}

// `focus` = area id to stress (others fade); `radius` of the rounded corners
export default function ShelfPicture({ areas, w, focus, photo, radius = defaultRadius }) {
  const h = (w * PHOTO.h) / PHOTO.w;
  return (
    <View style={{ width: w, height: h, borderRadius: radius, overflow: 'hidden', backgroundColor: '#56655d' }}>
      <Svg width={w} height={h} viewBox={viewBoxOf(areas)}>
        <Rect x={-2000} y={-2000} width={6000} height={6000} fill="#56655d" />
        {photo && <SvgImage x={0} y={0} width={PHOTO.w} height={PHOTO.h} href={{ uri: photo }} preserveAspectRatio="xMidYMid slice" />}
        {areas.filter((a) => a.pts).map((a) => {
          const xs = a.pts.map((q) => q[0]), ys = a.pts.map((q) => q[1]);
          const cx = xs.reduce((t, v) => t + v, 0) / xs.length, cy = ys.reduce((t, v) => t + v, 0) / ys.length;
          const size = Math.max(34, Math.min(100, Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) * 0.55));
          const on = !focus || a.id === focus;
          return (
            <React.Fragment key={a.id}>
              <Polygon points={a.pts.map((q) => q.join(',')).join(' ')} fill={FILL[a.status]} fillOpacity={photo ? (on ? 0.3 : 0.1) : on ? 0.7 : 0.18}
                stroke={a.id === focus ? '#ffffff' : FILL[a.status]} strokeWidth={a.id === focus ? 9 : photo ? 5 : 3} />
              {!photo && <SvgText x={cx} y={cy + size * 0.34} fontSize={size} textAnchor="middle" opacity={on ? 1 : 0.4}>{a.emoji}</SvgText>}
            </React.Fragment>
          );
        })}
      </Svg>
    </View>
  );
}
