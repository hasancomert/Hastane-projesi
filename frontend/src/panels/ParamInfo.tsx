/**
 * Parametrenin bilgi kutusu içeriği (şartname 2, 4.4, 10: "her sayının kaynağı ya da 'varsayım' etiketi"):
 * anlamı (etiket + türetme), durum rozeti (kaynaklı · hesap · varsayım · model seçimi) ve anlamı, kaynak başlıkları
 * (dış bağlantı + Kaynakça sekmesine bağlantı), varsayım/model seçimi gerekçesi, notlar, kaydırıcı aralığı ve notu,
 * modelin tanım kümesi, alternatif değerler, yapı uyarısı. Tüm metin /api/meta'dan gelir.
 */
import { Anchor, Badge, Box, Button, Divider, Group, Spoiler, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import type { ParamMeta, ParamValue, SourceRef } from '../api/types.ts';
import { ParamStatusBadge } from '../components/StatusBadges.tsx';
import { fmtDate, fmtNumber, fmtParamValue } from '../format/number.ts';
import { useApp } from '../store/appStore.ts';
import { PARAM_STATUS_UI } from '../theme/palette.ts';
import { IconBook } from '../ui/icons.tsx';
import { choiceLabel, describeAllowed, fmtDisplay, numberSpec, type Relevance } from './paramUtils.ts';

const SOURCE_KIND_TR: Record<string, string> = {
  resmi: 'resmî',
  veri: 'veri',
  akademik: 'akademik',
  ikincil: 'ikincil',
  haber: 'haber',
  hesap: 'proje hesabı',
};

/**
 * Kaynakça sekmesini açar ve kaynağa kaydırır. Faz 6'nın Kaynakça sekmesi her kaynağa `id="kaynak-<id>"` verirse
 * doğrudan o satıra gider; yoksa sekmenin başına. Ayrıca `hastane:kaynak` olayı yayınlanır (vurgulama için).
 */
export function showSourceInBibliography(sourceId: string): void {
  useApp.getState().setUi({ infoTab: 'kaynakca' });
  window.dispatchEvent(new CustomEvent('hastane:kaynak', { detail: { id: sourceId } }));
  const go = (n: number) => {
    const el = document.getElementById(`kaynak-${sourceId}`) ?? (n <= 0 ? document.querySelector('[data-testid="info-tabs"]') : null);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    else setTimeout(() => go(n - 1), 120);
  };
  requestAnimationFrame(() => go(4));
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Stack gap={2}>
      <Text size="xs" fw={700} tt="uppercase" c="dimmed" style={{ letterSpacing: '0.03em' }}>
        {title}
      </Text>
      {children}
    </Stack>
  );
}

function SourceLine({ s }: { s: SourceRef }) {
  const http = /^https?:\/\//i.test(s.url);
  return (
    <Box>
      <Text size="sm" style={{ lineHeight: 1.35 }}>
        {http ? (
          <Anchor href={s.url} target="_blank" rel="noopener noreferrer" size="sm" title="Kaynağın kendisi (internet gerekir)">
            {s.title}
          </Anchor>
        ) : (
          s.title
        )}
      </Text>
      <Group gap={6} mt={2} wrap="wrap">
        <Badge size="xs" variant="outline" color="gray" tt="none">
          {SOURCE_KIND_TR[s.kind] ?? s.kind}
        </Badge>
        <Text size="xs" c="dimmed">
          {s.publisher}
          {s.accessed ? ` · erişim ${fmtDate(s.accessed)}` : ''}
          {!http && s.url ? ` · ${s.url}` : ''}
        </Text>
        <Button
          size="compact-xs"
          variant="subtle"
          leftSection={<IconBook size={13} />}
          onClick={() => showSourceInBibliography(s.id)}
          aria-label={`${s.title}: Kaynakça sekmesinde göster`}
        >
          Kaynakçada
        </Button>
      </Group>
    </Box>
  );
}

function valueText(p: ParamMeta, v: ParamValue | undefined): string {
  if (v === undefined) return '—';
  if (typeof v === 'number') {
    if (p.allowed.kind === 'number' || p.allowed.kind === 'integer') return fmtDisplay(numberSpec(p), v);
    return choiceLabel(p, v);
  }
  if (typeof v === 'string' || typeof v === 'boolean') return choiceLabel(p, v);
  return fmtParamValue(v, p.unit);
}

export interface ParamInfoProps {
  param: ParamMeta;
  value: ParamValue | undefined;
  /** seçili senaryonun değeri (kayıttakinden farklıysa gösterilir) */
  scenarioValue?: ParamValue;
  scenarioTitle?: string;
  relevance?: Relevance | null;
}

