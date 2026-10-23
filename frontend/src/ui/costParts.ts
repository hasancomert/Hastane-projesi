/**
 * Maliyet kırılımı kategorileri (şartname 6.4 ve 7): sonuç panelindeki yığılmış çubuk, hastane kartı ve Faz 6'nın
 * "Maliyet kırılımı" grafiği AYNI sırayı, adları ve renkleri kullanmalı (renk kategoriyi izler, sırayı değil).
 *
 * Renkler veri görselleştirme rehberinin doğrulanmış 8'li kategorik paletinden; yığın sırası renk körlüğü için
 * seçildi: arsa · yapı · KDV · altyapı · donanım · sabit · işletme · erişim. Doğrulama (dataviz
 * validate_palette.js, bitişik çiftler, 2026-10-23): sekiz segmentin hepsi ve sıfır olan segmentler (arsa,
 * işletme varsayılan olarak 0) çıkarıldığında oluşan her bitişik dizi açık ve koyu temada geçti — en kötü bitişik
 * CVD ΔE 9,1 (açık) / 8,4 (koyu), normal görüş ΔE 19,6 / 19,3. Açık temada aqua, sarı ve pembe yüzeye 3:1'in
 * altında → çubuğun yanında değerleri yazan lejant tablosu her zaman gösterilir (rehberin "relief" kuralı).
 *
 * NOT (Faz 6): bu tablo palette.ts'e taşınabilir; panel geliştiricisinin yetki alanı src/panels + src/ui idi.
 */
import type { CostBreakdown, HospitalCosts } from '../api/types.ts';
import { COST_COLORS } from '../theme/palette.ts';
import type { Scheme } from '../theme/palette.ts';

export type CostKey = 'arsa' | 'yapi' | 'kdv' | 'altyapi' | 'donanim' | 'sabit' | 'isletme' | 'erisim';

export interface CostPartDef {
  key: CostKey;
  label: string;
  /** açılış (yatırım) mı, erişim (yolculuk) mı */
  group: 'acilis' | 'erisim';
  description: string;
}

/** Yığın sırası (soldan sağa). */
export const COST_PARTS: readonly CostPartDef[] = [
  { key: 'arsa', label: 'Arsa', group: 'acilis', description: 'Arsa bedeli (D grubunda “arsa dahil” açıksa): yatak başı arsa alanı × konuma bağlı m² fiyatı.' },
  { key: 'yapi', label: 'İnşaat (yapı)', group: 'acilis', description: '2026 Yapı Yaklaşık Birim Maliyeti (TL/m², KDV hariç) × kapalı alan (çekirdek alan + yatak başı alan).' },
  { key: 'kdv', label: 'KDV', group: 'acilis', description: 'Yapı, altyapı ve donanım üzerinden KDV (C grubunda “KDV dahil” açıksa).' },
  { key: 'altyapi', label: 'Altyapı ve çevre', group: 'acilis', description: 'Altyapı + çevre düzenlemesi (tebliğe dahil değil): yapı maliyetine oran.' },
  { key: 'donanim', label: 'Tıbbi donanım', group: 'acilis', description: 'Tıbbi donanım: yapı maliyetine oran.' },
  { key: 'sabit', label: 'Sabit açılış', group: 'acilis', description: 'Hastane başına sabit açılış maliyeti F_sabit (yataktan bağımsız; varsayım).' },
  { key: 'isletme', label: 'İşletme', group: 'acilis', description: 'İşletme giderlerinin planlama ufku boyunca bugünkü değeri (F grubunda açıksa).' },
  { key: 'erisim', label: 'Erişim', group: 'erisim', description: 'Hastaların yolculuk maliyetinin bugünkü değeri: λ · Σ nüfus × yol mesafesi.' },
];

/** Maliyet kırılımı renkleri: theme/palette.ts (tek renk kaynağı). */
export { COST_COLORS };

export interface CostSegment {
  key: CostKey;
  label: string;
  value: number;
  color: string;
  description: string;
}

/** Sonuç ya da hastane maliyetlerinden yığın segmentleri (sıfır olanlar dahil; çizen taraf süzer). */
export function costSegments(costs: CostBreakdown | HospitalCosts, scheme: Scheme): CostSegment[] {
  return COST_PARTS.map((p) => ({
    key: p.key,
    label: p.label,
    value: Number((costs as unknown as Record<string, number>)[p.key] ?? 0) || 0,
    color: COST_COLORS[scheme][p.key],
    description: p.description,
  }));
}
