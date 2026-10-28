/**
 * Veri ve varsayımlar tablosu (şartname 7.12, 10 "sayfadaki her sayının kaynağı ya da 'varsayım' etiketi"):
 * kayıttaki 69 parametre A–I gruplarında — etiket + anahtar, ŞU ANKİ değer ve birim (kayıttakinden farklıysa o da),
 * durum rozeti (kaynaklı · hesap · varsayım · model seçimi), kaynak başlıkları (→ Kaynakça çapası `kaynak-<id>`),
 * hesap için türetme, varsayım / model seçimi için gerekçe. Arama (Türkçe harf duyarsız) ve durum süzgeci.
 * Satır çapası `param-<anahtar>` (Kaynakça'daki parametre çiplerinden atlanır).
 */
import { Badge, Group, SegmentedControl, Spoiler, Stack, Switch, Table, Text, TextInput } from '@mantine/core';
import { useEffect, useMemo, useState } from 'react';

import type { ParamMeta, ParamStatus, ParamValue } from '../api/types.ts';
import { ParamStatusBadge } from '../components/StatusBadges.tsx';
import { fmtInt, fmtNumber } from '../format/number.ts';
import { choiceLabel, fmtDisplay, normalizeTr, numberSpec, sameNumber, shortUnit } from '../panels/paramUtils.ts';
import { paramValue, useApp } from '../store/appStore.ts';
import { PARAM_STATUS_UI } from '../theme/palette.ts';
import { IconSearch } from '../ui/icons.tsx';
import { useInfo } from './infoStore.ts';
import { InfoSection, SourceButton } from './ui.tsx';

const STATUS_ORDER: ParamStatus[] = ['kaynakli', 'hesap', 'varsayim', 'model_secimi'];

/** Değerin okunur gösterimi (sayı: gösterim birimiyle; seçenek: Türkçe etiket; profil: "km → katsayı" çiftleri). */
export function paramValueText(p: ParamMeta, v: ParamValue | undefined): string {
  if (v === undefined) return '—';
  if (Array.isArray(v)) return v.map((pair) => `${fmtNumber(pair[0], 1)} km → ${fmtNumber(pair[1], 3)}`).join(' · ');
  if (typeof v === 'number' && (p.allowed.kind === 'number' || p.allowed.kind === 'integer')) {
    const spec = numberSpec(p);
    const txt = fmtDisplay(spec, v);
    return spec.displayUnit ? txt : `${txt} ${shortUnit(p.unit)}`;
  }
  return choiceLabel(p, v);
}

function sameValue(a: ParamValue | undefined, b: ParamValue | undefined): boolean {
  if (typeof a === 'number' && typeof b === 'number') return sameNumber(a, b);
  return JSON.stringify(a) === JSON.stringify(b);
}

