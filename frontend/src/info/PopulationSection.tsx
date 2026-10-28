/**
 * Nüfus 2026 (hocanın 1. ve 7. maddeleri): TÜİK ADNKS 31.12.2025 il toplamı (= 2026 başı), 2030 projeksiyonu,
 * hücre nüfusunun TÜİK'e kalibrasyonu (kayıttaki gerekçe metni + gösterilen ızgaranın toplamlarıyla canlı denetim),
 * TÜİK kapsamı (geçici koruma) ve ilçe tablosu (/api/measures: TÜİK 2025 nüfusu, jeodezik alan, yoğunluk).
 *
 * Not: kalibrasyon ÖNCESİ ilçe oranları (WorldPop / GHS-POP / Kontur ÷ TÜİK) data/processed/ilce_kontrol_tablosu.csv
 * dosyasındadır; sunucu bu dosyayı sunmuyor — burada yalnız kayıttaki özet (gerekçe metni) gösterilir.
 */
import { Badge, Group, Paper, Stack, Table, Text } from '@mantine/core';
import { useMemo, type ReactNode } from 'react';

import { ParamStatusBadge } from '../components/StatusBadges.tsx';
import { fmtInt, fmtNumber, fmtPeople, fmtPercent } from '../format/number.ts';
import { selectGrid, useApp } from '../store/appStore.ts';
import { quoteWith } from './facts.ts';
import { InfoSection, JumpButton, LoadError, Loading, N, Prose, SourceButton } from './ui.tsx';
import { useMeasures, useParam } from './useInfoData.ts';

