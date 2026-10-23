/**
 * Ortak rozetler: parametre durumu (kaynaklı / hesap / varsayım / model seçimi), çözüm durumu, bağlantı.
 * Renk tek başına anlam taşımaz: her rozette metin etiketi var (erişilebilirlik).
 */
import { Badge, Tooltip, type BadgeProps } from '@mantine/core';

import type { ParamStatus, SolveStatus } from '../api/types.ts';
import { useApp, type ConnectionInfo } from '../store/appStore.ts';
import { PARAM_STATUS_UI, SOLVE_STATUS_UI, STATUS_MANTINE, type StatusTone } from '../theme/palette.ts';

export function ParamStatusBadge({ status, ...rest }: { status: ParamStatus } & Omit<BadgeProps, 'color'>) {
  const ui = PARAM_STATUS_UI[status];
  return (
    <Tooltip label={ui.hint}>
      <Badge color={ui.color} variant="light" size="sm" tt="none" {...rest}>
        {ui.label}
      </Badge>
    </Tooltip>
  );
}

export function SolveStatusBadge({ status, label, tone, ...rest }: { status: SolveStatus; label?: string; tone?: StatusTone } & Omit<BadgeProps, 'color'>) {
  const ui = SOLVE_STATUS_UI[status] ?? { tone: 'neutral', label: status };
  const color = STATUS_MANTINE[tone ?? ui.tone];
  return (
    <Badge color={color} variant="light" size="md" tt="none" {...rest}>
      {label ?? ui.label}
    </Badge>
  );
}

/** Bağlantı rozeti; verilmezse bağlantı bilgisini depodan kendisi okur (sağlık yoklaması yalnız rozeti yeniden çizer). */
export function ConnectionBadge({ connection: given }: { connection?: ConnectionInfo }) {
  const fromStore = useApp((s) => s.connection);
  const connection = given ?? fromStore;
  if (connection.mode === 'online') {
    return (
      <Tooltip label="Yerel sunucu çalışıyor; parametre değişiklikleri canlı yeniden çözülür.">
        <Badge color={STATUS_MANTINE.good} variant="dot" size="md" tt="none" data-testid="connection-badge">
          canlı çözüm
        </Badge>
      </Tooltip>
    );
  }
  if (connection.mode === 'connecting') {
    return (
      <Badge color="gray" variant="dot" size="md" tt="none" data-testid="connection-badge">
        bağlanıyor…
      </Badge>
    );
  }
  return (
    <Tooltip label={connection.note ?? 'Sunucuya ulaşılamıyor; önceden çözülmüş sonuç gösteriliyor.'}>
      <Badge color={STATUS_MANTINE.critical} variant="dot" size="md" tt="none" data-testid="connection-badge">
        çevrimdışı mod
      </Badge>
    </Tooltip>
  );
}
