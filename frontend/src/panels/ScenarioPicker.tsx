/**
 * Hazır senaryolar (şartname 6.3): Varsayılan · Düşük bütçe · Erişim öncelikli · Sadece genel hastane ·
 * Şehir hastanesi serbest · 2030 nüfusu (meta.scenarios).
 *
 * Radyo kartları (klavye: oklarla gezinme, Enter/Boşluk seçer). Her kartta bu ızgarada önceden çözülmüş mü (●) ve
 * canlı çözümün önerilen sürede fark ≤ %5 bulup bulmadığı (canlı ✓) yazar. Seçili senaryonun açıklaması, gerekçesi
 * (bilgi kutusu), önceden çözülmüş sonucunun özeti ve uyarıları altta. Kullanıcı senaryodan sonra parametre
 * değiştirdiyse "değiştirildi" rozeti; senaryoya tıklamak değişiklikleri siler (senaryonun değerlerine döner).
 */
import { Badge, Group, Spoiler, Stack, Text, Tooltip, VisuallyHidden } from '@mantine/core';
import { useRef, type KeyboardEvent } from 'react';

import type { GridKm, ScenarioMeta } from '../api/types.ts';
import { ParamStatusBadge } from '../components/StatusBadges.tsx';
import { fmtBillion, fmtGap, fmtInt } from '../format/number.ts';
import { InfoPopover } from '../ui/InfoPopover.tsx';
import './panels.css';

export interface ScenarioPickerProps {
  scenarios: ScenarioMeta[];
  /** seçili senaryo kimliği */
  value: string;
  /** kullanıcı senaryodan sonra parametre değiştirdi mi */
  modified: boolean;
  gridKm: GridKm;
  onChange(id: string): void;
  disabled?: boolean;
}

const GRIDS: GridKm[] = [2, 3, 5];

function gridAvailability(s: ScenarioMeta) {
  return GRIDS.map((km) => ({ km, pre: s.precomputed_grids.includes(km), live: s.live_grids.includes(km) }));
}

function overridesText(s: ScenarioMeta): string {
  const keys = Object.keys(s.overrides);
  return keys.length ? keys.join(', ') : 'değişiklik yok (kayıttaki değerler)';
}

