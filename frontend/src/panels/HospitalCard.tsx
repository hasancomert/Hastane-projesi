/**
 * Hastane kartı (şartname 6.2 tıklama, 7.4 "neden burada?", 7.5 "neden bu boyut?", 7.6 gölge fiyat).
 *
 *   - konum (ilçe, hücre), sınıf ve yatak, atanan talep, doluluk, hizmet ettiği nüfus ve hücre sayısı,
 *     ortalama / en uzak mesafe (R ile kıyas);
 *   - "Neden bu boyut?": sınıf eşiği etkisi (hospital.size: at_bound, next_class, prev_class — ör. "199 yatak,
 *     200 yatak eşiğinin altında kalıyor çünkü …");
 *   - "1 ek yatağın değeri" (duals.bed_value_tl) ile 1 yatağın açılış maliyetinin (size.per_bed_tl) kıyası;
 *   - maliyet kırılımı (yığılmış çubuk + tablo, sonuç paneliyle aynı renkler);
 *   - "Bu hücreyi kilitle / yasakla" (depoda setLock → otomatik yeniden çözüm);
 *   - "Bu hastane kapatılırsa?" (Faz 6: POST /api/explain `hastane_kapat`; iş türü kullanılamıyorsa düğme kapalı ve
 *     sunucunun Türkçe açıklaması ipucunda — Faz 6 yalnız sunucuda `available` açar, bu düğme kendiliğinden çalışır).
 */
import { ActionIcon, Badge, Button, CloseButton, Group, Paper, Stack, Text, Tooltip } from '@mantine/core';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { explain, type JobHandle } from '../api/jobs.ts';
import type { ExplainResult, Hospital } from '../api/types.ts';
import { fmtBeds, fmtInt, fmtKm, fmtMoney, fmtMoneyDelta, fmtNumber, fmtPeople, fmtPercent } from '../format/number.ts';
import { selectOkResult, useApp } from '../store/appStore.ts';
import { CLIENT_ID } from '../store/solver.ts';
import { CLASS_NAME_TR, classColor, type Scheme } from '../theme/palette.ts';
import { costSegments } from '../ui/costParts.ts';
import { IconBan, IconLock, IconTarget, IconUnlock } from '../ui/icons.tsx';
import { SegmentLegend, StackedBar, Swatch } from '../ui/StackedBar.tsx';
import { solutionIndex } from './solutionIndex.ts';
import './panels.css';

export interface HospitalCardProps {
  hospital: Hospital;
  /** `hospitals` dizisindeki sıra (duals.bed_value_tl, layers.demand.service_hospital ile aynı) */
  index: number;
  /** 1 ek yatağın değeri (TL) — duals.bed_value_tl[index]; dual yoksa null */
  bedValueTl: number | null;
  scheme: Scheme;
  onClose(): void;
  /** haritada bu hastaneye uç */
  onFocus?(): void;
  /** hücrenin kilit durumu ve değiştirme */
  lock?: 'open' | 'closed' | null;
  onLock?(lock: 'open' | 'closed' | null): void;
  /** kilit değiştirilebilir mi (çevrimdışıyken hayır) */
  canLock?: boolean;
}

