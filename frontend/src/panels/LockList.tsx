/**
 * Kilitler (şartname 6.5 "kilitle / yasakla"): 'aç' kilidi o hücrede hastaneyi zorunlu kılar, 'yasak' kilidi
 * hastane açılmasını engeller; kalan her şey yeniden optimize edilir (kilit değişince otomatik çözüm). Liste hücre
 * kimliği + ilçe; satıra tıklayınca hücre kartı açılır ve harita oraya uçar. Kilitler ızgaraya bağlıdır (ızgara
 * değişince silinir). "Manuel mod" (Faz 7) burada yer tutucu olarak durur.
 */
import { ActionIcon, Badge, Button, Group, Paper, Stack, Switch, Text, Tooltip } from '@mantine/core';

import type { GridCells, Locks } from '../api/types.ts';
import { gridIndexOf } from '../map/gridIndex.ts';
import { getMapApi } from '../map/mapApi.ts';
import type { Selection } from '../store/appStore.ts';
import { IconBan, IconLock, IconTarget, IconUnlock } from '../ui/icons.tsx';

export interface LockListProps {
  locks: Locks;
  grid: GridCells | null;
  gridKm: number;
  disabled?: boolean;
  onRemove(cellId: string): void;
  onClear(): void;
  onSelect(sel: Selection): void;
  manualMode: boolean;
  onManualMode?(v: boolean): void;
}

export function LockList({ locks, grid, gridKm, disabled, onRemove, onClear, onSelect, manualMode, onManualMode }: LockListProps) {
  const idx = grid ? gridIndexOf(grid, gridKm) : null;
  const rows = [...locks.open.map((c) => ({ cellId: c, kind: 'open' as const })), ...locks.closed.map((c) => ({ cellId: c, kind: 'closed' as const }))];
  const n = rows.length;

  const focus = (cellId: string) => {
    const gi = idx?.byId.get(cellId);
    if (gi === undefined || !idx) return;
    onSelect({ kind: 'cell', gridIndex: gi, cellId });
    const [lon, lat] = idx.centroid(gi);
    getMapApi()?.flyTo(lon, lat, 10);
  };

  return (
    <Paper withBorder radius="md" p="sm" data-testid="lock-list">
      <Stack gap={6}>
        <Group justify="space-between" gap="xs" wrap="nowrap">
          <Text size="sm" fw={600}>
            Kilitli / yasak hücreler
          </Text>
          <Badge size="sm" variant={n ? 'filled' : 'light'} color={n ? 'ocean' : 'gray'} tt="none">
            {n}
          </Badge>
        </Group>
        {n === 0 ? (
          <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
            Haritada bir hücreye ya da hastaneye tıklayıp kartındaki “Hastane zorunlu” / “Yasakla” düğmeleriyle kilit
            ekleyin; model kalanını yeniden optimize eder.
          </Text>
        ) : (
          <Stack gap={2} role="list" aria-label="Kilitli hücreler">
            {rows.map((r) => {
              const gi = idx?.byId.get(r.cellId);
              const ilce = gi !== undefined ? grid?.features[gi]?.properties.ilce : undefined;
              return (
                <Group key={`${r.kind}-${r.cellId}`} gap={6} wrap="nowrap" role="listitem">
                  <Tooltip label={r.kind === 'open' ? 'Hastane zorunlu (aç kilidi)' : 'Hastane yasak'}>
                    <Badge
                      size="sm"
                      variant="light"
                      color={r.kind === 'open' ? 'ocean' : 'red'}
                      leftSection={r.kind === 'open' ? <IconLock size={12} /> : <IconBan size={12} />}
                      tt="none"
                      style={{ flexShrink: 0 }}
                    >
                      {r.kind === 'open' ? 'zorunlu' : 'yasak'}
                    </Badge>
                  </Tooltip>
                  <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
                    <Text size="xs" truncate="end">
                      {ilce ?? '—'}
                    </Text>
                    <Text size="xs" c="dimmed" ff="monospace" truncate="end">
                      {r.cellId}
                    </Text>
                  </Stack>
                  <ActionIcon size="sm" variant="subtle" color="gray" aria-label={`${r.cellId}: hücreyi göster`} onClick={() => focus(r.cellId)}>
                    <IconTarget size={14} />
                  </ActionIcon>
                  <ActionIcon size="sm" variant="subtle" color="gray" aria-label={`${r.cellId}: kilidi kaldır`} onClick={() => onRemove(r.cellId)} disabled={disabled}>
                    <IconUnlock size={14} />
                  </ActionIcon>
                </Group>
              );
            })}
            <Button size="compact-xs" variant="subtle" color="red" onClick={onClear} disabled={disabled} mt={2} style={{ alignSelf: 'flex-start' }}>
              Tüm kilitleri kaldır
            </Button>
          </Stack>
        )}
        <Tooltip label="Hazırlanıyor: haritaya tıklayarak hastane ekle/kaldır/boyutlandır; /api/evaluate maliyeti ve “optimumdan %X pahalı” farkını hesaplar.">
          <div>
            <Switch
              size="sm"
              label="Manuel mod (yakında)"
              checked={manualMode}
              onChange={(e) => onManualMode?.(e.currentTarget.checked)}
              disabled
              data-testid="manual-mode-toggle"
            />
          </div>
        </Tooltip>
      </Stack>
    </Paper>
  );
}
