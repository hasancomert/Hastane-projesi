/**
 * Optimallik etiketi — başlık cümlesi, sonuç özeti, sonuç rozeti ve sertifika rozeti AYNI kuraldan beslenir
 * (şartname 7.1–7.2; tutarsızlık: önce önceden 1.800 sn çözülmüş %0,06'lık sonuç "En iyi bulunan çözüm" + sarı
 * "süre doldu", %0,31'lik canlı sonuç "Optimum" diye yazılıyordu).
 *
 * Kural (arayüzün hedef farkı = istekteki `solver.mip_gap`, varsayılan %1):
 *   - durduruldu (iptal / yerini yeni isteğe bıraktı)          → "Durduruldu — en iyi bulunan çözüm (fark %g)"
 *   - alt sınır var ve kanıtlı fark ≤ hedef                     → "Optimum (kanıtlı fark %g)"
 *   - alt sınır var, fark > hedef                               → "En iyi bulunan çözüm (fark %g)"
 *   - alt sınır yok (sezgisel)                                  → "En iyi bulunan çözüm (alt sınır yok)"
 * Motorun kendi durumu (`status`: sure_doldu, gap_ulasilamadi …) ipucunda aynen durur: önceden uzun çözülen
 * sonuçların motor hedefi %0,01'dir; süre dolsa da kanıtlı fark arayüzün hedefinin çok altındadır.
 */
import type { SolveResultOk } from '../api/types.ts';
import { fmtGap } from '../format/number.ts';
import type { StatusTone } from '../theme/palette.ts';

export type OptimalityKind = 'optimum' | 'best' | 'stopped' | 'heuristic';

export interface Optimality {
  kind: OptimalityKind;
  /** kanıtlı olarak hedef fark içinde */
  proven: boolean;
  /** cümle başı ("Optimum (kanıtlı fark %0,06)") */
  head: string;
  /** rozet metni ("optimum · kanıtlı fark %0,06") */
  badge: string;
  tone: StatusTone;
  /** ipucu: motorun durumu + kural */
  tooltip: string;
}

/** Arayüzün hedef farkı verilmediyse (meta yüklenmeden) kullanılan değer (meta.defaults.gap). */
export const DEFAULT_UI_TARGET_GAP = 0.01;

export function optimalityLabel(r: SolveResultOk, uiTargetGap: number | null | undefined, stopped = false): Optimality {
  const target = uiTargetGap ?? DEFAULT_UI_TARGET_GAP;
  const gap = r.bounds?.gap;
  const hasLb = r.bounds?.lb_tl != null && gap != null && Number.isFinite(gap);
  const engine = `Motorun durumu: ${r.status_tr || r.status} (${r.status}).`;
  const rule = `Arayüzün hedef farkı ${fmtGap(target)}: alt sınıra göre kanıtlı fark bunun içindeyse “optimum” denir.`;
  const budget = r.mode === 'butce' ? ' Bütçe modunda fark erişim maliyeti üzerindendir.' : '';
  if (stopped) {
    const g = hasLb ? ` (fark ${fmtGap(gap)})` : '';
    return {
      kind: 'stopped',
      proven: false,
      head: `Durduruldu — en iyi bulunan çözüm${g}`,
      badge: 'durduruldu — en iyi bulunan',
      tone: 'serious',
      tooltip: `Çözüm iptal edildi ya da yerini yeni isteğe bıraktı; motorun o ana dek bulduğu en iyi çözüm gösteriliyor.${g ? ` Kanıtlı fark${g}.` : ''} ${engine}`,
    };
  }
  if (hasLb && r.status !== 'sezgisel' && (gap as number) <= target + 1e-12) {
    return {
      kind: 'optimum',
      proven: true,
      head: `Optimum (kanıtlı fark ${fmtGap(gap)})`,
      badge: `optimum · kanıtlı fark ${fmtGap(gap)}`,
      tone: 'good',
      tooltip: `${rule} ${engine}${budget}`,
    };
  }
  if (hasLb) {
    return {
      kind: 'best',
      proven: false,
      head: `En iyi bulunan çözüm (fark ${fmtGap(gap)})`,
      badge: `en iyi bulunan · fark ${fmtGap(gap)}`,
      tone: 'warning',
      tooltip: `Kanıtlı fark hedefin (${fmtGap(target)}) üstünde: daha uzun süre ya da daha geniş hedef farkla yeniden çözülebilir. ${engine}${budget}`,
    };
  }
  return {
    kind: 'heuristic',
    proven: false,
    head: 'En iyi bulunan çözüm (alt sınır yok)',
    badge: 'sezgisel — alt sınır yok',
    tone: 'serious',
    tooltip: `Bu sonuç için alt sınır hesaplanamadı; optimallik kanıtlanmadı. ${engine}`,
  };
}

const HEAD_RE = /^\s*(Optimum|En iyi bulunan çözüm)\s*:\s*/;

/** Sunucunun özet cümlesinin (message_tr) başını tutarlı etiketle değiştirir. */
export function summarySentence(r: SolveResultOk, opt: Optimality): string {
  const msg = r.message_tr ?? '';
  const rest = msg.replace(HEAD_RE, '');
  return rest ? `${opt.head}: ${rest}` : opt.head;
}