/** Şartname 7.5: yatak sayısının sınıf eşiğine göre açıklaması. */
export function sizeExplanation(h: Hospital): string {
  const s = h.size;
  const beds = fmtInt(h.beds);
  const occ = fmtPercent(h.occupancy);
  if (s.at_bound === 'ust' && s.next_class) {
    const nx = s.next_class;
    return (
      `${beds} yatak, ${fmtInt(nx.beds)} yatak eşiğinin altında kalıyor çünkü ${fmtInt(nx.beds)} yatakta hastane ${nx.class} ` +
      `(${CLASS_NAME_TR[nx.class]}) sınıfına geçer ve açılış maliyeti ${fmtMoney(s.opening_tl)} → ${fmtMoney(nx.opening_tl)} ` +
      `(${fmtMoneyDelta(nx.opening_tl - s.opening_tl)}) sıçrar: daha büyük sınıfta hem m² birim maliyeti hem yatak başı alan büyür. ` +
      `Bu yüzden model yatağı ${h.class} sınıfının üst sınırında (${fmtInt(s.class_max_beds)}) tutuyor; fazla talep komşu hastanelere gidiyor.`
    );
  }
  if (s.at_bound === 'ust') {
    return `${beds} yatak: izinli en büyük boyutta (${h.class} üst sınırı ${fmtInt(s.class_max_beds)}). Daha büyük hastane bu ayarlarda yok; fazla talep komşu hastanelere gidiyor.`;
  }
  if (s.at_bound === 'alt') {
    const prev = s.prev_class
      ? ` Bir alt sınıfta (${s.prev_class.class}, en çok ${fmtInt(s.prev_class.beds)} yatak) açılış ${fmtMoney(s.prev_class.opening_tl)} olurdu (${fmtMoneyDelta(s.prev_class.opening_tl - s.opening_tl)}).`
      : '';
    return (
      `${beds} yatak: ${h.class} sınıfının alt sınırında (en az ${fmtInt(s.class_min_beds)} yatak). Atanan talep ${fmtNumber(h.assigned_beds, 1)} yatak, ` +
      `doluluk ${occ}. Model bu küçük hastaneyi yine de açıyor: çevresindeki nüfusu daha uzak hastanelere taşımak, en küçük hastanenin ` +
      `açılış maliyetinden pahalı (ya da R = en çok yol mesafesi kısıtı burada bir hastane gerektiriyor).${prev}`
    );
  }
  const next = s.next_class ? ` Bir sonraki eşik ${fmtInt(s.next_class.beds)} yatak (${s.next_class.class}); geçilirse açılış ${fmtMoneyDelta(s.next_class.opening_tl - s.opening_tl)} artar.` : '';
  return `${beds} yatak, ${h.class} aralığının (${fmtInt(s.class_min_beds)}–${fmtInt(s.class_max_beds)}) içinde: yatak sayısı atanan talebe göre ayarlanmış (doluluk ${occ}).${next}`;
}

/** Şartname 7.6: 1 ek yatağın değeri ile 1 yatağın açılış maliyeti (sınıf eşiğine göre yorum). */
function bedValueText(bedValue: number, perBed: number, atBound: Hospital['size']['at_bound']): string {
  if (bedValue <= perBed * 1e-6) {
    return 'Kapasite bağlayıcı değil: 1 ek yatak toplam erişim maliyetini azaltmıyor (boş yatak var ya da talep başka hastaneye eşit maliyetle gidebiliyor).';
  }
  const r = bedValue / perBed;
  const ratio = `oran ${fmtNumber(r, 2)}`;
  if ((atBound === null && r > 0.85 && r < 1.15) || (r > 0.97 && r < 1.03)) {
    return `1 ek yatağın erişim kazancı ≈ 1 yatağın açılış maliyeti (${fmtMoney(perBed)}; ${ratio}): yatak sayısı dengede — marjinal fayda ≈ marjinal maliyet.`;
  }
  if (r < 1) {
    return `1 ek yatak erişim maliyetini ${fmtMoney(bedValue)} azaltır ama ${fmtMoney(perBed)} tutar (${ratio}): eklemek değmez.`;
  }
  return atBound === 'ust'
    ? `1 ek yatak erişim maliyetini ${fmtMoney(bedValue)} azaltırdı, yatağın kendisi ${fmtMoney(perBed)} (${ratio}); yine de eklenmiyor çünkü bir sonraki yatak hastaneyi üst sınıfa taşır ve açılış maliyeti sıçrar.`
    : `1 ek yatak erişim maliyetini ${fmtMoney(bedValue)} azaltır, açılış maliyeti ${fmtMoney(perBed)} (${ratio}): boyut sınırı yüzünden yatak eklenemiyor.`;
}

/* ------------------------------------------------------------------------------------------------ "kapatılırsa?" */

type ImpactState = { state: 'idle' } | { state: 'running' } | { state: 'done'; text: string; delta: number | null } | { state: 'error'; text: string };

