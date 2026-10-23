/**
 * Katmanların çizim sırası (alttan üste) ve birleştirici. MapView yalnız `buildDeckLayers`'ı çağırır.
 * Yeni katman: dosyasını yazın, LAYER_DEFS'e (store/appStore.ts) ekleyin, aşağıdaki sıraya koyun.
 *
 * İki kat vardır (MapLibreOverlay interleaved): `beforeId: ctx.beforeId` alan hücre dolguları, sınırlar ve aday
 * çerçeveleri altlığın YAZILARININ ALTINDA çizilir (yer adları okunur kalır); diğerleri (akışlar, halkalar,
 * hastaneler, kilitler, seçim) en üstte. Her katta sıra bu dizinin sırasıdır.
 */
import type { Layer } from '@deck.gl/core';

import { accessLayer } from './access.ts';
import { boundariesLayer } from './boundaries.ts';
import { candidatesLayer } from './candidates.ts';
import { flowsLayer } from './flows.ts';
import { hospitalsLayer } from './hospitals.ts';
import { locksLayer } from './locks.ts';
import { populationLayer } from './population.ts';
import { ringsLayer } from './rings.ts';
import { selectionLayer } from './selection.ts';
import { serviceAreasLayer } from './serviceAreas.ts';
import { shadowPricesLayer } from './shadowPrices.ts';
import type { LayerBuilder, LayerContext } from './types.ts';

export type { LayerBuilder, LayerContext, MapTool, SolutionView } from './types.ts';

/** Alttan üste. */
export const LAYER_ORDER: readonly LayerBuilder[] = [
  populationLayer,
  accessLayer,
  serviceAreasLayer,
  shadowPricesLayer,
  candidatesLayer,
  boundariesLayer,
  flowsLayer,
  ringsLayer,
  hospitalsLayer,
  locksLayer,
  selectionLayer,
];

/** deck.gl katman kimlikleri (ipucu ve tıklama yönlendirmesi için). */
export const DECK_IDS = {
  hospitals: 'hastaneler',
  flows: 'akislar',
} as const;

export function buildDeckLayers(ctx: LayerContext): Layer[] {
  const out: Layer[] = [];
  for (const build of LAYER_ORDER) {
    try {
      const l = build(ctx);
      if (Array.isArray(l)) out.push(...l);
      else if (l) out.push(l);
    } catch (ex) {
      // bir katmanın hatası haritayı düşürmesin
      console.warn('Katman oluşturulamadı:', ex);
    }
  }
  return out;
}
