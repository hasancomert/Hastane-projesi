import type { Rgba } from '../../theme/palette.ts';

/**
 * Tek girdili not alma: argümanlar (referans olarak) bir önceki çağrıyla aynıysa önceki sonucu döndürür.
 * Katman verisi (düz diziler, kenar listeleri) böylece yalnız çözüm / ızgara / tema değişince yeniden kurulur;
 * deck.gl aynı `data` referansını görünce mozaiklemeyi tekrarlamaz.
 */
export function memoLast<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  let lastArgs: A | null = null;
  let last!: R;
  return (...args: A) => {
    if (lastArgs && lastArgs.length === args.length && args.every((a, i) => Object.is(a, lastArgs![i]))) return last;
    last = fn(...args);
    lastArgs = args;
    return last;
  };
}

/** deck.gl erişimcisinin `target` tamponuna rengi yazar (her hücre için yeni dizi ayırmaz). */
export function writeColor(target: number[], c: ArrayLike<number>, alphaMul = 1): Rgba {
  target[0] = c[0];
  target[1] = c[1];
  target[2] = c[2];
  target[3] = (c[3] ?? 255) * alphaMul;
  return target as unknown as Rgba;
}

/**
 * Altlık yazılarının altına çizim: deck.gl'in MapLibre katman grubu `beforeId` özelliği (deck.gl tiplerinde yok;
 * @deck.gl/maplibre çalışma anında `layer.props.beforeId` okur). Katman özelliklerine yayılarak verilir.
 */
export function underLabels(ctx: { beforeId?: string }): Record<never, never> {
  return ctx.beforeId ? ({ beforeId: ctx.beforeId } as Record<never, never>) : {};
}

/** Solukluk çarpanı: çözüm güncel değilse (parametre değişti, yeni çözüm bekleniyor) çözüm katmanları soluk. */
export const staleOpacity = (stale: boolean) => (stale ? 0.45 : 1);

/** Odak hastanesi: üzerine gelinen, yoksa seçili hastane (hizmet bölgesi, akış, halka vurgusu). */
export function focusHospital(ctx: { hoveredHospital: number | null; selection: { kind: string; index?: number } | null }): number | null {
  if (ctx.hoveredHospital !== null && ctx.hoveredHospital >= 0) return ctx.hoveredHospital;
  if (ctx.selection?.kind === 'hospital' && typeof ctx.selection.index === 'number') return ctx.selection.index;
  return null;
}

/**
 * Hiç açılmamış katmanı hiç kurma (ilk açılış hızlı), bir kez açılanı `visible: false` ile canlı tut (aç/kapa
 * anında; GPU tamponları korunur). Döndürdüğü değer: katman nesnesi oluşturulmalı mı.
 */
const everShown = new Set<string>();
export function keepAlive(id: string, visible: boolean): boolean {
  if (visible) everShown.add(id);
  return everShown.has(id);
}
