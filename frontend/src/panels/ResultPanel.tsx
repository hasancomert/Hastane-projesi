/**
 * Sonuç / açıklama paneli (şartname 6.4, 7.1–7.2, 7.4–7.6):
 *
 *   [Sonuç başlığı + durum rozeti]
 *   [Çözüm ilerlemesi (SolveProgress) — iş yokken son sonucun kaynağı ve süresi]
 *   [Hata / olursuz sonuç uyarıları]
 *   [Seçili hastane ya da hücre kartı (seçilince en üste kayar)]
 *   [Özet cümle (7.1)]
 *   [KPI'lar: hastane sayısı + sınıf dağılımı · yatak/talep · toplam maliyet + kırılım · kişi başı · doluluk ·
 *    ortalama / en uzak mesafe · 10/20/30 km içindeki nüfus]
 *   [Optimallik sertifikası (7.2): L, U, fark, bağımsız denetim]
 *   [Neden bu çözüm? (7.3–7.6 için yer tutucular + bu çözümden çıkan kısa açıklamalar)]
 *   [Kaç hastane? sınır kartları (varsayılan parametrelerle önceden hesaplı)]
 *
 * Eski sonuç (parametre değişti, yeni çözüm bekleniyor) soluk gösterilir; iptal edilen işin sonucu "durduruldu —
 * en iyi bulunan" diye etiketlenir, "optimum" denmez.
 */
import { Alert, Badge, Button, Group, Paper, Progress, ScrollArea, Stack, Text, Title, Tooltip } from '@mantine/core';
import { useEffect, useMemo, useRef, type ReactNode } from 'react';

import type { ClassCode, SolveResultOk } from '../api/types.ts';
import { SolveStatusBadge } from '../components/StatusBadges.tsx';
import { fmtBillion, fmtGap, fmtInt, fmtKm, fmtNumber, fmtPercent, fmtPeople, fmtTl } from '../format/number.ts';
import { gridIndexOf } from '../map/gridIndex.ts';
import { getMapApi } from '../map/mapApi.ts';
import { isDefaultProblemKey, selectGrid, selectOkResult, selectShowStale, selectUiTargetGap, useApp } from '../store/appStore.ts';
import { cancelSolve } from '../store/solver.ts';
import { CLASS_NAME_TR, CLASS_ORDER, classColor, type Scheme } from '../theme/palette.ts';
import { costSegments } from '../ui/costParts.ts';
import { KpiTile } from '../ui/Kpi.tsx';
import { SegmentLegend, StackedBar, Swatch } from '../ui/StackedBar.tsx';
import { BoundsBlock } from './BoundsBlock.tsx';
import { CellCard } from './CellCard.tsx';
import { HospitalCard } from './HospitalCard.tsx';
import { optimalityLabel, summarySentence, type Optimality } from './optimality.ts';
import { SolveProgress } from './SolveProgress.tsx';
import './panels.css';

export interface ResultPanelProps {
  /** dar ekranda çekmece içinde gösteriliyor */
  inDrawer?: boolean;
}

const LB_SOURCE_TR: Record<string, string> = {
  lagrange: 'Lagrange gevşetmesi',
  lagrange_b: 'Lagrange gevşetmesi',
  lagrange_2: 'Lagrange gevşetmesi',
  mip: 'dal-sınır (HiGHS)',
  core: 'çekirdek MIP',
  budget: 'bütçe Lagrange sınırı',
};

