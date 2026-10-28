/**
 * "Hocanın Listesi" (şartname 1 ve 7.14): hocanın 9 maddesi; her biri ✓, sayfada karşılandığı yere atlama
 * düğmeleri (info/jumps.ts — sunum turu ve açıklama paneliyle ortak) ve gösterilen çözümün CANLI sayılarıyla 1–2
 * cümle. Her sayı çözüm sonucundan, /api/meta'dan, /api/measures'tan, sınır kartından ya da önceden çözülmüş
 * p taramasından gelir; veri yoksa o cümle kısalır (sayı uydurulmaz).
 */
import { Badge, Group, Paper, Stack, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';

import type { SolveResultOk } from '../api/types.ts';
import { fmtBillion, fmtGap, fmtInt, fmtKm, fmtMoney, fmtNumber, fmtPercent, fmtPeople } from '../format/number.ts';
import { capacityFromResult } from '../panels/BoundsBlock.tsx';
import { optimalityLabel } from '../panels/optimality.ts';
import { isDefaultProblemKey, selectGrid, selectOkResult, selectUiTargetGap, useApp } from '../store/appStore.ts';
import { CLASS_NAME_TR, CLASS_ORDER } from '../theme/palette.ts';
import { classCosts, landShare, measureItem, minMax, plateauFacts, rangesText, sizePiles } from './facts.ts';
import { HOCA_ITEMS, type JumpId } from './jumps.ts';
import { JumpButton, N, Prose } from './ui.tsx';
import { useBoundsCard, useMeasures, useNumParam, useParam, useSweep } from './useInfoData.ts';

function HocaCard({ n, title, jumps, children }: { n: number; title: string; jumps: readonly JumpId[]; children: ReactNode }) {
  return (
    <Paper withBorder radius="md" p="md" data-testid={`hoca-${n}`} id={`hoca-${n}`}>
      <div className="hoca-card">
        <Group gap="sm" wrap="nowrap" align="flex-start">
          <span className="hoca-check" aria-label="karşılandı">
            ✓
          </span>
          <div style={{ minWidth: 0 }}>
            <Text size="xs" c="dimmed" fw={650}>
              Madde {n}
            </Text>
            <Title order={4} style={{ fontSize: '1.02rem', lineHeight: 1.3 }}>
              {title}
            </Title>
          </div>
        </Group>
        <Prose>{children}</Prose>
        <Group gap={6} className="hoca-actions" wrap="wrap">
          {jumps.map((j, i) => (
            <JumpButton key={j} id={j} variant={i === 0 ? 'light' : 'subtle'} />
          ))}
        </Group>
      </div>
    </Paper>
  );
}

function classMix(r: SolveResultOk): string {
  return CLASS_ORDER.filter((c) => (r.kpis.count_by_class[c] ?? 0) > 0)
    .map((c) => `${fmtInt(r.kpis.count_by_class[c])} ${CLASS_NAME_TR[c]}`)
    .join(', ');
}

export default function HocaTab() {
  const meta = useApp((s) => s.meta);
  const r = useApp(selectOkResult);
  const info = useApp((s) => s.resultInfo);
  const gridKm = useApp((s) => s.problem.gridKm);
  const grid = useApp(selectGrid);
  const nIlce = useApp((s) => s.boundaries?.ilceler.features.length ?? null);
  const target = useApp(selectUiTargetGap);
  const measures = useMeasures().data;
  const card = useBoundsCard(gridKm);
  const sweepKm: 3 | 5 | null = gridKm === 3 || gridKm === 5 ? gridKm : null;
  const sweepSt = useSweep(sweepKm ?? 3);
  const pop2025 = useParam('population.province_2025');
  const pop2030 = useNumParam('population.province_2030');
  const year = useNumParam('population.scenario_year');
  const pMin = useNumParam('grid.candidate_min_pop');
  const nearDens = useNumParam('grid.candidate_near_density');
  const maxWater = useNumParam('grid.candidate_max_water');
  const R = useNumParam('access.max_distance_km');
  const fSabit = useParam('cost.fixed_per_hospital');

  const isDefault = isDefaultProblemKey(info?.problemKey) && (info?.problemKey ? JSON.parse(info.problemKey)[0] === gridKm : false);
  const opt = r ? optimalityLabel(r, target, !!info?.stopped) : null;
  const plateau = sweepKm && sweepSt.data && sweepSt.data.grid_km === sweepKm ? plateauFacts(sweepSt.data) : null;
  const costs = classCosts(r, meta);
  const enabled = costs.rows.filter((c) => c.enabled);
  const fixedRange = minMax(enabled.map((c) => c.fixedTl));
  const perBedRange = minMax(enabled.map((c) => c.perBedTl));
  const piles = sizePiles(r);
  const land = landShare(measures, gridKm);
  const nCand = r?.layers?.candidates?.cell_id.length ?? r?.instance?.J ?? card?.max_hospitals_grid ?? null;
  const cap = r ? capacityFromResult(r) : null;
  const nDemand = r?.instance?.I ?? null;
  const nCells = grid?.features.length ?? meta?.grids.find((g) => g.km === gridKm)?.n_cells ?? null;
  const dist = measures?.districts?.rows ?? [];
  const densest = dist.length ? dist.reduce((a, b) => (b.density_per_km2 > a.density_per_km2 ? b : a)) : null;
  const sparsest = dist.length ? dist.reduce((a, b) => (b.density_per_km2 < a.density_per_km2 ? b : a)) : null;
  const m = (k: string) => measureItem(measures, k);
  const popVal = typeof pop2025.value === 'number' ? pop2025.value : null;

  const items: Record<number, ReactNode> = {
    1: (
      <>
        2026 başı Ankara nüfusu (TÜİK ADNKS, 31.12.2025): <N>{popVal !== null ? fmtPeople(popVal) : '—'}</N>
        {pop2025.p ? ` (${pop2025.p.status_tr})` : ''}. Izgara nüfusu her ilçede TÜİK 2025 toplamına kalibre edildi
        {r ? (
          <>
            ; gösterilen çözümdeki toplam nüfus <N>{fmtPeople(r.kpis.population)}</N>
          </>
        ) : null}
        {year === 2030 && pop2030 !== null ? <> (2030 senaryosu: TÜİK il projeksiyonu {fmtPeople(pop2030)})</> : null}.
      </>
    ),
    2: (
      <>
        Çevrimdışı altlık harita üzerinde il sınırı{nIlce ? <> ve <N>{fmtInt(nIlce)}</N> ilçe</> : null}; <N>{gridKm} km</N> ızgarada{' '}
        <N>{fmtInt(nCells)}</N> hücre{nDemand ? <>, bunların <N>{fmtInt(nDemand)}</N> tanesi nüfuslu talep hücresi</> : null}. Katmanlar haritanın sol
        üstünden açılıp kapanır.
      </>
    ),
    3: (
      <>
        <b>(a) Arsa / inşaat alanı:</b>{' '}
        {land ? (
          <>
            {land.label}: arsa <N>{fmtInt(land.landM2)} m²</N> (≈{fmtNumber(land.landKm2, 2)} km²) = {gridKm} km hücrenin ({fmtInt(land.cellKm2)} km²){' '}
            <N>%{fmtNumber(land.sharePct, 2)}</N>
            {land.floorM2 ? <>; kapalı alanı {fmtInt(land.floorM2)} m²</> : null} → <b>arazi bağlayıcı değil</b>.{' '}
          </>
        ) : (
          'Ölçüler yükleniyor… '
        )}
        <b>(b) Hizmet bölgesi:</b>{' '}
        {r ? (
          <>
            her talep hücresi hizmet aldığı hastanenin renginde; hastane başına ortalama <N>{fmtPeople(r.kpis.population / Math.max(1, r.kpis.n_hospitals))}</N>,
            nüfus ağırlıklı ortalama yol <N>{fmtKm(r.kpis.pop_weighted_mean_km)}</N>, en uzak atama <N>{fmtKm(r.kpis.max_km)}</N>
            {R !== null ? <> (R = {fmtNumber(R)} km)</> : null}.
          </>
        ) : (
          'çözüm bekleniyor.'
        )}
      </>
    ),
    4: measures?.card ? (
      <>
        Alan <N>{m('area')?.value}</N> (jeodezik; kalıcı su hariç {m('land_area')?.value}), çevre <N>{m('perimeter')?.value}</N>; doğu–batı{' '}
        <N>{m('width_ew')?.value}</N>, kuzey–güney <N>{m('height_ns')?.value}</N> (UTM kutusu); en uzak iki sınır noktası arası{' '}
        <N>{m('diameter')?.value}</N> ({m('diameter')?.tooltip.replace(/\s*\(.*\)\.?$/, '')}); Kızılay’dan en uzak sınır noktası <N>{m('kizilay_far')?.value}</N>.
      </>
    ) : (
      'Ankara ölçüleri yükleniyor…'
    ),
    5: (
      <>
        Her hücrede en fazla 1 hastane olduğundan ızgaraya sığan en fazla hastane = uygun aday hücre sayısı |J|:{' '}
        <N>{nCand !== null ? `${fmtInt(nCand)} hastane` : '—'}</N> ({gridKm} km). Bağlayıcı olan alan değil talep ve maliyet:
        {card?.coverage_lscp?.value != null ? (
          <>
            {' '}
            herkese R içinde hastane için en az <N>{fmtInt(card.coverage_lscp.value)}</N>
            {isDefault ? '' : ' (varsayılan parametrelerle)'}
          </>
        ) : null}
        {cap ? (
          <>
            {card?.coverage_lscp?.value != null ? ',' : ''} kapasite için en az <N>{fmtInt(cap.value)}</N>
          </>
        ) : null}
        {r ? (
          <>
            ; optimum <N>{fmtInt(r.kpis.n_hospitals)}</N>
          </>
        ) : null}
        .
      </>
    ),
    6: (
      <>
        Mevcut hastaneler modele hiç girmez (sıfırdan planlama): aday hücreler yalnız ızgaradan türetilir — nüfusu ≥{' '}
        <N>{pMin !== null ? fmtInt(pMin) : '—'}</N> kişi ya da yerleşime yakın (komşu yoğunluğu ≥ {nearDens !== null ? fmtInt(nearDens) : '—'} kişi/km²) ve su payı ≤{' '}
        {maxWater !== null ? fmtPercent(maxWater, 0) : '—'} olan hücreler. Haritada mevcut hastane katmanı yoktur; sonuç “bugün sıfırdan kurulsaydı” sorusunun
        yanıtıdır.
      </>
    ),
    7: (
      <>
        Her hücrenin nüfusu modelin ağırlığıdır: yatak talebi q<sub>i</sub> ve erişim maliyeti λ·w<sub>i</sub>·d<sub>ij</sub> nüfusla orantılı, bu yüzden hastaneler
        yoğun bölgelerde sıklaşır.
        {densest && sparsest ? (
          <>
            {' '}
            En yoğun ilçe {densest.ilce} (<N>{fmtInt(densest.density_per_km2)} kişi/km²</N>), en seyrek {sparsest.ilce} ({fmtNumber(sparsest.density_per_km2, 1)}{' '}
            kişi/km²).
          </>
        ) : null}
        {r ? (
          <>
            {' '}
            Nüfusun <N>{fmtPercent(r.kpis.share_pop_within_km['10'])}</N>’i bir hastaneye 10 km içinde.
          </>
        ) : null}
      </>
    ),
    8: r && opt ? (
      <>
        <b>{opt.head}</b>: <N>{fmtInt(r.kpis.n_hospitals)} hastane</N> ({classMix(r)}), toplam <N>{fmtBillion(r.costs.toplam)}</N>; yerleri haritada, her
        hastanenin “neden burada?” kartı tıklayınca.
        {plateau && isDefault ? (
          plateau.unique ? (
            <>
              {' '}
              U eğrisi ({plateau.km} km): p = {plateau.freeN} taranan p’ler arasında kanıtlı tek en iyi.
            </>
          ) : (
            <>
              {' '}
              U eğrisi ({plateau.km} km): dip düz — p = <N>{rangesText(plateau.plateau)}</N> arası “fark gap içinde”; {plateau.freeN} bulunan en iyisi ama tek
              optimum olduğu bu gap’lerle kanıtlanamıyor.
            </>
          )
        ) : null}
      </>
    ) : (
      'Çözüm bekleniyor…'
    ),
    9: r ? (
      <>
        Açılış maliyeti <N>{fmtBillion(r.costs.acilis)}</N> (toplamın {fmtPercent(r.costs.acilis / r.costs.toplam)}). Hastane başına sabit kısım{' '}
        {fixedRange ? (
          <N>
            {fmtNumber(fixedRange[0] / 1e9, 2)}–{fmtNumber(fixedRange[1] / 1e9, 2)} milyar TL
          </N>
        ) : (
          '—'
        )}{' '}
        (çekirdek alan + F<sub>sabit</sub> {typeof fSabit.value === 'number' ? fmtMoney(fSabit.value) : '—'}
        {fSabit.p ? `, ${fSabit.p.status_tr}` : ''}), yatak başına{' '}
        {perBedRange ? (
          <N>
            {fmtNumber(perBedRange[0] / 1e6, 2)}–{fmtNumber(perBedRange[1] / 1e6, 2)} milyon TL
          </N>
        ) : (
          '—'
        )}{' '}
        (sınıf büyüdükçe artar)
        {piles.upper.length ? (
          <>
            {' '}
            → hastaneler sınıf eşiğinin altında yığılır:{' '}
            {piles.upper.map((p, i) => (
              <span key={p.code}>
                {i ? ', ' : ''}
                <N>{fmtInt(p.n)}</N> tanesi {fmtInt(p.beds)} yatakta
              </span>
            ))}
          </>
        ) : null}
        .
      </>
    ) : (
      'Çözüm bekleniyor…'
    ),
  };

  return (
    <Stack gap="md" data-testid="hoca-list">
      <Group justify="space-between" align="flex-end" wrap="wrap" gap="xs">
        <div>
          <Title order={3} style={{ fontSize: '1.15rem' }}>
            Hocanın 9 maddesi — hepsi bu sayfada
          </Title>
          <Text size="sm" c="dimmed" className="info-prose">
            Her madde ✓; düğmeler sayfada yanıtlandığı yere götürür (harita katmanı açılır, ilgili kart vurgulanır). Sayılar gösterilen çözümden
            {opt ? ` (${opt.badge})` : ''} ve veri dosyalarından gelir; parametre değişince güncellenir.
          </Text>
        </div>
        <Badge size="lg" color="teal" variant="light" tt="none">
          ✓ {HOCA_ITEMS.length} / {HOCA_ITEMS.length} madde karşılandı
        </Badge>
      </Group>
      <div className="hoca-grid">
        {HOCA_ITEMS.map((it) => (
          <HocaCard key={it.n} n={it.n} title={it.title} jumps={it.jumps}>
            {items[it.n]}
          </HocaCard>
        ))}
      </div>
      {r && opt && (
        <Text size="xs" c="dimmed">
          Optimallik: alt sınır {fmtBillion(r.bounds.lb_tl)}, bulunan çözüm {fmtBillion(r.bounds.ub_tl)}, fark {fmtGap(r.bounds.gap)} — ayrıntı “Model → Alt sınır nereden
          geliyor?” bölümünde.
        </Text>
      )}
    </Stack>
  );
}
