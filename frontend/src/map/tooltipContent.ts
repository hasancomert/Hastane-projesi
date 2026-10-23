/**
 * Harita ipucu içeriği (Türkçe, biçimlenmiş): imlecin altındaki hastane, hasta akışı yayı ya da hücre. Hücre
 * ipucu açık olan katmanlara göre satır ekler (hizmet bölgesi, erişim, gölge fiyat, aday) — ekranda ne
 * görünüyorsa onu açıklar. Saf fonksiyon: depo durumu + hedef → başlık, satırlar, ipucu cümlesi.
 */
import type { GridCells, Hospital } from '../api/types.ts';
import { fmtInt, fmtKm, fmtMoney, fmtNumber, fmtPeople, fmtPercent } from '../format/number.ts';
import type { LayerId } from '../store/appStore.ts';
import { CLASS_NAME_TR } from '../theme/palette.ts';
import type { GridIndex } from './gridIndex.ts';
import { currentR } from './layers/access.ts';
import { candPosByCell, demandPosByCell, dualPosByCell, popByCell } from './layers/cellData.ts';
import { flowArcIndex } from './layers/flows.ts';
import type { MapTool, SolutionView } from './layers/types.ts';
import type { HoverTarget } from './mapUiStore.ts';

export interface TooltipRow {
  label: string;
  value: string;
  /** durum vurgusu (R dışı, kilit) — simge + metinle birlikte */
  tone?: 'critical' | 'good' | 'warning';
}

export interface TooltipContent {
  title: string;
  subtitle?: string;
  /** başlığın yanında sınıf rengi noktası */
  classCode?: Hospital['class'];
  rows: TooltipRow[];
  hint?: string;
}

export interface TooltipInput {
  target: HoverTarget;
  grid: GridCells | null;
  gridIndex: GridIndex | null;
  solution: SolutionView | null;
  meta: { access_rings?: { R_km: number } } | null;
  visible: Record<LayerId, boolean>;
  locks: { open: string[]; closed: string[] };
  tool: MapTool;
  canLock: boolean;
}

export function hospitalTitle(h: Hospital, index: number): string {
  return `Hastane ${index + 1} · ${h.district}`;
}

function lockOf(cellId: string, locks: { open: string[]; closed: string[] }): 'open' | 'closed' | null {
  if (locks.open.includes(cellId)) return 'open';
  if (locks.closed.includes(cellId)) return 'closed';
  return null;
}

function toolHint(tool: MapTool, lock: 'open' | 'closed' | null, canLock: boolean, what: 'hücre' | 'hastane'): string {
  if (tool === 'select') return what === 'hastane' ? 'Tıklayın: hastane kartı · sağ tık: kilit menüsü' : 'Tıklayın: hücre kartı · sağ tık: kilit menüsü';
  if (!canLock) return 'Çevrimdışı: kilitler canlı çözüm gerektirir';
  if (tool === 'lockOpen') return lock === 'open' ? 'Tıklayın: zorunluluğu kaldır' : 'Tıklayın: bu hücrede hastane ZORUNLU olsun';
  return lock === 'closed' ? 'Tıklayın: yasağı kaldır' : 'Tıklayın: bu hücrede hastane YASAK olsun';
}