/** Grafik/bilgi sekmesine git. */
function goToSection(testId: 'chart-tabs' | 'info-tabs') {
  requestAnimationFrame(() => document.querySelector(`[data-testid="${testId}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
}

/* ------------------------------------------------------------------------------------------------ KPI'lar */

function ClassDistribution({ r, scheme }: { r: SolveResultOk; scheme: Scheme }) {
  const c = r.kpis.count_by_class;
  const segs = CLASS_ORDER.map((code) => ({
    key: code,
    label: `${code} ${CLASS_NAME_TR[code]}`,
    value: c[code] ?? 0,
    color: classColor(code, scheme),
    valueText: `${fmtInt(c[code] ?? 0)} hastane`,
  }));
  return (
    <Stack gap={4}>
      <StackedBar segments={segs} ariaLabel={`Sınıf dağılımı: ${segs.map((s) => `${s.value} ${CLASS_NAME_TR[s.key as ClassCode]}`).join(', ')}`} height={10} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(5.5rem, 1fr))', gap: '2px 8px' }}>
        {segs.map((s) => (
          <Group key={s.key} gap={4} wrap="nowrap" style={{ opacity: s.value ? 1 : 0.75, minWidth: 0 }}>
            <Swatch color={s.color} round />
            <Text size="xs" truncate="end" title={`${s.key} ${CLASS_NAME_TR[s.key as ClassCode]}`}>
              {fmtInt(s.value)} {s.key === 'V-D' ? 'şehir' : CLASS_NAME_TR[s.key as ClassCode]}
            </Text>
          </Group>
        ))}
      </div>
    </Stack>
  );
}

function CostBlock({ r, scheme }: { r: SolveResultOk; scheme: Scheme }) {
  const segs = costSegments(r.costs, scheme).map((s) => ({ ...s, valueText: fmtNumber(s.value / 1e9, 2) }));
  const total = r.costs.toplam;
  const open = r.costs.acilis;
  const acc = r.costs.erisim;
  return (
    <KpiTile
      wide
      testId="kpi-cost"
      label="Toplam maliyet (2026 TL, bugünkü değer)"
      value={fmtNumber(total / 1e9, 2)}
      unit="milyar TL"
      hint={
        <>
          açılış <b>{fmtBillion(open)}</b> ({fmtPercent(open / total)}) · erişim <b>{fmtBillion(acc)}</b> ({fmtPercent(acc / total)})
        </>
      }
    >
      <StackedBar segments={segs} total={total} ariaLabel="Toplam maliyetin kırılımı" height={14} unit="milyar TL" />
      <SegmentLegend segments={segs} total={total} unitCaption="milyar TL" />
      <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
        {r.mode === 'butce'
          ? 'Bütçe modu: açılış maliyeti bütçeyi aşmadan erişim maliyeti en küçüklenir.'
          : r.mode === 'sabit_p'
            ? 'Sabit p modu: hastane sayısı sabit; konum ve boyutlar toplam maliyeti en küçükler.'
            : 'Daha çok hastane → açılış maliyeti artar, erişim maliyeti azalır. Optimum bu ikisinin toplamını en küçükler (U eğrisi).'}
      </Text>
    </KpiTile>
  );
}

function WithinBlock({ r }: { r: SolveResultOk }) {
  const sh = r.kpis.share_pop_within_km;
  const keys = Object.keys(sh).sort((a, b) => Number(a) - Number(b));
  const beyond = r.layers.access?.beyond_R_population;
  return (
    <KpiTile wide testId="kpi-within" label="Hastaneye yol mesafesi şu sınırın içinde olan nüfus">
      <Stack gap={6} mt={2}>
        {keys.map((k) => (
          <Group key={k} gap={8} wrap="nowrap">
            <Text size="sm" w="3.4rem" style={{ flexShrink: 0 }}>
              {k} km
            </Text>
            <Progress value={(sh[k] ?? 0) * 100} size="lg" radius="sm" style={{ flex: 1 }} aria-label={`${k} km içinde nüfus payı ${fmtPercent(sh[k])}`} />
            <Text size="sm" fw={650} w="3.8rem" ta="right" style={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
              {fmtPercent(sh[k])}
            </Text>
          </Group>
        ))}
        {beyond != null && (
          <Text size="xs" c={beyond > 0 ? 'red' : 'dimmed'}>
            R = {fmtNumber(r.layers.rings.R_km, 0)} km dışında kalan nüfus: {fmtPeople(beyond)}
          </Text>
        )}
      </Stack>
    </KpiTile>
  );
}

function Kpis({ r, scheme }: { r: SolveResultOk; scheme: Scheme }) {
  const k = r.kpis;
  const surplus = k.total_demand_beds > 0 ? k.total_beds / k.total_demand_beds - 1 : null;
  return (
    <div className="rp-kpis" data-testid="kpi-grid">
      <KpiTile testId="kpi-hospitals" label="Hastane sayısı" value={fmtInt(k.n_hospitals)}>
        <ClassDistribution r={r} scheme={scheme} />
      </KpiTile>
      <KpiTile
        testId="kpi-beds"
        label="Toplam yatak"
        value={fmtInt(k.total_beds)}
        hint={
          <>
            talep {fmtInt(k.total_demand_beds)} yatak
            {surplus !== null && surplus > 0.0005 ? ` · kapasite fazlası ${fmtPercent(surplus)}` : ''}
            <br />
            {fmtNumber(k.beds_per_1000, 2)} yatak / 1000 kişi
          </>
        }
      >
        <Progress
          value={Math.min(100, (k.total_demand_beds / Math.max(1, k.total_beds)) * 100)}
          size="sm"
          radius="sm"
          aria-label={`Talebin yatak kapasitesine oranı ${fmtPercent(k.total_demand_beds / Math.max(1, k.total_beds))}`}
        />
      </KpiTile>
      <CostBlock r={r} scheme={scheme} />
      <KpiTile testId="kpi-per-capita" label="Kişi başı maliyet" value={fmtTl(k.cost_per_capita_tl)} hint={`${fmtPeople(k.population)} için`} />
      <KpiTile testId="kpi-occupancy" label="Ortalama doluluk" value={fmtPercent(k.mean_occupancy)} hint="atanan talep / yatak" />
      <KpiTile testId="kpi-mean-km" label="Ortalama mesafe (nüfus ağırlıklı)" value={fmtNumber(k.pop_weighted_mean_km, 1)} unit="km" hint="yol mesafesi" />
      <KpiTile
        testId="kpi-max-km"
        label="En uzak mesafe"
        value={fmtNumber(k.max_km, 1)}
        unit="km"
        hint={`sınır R = ${fmtKm(r.layers.rings.R_km, 0)}`}
      />
      <WithinBlock r={r} />
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ sertifika */

function Certificate({ r, opt, stale }: { r: SolveResultOk; opt: Optimality; stale: boolean }) {
  const b = r.bounds;
  const text =
    r.certificate_tr ||
    (b.lb_tl != null && b.ub_tl != null
      ? `Çözücünün alt sınırı L = ${fmtBillion(b.lb_tl)}, bulunan çözüm U = ${fmtBillion(b.ub_tl)} → fark ${fmtGap(b.gap)}. Bu çözümden ${fmtGap(b.gap)}'den daha iyi bir çözüm yoktur.`
      : 'Bu sonuç için alt sınır yok (sezgisel çözüm).');
  return (
    <Paper withBorder radius="md" p="sm" data-testid="certificate">
      <Stack gap={6}>
        <Group justify="space-between" gap="xs" wrap="nowrap">
          <Text size="sm" fw={650}>
            Optimallik sertifikası
          </Text>
          {!stale && (
            <Tooltip label={opt.tooltip} multiline maw={320}>
              <span data-testid="certificate-badge">
                <SolveStatusBadge status={r.status} tone={opt.tone} label={opt.badge} size="sm" />
              </span>
            </Tooltip>
          )}
        </Group>
        <div className="sp-stats">
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Alt sınır L
            </Text>
            <Text size="sm" fw={650}>
              {fmtBillion(b.lb_tl)}
            </Text>
          </Stack>
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Bulunan U
            </Text>
            <Text size="sm" fw={650}>
              {fmtBillion(b.ub_tl)}
            </Text>
          </Stack>
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Fark
            </Text>
            <Text size="lg" fw={750} style={{ lineHeight: 1.1 }}>
              {fmtGap(b.gap)}
            </Text>
          </Stack>
        </div>
        <Text size="sm" style={{ lineHeight: 1.45 }}>
          {text}
        </Text>
        <Text size="xs" c="dimmed">
          Alt sınırın kaynağı: {b.lb_source ? LB_SOURCE_TR[b.lb_source] ?? b.lb_source : '—'}
          {b.lb_scope === 'k_listesi' ? ' · k en yakın aday listesiyle kurulan model için' : ''}
          {r.target_gap != null ? ` · hedef fark ${fmtGap(r.target_gap)}` : ''}
          {r.mode === 'butce' ? ' · bütçe modunda amaç erişim maliyetidir' : ''}
        </Text>
        {r.check && (
          <Tooltip label="Çözüm, motordan bağımsız bir denetçiyle yeniden kontrol edildi: tam atama, kapasite, sınıf sınırları, R, hücre başına en fazla 1 hastane, kilitler, maliyetin yeniden hesabı." multiline maw={320}>
            <Badge size="sm" variant="light" color={r.check.ok ? 'teal' : 'red'} tt="none" style={{ alignSelf: 'flex-start', cursor: 'help' }}>
              {r.check.ok ? '✓ ' : '✗ '}
              {r.check.summary_tr}
            </Badge>
          </Tooltip>
        )}
      </Stack>
    </Paper>
  );
}

/* ------------------------------------------------------------------------------------------------ "neden?" */

function WhyItem({ title, children, phase6, action }: { title: string; children: ReactNode; phase6?: boolean; action?: ReactNode }) {
  return (
    <Stack gap={2}>
      <Group gap={6} wrap="nowrap" justify="space-between">
        <Text size="sm" fw={650}>
          {title}
        </Text>
        {phase6 && (
          <Badge size="xs" variant="light" color="gray" tt="none">
            yakında
          </Badge>
        )}
      </Group>
      <Text size="xs" c="dimmed" style={{ lineHeight: 1.45 }}>
        {children}
      </Text>
      {action}
    </Stack>
  );
}

function WhyBlock({ r }: { r: SolveResultOk }) {
  const setUi = useApp((s) => s.setUi);
  const explainOk = useApp((s) => !!s.meta?.job_types.explain?.available);
  const n = r.kpis.n_hospitals;
  const atUpper = useMemo(() => {
    const out: { cls: ClassCode; n: number; beds: number; next: ClassCode; total: number }[] = [];
    for (const code of CLASS_ORDER) {
      const hs = r.hospitals.filter((h) => h.class === code);
      const up = hs.filter((h) => h.size.at_bound === 'ust' && h.size.next_class);
      if (up.length) out.push({ cls: code, n: up.length, beds: up[0].size.class_max_beds, next: up[0].size.next_class!.class, total: hs.length });
    }
    return out;
  }, [r]);
  const avgOpen = n > 0 ? r.costs.acilis / n : null;
  return (
    <Paper withBorder radius="md" p="sm" data-testid="why-block">
      <Stack gap="xs">
        <Text size="sm" fw={650}>
          Neden bu çözüm?
        </Text>
        <WhyItem
          title={`Neden ${fmtInt(n)} hastane?`}
          phase6={!explainOk}
          action={
            <Button
              size="compact-xs"
              variant="subtle"
              style={{ alignSelf: 'flex-start' }}
              onClick={() => {
                setUi({ chartTab: 'u_egrisi' });
                goToSection('chart-tabs');
              }}
            >
              U eğrisine git
            </Button>
          }
        >
          Hastane başına ortalama açılış {avgOpen !== null ? fmtBillion(avgOpen) : '—'}; bir hastane daha açmak bu kadar yatırım ister ama
          hastaları yaklaştırıp erişim maliyetini düşürür. {fmtInt(n)} bu iki etkinin dengelendiği sayıdır. N − 1 ve N + 1 hastaneli en iyi
          çözümlerle kıyas (açılış farkı, erişim farkı, net fark) yakında eklenecek.
        </WhyItem>
        <WhyItem title="Neden burada?">
          Haritada bir hastaneye tıklayın: hizmet ettiği nüfus, ortalama ve en uzak mesafe, maliyet kırılımı ve kilit/yasak düğmeleri kartta.
          Bir hücreye tıklayınca nüfusu, hizmet aldığı hastane ve gölge fiyatı görünür.
        </WhyItem>
        <WhyItem title="Neden bu boyut?">
          {atUpper.length > 0
            ? atUpper
                .map((a) => `${fmtInt(a.total)} ${CLASS_NAME_TR[a.cls]} hastaneden ${fmtInt(a.n)} tanesi tam ${fmtInt(a.beds)} yatakta: ${fmtInt(a.beds + 1)}. yatakta ${a.next} sınıfına geçip açılış maliyeti sıçrayacağı için eşiğin hemen altında kalıyor.`)
                .join(' ')
            : 'Yatak sayıları atanan talebe göre ayarlanmış; sınıf eşiğinde yığılma yok.'}{' '}
          Ayrıntı hastane kartında.
        </WhyItem>
        <WhyItem title="Gölge fiyatlar">
          {r.duals
            ? 'Açık hastaneler sabitken atama LP’sinin dual değerleri: hücre kartında “1 kişinin marjinal maliyeti”, hastane kartında “1 ek yatağın değeri”; haritada gölge fiyat katmanı.'
            : 'Bu sonuçta dual değer yok.'}
        </WhyItem>
      </Stack>
    </Paper>
  );
}

/* ------------------------------------------------------------------------------------------------ hata metni */

/** Sunucunun 422 iletisindeki API terimlerini arayüzün kendi adlarına çevirir. */
export function uiErrorText(msg: string): string {
  return msg
    .replace(/aralık dışı değerler için allow_outside_range=true gönderin\.?/g, 'aralık dışı değerler için parametre panelindeki “Kaydırıcı aralığı dışına izin ver” anahtarını açın.')
    .replace(/allow_outside_range=true/g, '“Kaydırıcı aralığı dışına izin ver”');
}

/* ------------------------------------------------------------------------------------------------ panel */

export function ResultPanel({ inDrawer }: ResultPanelProps) {
  const result = useApp((s) => s.result);
  const ok = useApp(selectOkResult);
  const info = useApp((s) => s.resultInfo);
  // Çevrimdışı yedek sonuç "eski" sayılmaz: bootstrap.applyFallback gösterilen problemi yedeğin problemine eşitler
  // (ızgara + senaryo); selectShowStale ayrıca güvence (çevrimdışıyken denetimler zaten kapalı).
  const stale = useApp(selectShowStale);
  const job = useApp((s) => s.job);
  const error = useApp((s) => s.error);
  const selection = useApp((s) => s.selection);
  const scheme = useApp((s) => s.colorScheme);
  const grid = useApp(selectGrid);
  const gridKm = useApp((s) => s.problem.gridKm);
  const locks = useApp((s) => s.problem.locks);
  const online = useApp((s) => s.connection.mode === 'online');
  const meta = useApp((s) => s.meta);
  const select = useApp((s) => s.select);
  const setLock = useApp((s) => s.setLock);
  const setError = useApp((s) => s.setError);
  const recommended = useApp((s) => s.meta?.defaults.recommended_time_limit_s[String(s.problem.gridKm)] ?? null);
  const overrideLimit = useApp((s) => s.problem.overrides['solver.time_limit_s']);
  const timeLimitS = typeof overrideLimit === 'number' ? overrideLimit : recommended;
  const targetGap = useApp(selectUiTargetGap);
  const opt = ok ? optimalityLabel(ok, targetGap, !!info?.stopped) : null;
  const viewport = useRef<HTMLDivElement>(null);
  const top = useRef<HTMLDivElement>(null);

  // seçim değişince kartı görünür yap (panelin başına kaydır)
  const selKey = selection ? `${selection.kind}:${selection.cellId}` : '';
  useEffect(() => {
    if (!selKey) return;
    if (viewport.current) viewport.current.scrollTo({ top: 0, behavior: 'smooth' });
    else top.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [selKey]);

  const lockOf = (cellId: string) => (locks.open.includes(cellId) ? 'open' : locks.closed.includes(cellId) ? 'closed' : null);
  const labelOf = (key: string | null | undefined) => (key && meta?.parameters[key]?.label) || key || '';

  const selectedCard =
    selection?.kind === 'hospital' && ok?.hospitals[selection.index] ? (
      <HospitalCard
        hospital={ok.hospitals[selection.index]}
        index={selection.index}
        bedValueTl={ok.duals?.bed_value_tl[selection.index] ?? null}
        scheme={scheme}
        onClose={() => select(null)}
        onFocus={() => getMapApi()?.flyToHospital(selection.index)}
        lock={lockOf(selection.cellId)}
        onLock={(l) => setLock(selection.cellId, l)}
        canLock={online}
      />
    ) : selection?.kind === 'cell' ? (
      <CellCard
        cellId={selection.cellId}
        gridIndex={selection.gridIndex}
        cell={grid?.features[selection.gridIndex]?.properties ?? null}
        solution={ok}
        lock={lockOf(selection.cellId)}
        onLock={(l) => setLock(selection.cellId, l)}
        onClose={() => select(null)}
        canLock={online}
      />
    ) : null;

  const body = (
    <Stack gap="sm" p="md" data-testid="result-panel" ref={top}>
      {/* rozet uzun ("optimum · kanıtlı fark %0,06"): dar panelde başlığın altına iner, kesilmez */}
      <Group justify="space-between" gap="xs" wrap="wrap" style={{ rowGap: 6 }}>
        {!inDrawer ? <Title order={2}>Sonuç</Title> : null}
        <Group gap={4} wrap="wrap" justify="flex-end" style={{ minWidth: 0 }}>
          {ok && opt && !stale && (
            <Tooltip label={opt.tooltip} multiline maw={320}>
              <span data-testid="result-status-badge" data-kind={opt.kind}>
                <SolveStatusBadge status={ok.status} tone={opt.tone} label={opt.badge} size="md" />
              </span>
            </Tooltip>
          )}
          {stale && (
            <Tooltip label="Parametreler değişti; gösterilen sonuç önceki parametrelere ait. Optimallik etiketi yeni çözüm gelince görünür.">
              <Badge size="md" variant="light" color="orange" tt="none" data-testid="stale-badge">
                önceki sonuç
              </Badge>
            </Tooltip>
          )}
        </Group>
      </Group>

      <SolveProgress
        job={job}
        resultInfo={info}
        result={ok}
        timeLimitS={timeLimitS}
        targetGap={targetGap}
        onCancel={() => void cancelSolve()}
      />

      {error && (
        <Alert color="red" variant="light" title="Çözüm başlatılamadı" withCloseButton onClose={() => setError(null)} closeButtonLabel="Uyarıyı kapat">
          <Stack gap={2}>
            <Text size="sm">{uiErrorText(error.message)}</Text>
            {error.items?.map((it, i) => (
              <Text key={i} size="sm">
                • {it.key ? <b>{labelOf(it.key)}: </b> : null}
                {uiErrorText(it.message_tr)}
              </Text>
            ))}
          </Stack>
        </Alert>
      )}
      {result && !result.ok && (
        <Alert color="orange" variant="light" title={result.status_tr ?? 'Çözüm yok'} data-testid="result-failed">
          <Text size="sm">{result.message_tr}</Text>
        </Alert>
      )}

      {selectedCard}

      {ok && opt && stale && (
        <Text size="sm" style={{ color: 'var(--mantine-color-orange-text)' }} data-testid="stale-note">
          Parametreler değişti; aşağıdaki sonuç önceki parametrelere ait. Yeni çözüm {job ? 'hesaplanıyor…' : error ? 'başlatılamadı (yukarıdaki uyarı).' : 'için “Şimdi çöz”e basın.'}
        </Text>
      )}
      {ok && opt && (
        <Stack gap="sm" className={stale ? 'rp-stale' : undefined} aria-busy={stale || undefined}>
          <Text size="md" className="rp-summary" data-testid="result-summary" style={{ lineHeight: 1.45 }}>
            {stale ? '(önceki sonuç) ' : ''}
            {summarySentence(ok, opt)}
          </Text>
          <Kpis r={ok} scheme={scheme} />
          <Certificate r={ok} opt={opt} stale={stale} />
          <WhyBlock r={ok} />
          <BoundsBlock r={ok} gridKm={ok.grid_km ?? gridKm} online={online} resultKey={info?.problemKey ?? null} isDefault={isDefaultProblemKey(info?.problemKey)} stale={stale} />
          {ok.warnings && ok.warnings.length > 0 && (
            <Alert color="yellow" variant="light" title="Uyarılar" p="xs">
              {ok.warnings.map((w, i) => (
                <Text key={i} size="xs">
                  • {w}
                </Text>
              ))}
            </Alert>
          )}
        </Stack>
      )}
      {!ok && !result && !job && (
        <Text size="sm" c="dimmed">
          Sonuç bekleniyor…
        </Text>
      )}
    </Stack>
  );
  return inDrawer ? body : (
    <ScrollArea h="100%" type="auto" offsetScrollbars viewportRef={viewport}>
      {body}
    </ScrollArea>
  );
}

/** Dışarıdan: bir hücreyi seçip haritada göster (kilit listesi, U eğrisi vb.). */
export function focusCell(cellId: string): void {
  const s = useApp.getState();
  const g = s.grids[s.problem.gridKm];
  if (!g) return;
  const idx = gridIndexOf(g, s.problem.gridKm);
  const gi = idx.byId.get(cellId);
  if (gi === undefined) return;
  s.select({ kind: 'cell', gridIndex: gi, cellId });
  const [lon, lat] = idx.centroid(gi);
  getMapApi()?.flyTo(lon, lat, 10);
}

export default ResultPanel;
