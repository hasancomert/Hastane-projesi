/**
 * Çözüm ilerlemesi (şartname 6.3, 7.10): aşama (Türkçe), aşama adımları, geçen süre / süre sınırı çubuğu, canlı alt
 * sınır L, bulunan çözüm U ve fark (SSE `progress` olayları), farkın küçük grafiği, kuyruk konumu, yeniden bağlanma
 * uyarısı, "İptal". İş yokken son sonucun tek satırlık özeti: kaynağı (canlı / önbellekten / önceden çözülmüş /
 * çevrimdışı yedek), süresi, durumu (optimal / süre doldu / durduruldu …).
 *
 * Sayfa çözüm sırasında donmaz: çözüm sunucunun işçi sürecindedir; burada yalnız 250 ms'lik bir saat yenilenir.
 */
import { Button, Group, Loader, Progress, Stack, Text, Tooltip } from '@mantine/core';
import { useEffect, useMemo, useState } from 'react';

import type { SolveResultOk, TraceEvent } from '../api/types.ts';
import { SolveStatusBadge } from '../components/StatusBadges.tsx';
import { fmtBillion, fmtDuration, fmtGap, fmtInt } from '../format/number.ts';
import type { JobInfo, ResultInfo } from '../store/appStore.ts';
import { IconStop } from '../ui/icons.tsx';
import { Sparkline } from '../ui/Sparkline.tsx';
import './panels.css';

export interface SolveProgressProps {
  /** çalışan / bekleyen iş (yoksa null: son sonucun özeti gösterilir) */
  job: JobInfo | null;
  /** son gösterilen sonucun bilgisi (kaynak: canlı / önbellek / önceden çözülmüş / yedek) */
  resultInfo?: ResultInfo | null;
  /** süre sınırı (sn): ilerleme çubuğu için (meta.defaults.recommended_time_limit_s[gridKm] ya da override) */
  timeLimitS?: number | null;
  onCancel(): void;
  compact?: boolean;
  /** son sonuç (özet satırı için: durum, süre, fark); verilmezse yalnız kaynak yazılır */
  result?: SolveResultOk | null;
  /** hedef fark (solver.mip_gap) — grafikte referans çizgisi */
  targetGap?: number | null;
}

const PHASE_TEXT: Record<JobInfo['phase'], string> = {
  debounce: 'Değişiklik algılandı — çözüm birazdan başlayacak',
  submitting: 'İstek gönderiliyor…',
  queued: 'Kuyrukta bekliyor',
  running: 'Çözülüyor',
  cancelling: 'Durduruluyor — en iyi çözüm alınıyor…',
};

const SOURCE_TEXT: Record<ResultInfo['source'], string> = {
  live: 'canlı çözüm',
  cache: 'önbellekten',
  precomputed: 'önceden çözülmüş',
  fallback: 'çevrimdışı yedek',
};

const SOURCE_HINT: Record<ResultInfo['source'], string> = {
  live: 'Bu parametrelerle şimdi çözüldü.',
  cache: 'Aynı parametrelerle daha önce (en az aynı çabayla) çözülmüş sonuç önbellekten anında geldi.',
  precomputed: 'Önceden uzun süreyle çözülmüş sonuç.',
  fallback: 'Sunucuya ulaşılamadı; önceden çözülmüş yedek sonuç gösteriliyor.',
};

/**
 * Motorun aşamaları (docs/API.md §4): baslangic → sezgisel → lagrange → cekirdek_mip → [mip] → [lagrange_2] → bitti.
 * Adım etiketlerinde "Alt sınır" ifadesi bilerek yok (o ifade yalnız canlı L değeri gelince görünür).
 */
const STEPS: { id: string; label: string; also?: string[]; optional?: boolean }[] = [
  { id: 'baslangic', label: 'Başlangıç' },
  { id: 'sezgisel', label: 'Sezgisel' },
  { id: 'lagrange', label: 'Lagrange' },
  { id: 'cekirdek_mip', label: 'İyileştirme' },
  { id: 'mip', label: 'Dal-sınır', also: ['lagrange_2'], optional: true },
  { id: 'bitti', label: 'Bitti' },
];