function useCloseImpact(cellId: string) {
  const [st, setSt] = useState<ImpactState>({ state: 'idle' });
  const handle = useRef<JobHandle<ExplainResult> | null>(null);
  useEffect(() => {
    setSt({ state: 'idle' });
    return () => handle.current?.detach();
  }, [cellId]);
  const run = () => {
    const s = useApp.getState();
    const r = selectOkResult(s);
    if (!r) return;
    handle.current?.detach();
    setSt({ state: 'running' });
    const h = explain({
      grid_km: s.problem.gridKm,
      overrides: s.problem.overrides,
      ...(s.problem.locks.open.length || s.problem.locks.closed.length ? { locks: s.problem.locks } : {}),
      hospitals: r.hospitals.map((x) => ({ cell_id: x.cell_id, class: x.class, beds: x.beds })),
      what: ['hastane_kapat'],
      close: [cellId],
      client_id: CLIENT_ID,
    });
    handle.current = h;
    void h.done.then((out) => {
      if (handle.current !== h) return;
      if (out.kind === 'done') {
        const it = out.result.items?.find((x) => x.kind === 'hastane_kapat');
        setSt({ state: 'done', text: it?.message_tr ?? 'Sonuç alındı.', delta: it?.delta_tl ?? null });
      } else if (out.kind === 'failed') setSt({ state: 'error', text: out.message });
      else if (out.kind === 'cancelled') setSt({ state: 'error', text: out.reason ?? 'İptal edildi.' });
    });
  };
  return { st, run };
}

/* ------------------------------------------------------------------------------------------------ kart */

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

