/**
 * Harita katmanı sözleşmesi.
 *
 * Her katman dosyası bir `LayerBuilder` dışa aktarır: depodan türetilmiş `LayerContext`'i alır, deck.gl
 * katman(lar)ı ya da null döndürür. MapView her durum değişikliğinde `buildDeckLayers(ctx)`'i çağırır ve sonucu
 * MapLibreOverlay'e (interleaved) verir; deck.gl aynı `id`'li katmanları verimli günceller.
 *
 * Kurallar:
 *   - `id`'ler sabit; büyük veri dizileri modül düzeyinde not edilir (memo.ts — girdi nesnesi aynıysa aynı dizi),
 *     renk/genişlik değişiklikleri `updateTriggers` ile (yeniden mozaikleme yok);
 *   - renkler theme/palette.ts ve map/mapColors.ts'ten (sınıf rengi → classColorRgba; sıralı rampa → mapColors);
 *   - tıklama/üzerine gelme MapView'de merkezîdir (seçim aracı, kilit aracı, hücre bulma); katmanlar yalnız
 *     `pickable` olur;
 *   - hücre dizileri `grid_index` (ızgara sırası) ya da `layers.demand` / `layers.candidates` sırasıdır
 *     (bkz. api/types.ts ResultLayers);
 *   - görünürlük `visible: ctx.visible[id]` ile verilir (GPU tamponları korunur, aç/kapa anında);
 *   - hücre dolguları `beforeId: ctx.beforeId` alır: altlığın yazılarının ALTINDA çizilir (yer adları okunur kalır).
 */
import type { Layer } from '@deck.gl/core';

import type { Boundaries, GridCells, GridKm, Locks, Meta, SolveResultOk } from '../../api/types.ts';
import type { LayerId, Selection } from '../../store/appStore.ts';
import type { Scheme } from '../../theme/palette.ts';
import type { GridIndex } from '../gridIndex.ts';

/** Haritada gösterilen çözümün ortak alt kümesi (canlı çözüm, önbellek, yedek ya da Faz 7 manuel değerlendirme). */
export type SolutionView = Pick<SolveResultOk, 'hospitals' | 'assignment' | 'layers' | 'kpis' | 'costs'> & {
  duals?: SolveResultOk['duals'];
};

/** Harita aracı: seç (kartlar) · zorunlu kıl ('aç' kilidi) · yasakla. */
export type MapTool = 'select' | 'lockOpen' | 'lockClosed';

export interface LayerContext {
  scheme: Scheme;
  gridKm: GridKm;
  grid: GridCells | null;
  gridIndex: GridIndex | null;
  boundaries: Boundaries | null;
  meta: Meta | null;
  /** gösterilen çözüm (yoksa ya da olursuzsa null) */
  solution: SolutionView | null;
  /** çözüm güncel parametrelere ait değilse true (katmanlar soluklaştırılır) */
  stale: boolean;
  locks: Locks;
  selection: Selection;
  hoveredHospital: number | null;
  visible: Record<LayerId, boolean>;
  /** haritanın o anki yakınlaştırması (sembol ölçeği, etiket yoğunluğu) */
  zoom: number;
  actions: {
    select(sel: Selection): void;
    hoverHospital(index: number | null): void;
  };
  /** altlık yazılarının altına çizilecek katmanlar için MapLibre katman kimliği (basemap.ts FILL_ANCHOR) */
  beforeId?: string;
  /** imlecin altındaki hücre (grid_index; kilit aracında önizleme çerçevesi) */
  hoveredCell?: number | null;
  tool?: MapTool;
}

export type LayerBuilder = (ctx: LayerContext) => Layer | Layer[] | null;