export function tooltipContent(inp: TooltipInput): TooltipContent | null {
  const { target, solution: sol, gridIndex: gi } = inp;
  const hs = sol?.hospitals ?? [];

  if (target.kind === 'hospital') {
    const h = hs[target.index];
    if (!h) return null;
    const lock = lockOf(h.cell_id, inp.locks);
    return {
      title: hospitalTitle(h, target.index),
      subtitle: `${CLASS_NAME_TR[h.class]} hastane (${h.class})`,
      classCode: h.class,
      rows: [
        { label: 'Yatak', value: `${fmtInt(h.beds)} · doluluk ${fmtPercent(h.occupancy)}` },
        { label: 'Hizmet ettiği nüfus', value: fmtPeople(h.population) },
        { label: 'Ortalama / en uzak', value: `${fmtKm(h.mean_km)} / ${fmtKm(h.max_km)}` },
        { label: 'Açılış maliyeti', value: fmtMoney(h.costs?.acilis) },
        ...(lock ? [{ label: 'Kilit', value: lock === 'open' ? '+ zorunlu (kullanıcı)' : '× yasak', tone: lock === 'open' ? ('good' as const) : ('critical' as const) }] : []),
      ],
      hint: toolHint(inp.tool, lock, inp.canLock, 'hastane'),
    };
  }

  if (target.kind === 'flow') {
    if (!sol || !gi) return null;
    const k = flowArcIndex(gi, sol.assignment, hs, target.arc);
    if (k < 0) return null;
    const a = sol.assignment;
    const h = hs[a.hospital[k]];
    if (!h) return null;
    return {
      title: `${fmtPeople(a.population[k])} → ${hospitalTitle(h, a.hospital[k])}`,
      subtitle: `Hücre ${a.demand_cell[k]}`,
      classCode: h.class,
      rows: [
        { label: 'Yol mesafesi', value: fmtKm(a.km[k]) },
        { label: 'Hücre talebinin payı', value: fmtPercent(a.share[k], 0) },
      ],
      hint: 'Tıklayın: hastane kartı',
    };
  }

  // hücre
  if (!gi || !inp.grid) return null;
  const i = target.gridIndex;
  const f = inp.grid.features[i];
  if (!f) return null;
  const cellId = f.properties.id;
  const pop = popByCell(inp.grid, gi, sol?.layers?.demand ?? null)[i];
  const rows: TooltipRow[] = [
    { label: 'Nüfus', value: `${fmtPeople(pop)} · ${fmtNumber(pop / gi.cellAreaKm2, pop / gi.cellAreaKm2 < 10 ? 1 : 0)} kişi/km²` },
  ];
  const v = inp.visible;
  if (sol?.layers?.demand && (v.serviceAreas || v.flows)) {
    const k = demandPosByCell(gi, sol.layers.demand)[i];
    if (k >= 0) {
      const hi = sol.layers.demand.service_hospital[k];
      const h = hs[hi];
      const share = sol.layers.demand.service_share[k];
      if (h) rows.push({ label: 'Hizmet aldığı hastane', value: `${hi + 1} · ${h.district} (${h.class})${share < 0.999 ? ` · talebin ${fmtPercent(share, 0)}'i` : ''}` });
    } else rows.push({ label: 'Hizmet', value: 'talep yok (nüfussuz hücre)' });
  }
  if (sol?.layers?.access && (v.access || v.rings)) {
    const d = sol.layers.access.nearest_km[i];
    const R = currentR({ solution: sol, meta: inp.meta });
    const beyond = d > R + 1e-9;
    rows.push({ label: 'En yakın açık hastane', value: `${fmtKm(d)} yol${beyond ? ` — R (${fmtNumber(R, 0)} km) dışında` : ''}`, tone: beyond ? 'critical' : undefined });
  }
  if (v.shadowPrices && sol?.duals) {
    const k = dualPosByCell(gi, sol.duals)[i];
    if (k >= 0) rows.push({ label: 'Marjinal hizmet maliyeti', value: `${fmtNumber(sol.duals.marginal_cost_per_person_tl[k], 0)} TL/kişi` });
  }
  if (v.candidates) {
    const cand = sol?.layers?.candidates ?? null;
    if (cand) {
      const k = candPosByCell(gi, cand)[i];
      rows.push({ label: 'Aday', value: k >= 0 ? `evet — ${cand.origin_tr?.[String(cand.origin[k])] ?? 'aday kuralı'}${cand.open[k] ? ' · hastane açık' : ''}` : 'hayır' });
    } else rows.push({ label: 'Aday', value: f.properties.aday ? 'evet (varsayılan parametreler)' : 'hayır' });
  }
  const lock = lockOf(cellId, inp.locks);
  if (lock) rows.push({ label: 'Kilit', value: lock === 'open' ? '+ hastane zorunlu' : '× hastane yasak', tone: lock === 'open' ? 'good' : 'critical' });
  return {
    title: `Hücre ${cellId}`,
    subtitle: `${f.properties.ilce} · ${fmtNumber(gi.km, 0)} km × ${fmtNumber(gi.km, 0)} km`,
    rows,
    hint: toolHint(inp.tool, lock, inp.canLock, 'hücre'),
  };
}