export function ParamTable() {
  const meta = useApp((s) => s.meta);
  const problem = useApp((s) => s.problem);
  const query = useInfo((s) => s.paramQuery);
  const setQuery = useInfo((s) => s.setParamQuery);
  const status = useInfo((s) => s.paramStatus);
  const setStatus = useInfo((s) => s.setParamStatus);
  const pending = useInfo((s) => s.pendingAnchor);
  const [changedOnly, setChangedOnly] = useState(false);

  // bir parametre satırına atlanıyorsa süzgeçler satırı gizlemesin
  useEffect(() => {
    if (pending?.startsWith('param-')) {
      setQuery('');
      setStatus('hepsi');
      setChangedOnly(false);
    }
  }, [pending, setQuery, setStatus]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { hepsi: 0 };
    for (const p of Object.values(meta?.parameters ?? {})) {
      c.hepsi++;
      c[p.status] = (c[p.status] ?? 0) + 1;
    }
    return c;
  }, [meta]);

  const groups = useMemo(() => {
    if (!meta) return [];
    const q = normalizeTr(query.trim());
    const st = { meta, problem };
    return meta.groups
      .map((g) => ({
        ...g,
        rows: g.keys
          .map((k) => meta.parameters[k])
          .filter((p): p is ParamMeta => !!p)
          .map((p) => ({ p, value: paramValue(st, p.key) }))
          .filter(({ p, value }) => {
            if (status !== 'hepsi' && p.status !== status) return false;
            if (changedOnly && sameValue(value, p.value)) return false;
            if (!q) return true;
            const hay = normalizeTr([p.key, p.label, p.unit, p.rationale ?? '', p.derivation ?? '', ...p.sources.map((s) => s.title)].join(' '));
            return q.split(/\s+/).every((w) => hay.includes(w));
          }),
      }))
      .filter((g) => g.rows.length > 0);
  }, [meta, problem, query, status, changedOnly]);

  if (!meta) return <Text c="dimmed">Parametre kaydı yükleniyor…</Text>;
  const nShown = groups.reduce((a, g) => a + g.rows.length, 0);

  return (
    <InfoSection id="veri-parametreler" kicker="Şartname 7.12 · veri ve varsayımlar" title={`Parametreler (${fmtInt(counts.hepsi)})`}>
      <Text size="sm" c="dimmed" className="info-prose">
        Her parametrenin şu anki değeri, birimi ve durumu. <b>Kaynaklı</b>: değer kaynakta yazıyor · <b>hesap</b>: kaynaklı girdilerden formülle · <b>varsayım</b>:
        kaynağı yok, gerekçeli · <b>model seçimi</b>: modelleme tercihi. Kaynak başlığına tıklayınca Kaynakça’daki satırına gidilir.
      </Text>
      <Group gap="sm" wrap="wrap" align="center">
        <TextInput
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          placeholder="Ara: ad, anahtar, birim, kaynak, gerekçe…"
          leftSection={<IconSearch size={15} />}
          aria-label="Parametre ara"
          style={{ flex: '1 1 18rem', maxWidth: '28rem' }}
          data-testid="param-search"
        />
        <SegmentedControl
          size="xs"
          value={status}
          onChange={(v) => setStatus(v as ParamStatus | 'hepsi')}
          data={[
            { value: 'hepsi', label: `Tümü ${counts.hepsi ?? 0}` },
            ...STATUS_ORDER.map((s) => ({ value: s, label: `${PARAM_STATUS_UI[s].label} ${counts[s] ?? 0}` })),
          ]}
          aria-label="Durum süzgeci"
          data-testid="param-status-filter"
        />
        <Switch size="sm" checked={changedOnly} onChange={(e) => setChangedOnly(e.currentTarget.checked)} label="Yalnız değişenler" />
        <Text size="xs" c="dimmed">
          {fmtInt(nShown)} / {fmtInt(counts.hepsi)} gösteriliyor
        </Text>
      </Group>
      <Table.ScrollContainer minWidth={1100}>
        <Table className="info-table" layout="fixed" highlightOnHover verticalSpacing={6} fz="sm" data-testid="param-table">
          <Table.Thead>
            <Table.Tr>
              <Table.Th style={{ width: '22%' }}>Parametre</Table.Th>
              <Table.Th style={{ width: '15%' }}>Değer</Table.Th>
              <Table.Th style={{ width: '8.5rem' }}>Durum</Table.Th>
              <Table.Th style={{ width: '22%' }}>Kaynak</Table.Th>
              <Table.Th>Gerekçe / türetme</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {groups.map((g) => [
              <Table.Tr key={`g-${g.id}`}>
                <Table.Td colSpan={5} style={{ background: 'var(--mantine-color-default-hover)' }}>
                  <Text size="sm" fw={700}>
                    {g.title}{' '}
                    <Text span size="xs" c="dimmed" fw={500}>
                      ({fmtInt(g.rows.length)})
                    </Text>
                  </Text>
                </Table.Td>
              </Table.Tr>,
              ...g.rows.map(({ p, value }) => {
                const changed = !sameValue(value, p.value);
                const why = p.status === 'varsayim' || p.status === 'model_secimi' ? p.rationale : p.derivation ?? p.rationale;
                return (
                  <Table.Tr key={p.key} id={`param-${p.key}`} data-testid={`param-row-${p.key}`}>
                    <Table.Td>
                      <Text size="sm" fw={600} style={{ lineHeight: 1.3 }}>
                        {p.label}
                      </Text>
                      <Text size="xs" c="dimmed" ff="monospace" style={{ overflowWrap: 'anywhere' }}>
                        {p.key}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" fw={changed ? 700 : 500} style={{ fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' }}>
                        {paramValueText(p, value)}
                      </Text>
                      {changed && (
                        <Group gap={4} mt={2}>
                          <Badge size="xs" color="orange" variant="light" tt="none">
                            değişti
                          </Badge>
                          <Text size="xs" c="dimmed">
                            kayıtta {paramValueText(p, p.value)}
                          </Text>
                        </Group>
                      )}
                      {p.unit && !paramValueText(p, value).endsWith(shortUnit(p.unit)) && (
                        <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
                          {p.unit}
                        </Text>
                      )}
                      {p.unit && paramValueText(p, value).endsWith(shortUnit(p.unit)) && p.unit !== shortUnit(p.unit) && (
                        <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
                          {p.unit.slice(shortUnit(p.unit).length).trim()}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <ParamStatusBadge status={p.status} />
                    </Table.Td>
                    <Table.Td>
                      {p.sources.length ? (
                        <Stack gap={0} align="flex-start">
                          {p.sources.map((s) => (
                            <SourceButton key={s.id} id={s.id} title={s.title} />
                          ))}
                        </Stack>
                      ) : (
                        <Text size="xs" c="dimmed">
                          kaynak yok — {p.status_tr}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {why ? (
                        <Spoiler maxHeight={64} showLabel="devamı" hideLabel="kısalt">
                          <Text size="xs" style={{ lineHeight: 1.45 }}>
                            {why}
                          </Text>
                        </Spoiler>
                      ) : (
                        <Text size="xs" c="dimmed">
                          —
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                );
              }),
            ])}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {nShown === 0 && (
        <Text size="sm" c="dimmed">
          Aramaya uyan parametre yok.
        </Text>
      )}
    </InfoSection>
  );
}
