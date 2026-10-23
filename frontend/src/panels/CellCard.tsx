/**
 * Hücre kartı (şartname 6.2): tıklanan ızgara hücresinin bilgisi.
 *
 *   - kimlik, ilçe; senaryonun nüfusu (layers.demand.population; talep hücresi değilse GeoJSON), yoğunluk, yatak
 *     talebi, su ve yapılaşmış alan payı;
 *   - hizmet aldığı hastane (layers.demand.service_hospital) ve atama yaylarının yol mesafesi/payı (assignment);
 *     en yakın açık hastane (layers.access) — kapasite yüzünden farklı olabilir;
 *   - aday durumu: aday (aday kuralı / kapsama onarımı / kullanıcı kilidi) ya da uygun değil + olası neden;
 *   - gölge fiyat (duals: bu hücreye 1 kişi daha eklenseydi toplam maliyet ne kadar artardı);
 *   - kilit düğmeleri: hastane zorunlu / yasakla / kaldır (depoda setLock, otomatik yeniden çözüm).
 */
import { Badge, Button, CloseButton, Group, Paper, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core';
import type { ReactNode } from 'react';

import type { GridCellProps, SolveResultOk } from '../api/types.ts';
import { fmtBeds, fmtInt, fmtKm, fmtNumber, fmtPercent, fmtPercentShort, fmtTl } from '../format/number.ts';
import { paramValue, selectGrid, useApp } from '../store/appStore.ts';
import { gridIndexOf } from '../map/gridIndex.ts';
import { CLASS_NAME_TR, classColor } from '../theme/palette.ts';
import { IconBan, IconLock, IconUnlock } from '../ui/icons.tsx';
import { Swatch } from '../ui/StackedBar.tsx';
import { cellDual, solutionIndex } from './solutionIndex.ts';
import './panels.css';

export interface CellCardProps {
  cellId: string;
  /** ızgara sırası (GeoJSON özellik sırası, layers.access sırası) */
  gridIndex: number;
  /** GeoJSON hücre özellikleri (ızgara yüklenmediyse null) */
  cell: GridCellProps | null;
  /** gösterilen çözüm (yoksa yalnız hücre bilgisi) */
  solution: SolveResultOk | null;
  lock: 'open' | 'closed' | null;
  onLock(lock: 'open' | 'closed' | null): void;
  onClose(): void;
  /** kilit değiştirilebilir mi (çevrimdışıyken hayır) */
  canLock?: boolean;
}

function Row({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <>
      <Text size="sm" c="dimmed" title={hint}>
        {label}
      </Text>
      <Text size="sm">{children}</Text>
    </>
  );
}

/**
 * Aday olmamanın nedeni — kural backend/model/grid_io.py ile aynı:
 *   uygun = il içi pay ≥ grid.candidate_min_inside VE su payı < grid.candidate_max_water
 *   aday  = uygun VE (nüfus ≥ P_min VEYA 8 komşudan en kalabalığının nüfusu ≥ grid.candidate_near_density × s²)
 * Hücre kimliği "<s>km_<doğu/s>_<kuzey/s>" olduğundan komşular kimlikten bulunur. İl içi pay GeoJSON'da yok:
 * diğer koşullar sağlanıyorsa neden odur (sınır hücresi).
 */
function notCandidateReasons(
  cellId: string,
  cell: GridCellProps | null,
  get: (k: string) => unknown,
  popOf: (id: string) => number | null,
): string[] {
  if (!cell) return [];
  const out: string[] = [];
  const maxWater = Number(get('grid.candidate_max_water'));
  const minPop = Number(get('grid.candidate_min_pop'));
  const nearDens = Number(get('grid.candidate_near_density'));
  if (Number.isFinite(maxWater) && cell.water / 100 >= maxWater) out.push(`büyük kısmı su (%${fmtNumber(cell.water, 0)} ≥ ${fmtPercentShort(maxWater)})`);
  const m = /^(\d+)km_(-?\d+)_(-?\d+)$/.exec(cellId);
  const own = popOf(cellId) ?? cell.pop;
  if (Number.isFinite(minPop) && own < minPop) {
    let neighbourMax: number | null = null;
    if (m) {
      const [s, x, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
      for (let dx = -1; dx <= 1; dx++)
        for (let dy = -1; dy <= 1; dy++) {
          if (!dx && !dy) continue;
          const p = popOf(`${s}km_${x + dx}_${y + dy}`);
          if (p !== null) neighbourMax = Math.max(neighbourMax ?? 0, p);
        }
      const thr = nearDens * s * s;
      if (neighbourMax === null || neighbourMax < thr) {
        out.push(
          `nüfus az (${fmtInt(own)} < P_min ${fmtInt(minPop)} kişi) ve yerleşime yakın değil (en kalabalık komşu ${fmtInt(neighbourMax ?? 0)} kişi < ${fmtInt(thr)} kişi = ${fmtInt(nearDens)} kişi/km² × ${s * s} km²)`,
        );
      }
    } else {
      out.push(`nüfus az (${fmtInt(own)} < P_min ${fmtInt(minPop)} kişi)`);
    }
  }
  if (out.length === 0) out.push(`hücrenin il sınırı içinde kalan payı eşiğin (${fmtPercentShort(Number(get('grid.candidate_min_inside')))}) altında (sınır hücresi)`);
  return out;
}

export function CellCard({ cellId, gridIndex, cell, solution, lock, onLock, onClose, canLock = true }: CellCardProps) {
  const scheme = useApp((s) => s.colorScheme);
  const select = useApp((s) => s.select);
  const meta = useApp((s) => s.meta);
  const problem = useApp((s) => s.problem);
  const grid = useApp(selectGrid);
  const get = (k: string) => paramValue({ meta, problem }, k);

  const idx = solution ? solutionIndex(solution) : null;
  const dPos = idx?.demandPos.get(cellId);
  const cPos = idx?.candPos.get(cellId);
  const hospitalHere = idx?.hospitalByCell.get(cellId);
  const demand = solution?.layers.demand;
  const year = (solution?.overrides?.['population.scenario_year'] as number | undefined) ?? (get('population.scenario_year') as number | undefined) ?? 2025;
  const pop = dPos !== undefined && demand ? demand.population[dPos] : cell ? (year === 2030 ? cell.pop2030 : cell.pop) : null;
  const beds = dPos !== undefined && demand ? demand.demand_beds[dPos] : null;
  const serviceH = dPos !== undefined && demand ? demand.service_hospital[dPos] : -1;
  const arcs = idx?.arcsByCell.get(cellId) ?? [];
  const nearestKm = solution?.layers.access?.nearest_km[gridIndex];
  const nearestH = solution?.layers.access?.nearest_hospital[gridIndex];
  const R = solution?.layers.rings.R_km;
  const dual = solution ? cellDual(solution, cellId) : null;
  const origin = cPos !== undefined ? solution?.layers.candidates.origin[cPos] : undefined;
  const originText = origin !== undefined ? solution?.layers.candidates.origin_tr[String(origin)] ?? '' : '';

  const hospitalLink = (i: number) => {
    const h = solution?.hospitals[i];
    if (!h) return '—';
    return (
      <UnstyledButton
        onClick={() => select({ kind: 'hospital', index: i, cellId: h.cell_id })}
        aria-label={`Hastane ${i + 1} kartını aç`}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, textDecoration: 'underline', textUnderlineOffset: 2 }}
      >
        <Swatch color={classColor(h.class, scheme)} round />
        Hastane {i + 1} · {h.district} ({h.class})
      </UnstyledButton>
    );
  };

  let candidate: ReactNode;
  if (hospitalHere !== undefined) {
    candidate = (
      <Text size="sm">
        Bu hücrede <b>açık hastane</b> var: {hospitalLink(hospitalHere)}
      </Text>
    );
  } else if (cPos !== undefined) {
    candidate = (
      <Text size="sm" component="div">
        <Badge size="sm" variant="light" color="teal" tt="none" mr={6}>
          aday
        </Badge>
        hastane açılabilir ({originText || 'aday kuralı'}); model açmamayı seçti.
      </Text>
    );
  } else {
    const gi = grid ? gridIndexOf(grid, problem.gridKm) : null;
    const ghs = String(get('population.grid_source') ?? '').startsWith('ghs');
    const popOf = (id: string) => {
      const i = gi?.byId.get(id);
      const pr = i !== undefined ? grid?.features[i]?.properties : undefined;
      if (!pr) return null;
      return year === 2030 ? pr.pop2030 : ghs ? pr.pop_ghs : pr.pop;
    };
    const reasons = solution ? notCandidateReasons(cellId, cell, get, popOf) : [];
    candidate = (
      <Text size="sm" component="div" style={{ lineHeight: 1.4 }}>
        <Badge size="sm" variant="light" color="gray" tt="none" mr={6}>
          uygun değil
        </Badge>
        {solution ? `Aday değil: ${reasons.join('; ')}.` : 'Aday bilgisi çözümle gelir.'}
      </Text>
    );
  }

  return (
    <Paper withBorder radius="md" p="sm" data-testid="cell-card" component="section" aria-label={`Hücre ${cellId}`}>
      <Stack gap="xs">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Stack gap={0} style={{ minWidth: 0 }}>
            <Text fw={700} size="md">
              Hücre · {cell?.ilce ?? '—'}
            </Text>
            <Text size="xs" c="dimmed" ff="monospace">
              {cellId} · ızgara sırası {fmtInt(gridIndex)}
            </Text>
          </Stack>
          <CloseButton onClick={onClose} aria-label="Hücre kartını kapat" />
        </Group>

        <div className="rp-kv">
          <Row label={`Nüfus (${year})`} hint="Senaryonun nüfusu (TÜİK'e kalibre)">
            {pop === null ? '—' : `${fmtInt(pop)} kişi`}
          </Row>
          <Row label="Yoğunluk">{cell ? `${fmtInt(cell.dens)} kişi/km²` : '—'}</Row>
          <Row label="Yatak talebi">{beds === null ? (pop === 0 ? '0 (nüfussuz)' : '—') : fmtBeds(beds, 1)}</Row>
          <Row label="Su payı">{cell && Number.isFinite(cell.water) ? `%${fmtNumber(cell.water, 0)}` : '—'}</Row>
          <Row label="Yapılaşmış alan payı">{cell && cell.built != null && Number.isFinite(cell.built) ? `%${fmtNumber(cell.built, 0)}` : '—'}</Row>
        </div>

        {solution && (
          <Stack gap={4}>
            <Text size="sm" fw={650}>
              Hizmet
            </Text>
            {serviceH >= 0 ? (
              <Text size="sm">Hizmet aldığı hastane: {hospitalLink(serviceH)}</Text>
            ) : (
              <Text size="sm" c="dimmed">
                {dPos === undefined ? 'Nüfussuz hücre: talep yok, atama yok.' : 'Atanmamış.'}
              </Text>
            )}
            {arcs.length > 0 && (
              <Stack gap={2}>
                {arcs.map((a) => (
                  <Text key={a} size="xs" c="dimmed">
                    → Hastane {solution.assignment.hospital[a] + 1}: yol {fmtKm(solution.assignment.km[a])} · talep payı {fmtPercent(solution.assignment.share[a], 0)} ·{' '}
                    {fmtInt(solution.assignment.population[a])} kişi
                  </Text>
                ))}
              </Stack>
            )}
            {nearestKm != null && Number.isFinite(nearestKm) && (
              <Text size="xs" c={R && nearestKm > R ? 'red' : 'dimmed'}>
                En yakın açık hastane: {fmtKm(nearestKm)}
                {nearestH != null && nearestH >= 0 && nearestH !== serviceH ? ` (Hastane ${nearestH + 1}; kapasite yüzünden başka hastaneye atanmış)` : ''}
                {R && nearestKm > R ? ` — R = ${fmtNumber(R, 0)} km dışında` : ''}
              </Text>
            )}
          </Stack>
        )}

        <Stack gap={4}>
          <Text size="sm" fw={650}>
            Aday durumu
          </Text>
          {candidate}
        </Stack>

        {dual && (
          <Stack gap={2}>
            <Group justify="space-between" gap="xs">
              <Text size="sm" fw={650}>
                Gölge fiyat
              </Text>
              <Text size="sm" fw={700}>
                {fmtTl(dual.perPerson)} / kişi
              </Text>
            </Group>
            <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
              Bu hücreye 1 kişi daha eklenseydi toplam maliyet ≈ {fmtTl(dual.perPerson)} artardı (atama LP’sinin talep kısıtının dual değeri;
              açık hastaneler sabitken). {solution?.duals?.note_tr ?? ''}
            </Text>
          </Stack>
        )}

        <Group gap="xs" wrap="wrap">
          <Tooltip label="Bu hücrede hastane açılması zorunlu olur; model kalanını yeniden optimize eder.">
            <Button
              size="compact-sm"
              variant={lock === 'open' ? 'filled' : 'light'}
              leftSection={<IconLock size={14} />}
              disabled={!canLock}
              onClick={() => onLock(lock === 'open' ? null : 'open')}
              aria-pressed={lock === 'open'}
            >
              Hastane zorunlu
            </Button>
          </Tooltip>
          <Tooltip label={cPos === undefined && hospitalHere === undefined && solution ? 'Hücre zaten aday değil; yasak bir şey değiştirmez.' : 'Bu hücrede hastane açılamaz; model başka yer arar.'}>
            <Button
              size="compact-sm"
              color="red"
              variant={lock === 'closed' ? 'filled' : 'light'}
              leftSection={<IconBan size={14} />}
              disabled={!canLock}
              onClick={() => onLock(lock === 'closed' ? null : 'closed')}
              aria-pressed={lock === 'closed'}
            >
              Yasakla
            </Button>
          </Tooltip>
          {lock && (
            <Button size="compact-sm" variant="subtle" color="gray" leftSection={<IconUnlock size={14} />} disabled={!canLock} onClick={() => onLock(null)}>
              Kilidi kaldır
            </Button>
          )}
        </Group>
        {hospitalHere !== undefined && solution && (
          <Text size="xs" c="dimmed">
            Bu hücredeki hastanenin sınıfı {solution.hospitals[hospitalHere].class} ({CLASS_NAME_TR[solution.hospitals[hospitalHere].class]}).
          </Text>
        )}
      </Stack>
    </Paper>
  );
}

export default CellCard;