export function HospitalCard({ hospital: h, index, bedValueTl, scheme, onClose, onFocus, lock = null, onLock, canLock = true }: HospitalCardProps) {
  const solution = useApp(selectOkResult);
  const explainMeta = useApp((s) => s.meta?.job_types.explain);
  const R = solution?.layers.rings.R_km ?? null;
  const served = solution ? solutionIndex(solution).servedCells[index] ?? null : null;
  const { st, run } = useCloseImpact(h.cell_id);
  const explainAvailable = !!explainMeta?.available;
  const segments = costSegments(h.costs, scheme).map((s) => ({ ...s, valueText: fmtNumber(s.value / 1e6, 1) }));
  const total = h.costs.acilis + h.costs.erisim;

  return (
    <Paper withBorder radius="md" p="sm" data-testid="hospital-card" aria-label={`Hastane ${index + 1}, ${h.district}`} component="section">
      <Stack gap="xs">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Group gap="xs" wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
            <span style={{ marginTop: 5 }}>
              <Swatch color={classColor(h.class, scheme)} size={14} round />
            </span>
            <Stack gap={0} style={{ minWidth: 0 }}>
              <Text fw={700} size="md">
                Hastane {index + 1} · {h.district}
              </Text>
              <Text size="xs" c="dimmed">
                {h.class} ({CLASS_NAME_TR[h.class]}) · hücre {h.cell_id}
                {h.locked_open ? ' · kilitli' : ''}
              </Text>
            </Stack>
          </Group>
          <Group gap={2} wrap="nowrap">
            {onFocus && (
              <Tooltip label="Haritada göster">
                <ActionIcon variant="subtle" color="gray" onClick={onFocus} aria-label="Hastaneyi haritada göster">
                  <IconTarget size={16} />
                </ActionIcon>
              </Tooltip>
            )}
            <CloseButton onClick={onClose} aria-label="Hastane kartını kapat" />
          </Group>
        </Group>

        <div className="rp-kv">
          <Row label="Yatak">
            {fmtNumber(h.beds, 1)} <Text span size="xs" c="dimmed">({fmtInt(h.size.class_min_beds)}–{fmtInt(h.size.class_max_beds)})</Text>
          </Row>
          <Row label="Atanan talep">{fmtBeds(h.assigned_beds, 1)}</Row>
          <Row label="Doluluk">{fmtPercent(h.occupancy)}</Row>
          <Row label="Nüfus">{fmtPeople(h.population)}</Row>
          {served !== null && <Row label="Hizmet bölgesi">{fmtInt(served)} hücre</Row>}
          <Row label="Ort. mesafe">{fmtKm(h.mean_km)}</Row>
          <Row label="En uzak" hint="Yol mesafesi; R sınırıyla kıyaslanır">
            {fmtKm(h.max_km)}
            {R ? <Text span size="xs" c="dimmed"> (R = {fmtNumber(R, 0)} km)</Text> : null}
          </Row>
        </div>

        <Stack gap={4}>
          <Text size="sm" fw={650}>
            Neden bu boyut?
          </Text>
          <Text size="sm" style={{ lineHeight: 1.45 }} data-testid="size-explanation">
            {sizeExplanation(h)}
          </Text>
        </Stack>

        <Stack gap={4}>
          <Group justify="space-between" gap="xs">
            <Text size="sm" fw={650}>
              1 ek yatağın değeri
            </Text>
            <Text size="sm" fw={700}>
              {bedValueTl === null ? '—' : fmtMoney(bedValueTl)}
            </Text>
          </Group>
          <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
            {bedValueTl === null
              ? 'Dual değerler bu sonuçta yok.'
              : `${bedValueText(bedValueTl, h.size.per_bed_tl, h.size.at_bound)} (Atama LP’sinin kapasite kısıtının dual değeri; açık hastaneler sabitken. ${solution?.duals?.note_tr ?? ''})`}
          </Text>
        </Stack>

        <Stack gap={4}>
          <Group justify="space-between" gap="xs">
            <Text size="sm" fw={650}>
              Maliyet
            </Text>
            <Text size="sm">
              açılış <b>{fmtMoney(h.costs.acilis)}</b> · erişim <b>{fmtMoney(h.costs.erisim)}</b>
            </Text>
          </Group>
          <StackedBar segments={segments} total={total} ariaLabel={`Hastane ${index + 1} maliyet kırılımı`} height={10} unit="milyon TL" />
          <SegmentLegend segments={segments} total={total} columns={1} unitCaption="milyon TL" />
          <Text size="xs" c="dimmed">
            Yatak başı {fmtMoney(h.size.per_bed_tl)} · hastane başı sabit {fmtMoney(h.size.fixed_tl)} (sınıf {h.class}, arsa hariç)
          </Text>
        </Stack>

        {onLock && (
          <Group gap="xs" wrap="wrap">
            <Button
              size="compact-sm"
              variant={lock === 'open' ? 'filled' : 'light'}
              leftSection={<IconLock size={14} />}
              disabled={!canLock}
              onClick={() => onLock(lock === 'open' ? null : 'open')}
              aria-pressed={lock === 'open'}
            >
              {lock === 'open' ? 'Kilitli (kaldır)' : 'Bu hücreyi kilitle'}
            </Button>
            <Button
              size="compact-sm"
              color="red"
              variant={lock === 'closed' ? 'filled' : 'light'}
              leftSection={<IconBan size={14} />}
              disabled={!canLock}
              onClick={() => onLock(lock === 'closed' ? null : 'closed')}
              aria-pressed={lock === 'closed'}
            >
              {lock === 'closed' ? 'Yasak (kaldır)' : 'Yasakla'}
            </Button>
            {lock && (
              <Button size="compact-sm" variant="subtle" color="gray" leftSection={<IconUnlock size={14} />} disabled={!canLock} onClick={() => onLock(null)}>
                Kilidi kaldır
              </Button>
            )}
          </Group>
        )}
        {onLock && (
          <Text size="xs" c="dimmed" style={{ lineHeight: 1.35 }}>
            Kilit: bu hücrede hastane zorunlu olur, model kalanını yeniden optimize eder. Yasak: burada hastane açılamaz, model başka
            yer arar.
          </Text>
        )}

        <Stack gap={4}>
          <Tooltip label={explainAvailable ? 'Kalan hastanelerle atamayı yeniden hesaplar ve toplam maliyet farkını gösterir.' : 'Bu açıklama (kalan hastanelerle maliyet farkı) hazırlanıyor; şimdilik kullanılamıyor.'} multiline maw={320}>
            <span style={{ alignSelf: 'flex-start' }}>
              <Button
                size="compact-sm"
                variant="default"
                onClick={run}
                disabled={!explainAvailable || st.state === 'running' || !canLock}
                loading={st.state === 'running'}
                data-testid="close-impact"
              >
                Bu hastane kapatılırsa?
              </Button>
            </span>
          </Tooltip>
          {!explainAvailable && (
            <Badge size="xs" variant="light" color="gray" tt="none" style={{ alignSelf: 'flex-start' }}>
              yakında
            </Badge>
          )}
          {st.state === 'done' && (
            <Text size="sm" style={{ lineHeight: 1.4 }}>
              {st.delta != null ? <b>{fmtMoneyDelta(st.delta)} · </b> : null}
              {st.text}
            </Text>
          )}
          {st.state === 'error' && (
            <Text size="xs" style={{ color: 'var(--mantine-color-orange-text)' }}>
              {st.text}
            </Text>
          )}
        </Stack>
      </Stack>
    </Paper>
  );
}

export default HospitalCard;
