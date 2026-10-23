/**
 * "Kaç hastane? En az / en fazla" kartı (hocanın 5. maddesi "ızgaraya kaç hastane sığar"; şartname 7.3 hazırlığı).
 *
 *   - |J| (aday hücre sayısı) ve kapasite alt sınırı ⌈toplam yatak talebi / en büyük izinli hastane⌉ her zaman
 *     GÖSTERİLEN çözümün kendi ayarlarından hesaplanır (result.instance: aday sayısı, talep, sınıfların yatak üst
 *     sınırları ve açık/kapalı durumu) — senaryo değişince de doğrudur (ör. "Sadece genel hastane": ⌈18.617,5 / 999⌉ = 19).
 *   - Kapsama (küme kapsama, LSCP) ve gerçek en az (p_min) ek bir optimizasyon ister: varsayılan parametrelerde
 *     önceden uzun süreyle hesaplanmış kart (GET /api/bounds/{km}); başka ayarlarda bu satırlar soluk ve
 *     "varsayılan parametrelerle" etiketlidir, "Bu ayarlarla hesapla" düğmesi POST /api/bounds işini başlatır
 *     (≈20 sn, iptal edilebilir; tek çözücü işçisini meşgul etmesin diye yeni bir çözüm başlayınca kendiliğinden
 *     iptal edilir).
 */
import { Badge, Button, Group, Loader, Paper, Stack, Text, Tooltip } from '@mantine/core';
import { useEffect, useRef, useState } from 'react';

import { getBoundsCards } from '../api/client.ts';
import { bounds as runBounds, type JobHandle } from '../api/jobs.ts';
import type { BoundsRequest, BoundsResult, BoundStep, GridKm, SolveResultOk } from '../api/types.ts';
import { fmtDuration, fmtInt, fmtNumber } from '../format/number.ts';
import { useApp } from '../store/appStore.ts';
import { CLIENT_ID } from '../store/solver.ts';

/* ------------------------------------------------------------------------------------------------ önbellekler */

const defaultCards = new Map<GridKm, Promise<BoundsResult | null>>();
function loadDefaultCard(km: GridKm) {
  let p = defaultCards.get(km);
  if (!p) {
    p = getBoundsCards(km).catch(() => {
      defaultCards.delete(km);
      return null;
    });
    defaultCards.set(km, p);
  }
  return p;
}

/** Bu oturumda "Bu ayarlarla hesapla" ile bulunan kartlar (anahtar: sonucun problem anahtarı). */
const liveCards = new Map<string, BoundsResult>();

/* ------------------------------------------------------------------------------------------------ hesaplar */

/** Sonucun kendi ayarlarından kapasite alt sınırı (backend/model/bounds.py capacity_bound ile aynı formül). */
export function capacityFromResult(r: SolveResultOk): { value: number; demand: number; maxBeds: number; cityOn: boolean } | null {
  const inst = r.instance;
  const demand = inst?.total_demand_beds ?? r.kpis.total_demand_beds;
  const classes = inst?.classes?.filter((c) => c.enabled) ?? [];
  if (!classes.length || !(demand > 0)) return null;
  const maxBeds = Math.max(...classes.map((c) => c.U));
  return { value: Math.ceil(demand / maxBeds - 1e-9), demand, maxBeds, cityOn: classes.some((c) => c.code === 'V-D') };
}

function stepText(st: BoundStep | undefined): string {
  if (!st || st.value == null) return '—';
  if (st.proven) return `${fmtInt(st.value)} ✓`;
  if (st.lower_bound != null && st.lower_bound !== st.value) return `${fmtInt(st.lower_bound)}–${fmtInt(st.value)}`;
  return `≤ ${fmtInt(st.value)}`;
}

function stepHint(st: BoundStep | undefined, what: string): string {
  if (!st || st.value == null) return `${what}: hesaplanamadı.`;
  if (st.proven) return `${what}: kanıtlı.`;
  return `${what}: kanıtlanamadı — en az ${fmtInt(st.lower_bound)}, bulunan ${fmtInt(st.value)}.`;
}

/* ------------------------------------------------------------------------------------------------ bileşen */

export interface BoundsBlockProps {
  r: SolveResultOk;
  gridKm: GridKm;
  online: boolean;
  /** gösterilen sonucun problem anahtarı (resultInfo.problemKey) */
  resultKey: string | null;
  /** sonuç varsayılan parametrelerle mi (yalnız ızgara farklı olabilir) */
  isDefault: boolean;
  /** sonuç güncel problemden eski mi (o zaman "hesapla" kapalı: istek güncel problemi gönderirdi) */
  stale: boolean;
}

interface Running {
  key: string;
  handle: JobHandle<BoundsResult>;
  startedAt: number;
}