function stepIndex(phase: string | number | undefined): number {
  if (phase === undefined) return -1;
  const p = String(phase);
  return STEPS.findIndex((s) => s.id === p || s.also?.includes(p));
}

function lastWith(trace: TraceEvent[], f: (e: TraceEvent) => boolean): TraceEvent | null {
  for (let i = trace.length - 1; i >= 0; i--) if (f(trace[i])) return trace[i];
  return null;
}

export function SolveProgress({ job, resultInfo, timeLimitS, onCancel, compact, result, targetGap }: SolveProgressProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!job) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [job]);

  const trace = job?.trace;
  const gapPoints = useMemo<[number, number][]>(
    () => (trace ?? []).filter((e) => e.gap != null && e.t != null && e.gap > 0).map((e) => [e.t as number, e.gap as number]),
    [trace],
  );

  /* ---------------- iş yok: son sonucun özeti */
  if (!job) {
    if (!resultInfo) return null;
    const total = result?.times?.total_s;
    const pre = result?.precomputed as { time_limit_s?: number; wall_s?: number } | undefined;
    return (
      <Stack gap={4} data-testid="solve-progress" data-state="idle">
        <Group gap={6} wrap="wrap">
          <Tooltip label={SOURCE_HINT[resultInfo.source]}>
            <Text size="sm" fw={600} data-testid={resultInfo.source === 'cache' ? 'from-cache' : undefined}>
              Sonuç: {SOURCE_TEXT[resultInfo.source]}
            </Text>
          </Tooltip>
          {resultInfo.stopped && <SolveStatusBadge status="durduruldu" label="durduruldu — en iyi bulunan" size="sm" />}
        </Group>
        {result && (
          <Text size="sm" c="dimmed">
            {pre?.wall_s ? `önceden uzun süreyle çözüldü (çözüm süresi ${fmtDuration(pre.wall_s)})` : total != null ? `çözüm süresi ${fmtDuration(total)}` : ''}
            {result.bounds.gap != null ? ` · fark ${fmtGap(result.bounds.gap)}` : ''}
            {result.target_gap != null ? ` (hedef ${fmtGap(result.target_gap)})` : ''}
            {result.time_limit_s ? ` · süre sınırı ${fmtInt(result.time_limit_s)} sn` : ''}
          </Text>
        )}
        {resultInfo.stopped && resultInfo.stopReason && (
          <Text size="xs" style={{ color: 'var(--mantine-color-orange-text)' }}>
            {resultInfo.stopReason}
          </Text>
        )}
      </Stack>
    );
  }

  /* ---------------- iş sürüyor */
  const elapsed = Math.max(0, (now - job.startedAt) / 1000);
  const p = job.progress;
  const lbEv = lastWith(job.trace, (e) => e.lb != null);
  const ubEv = lastWith(job.trace, (e) => e.ub != null);
  const gapEv = lastWith(job.trace, (e) => e.gap != null);
  const lb = lbEv?.lb ?? null;
  const ub = ubEv?.ub ?? null;
  const gap = gapEv?.gap ?? null;
  const running = job.phase === 'running' || job.phase === 'cancelling';
  const frac = timeLimitS && running ? elapsed / timeLimitS : null;
  const overtime = frac !== null && frac > 1;
  const cur = stepIndex(p?.phase);
  const phaseText = job.phase === 'running' && p?.phase_tr ? p.phase_tr : PHASE_TEXT[job.phase];
  const gapOk = gap != null && targetGap != null && gap <= targetGap;

  return (
    <Stack gap={compact ? 4 : 6} data-testid="solve-progress" data-state={job.phase} aria-live="polite" aria-busy="true">
      {/* üstten hizalı: aşama adı 1–2 satır arasında değişince İptal düğmesi yerinde kalsın (tıklanabilir) */}
      <Group justify="space-between" gap="xs" wrap="nowrap" align="flex-start">
        <Group gap={8} wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
          <Loader size="xs" type="oval" aria-hidden style={{ marginTop: 3, flexShrink: 0 }} />
          <Text size="sm" fw={650} style={{ minWidth: 0 }} lineClamp={2}>
            {phaseText}
            {job.phase === 'queued' && job.position ? ` (önünde ${job.position} iş)` : ''}
          </Text>
        </Group>
        <Button
          size="compact-sm"
          variant="light"
          color="red"
          leftSection={<IconStop size={13} />}
          onClick={onCancel}
          disabled={job.phase === 'cancelling'}
          aria-label="İptal"
          style={{ flexShrink: 0 }}
        >
          İptal
        </Button>
      </Group>

      {!compact && job.phase !== 'debounce' && (
        <div className="sp-steps" aria-label="Çözüm aşamaları">
          {STEPS.filter((s) => !s.optional || job.trace.some((e) => stepIndex(e.phase) === STEPS.indexOf(s))).map((s, i, arr) => {
            const idx = STEPS.indexOf(s);
            const state = cur < 0 ? 'todo' : idx < cur ? 'done' : idx === cur ? 'current' : 'todo';
            return (
              <span key={s.id} className="sp-step" data-state={state} aria-current={state === 'current' ? 'step' : undefined}>
                {state === 'done' ? '✓ ' : ''}
                {s.label}
                {i < arr.length - 1 ? ' ›' : ''}
              </span>
            );
          })}
        </div>
      )}

      <Progress
        value={frac === null ? 100 : Math.min(1, frac) * 100}
        animated={frac === null || overtime}
        striped={frac === null || overtime}
        size="md"
        color={overtime ? 'orange' : undefined}
        aria-label="Süre sınırına göre geçen süre"
      />
      <Group justify="space-between" gap="xs">
        <Text size="xs" c="dimmed" style={{ fontVariantNumeric: 'tabular-nums' }}>
          Geçen {fmtDuration(elapsed)}
          {timeLimitS ? ` / süre sınırı ${fmtInt(timeLimitS)} sn` : ''}
        </Text>
        {overtime && (
          <Text size="xs" style={{ color: 'var(--mantine-color-orange-text)' }}>
            son işlemler (denetim, dual değerler)
          </Text>
        )}
        {job.reconnects > 0 && (
          <Text size="xs" style={{ color: 'var(--mantine-color-orange-text)' }}>
            bağlantı yeniden kuruldu ({job.reconnects})
          </Text>
        )}
      </Group>

      {(lb != null || ub != null) && (
        <div className="sp-stats">
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Alt sınır L
            </Text>
            <Text size="sm" fw={600}>
              {lb != null ? fmtBillion(lb) : '—'}
            </Text>
          </Stack>
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Bulunan U
            </Text>
            <Text size="sm" fw={600}>
              {ub != null ? fmtBillion(ub) : '—'}
            </Text>
          </Stack>
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Fark (U−L)/U
            </Text>
            <Text size="sm" fw={700} c={gapOk ? 'teal.9' : undefined}>
              {fmtGap(gap)}
              {gapOk ? ' ✓' : ''}
            </Text>
          </Stack>
        </div>
      )}
      {!compact && gapPoints.length >= 2 && (
        <Stack gap={0}>
          <Sparkline points={gapPoints} height={38} reference={targetGap ?? null} log ariaLabel={`Fark zamanla: şu an ${fmtGap(gap)}`} />
          <Text size="xs" c="dimmed">
            Fark (log ölçek) zamanla{targetGap != null ? ` · ince çizgi: hedef ${fmtGap(targetGap)}` : ''}
          </Text>
        </Stack>
      )}
    </Stack>
  );
}
