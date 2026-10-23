/**
 * Hastaneler (şartname 6.2): daire ALANI ∝ yatak (yarıçap ∝ √yatak), renk = sınıf (theme/palette.ts
 * CLASS_COLORS), yüzey renginde 1,5–2 px halka (üst üste binen işaretler ayrılsın; açık temada yeşil sınıf yüzeye
 * 3:1'in altında — halka ve lejant etiketi bunu telafi eder). Yarıçap ekran pikseli cinsindendir ve yakınlaştırmayla
 * ölçeklenir (orantılı sembol haritası: her ölçekte "alan ∝ yatak" korunur, uzaktan bakınca da okunur).
 * Yakın ölçekte (≥ 10,5) yatak sayısı etiketi. Seçili hastanenin halesi selection.ts'te.
 */
import type { Layer } from '@deck.gl/core';
import { ScatterplotLayer, TextLayer } from '@deck.gl/layers';

import type { Hospital } from '../../api/types.ts';
import { fmtInt } from '../../format/number.ts';
import { FONT_STACK } from '../../theme/theme.ts';
import { ink, surfaceRing } from '../mapColors.ts';
import { staleOpacity, writeColor } from './memo.ts';
import { hospitalColor } from './serviceAreas.ts';
import type { LayerBuilder } from './types.ts';

/** px / √yatak, başvuru yakınlaştırmasında (8,5 ≈ Ankara'nın tamamı). 50 yatak ≈ 4 px, 400 ≈ 11 px, 4.050 ≈ 35 px. */
export const PX_PER_SQRT_BED = 0.55;

/** Sembol ölçeği: yakınlaştırdıkça işaretler büyür (ama haritayla aynı hızda değil). */
export function symbolScale(zoom: number): number {
  return Math.min(3, Math.max(0.75, Math.pow(2, (zoom - 8.5) * 0.4)));
}

/** yatak → ekran yarıçapı (px) o anki yakınlaştırmada (lejanttaki boyut anahtarı da bunu kullanır). */
export function bedsToPx(beds: number, zoom: number): number {
  return PX_PER_SQRT_BED * Math.sqrt(Math.max(beds, 1)) * symbolScale(zoom);
}

/** Etiketlerin göründüğü en küçük yakınlaştırma. */
export const LABEL_MIN_ZOOM = 10.5;

export const hospitalsLayer: LayerBuilder = (ctx) => {
  const hs = ctx.solution?.hospitals;
  if (!hs?.length) return null;
  const ring = surfaceRing(ctx.scheme);
  const scale = symbolScale(ctx.zoom);
  const opacity = staleOpacity(ctx.stale);
  const showLabels = ctx.visible.hospitals && ctx.zoom >= LABEL_MIN_ZOOM;
  // dış kontur (1,5 px, mürekkep renginde: açık temada koyu, koyu temada açık): yüzey renkli halkanın dışında —
  // her zeminde (altlık, hizmet bölgesi tint'i, nüfus rampası) hastane işaretleri ayrılsın. Ayrı bir alt katman:
  // deck.gl dairesinde tek çizgi var.
  const halo = ink(ctx.scheme, 'primary', ctx.scheme === 'dark' ? 170 : 200);
  const haloPx = 1.5;
  const layers: Layer[] = [
    new ScatterplotLayer<Hospital>({
      id: 'hastaneler-kontur',
      data: hs,
      visible: ctx.visible.hospitals,
      pickable: false,
      opacity,
      getPosition: (h) => [h.lon, h.lat],
      // ana dairenin yarıçapı + halkanın yarısı + kontur (yarıçap ekran pikseli; radiusScale ile ölçeklenir)
      getRadius: (h) => PX_PER_SQRT_BED * Math.sqrt(Math.max(h.beds, 1)) + ((h.beds >= 400 ? 2 : 1.5) / 2 + haloPx) / scale,
      radiusUnits: 'pixels',
      radiusScale: scale,
      radiusMinPixels: 3 + haloPx + 1,
      getFillColor: halo,
      stroked: false,
      updateTriggers: { getRadius: [scale, hs], getFillColor: ctx.scheme },
    }),
    new ScatterplotLayer<Hospital>({
      id: 'hastaneler',
      data: hs,
      visible: ctx.visible.hospitals,
      pickable: true,
      autoHighlight: true,
      highlightColor: ctx.scheme === 'dark' ? [255, 255, 255, 80] : [255, 255, 255, 110],
      opacity,
      getPosition: (h) => [h.lon, h.lat],
      getRadius: (h) => PX_PER_SQRT_BED * Math.sqrt(Math.max(h.beds, 1)),
      radiusUnits: 'pixels',
      radiusScale: scale,
      radiusMinPixels: 3,
      getFillColor: (h, { target }) => writeColor(target, hospitalColor(h, ctx.scheme)),
      stroked: true,
      getLineColor: ring,
      lineWidthUnits: 'pixels',
      getLineWidth: (h) => (h.beds >= 400 ? 2 : 1.5),
      updateTriggers: { getFillColor: [ctx.scheme, hs], getLineColor: ctx.scheme },
    }),
  ];
  if (showLabels) {
    layers.push(
      new TextLayer<Hospital>({
        id: 'hastane-etiket',
        data: hs,
        visible: true,
        opacity,
        getPosition: (h) => [h.lon, h.lat],
        getText: (h) => `${fmtInt(h.beds)} yatak`,
        getPixelOffset: (h) => [0, bedsToPx(h.beds, ctx.zoom) + 10],
        getSize: 12.5,
        sizeUnits: 'pixels',
        getColor: ink(ctx.scheme, 'primary'),
        fontFamily: FONT_STACK,
        fontWeight: 600,
        characterSet: 'auto',
        fontSettings: { sdf: true, fontSize: 48, buffer: 8 },
        outlineWidth: 0.25,
        outlineColor: surfaceRing(ctx.scheme, 240),
        getTextAnchor: 'middle',
        getAlignmentBaseline: 'center',
        pickable: false,
        updateTriggers: { getPixelOffset: [ctx.zoom, hs], getColor: ctx.scheme, getText: hs },
      }),
    );
  }
  return layers;
};