export function BoundsBlock({ r, gridKm, online, resultKey, isDefault, stale }: BoundsBlockProps) {
  const [card, setCard] = useState<BoundsResult | null>(null);
  const [live, setLive] = useState<BoundsResult | null>(resultKey ? liveCards.get(resultKey) ?? null : null);
  const [running, setRunning] = useState<Running | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const runningRef = useRef<Running | null>(null);
  runningRef.current = running;
  const solving = useApp((s) => !!s.job && s.job.phase !== 'debounce');

  // varsayılan kart (önceden hesaplı) — ızgara değişince
  useEffect(() => {
    if (!online) return;
    let alive = true;
    void loadDefaultCard(gridKm).then((c) => alive && setCard(c));
    return () => {
      alive = false;
    };
  }, [gridKm, online]);

  // gösterilen sonuç değişince o sonucun canlı kartı (varsa)
  useEffect(() => {
    setLive(resultKey ? liveCards.get(resultKey) ?? null : null);
    setErr(null);
  }, [resultKey]);

  // yeni çözüm başlayınca sınır işi iptal (tek işçi: çözüm kuyrukta beklemesin)
  useEffect(() => {
    if (solving && runningRef.current) void runningRef.current.handle.cancel();
  }, [solving]);

  // bileşen kalkınca çalışan işi bırak
  useEffect(
    () => () => {
      runningRef.current?.handle.detach();
    },
    [],
  );

  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [running]);

  const run = () => {
    if (!resultKey || running) return;
    const p = useApp.getState().problem;
    const req: BoundsRequest = { grid_km: p.gridKm, overrides: p.overrides, client_id: CLIENT_ID, replace: true, time_limit: 20 };
    if (p.locks.open.length || p.locks.closed.length) req.locks = p.locks;
    if (p.allowOutsideRange) req.allow_outside_range = true;
    const handle = runBounds(req);
    const me: Running = { key: resultKey, handle, startedAt: Date.now() };
    setErr(null);
    setRunning(me);
    void handle.done.then((out) => {
      if (runningRef.current !== me) return;
      setRunning(null);
      if ((out.kind === 'done' || out.kind === 'cancelled') && out.result) {
        liveCards.set(me.key, out.result);
        setLive(out.result);
        if (out.kind === 'cancelled') setErr('Hesap durduruldu (yeni çözüm başladı ya da iptal edildi): kanıtlanamayan satırlar aralık olarak gösteriliyor.');
      } else if (out.kind === 'cancelled') {
        setErr('Hesap durduruldu.');
      } else if (out.kind === 'failed') {
        setErr(out.message);
      }
    });
  };

  const cap = capacityFromResult(r);
  const nCand = r.instance?.J ?? r.layers?.candidates?.cell_id.length ?? null;
  // kapsama ve p_min: canlı kart > (varsayılan parametrelerse) önceden hesaplı kart > soluk varsayılan kart
  const src: 'live' | 'default' | 'other' = live ? 'live' : isDefault ? 'default' : 'other';
  const ext = live ?? card;
  const dimExt = src === 'other';
  const pm = ext?.p_min;
  const lscp = ext?.coverage_lscp;
  const n = r.kpis.n_hospitals;

  const rows: { label: string; value: string; hint: string; dim?: boolean; testId?: string }[] = [
    {
      label: 'Izgaraya sığan en fazla (aday hücre |J|)',
      value: fmtInt(nCand),
      hint: 'Hastane açılabilen hücre sayısı (her hücrede en çok 1 hastane): bu çözümün ayarlarıyla.',
      testId: 'bound-max',
    },
    {
      label: 'Kapasite alt sınırı',
      value: cap ? fmtInt(cap.value) : '—',
      hint: cap
        ? `⌈toplam yatak talebi / en büyük izinli hastane⌉ = ⌈${fmtNumber(cap.demand, 1)} / ${fmtInt(cap.maxBeds)}⌉ = ${fmtInt(cap.value)}${cap.cityOn ? ' (şehir hastanesi açık)' : ' (şehir hastanesi kapalı: en büyük genel hastane)'}; bu çözümün ayarlarıyla.`
        : 'Hesaplanamadı.',
      testId: 'bound-capacity',
    },
    {
      label: 'Kapsama alt sınırı (R içinde, küme kapsama)',
      value: stepText(lscp),
      hint: `${stepHint(lscp, 'Her nüfuslu hücreye R içinde en az bir hastane için gereken en az sayı')}${dimExt ? ' Varsayılan parametrelerle hesaplandı; bu ayarlarda farklı olabilir.' : ''}`,
      dim: dimExt,
      testId: 'bound-lscp',
    },
    {
      label: 'Gerçek en az (tüm kısıtlarla p_min)',
      value: stepText(pm),
      hint: `${stepHint(pm, 'Kapasite, sınıf, R ve kilitlerle olurlu en az hastane sayısı')}${dimExt ? ' Varsayılan parametrelerle hesaplandı; bu ayarlarda farklı olabilir.' : ''}`,
      dim: dimExt,
      testId: 'bound-pmin',
    },
  ];

  // p_min kanıtlanamadıysa gerçek en az [alt sınır, bulunan] aralığındadır: karşılaştırma üst uçla yapılır
  const pmin = !dimExt && pm?.value != null ? pm.value : null;
  const pminText = pm && pmin !== null ? (pm.proven || pm.lower_bound == null || pm.lower_bound === pm.value ? fmtInt(pmin) : `en çok ${fmtInt(pmin)}`) : '';
  const note =
    pmin === null
      ? 'Optimum sayıyı alt sınırlarla karşılaştırın: sayı alt sınırların çok üstündeyse onu kısıtlar değil, açılış ve erişim maliyetinin dengesi belirler (U eğrisi).'
      : n >= pmin + 3
        ? `Optimum (${fmtInt(n)}) gerçek en azın (${pminText}) çok üstünde: hastane sayısını kapasite ya da kapsama değil, açılış ve erişim maliyetinin dengesi belirliyor.`
        : `Optimum (${fmtInt(n)}) gerçek en aza (${pminText}) yakın: hastane sayısını büyük ölçüde kısıtlar (kapasite, R, kilitler) belirliyor.`;

  return (
    <Paper withBorder radius="md" p="sm" data-testid="bounds-block" data-source={src}>
      <Stack gap={6}>
        <Group justify="space-between" gap="xs" wrap="wrap">
          <Text size="sm" fw={650}>
            Kaç hastane? En az / en fazla
          </Text>
          {src === 'other' && (
            <Tooltip label="Kapsama ve p_min satırları varsayılan parametrelerle önceden hesaplandı; |J| ve kapasite bu çözümün ayarlarıyla.">
              <Badge size="sm" variant="light" color="gray" tt="none" style={{ flexShrink: 0 }}>
                kısmen varsayılan
              </Badge>
            </Tooltip>
          )}
          {src === 'live' && (
            <Badge size="sm" variant="light" color="teal" tt="none" style={{ flexShrink: 0 }}>
              bu ayarlarla
            </Badge>
          )}
        </Group>
        <div className="rp-kv">
          {rows.flatMap((row) => [
            <Tooltip key={`l-${row.label}`} label={row.hint} multiline maw={340}>
              <Text size="sm" c="dimmed" style={{ cursor: 'help', opacity: row.dim ? 0.55 : 1 }}>
                {row.label}
                {row.dim ? ' *' : ''}
              </Text>
            </Tooltip>,
            <Text key={`v-${row.label}`} size="sm" data-testid={row.testId} data-dim={row.dim || undefined} style={{ opacity: row.dim ? 0.55 : 1 }}>
              {row.value}
            </Text>,
          ])}
          <Text size="sm" fw={650}>
            Bu çözüm
          </Text>
          <Text size="sm" fw={750}>
            {fmtInt(n)}
          </Text>
        </div>
        {dimExt && (
          <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
            * varsayılan parametrelerle (bu ayarlarda farklı olabilir).
          </Text>
        )}
        {src !== 'live' && !isDefault && online && (
          <Stack gap={4}>
            <Button
              style={{ alignSelf: 'flex-start' }}
              size="compact-sm"
              variant="light"
              onClick={running ? () => void running.handle.cancel() : run}
              disabled={!running && (stale || solving || !resultKey)}
              data-testid="bounds-run"
              leftSection={running ? <Loader size={12} /> : undefined}
              color={running ? 'red' : undefined}
            >
              {running ? 'Durdur' : 'Bu ayarlarla hesapla'}
            </Button>
            <Text size="xs" c="dimmed" style={{ lineHeight: 1.35 }}>
              {running
                ? `Kapsama ve p_min hesaplanıyor… ${fmtDuration((now - running.startedAt) / 1000)} / ≈20 sn`
                : stale || solving
                  ? 'Önce çözüm bitsin.'
                  : 'Kapsama ve p_min bu ayarlarla (≈20 sn).'}
            </Text>
          </Stack>
        )}
        {err && (
          <Text size="xs" style={{ color: 'var(--mantine-color-orange-text)', lineHeight: 1.35 }}>
            {err}
          </Text>
        )}
        <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
          {note} (✓ = kanıtlı)
        </Text>
      </Stack>
    </Paper>
  );
}

export default BoundsBlock;