export function ScenarioPicker({ scenarios, value, modified, gridKm, onChange, disabled }: ScenarioPickerProps) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = scenarios.find((s) => s.id === value);
  const pre = current?.grids[String(gridKm)]?.precomputed ?? null;
  const live = current?.grids[String(gridKm)]?.live_check ?? null;

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const n = scenarios.length;
    const cols = 2;
    let j = -1;
    if (e.key === 'ArrowRight') j = (i + 1) % n;
    else if (e.key === 'ArrowLeft') j = (i - 1 + n) % n;
    else if (e.key === 'ArrowDown') j = Math.min(n - 1, i + cols);
    else if (e.key === 'ArrowUp') j = Math.max(0, i - cols);
    if (j >= 0) {
      e.preventDefault();
      refs.current[j]?.focus();
      onChange(scenarios[j].id);
    }
  };

  return (
    <Stack gap={6} data-testid="scenario-picker">
      <Group justify="space-between" gap="xs" wrap="nowrap">
        <Text size="sm" fw={600} id="scenario-picker-label">
          Hazır senaryo
        </Text>
        {modified && (
          <Tooltip label="Senaryodan sonra parametre değiştirildi. Senaryo kartına tıklamak değişiklikleri siler.">
            <Badge size="sm" variant="light" color="orange" tt="none">
              değiştirildi
            </Badge>
          </Tooltip>
        )}
      </Group>
      <div className="sc-grid" role="radiogroup" aria-labelledby="scenario-picker-label">
        {scenarios.map((s, i) => {
          const checked = s.id === value;
          const av = gridAvailability(s).find((g) => g.km === gridKm)!;
          return (
            <Tooltip
              key={s.id}
              openDelay={500}
              label={
                <span>
                  <b>{s.title}</b>: {s.description_tr.length > 260 ? `${s.description_tr.slice(0, 259)}…` : s.description_tr}
                </span>
              }
            >
              <button
                ref={(el) => {
                  refs.current[i] = el;
                }}
                type="button"
                role="radio"
                aria-checked={checked}
                tabIndex={checked || (!scenarios.some((x) => x.id === value) && i === 0) ? 0 : -1}
                className="sc-card"
                disabled={disabled}
                onClick={() => onChange(s.id)}
                onKeyDown={(e) => onKey(e, i)}
                data-scenario={s.id}
              >
                <span className="sc-title">{s.title}</span>
                <VisuallyHidden>
                  {av.pre ? `${gridKm} km için önceden çözülmüş sonuç var` : `${gridKm} km için önceden çözülmüş sonuç yok`}
                  {av.live ? ', canlı çözüm önerilen sürede çalışıyor' : ''}
                </VisuallyHidden>
                <span className="sc-meta">
                  {av.pre ? '● önbellekte' : '○ canlı çözülür'}
                </span>
              </button>
            </Tooltip>
          );
        })}
      </div>
      {current && (
        <Stack gap={4}>
          <Group gap={6} wrap="wrap">
            <InfoPopover label={`${current.title} senaryosu: açıklama ve gerekçe`} size="xs">
              <Stack gap="xs">
                <Text fw={700}>{current.title}</Text>
                <Text size="sm" style={{ lineHeight: 1.4 }}>
                  {current.description_tr}
                </Text>
                {current.rationale && (
                  <Stack gap={2}>
                    <Text size="xs" fw={700} c="dimmed" tt="uppercase">
                      Gerekçe
                    </Text>
                    <Text size="sm" style={{ lineHeight: 1.4 }}>
                      {current.rationale}
                    </Text>
                  </Stack>
                )}
                {current.derivation && (
                  <Stack gap={2}>
                    <Text size="xs" fw={700} c="dimmed" tt="uppercase">
                      Değerin dayanağı
                    </Text>
                    <Spoiler maxHeight={120} showLabel="devamı" hideLabel="kısalt">
                      <Text size="xs" style={{ lineHeight: 1.45 }}>
                        {current.derivation}
                      </Text>
                    </Spoiler>
                  </Stack>
                )}
                <Group gap={6}>
                  <ParamStatusBadge status={current.status} size="xs" />
                  <Text size="xs" c="dimmed">
                    Değiştirdiği parametreler: {overridesText(current)}
                  </Text>
                </Group>
                {current.warnings.map((w, i) => (
                  <Text key={i} size="xs" style={{ color: 'var(--mantine-color-orange-text)' }}>
                    {w}
                  </Text>
                ))}
              </Stack>
            </InfoPopover>
            {gridAvailability(current).map((g) => (
              <Tooltip
                key={g.km}
                label={`${g.km} km: ${g.pre ? 'önceden uzun süreyle çözüldü (aynı istek önbellekten anında gelir)' : 'önceden çözülmüş sonuç yok'}; ${g.live ? 'canlı çözüm önerilen sürede fark ≤ %5 buldu' : 'canlı çözüm önerilen sürede fark ≤ %5’e ulaşamadı'}.`}
              >
                <Badge size="sm" variant={g.km === gridKm ? 'outline' : 'light'} color={g.pre ? 'teal' : 'gray'} tt="none" style={{ cursor: 'help' }}>
                  {g.km} km {g.pre ? '●' : '○'}
                </Badge>
              </Tooltip>
            ))}
          </Group>
          <Spoiler maxHeight={40} showLabel="devamı" hideLabel="kısalt" styles={{ control: { fontSize: 'var(--mantine-font-size-xs)' } }}>
            <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
              {current.description_tr}
            </Text>
          </Spoiler>
          {pre && (
            <Text size="xs" c="dimmed">
              Önceden çözülmüş ({gridKm} km, {fmtInt(pre.time_limit_s)} sn): {fmtInt(pre.n_hospitals)} hastane · {current.mode === 'butce' ? 'erişim maliyeti' : 'toplam'} {fmtBillion(pre.ub_tl)} · fark {fmtGap(pre.gap)}
              {live && live.t_to_1pct_s != null ? ` · canlı çözüm %1 farka ${live.t_to_1pct_s.toLocaleString('tr-TR', { maximumFractionDigits: 1 })} sn’de ulaştı` : ''}
            </Text>
          )}
        </Stack>
      )}
    </Stack>
  );
}

export default ScenarioPicker;