function Tile({ label, value, children, testId }: { label: string; value: string; children?: ReactNode; testId?: string }) {
  return (
    <Paper withBorder radius="md" p="xs" data-testid={testId}>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text fw={700} style={{ fontSize: '1.3rem', lineHeight: 1.2, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </Text>
      {children}
    </Paper>
  );
}

export function PopulationSection() {
  const st = useMeasures();
  const grid = useApp(selectGrid);
  const gridKm = useApp((s) => s.problem.gridKm);
  const p25 = useParam('population.province_2025');
  const p30 = useParam('population.province_2030');
  const src = useParam('population.grid_source');
  const year = useParam('population.scenario_year');

  // gösterilen ızgaranın il toplamları (kalibrasyon denetimi): WorldPop (pop), GHS-POP (pop_ghs), 2030 (pop2030)
  const sums = useMemo(() => {
    if (!grid) return null;
    let wp = 0;
    let ghs = 0;
    let y30 = 0;
    for (const f of grid.features) {
      wp += f.properties.pop || 0;
      ghs += f.properties.pop_ghs || 0;
      y30 += f.properties.pop2030 || 0;
    }
    return { wp, ghs, y30 };
  }, [grid]);

  const m = st.data;
  const rows = m?.districts?.rows ?? [];
  const total = m?.districts?.sum_pop_2025 ?? rows.reduce((a, r) => a + r.pop_2025, 0);
  const maxPop = rows.length ? Math.max(...rows.map((r) => r.pop_2025)) : 1;
  const v25 = typeof p25.value === 'number' ? p25.value : null;
  const v30 = typeof p30.value === 'number' ? p30.value : null;
  const coverage = quoteWith(p25.p?.notes, 'geçici koruma');
  const ok = (a: number, b: number | null) => b !== null && Math.abs(a - b) < 0.5;

  return (
    <Stack gap="lg">
      <InfoSection
        id="veri-nufus"
        kicker="Hocanın 1. ve 7. maddeleri"
        title="Ankara nüfusu 2026 ve nüfus yoğunluğu"
        aside={<JumpButton id="nufus_harita" label="Haritada nüfus yoğunluğu" />}
      >
        <div className="olcu-grid">
          <Tile label={p25.p?.label ?? 'Ankara nüfusu 31.12.2025'} value={v25 !== null ? fmtPeople(v25) : '—'} testId="pop-2025">
            <Group gap={4} mt={4} wrap="wrap">
              {p25.p && <ParamStatusBadge status={p25.p.status} />}
              {p25.p?.sources.slice(0, 2).map((s) => (
                <SourceButton key={s.id} id={s.id} title={s.title} compact />
              ))}
            </Group>
          </Tile>
          <Tile label={p30.p?.label ?? 'Ankara nüfusu 2030 (projeksiyon)'} value={v30 !== null ? fmtPeople(v30) : '—'}>
            <Group gap={4} mt={4}>
              {p30.p && <ParamStatusBadge status={p30.p.status} />}
              <Text size="xs" c="dimmed">
                “2030 nüfusu” senaryosunda kullanılır
              </Text>
            </Group>
          </Tile>
          {m?.water?.province_density_per_km2 != null && (
            <Tile label="İl nüfus yoğunluğu (jeodezik alanla)" value={`${fmtNumber(m.water.province_density_per_km2, 1)} kişi/km²`}>
              {m.water.province_density_land_per_km2 != null && (
                <Text size="xs" c="dimmed">
                  kalıcı su hariç alanla {fmtNumber(m.water.province_density_land_per_km2, 1)} kişi/km²
                </Text>
              )}
            </Tile>
          )}
          {sums && (
            <Tile label={`Kalibrasyon denetimi (${gridKm} km ızgara toplamı)`} value={fmtPeople(sums.wp)} testId="pop-check">
              <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
                WorldPop {ok(sums.wp, v25) ? '= TÜİK 2025 ✓' : '≠ TÜİK 2025'} · GHS-POP {fmtInt(sums.ghs)} {ok(sums.ghs, v25) ? '✓' : ''} · 2030 {fmtInt(sums.y30)}{' '}
                {ok(sums.y30, v30) ? '= TÜİK projeksiyonu ✓' : ''}
              </Text>
            </Tile>
          )}
        </div>
        <Prose>
          <b>Kalibrasyon.</b> {src.p?.rationale?.replace(/^Öğrenci kararı \([^)]*\)\.\s*/, '') ?? 'Hücre nüfusu ilçe bazında TÜİK 2025 toplamına ölçeklenir.'}{' '}
          {year.p?.rationale ? <>{year.p.rationale}</> : null}
        </Prose>
        {coverage && (
          <Prose size="xs">
            <b>TÜİK kapsamı:</b> “{coverage}” (ADNKS 2025 metaverisi; bkz. Sınırlılıklar).
          </Prose>
        )}
      </InfoSection>

      <InfoSection id="veri-ilceler" kicker="TÜİK ADNKS 2025 · jeodezik ilçe alanları" title="İlçeler: nüfus ve yoğunluk">
        {st.status === 'loading' || st.status === 'idle' ? (
          <Loading what="İlçe tablosu" />
        ) : !m ? (
          <LoadError what="İlçe tablosu" error={st.error} file="data/processed/ankara_olculer.json (GET /api/measures)" />
        ) : (
          <>
            <Table.ScrollContainer minWidth={720}>
              <Table className="info-table" highlightOnHover verticalSpacing={4} fz="sm" data-testid="district-table">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>İlçe</Table.Th>
                    <Table.Th className="num">Nüfus 2025 (TÜİK)</Table.Th>
                    <Table.Th style={{ width: '22%' }}>İl içindeki payı</Table.Th>
                    <Table.Th className="num">Alan (jeodezik)</Table.Th>
                    <Table.Th className="num">Yoğunluk (kişi/km²)</Table.Th>
                    <Table.Th className="num">Kara yoğunluğu (kişi/km²)</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {rows.map((r) => (
                    <Table.Tr key={r.ilce_key}>
                      <Table.Td>{r.ilce}</Table.Td>
                      <Table.Td className="num">{fmtInt(r.pop_2025)}</Table.Td>
                      <Table.Td>
                        <Group gap={6} wrap="nowrap">
                          <div style={{ flex: 1, height: 8, background: 'var(--mantine-color-default-border)', borderRadius: 2 }}>
                            <div style={{ width: `${(100 * r.pop_2025) / maxPop}%`, height: '100%', background: 'var(--mantine-color-dimmed)', borderRadius: 2 }} />
                          </div>
                          <Text size="xs" style={{ width: '3.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                            {fmtPercent(r.pop_2025 / total)}
                          </Text>
                        </Group>
                      </Table.Td>
                      <Table.Td className="num">{fmtNumber(r.area_geodesic_km2, 1)} km²</Table.Td>
                      <Table.Td className="num">{fmtNumber(r.density_per_km2, r.density_per_km2 < 100 ? 1 : 0)}</Table.Td>
                      <Table.Td className="num">{r.density_land_per_km2 != null ? fmtNumber(r.density_land_per_km2, r.density_land_per_km2 < 100 ? 1 : 0) : '—'}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
                <Table.Tfoot>
                  <Table.Tr>
                    <Table.Th>Toplam ({fmtInt(rows.length)} ilçe)</Table.Th>
                    <Table.Th className="num">
                      <N>{fmtInt(total)}</N>
                      {v25 !== null && total === v25 ? (
                        <Badge size="xs" color="teal" variant="light" ml={4} tt="none">
                          = il ✓
                        </Badge>
                      ) : null}
                    </Table.Th>
                    <Table.Th />
                    <Table.Th className="num">{m.districts?.sum_area_geodesic_km2 != null ? `${fmtNumber(m.districts.sum_area_geodesic_km2, 1)} km²` : ''}</Table.Th>
                    <Table.Th />
                    <Table.Th />
                  </Table.Tr>
                </Table.Tfoot>
              </Table>
            </Table.ScrollContainer>
            {m.districts?.method && <Prose size="xs">{m.districts.method}</Prose>}
          </>
        )}
      </InfoSection>
    </Stack>
  );
}
