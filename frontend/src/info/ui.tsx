/**
 * Bilgi sekmelerinin ortak küçük bileşenleri: bölüm başlığı (çapalı), atlama düğmesi, kaynakça düğmesi,
 * yükleniyor / hata notları.
 */
import { Alert, Button, Group, Loader, Stack, Text, Title, Tooltip } from '@mantine/core';
import type { ReactNode } from 'react';

import { showSourceInBibliography } from '../panels/ParamInfo.tsx';
import { IconBook } from '../ui/icons.tsx';
import { JUMPS, jumpTo, type JumpId } from './jumps.ts';

export function InfoSection({ id, title, kicker, aside, children }: { id?: string; title: ReactNode; kicker?: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <Stack gap="xs" component="section" id={id} aria-labelledby={id ? `${id}-baslik` : undefined}>
      <Group justify="space-between" align="flex-end" gap="xs" wrap="wrap">
        <div style={{ minWidth: 0 }}>
          {kicker && <div className="info-kicker">{kicker}</div>}
          <Title order={3} id={id ? `${id}-baslik` : undefined} style={{ fontSize: '1.15rem', lineHeight: 1.3 }}>
            {title}
          </Title>
        </div>
        {aside}
      </Group>
      {children}
    </Stack>
  );
}

export function Prose({ children, size = 'sm', c }: { children: ReactNode; size?: 'xs' | 'sm' | 'md'; c?: string }) {
  return (
    <Text size={size} className="info-prose" c={c} component="div">
      {children}
    </Text>
  );
}

/** Sayı vurgusu (kalın, eşit genişlikli rakamlar). */
export const N = ({ children }: { children: ReactNode }) => <span className="info-num">{children}</span>;

export function JumpButton({ id, variant = 'light', size = 'compact-sm', label }: { id: JumpId; variant?: 'light' | 'subtle' | 'default' | 'filled'; size?: string; label?: string }) {
  const j = JUMPS[id];
  return (
    <Tooltip label={j.hint} openDelay={350} multiline maw={300}>
      <Button size={size} variant={variant} onClick={() => void jumpTo(id)} data-jump={id} rightSection={<span aria-hidden>→</span>}>
        {label ?? j.label}
      </Button>
    </Tooltip>
  );
}

export function SourceButton({ id, title, compact }: { id: string; title?: string; compact?: boolean }) {
  return (
    <Tooltip label={title ? `${title} — Kaynakça sekmesinde göster` : 'Kaynakça sekmesinde göster'} openDelay={300} multiline maw={320}>
      <Button
        size="compact-xs"
        variant="subtle"
        leftSection={<IconBook size={13} />}
        onClick={() => showSourceInBibliography(id)}
        aria-label={`${title ?? id}: Kaynakça sekmesinde göster`}
        styles={{ label: { whiteSpace: 'normal', textAlign: 'left', lineHeight: 1.3 } }}
        style={{ height: 'auto', minHeight: 22, paddingBlock: 2 }}
      >
        {compact ? 'kaynak' : (title ?? id)}
      </Button>
    </Tooltip>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <Group gap="xs" py="sm">
      <Loader size="xs" />
      <Text size="sm" c="dimmed">
        {what} yükleniyor…
      </Text>
    </Group>
  );
}

export function LoadError({ what, error, file }: { what: string; error: string | null; file?: string }) {
  return (
    <Alert color="orange" variant="light" title={`${what} yüklenemedi`}>
      <Text size="sm">
        {error ?? 'Sunucuya ulaşılamadı.'}
        {file ? ` Veri dosyası: ${file}. Sunucu açılınca sayfa kendiliğinden yeniden dener.` : ' Sunucu açılınca sayfa kendiliğinden yeniden dener.'}
      </Text>
    </Alert>
  );
}
