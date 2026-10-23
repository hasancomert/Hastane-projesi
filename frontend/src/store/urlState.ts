/**
 * Problem durumu adres çubuğunun `#` kısmında: sayfa yeniden yüklenince (ya da bağlantı paylaşılınca) senaryo,
 * ızgara, parametre değişiklikleri ve kilitler korunur. Varsayılan problemde `#` boştur.
 *
 *   #izgara=5&senaryo=erisim_oncelikli&p.cost.fixed_per_hospital=300000000&zorunlu=3km_1_2,3km_3_4&yasak=…&aralik=1
 *
 * Yalnız seçili senaryodan FARKLI değerler yazılır (`p.<anahtar>` = JSON değer); okurken bilinmeyen anahtar,
 * senaryo ya da ızgara yok sayılır (sunucu yine doğrular). Çevrimdışıyken yazılmaz ve okunmaz (yedek sonuç
 * varsayılan problemdir); sunucu dönünce adresteki problem korunmuş olur.
 */
import type { GridKm, Meta, Overrides, ParamValue } from '../api/types.ts';
import { useApp, type ProblemState } from './appStore.ts';

const GRIDS: GridKm[] = [2, 3, 5];

function same(a: unknown, b: unknown): boolean {
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) <= 1e-12 * Math.max(1, Math.abs(a), Math.abs(b));
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Problemi `#…` metnine çevirir (varsayılan problem → ''). */
export function encodeProblem(p: ProblemState, meta: Meta): string {
  const q = new URLSearchParams();
  if (p.gridKm !== (meta.defaults.grid_km ?? 3)) q.set('izgara', String(p.gridKm));
  if (p.scenarioId && p.scenarioId !== 'varsayilan') q.set('senaryo', p.scenarioId);
  const sc = meta.scenarios.find((s) => s.id === p.scenarioId);
  const base: Overrides = sc?.overrides ?? {};
  for (const k of Object.keys(p.overrides).sort()) {
    if (k in base && same(base[k], p.overrides[k])) continue;
    q.set(`p.${k}`, JSON.stringify(p.overrides[k]));
  }
  if (p.locks.open.length) q.set('zorunlu', [...p.locks.open].sort().join(','));
  if (p.locks.closed.length) q.set('yasak', [...p.locks.closed].sort().join(','));
  if (p.allowOutsideRange) q.set('aralik', '1');
  const s = q.toString();
  return s ? `#${s}` : '';
}

/** `#…` metninden problem (meta ile doğrulanmış); hiçbir şey yoksa null. */
export function decodeProblem(hash: string, meta: Meta): ProblemState | null {
  const raw = hash.replace(/^#/, '');
  if (!raw) return null;
  let q: URLSearchParams;
  try {
    q = new URLSearchParams(raw);
  } catch {
    return null;
  }
  const km = Number(q.get('izgara') ?? meta.defaults.grid_km ?? 3);
  const gridKm = (GRIDS.includes(km as GridKm) ? km : meta.defaults.grid_km ?? 3) as GridKm;
  const scId = q.get('senaryo') ?? 'varsayilan';
  const sc = meta.scenarios.find((s) => s.id === scId) ?? meta.scenarios.find((s) => s.id === 'varsayilan');
  const overrides: Overrides = { ...(sc?.overrides ?? {}) };
  delete overrides['grid.cell_km'];
  for (const [k, v] of q.entries()) {
    if (!k.startsWith('p.')) continue;
    const key = k.slice(2);
    if (!meta.parameters[key] || key === 'grid.cell_km') continue;
    try {
      overrides[key] = JSON.parse(v) as ParamValue;
    } catch {
      /* bozuk değer: yok say */
    }
  }
  const prefix = `${gridKm}km_`;
  const ids = (name: string) => (q.get(name) ?? '').split(',').map((x) => x.trim()).filter((x) => x.startsWith(prefix));
  const open = ids('zorunlu');
  const closed = ids('yasak').filter((c) => !open.includes(c));
  return { gridKm, scenarioId: sc?.id ?? 'varsayilan', overrides, locks: { open, closed }, allowOutsideRange: q.get('aralik') === '1' };
}

/** Açılışta (meta yüklendikten sonra, ilk çözümden önce) adresteki problemi depoya yazar. */
export function restoreProblemFromUrl(): boolean {
  try {
    const meta = useApp.getState().meta;
    if (!meta) return false;
    const p = decodeProblem(window.location.hash, meta);
    if (!p) return false;
    useApp.setState({ problem: p, selection: null });
    return true;
  } catch {
    return false;
  }
}

/** Problem değişince adresi güncelle (geçmişe kayıt eklemeden). Dönen fonksiyon aboneliği kaldırır. */
export function startUrlSync(): () => void {
  let last = '';
  return useApp.subscribe((s) => {
    if (!s.meta || s.connection.mode !== 'online') return;
    let h: string;
    try {
      h = encodeProblem(s.problem, s.meta);
    } catch {
      return;
    }
    if (h === last) return;
    last = h;
    try {
      if (h === window.location.hash || (h === '' && !window.location.hash)) return;
      const url = `${window.location.pathname}${window.location.search}${h}`;
      window.history.replaceState(window.history.state, '', url);
    } catch {
      /* adres yazılamıyor (sandbox): durum yalnız bu oturumda */
    }
  });
}