export function ParamInfo({ param: p, value, scenarioValue, scenarioTitle, relevance }: ParamInfoProps) {
  const statusUi = PARAM_STATUS_UI[p.status];
  const needsRationale = p.status === 'varsayim' || p.status === 'model_secimi';
  const spec = p.allowed.kind === 'number' || p.allowed.kind === 'integer' ? numberSpec(p) : undefined;
  return (
    <Stack gap="xs" data-testid="param-info">
      <Stack gap={2}>
        <Text fw={700} style={{ lineHeight: 1.3 }}>
          {p.label}
        </Text>
        <Text size="xs" c="dimmed" ff="monospace">
          {p.key} · {p.unit}
        </Text>
      </Stack>
      <Group gap={6} wrap="nowrap" align="flex-start">
        <ParamStatusBadge status={p.status} style={{ flexShrink: 0 }} />
        <Text size="xs" c="dimmed" style={{ lineHeight: 1.35 }}>
          {statusUi.hint}
        </Text>
      </Group>
      <Group gap="lg">
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Şu anki değer
          </Text>
          <Text size="sm" fw={650}>
            {valueText(p, value)}
          </Text>
        </Stack>
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Kayıttaki varsayılan
          </Text>
          <Text size="sm">{valueText(p, p.value)}</Text>
        </Stack>
        {scenarioValue !== undefined && (
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Senaryo ({scenarioTitle})
            </Text>
            <Text size="sm">{valueText(p, scenarioValue)}</Text>
          </Stack>
        )}
      </Group>
      {relevance && (
        <Text size="xs" style={{ color: relevance.active ? 'var(--mantine-color-dimmed)' : 'var(--mantine-color-orange-text)' }}>
          {relevance.note}
        </Text>
      )}
      {p.derivation && (
        <Section title="Nasıl hesaplandı">
          <Text size="sm" style={{ lineHeight: 1.4 }}>
            {p.derivation}
          </Text>
        </Section>
      )}
      {p.rationale && (
        <Section title={p.status === 'varsayim' ? 'Varsayımın gerekçesi' : p.status === 'model_secimi' ? 'Model seçiminin gerekçesi' : 'Gerekçe'}>
          <Spoiler maxHeight={132} showLabel="devamı" hideLabel="kısalt">
            <Text size="sm" style={{ lineHeight: 1.4 }}>
              {p.rationale}
            </Text>
          </Spoiler>
        </Section>
      )}
      {!p.rationale && needsRationale && (
        <Text size="xs" style={{ color: 'var(--mantine-color-orange-text)' }}>
          Bu değer {p.status_tr}; kayıtta ayrıca gerekçe yazılmamış.
        </Text>
      )}
      <Section title={`Kaynaklar (${p.sources.length})`}>
        {p.sources.length > 0 ? (
          <Stack gap={6}>
            {p.sources.map((s) => (
              <SourceLine key={s.id} s={s} />
            ))}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">
            Kaynak yok — değer “{p.status_tr}” olarak etiketli{p.rationale ? '; gerekçesi yukarıda.' : '.'}
          </Text>
        )}
      </Section>
      {p.notes && (
        <Section title="Notlar">
          <Spoiler maxHeight={88} showLabel="devamı" hideLabel="kısalt">
            <Text size="xs" style={{ lineHeight: 1.45 }}>
              {p.notes}
            </Text>
          </Spoiler>
        </Section>
      )}
      {p.alternatives.length > 0 && (
        <Section title="Alternatif değerler">
          <Stack gap={4}>
            {p.alternatives.map((a, i) => (
              <Group key={i} gap={6} wrap="nowrap" align="flex-start">
                <Text size="sm" fw={650} style={{ whiteSpace: 'nowrap' }}>
                  {valueText(p, a.value)}
                </Text>
                <Text size="xs" c="dimmed" style={{ lineHeight: 1.35 }}>
                  {a.label}
                  {a.status ? ` · ${PARAM_STATUS_UI[a.status]?.label ?? a.status}` : ''}
                </Text>
              </Group>
            ))}
          </Stack>
        </Section>
      )}
      <Divider />
      <Stack gap={2}>
        {p.range && spec && (
          <Text size="xs">
            <b>Kaydırıcı aralığı:</b> {fmtDisplay(spec, p.range[0])} – {fmtDisplay(spec, p.range[1])}
            {p.range_note ? <Text component="span" size="xs" c="dimmed"> — {p.range_note}</Text> : null}
          </Text>
        )}
        <Text size="xs">
          <b>Modelin tanım kümesi:</b> {describeAllowed(p, spec)} (dışı sunucuda her zaman reddedilir)
        </Text>
        {p.structure && (
          <Text size="xs" style={{ color: 'var(--mantine-color-orange-text)' }}>
            Yapı parametresi: değişince talep/aday kümeleri ve mesafe listeleri yeniden kurulur; çözüm daha uzun sürer.
          </Text>
        )}
        {p.range && spec && spec.factor !== 1 && (
          <Text size="xs" c="dimmed">
            Giriş birimi: {spec.displayUnit} (kayıtta {p.unit}; 1 birim = {fmtNumber(spec.factor, 4)}).
          </Text>
        )}
      </Stack>
    </Stack>
  );
}
