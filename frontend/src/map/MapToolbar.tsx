/**
 * Harita araç çubuğu (sağ üst): araç seçimi — Seç (kartlar) · Zorunlu kıl (+) · Yasakla (×) —, 3B eğim, Ankara'ya
 * sığdır, PNG indir. Kilit araçları çevrimdışıyken kapalı (kilit yeniden çözüm ister). Klavye: Esc → Seç aracı.
 */
import { ActionIcon, Paper, Tooltip } from '@mantine/core';

import { IconDownload, IconFit, IconPlusCell, IconPointer, IconTilt, IconXCell } from './icons.tsx';
import type { MapTool } from './layers/types.ts';

export interface MapToolbarProps {
  tool: MapTool;
  onTool(t: MapTool): void;
  canLock: boolean;
  tilted: boolean;
  onTilt(): void;
  onFit(): void;
  onExport(): void;
  exporting?: boolean;
  /** çevrimdışı bandı haritanın üstünü kaplarken aşağı kaydır */
  top?: number;
}

const TOOLS: { id: MapTool; label: string; hint: string; icon: React.ReactNode; color?: string }[] = [
  { id: 'select', label: 'Seç', hint: 'Hastaneye ya da hücreye tıklayınca kartı açılır (Esc: seçimi kaldır)', icon: <IconPointer /> },
  { id: 'lockOpen', label: 'Hastane zorunlu', hint: "Tıkladığınız hücrede hastane ZORUNLU olsun ('aç' kilidi); kalanı yeniden optimize edilir", icon: <IconPlusCell />, color: 'green' },
  { id: 'lockClosed', label: 'Hastane yasak', hint: 'Tıkladığınız hücrede hastane YASAK olsun; kalanı yeniden optimize edilir', icon: <IconXCell />, color: 'red' },
];

export function MapToolbar({ tool, onTool, canLock, tilted, onTilt, onFit, onExport, exporting, top = 10 }: MapToolbarProps) {
  return (
    <div className="map-toolbar" data-export="skip" style={{ position: 'absolute', top, right: 10, zIndex: 4, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Paper shadow="sm" radius="md" withBorder p={3}>
        <ActionIcon.Group orientation="vertical">
          {TOOLS.map((t) => {
            const disabled = t.id !== 'select' && !canLock;
            return (
              <Tooltip key={t.id} position="left" label={disabled ? `${t.label}: çevrimdışı modda kilit eklenemez (canlı çözüm kapalı)` : `${t.label} — ${t.hint}`}>
                <ActionIcon
                  size="lg"
                  variant={tool === t.id ? 'filled' : 'subtle'}
                  color={t.color ?? undefined}
                  onClick={() => onTool(t.id)}
                  disabled={disabled}
                  aria-label={t.label}
                  aria-pressed={tool === t.id}
                  data-testid={`map-tool-${t.id}`}
                >
                  {t.icon}
                </ActionIcon>
              </Tooltip>
            );
          })}
        </ActionIcon.Group>
      </Paper>
      <Paper shadow="sm" radius="md" withBorder p={3}>
        <ActionIcon.Group orientation="vertical">
          <Tooltip position="left" label={tilted ? 'Düz görünüme dön (kuzey yukarı)' : '3B görünüm: haritayı eğ (sağ tıkla sürükleyerek döndürün)'}>
            <ActionIcon size="lg" variant={tilted ? 'filled' : 'subtle'} onClick={onTilt} aria-label="3B görünüm" aria-pressed={tilted} data-testid="map-tilt">
              <IconTilt />
            </ActionIcon>
          </Tooltip>
          <Tooltip position="left" label="Ankara'nın tamamına sığdır">
            <ActionIcon size="lg" variant="subtle" onClick={onFit} aria-label="Ankara'ya sığdır" data-testid="map-fit">
              <IconFit />
            </ActionIcon>
          </Tooltip>
          <Tooltip position="left" label="Haritayı PNG olarak indir (başlık ve özetle; lejant açıksa o da)">
            <ActionIcon size="lg" variant="subtle" onClick={onExport} loading={exporting} aria-label="Haritayı PNG olarak indir" data-testid="map-export">
              <IconDownload />
            </ActionIcon>
          </Tooltip>
        </ActionIcon.Group>
      </Paper>
    </div>
  );
}
