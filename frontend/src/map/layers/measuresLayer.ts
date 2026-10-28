/**
 * Ankara ölçüleri katmanı (hocanın 4. maddesi "toplam mesafe"; Veri → Ankara ölçüleri kartının "Haritada çiz"
 * düğmesi `layers.measures`'ı açar). Veri /api/measures'tan (data/processed/ankara_olculer.json; info/infoStore.ts
 * bir kez yükler) — burada hiçbir sayı üretilmez, etiketler dosyadaki Türkçe `text` alanlarıdır.
 *
 *   - en uzak iki sınır noktası (çap): kalın mürekkep çizgi;
 *   - Kızılay → en uzak sınır noktası: ikincil mürekkep;
 *   - en batı ↔ en doğu ve en kuzey ↔ en güney sınır noktaları: ince soluk çizgiler;
 *   - Kızılay ve ilin (eşit alanlı) ağırlık merkezi: işaret + ad.
 *
 * Renkler yalnız mürekkep tonları (ölçü bir açıklamadır, veri serisi değil — sınıf ve rampa renkleriyle karışmaz);
 * her çizginin altında yüzey renginde bir "kılıf" var (hizmet bölgesi renkleri üstünde okunur). Veri henüz yoksa
 * katman yüklemeyi başlatır ve boş döner; yükleme bitince harita yeniden çizilir (infoStore.touchMapIfMeasuresShown).
 */
import { LineLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';

import type { MeasurePoint } from '../../api/infoTypes.ts';
import { loadMeasures, measuresNow } from '../../info/infoStore.ts';
import { FONT_STACK } from '../../theme/theme.ts';
import { ink, surfaceRing } from '../mapColors.ts';
import { keepAlive, memoLast } from './memo.ts';
import type { LayerBuilder } from './types.ts';

type Pos = [number, number];

interface Segment {
  id: 'cap' | 'kizilay' | 'dogu_bati' | 'kuzey_guney';
  from: Pos;
  to: Pos;
  /** etiketin çizgi üzerindeki konumu (0–1; üst üste binmesin diye her çizgide farklı) */
  at: number;
  label: string;
  weight: 'primary' | 'secondary' | 'muted';
}

interface Marker {
  pos: Pos;
  label: string;
  kind: 'kizilay' | 'merkez' | 'uc';
}

const pos = (p: MeasurePoint | undefined): Pos | null => (p && Number.isFinite(p.lon) && Number.isFinite(p.lat) ? [p.lon, p.lat] : null);

const geometry = memoLast((m: ReturnType<typeof measuresNow>) => {
  const segs: Segment[] = [];
  const marks: Marker[] = [];
  if (!m) return { segs, marks };
  const d = m.diameter;
  const a = pos(d?.p1);
  const b = pos(d?.p2);
  if (d && a && b) {
    segs.push({ id: 'cap', from: a, to: b, at: 0.45, label: `En uzak iki sınır noktası: ${d.distance_km.text}`, weight: 'primary' });
    marks.push({ pos: a, label: d.p1.district?.ilce ?? '', kind: 'uc' }, { pos: b, label: d.p2.district?.ilce ?? '', kind: 'uc' });
  }
  const k = m.kizilay;
  const kp = pos(k?.point);
  const kf = pos(k?.farthest);
  if (k && kp && kf) segs.push({ id: 'kizilay', from: kp, to: kf, at: 0.58, label: `Kızılay → en uzak nokta: ${k.farthest.distance_km.text}`, weight: 'secondary' });
  const e = m.extent;
  const w = pos(e?.west);
  const ea = pos(e?.east);
  if (e?.west_east_geodesic_km && w && ea) segs.push({ id: 'dogu_bati', from: w, to: ea, at: 0.72, label: `En batı ↔ en doğu: ${e.west_east_geodesic_km.text}`, weight: 'muted' });
  const n = pos(e?.north);
  const s = pos(e?.south);
  if (e?.north_south_geodesic_km && n && s) segs.push({ id: 'kuzey_guney', from: n, to: s, at: 0.22, label: `En kuzey ↔ en güney: ${e.north_south_geodesic_km.text}`, weight: 'muted' });
  for (const p of [e?.north, e?.south, e?.east]) {
    const q = pos(p);
    if (q) marks.push({ pos: q, label: '', kind: 'uc' });
  }
  if (kp) marks.push({ pos: kp, label: k?.point.name ?? 'Kızılay', kind: 'kizilay' });
  const c = pos(m.centroid?.point);
  if (c) marks.push({ pos: c, label: 'İlin ağırlık merkezi', kind: 'merkez' });
  return { segs, marks };
});

const WIDTH: Record<Segment['weight'], number> = { primary: 3.5, secondary: 2.5, muted: 1.5 };

export const measuresLayer: LayerBuilder = (ctx) => {
  const vis = !!ctx.visible.measures;
  if (!keepAlive('olculer', vis)) return null;
  const m = measuresNow();
  if (!m) {
    if (vis) void loadMeasures();
    return null;
  }
  const { segs, marks } = geometry(m);
  const casing = surfaceRing(ctx.scheme, 235);
  const lineColor = (s: Segment) => ink(ctx.scheme, s.weight, s.weight === 'muted' ? 230 : 255);
  const labelAt = (s: Segment): Pos => [s.from[0] + (s.to[0] - s.from[0]) * s.at, s.from[1] + (s.to[1] - s.from[1]) * s.at];
  const common = { visible: vis, pickable: false, updateTriggers: { getColor: ctx.scheme, getFillColor: ctx.scheme, getLineColor: ctx.scheme, getBackgroundColor: ctx.scheme, getBorderColor: ctx.scheme } };
  return [
    new LineLayer<Segment>({
      id: 'olcu-kilif',
      data: segs,
      getSourcePosition: (s) => s.from,
      getTargetPosition: (s) => s.to,
      getColor: casing,
      getWidth: (s) => WIDTH[s.weight] + 3,
      widthUnits: 'pixels',
      ...common,
    }),
    new LineLayer<Segment>({
      id: 'olcu-cizgi',
      data: segs,
      getSourcePosition: (s) => s.from,
      getTargetPosition: (s) => s.to,
      getColor: lineColor,
      getWidth: (s) => WIDTH[s.weight],
      widthUnits: 'pixels',
      ...common,
    }),
    new ScatterplotLayer<Marker>({
      id: 'olcu-nokta',
      data: marks,
      getPosition: (d) => d.pos,
      getRadius: (d) => (d.kind === 'uc' ? 3.5 : 6),
      radiusUnits: 'pixels',
      stroked: true,
      getFillColor: (d) => ink(ctx.scheme, d.kind === 'uc' ? 'secondary' : 'primary'),
      getLineColor: casing,
      lineWidthUnits: 'pixels',
      getLineWidth: 2,
      ...common,
    }),
    new TextLayer<Segment>({
      id: 'olcu-etiket',
      data: segs,
      getPosition: labelAt,
      getText: (s) => s.label,
      getSize: 13,
      sizeUnits: 'pixels',
      getColor: (s) => ink(ctx.scheme, s.weight === 'muted' ? 'secondary' : 'primary'),
      fontFamily: FONT_STACK,
      fontWeight: 650,
      characterSet: 'auto',
      background: true,
      getBackgroundColor: surfaceRing(ctx.scheme, 230),
      backgroundPadding: [5, 2, 5, 2],
      getBorderColor: ink(ctx.scheme, 'muted', 160),
      getBorderWidth: 1,
      getTextAnchor: 'middle',
      getAlignmentBaseline: 'center',
      ...common,
    }),
    new TextLayer<Marker>({
      id: 'olcu-nokta-etiket',
      data: marks.filter((d) => d.label && d.kind !== 'uc'),
      getPosition: (d) => d.pos,
      getText: (d) => d.label,
      getSize: 12.5,
      sizeUnits: 'pixels',
      // Kızılay sağ üstte, ağırlık merkezi sol altta: merkezdeki hastane kümesiyle ve birbirleriyle çakışmasın
      getPixelOffset: (d) => (d.kind === 'kizilay' ? [10, -8] : [-10, 10]),
      getTextAnchor: (d) => (d.kind === 'kizilay' ? 'start' : 'end'),
      getColor: ink(ctx.scheme, 'primary'),
      fontFamily: FONT_STACK,
      fontWeight: 650,
      characterSet: 'auto',
      fontSettings: { sdf: true, fontSize: 48, buffer: 8 },
      outlineWidth: 0.3,
      outlineColor: surfaceRing(ctx.scheme, 240),
      getAlignmentBaseline: (d) => (d.kind === 'kizilay' ? 'bottom' : 'top'),
      ...common,
    }),
  ];
};
