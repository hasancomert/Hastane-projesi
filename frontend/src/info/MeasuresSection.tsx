/**
 * Ankara ölçüleri (hocanın 4. maddesi "Ankara'nın toplam mesafesi"; şartname 4.3 madde 3 ve 6). Tüm değerler ve
 * Türkçe metinler /api/measures'tan (data/processed/ankara_olculer.json, pipeline/23_ankara_measures.py):
 *   - kart: alan (jeodezik), kalıcı su hariç alan, çevre, doğu–batı / kuzey–güney genişlik, en uzak iki sınır
 *     noktası, Kızılay → en uzak / en yakın sınır, ağırlık merkezi;
 *   - "Haritada çiz": `layers.measures` (map/layers/measuresLayer.ts);
 *   - kaynaklardaki yüzölçümü değerleri ile karşılaştırma (göller hariç olanlar işaretli ve kara alanıyla kıyaslı);
 *   - hastane arsası ve kapalı alanı × hücre alanı ("arazi bağlayıcı değil").
 */
import { Alert, Badge, Button, Group, Paper, Spoiler, Stack, Table, Text, Tooltip } from '@mantine/core';

import type { AreaComparisonRow } from '../api/infoTypes.ts';
import type { ParamStatus } from '../api/types.ts';
import { fmtInt, fmtKm2, fmtNumber } from '../format/number.ts';
import { ParamStatusBadge } from '../components/StatusBadges.tsx';
import { useApp } from '../store/appStore.ts';
import { PARAM_STATUS_UI } from '../theme/palette.ts';
import { jumpTo } from './jumps.ts';
import { InfoSection, LoadError, Loading, N, Prose, SourceButton } from './ui.tsx';
import { useMeasures } from './useInfoData.ts';

/** Kayıt durumları rozetle; dosyadaki ek durumlar (ör. 'kaynakli_ikincil') metinle. */
function StatusTag({ status }: { status?: string }) {
  if (!status) return null;
  if (status in PARAM_STATUS_UI) return <ParamStatusBadge status={status as ParamStatus} />;
  const known = status.startsWith('kaynakli') ? 'ikincil kaynak' : status.replace(/_/g, ' ');
  return (
    <Badge size="sm" variant="light" color="teal" tt="none">
      {known}
    </Badge>
  );
}

function signedPct(x: number | undefined, digits = 2): string {
  if (x === undefined || !Number.isFinite(x)) return '—';
  const s = x > 0 ? '+' : x < 0 ? '−' : '±';
  const a = Math.abs(x);
  return `${s}%${fmtNumber(a, a > 0 && a < 0.01 ? 4 : digits)}`;
}

function signedKm2(x: number | undefined): string {
  if (x === undefined || !Number.isFinite(x)) return '—';
  const s = x > 0 ? '+' : x < 0 ? '−' : '±';
  const a = Math.abs(x);
  return `${s}${fmtNumber(a, a > 0 && a < 0.1 ? 3 : 1)} km²`;
}

export function MeasuresSection() {
  const st = useMeasures();
  const shown = useApp((s) => !!s.layers.measures);
  const setLayerVisible = useApp((s) => s.setLayerVisible);
  const gridKm = useApp((s) => s.problem.gridKm);
  if (st.status === 'loading' || st.status === 'idle') return <Loading what="Ankara ölçüleri" />;
  if (st.status === 'error' || !st.data) return <LoadError what="Ankara ölçüleri" error={st.error} file="data/processed/ankara_olculer.json (GET /api/measures)" />;
  const m = st.data;
  const card = m.card;
  const ac = m.area_comparison;
  const hl = m.hospital_land;
  const ownRows: AreaComparisonRow[] = (ac?.own_rows ?? []) as AreaComparisonRow[];
  const cellKms = hl?.cases?.[0]?.shares.map((s) => s.cell_km) ?? [];

  return (
    <Stack gap="lg">
      <InfoSection
        id="veri-olculer"
        kicker="Hocanın 4. maddesi · toplam mesafe"
        title={card?.title ?? 'Ankara’nın büyüklüğü'}
        aside={
          <Group gap={6}>
            {shown && (
              <Button size="compact-sm" variant="subtle" onClick={() => setLayerVisible('measures', false)} data-testid="measures-hide">
                Haritadan kaldır
              </Button>
            )}
            <Button size="compact-sm" variant="light" onClick={() => void jumpTo('olculer_harita')} data-testid="measures-show" rightSection={<span aria-hidden>→</span>}>
              Haritada göster
            </Button>
          </Group>
        }
      >
        <div className="olcu-grid" data-testid="measures-card">
          {card?.items.map((it) => (
            <Paper key={it.key} withBorder radius="md" p="xs" data-testid={`olcu-${it.key}`}>
              <Text size="xs" c="dimmed" style={{ lineHeight: 1.3 }}>
                {it.label}
              </Text>
              <Text fw={700} style={{ fontSize: '1.3rem', lineHeight: 1.2, fontVariantNumeric: 'tabular-nums' }}>
                {it.value_text}
              </Text>
              <Text size="xs" c="dimmed" style={{ lineHeight: 1.35 }}>
                {it.tooltip}
              </Text>
            </Paper>
          ))}
        </div>
        <Text size="xs" c="dimmed">
          Haritada: <span className="olcu-legend-line" data-w="primary" />
          en uzak iki sınır noktası · <span className="olcu-legend-line" data-w="secondary" />
          Kızılay → en uzak sınır noktası · <span className="olcu-legend-line" data-w="muted" />
          en batı ↔ en doğu ve en kuzey ↔ en güney sınır noktaları; işaretler: Kızılay ve ilin eşit alanlı ağırlık merkezi.
        </Text>
        <Spoiler maxHeight={0} showLabel="Yöntem: nasıl ölçüldü?" hideLabel="Yöntemi gizle">
          <Stack gap={6} pt={4}>
            {m.area?.geodesic_km2?.method && <Prose size="xs">Alan: {m.area.geodesic_km2.method} {m.area.note}</Prose>}
            {m.perimeter?.note && <Prose size="xs">Çevre: {m.perimeter.note}</Prose>}
            {m.extent?.note && <Prose size="xs">Genişlikler: {m.extent.note}</Prose>}
            {m.diameter?.proof && <Prose size="xs">En uzak iki nokta: {m.diameter.proof}</Prose>}
            {m.centroid?.method && <Prose size="xs">Ağırlık merkezi: {m.centroid.method}</Prose>}
            {m.meta?.generated_by && (
              <Text size="xs" c="dimmed">
                Üretim: {m.meta.generated_by}
                {m.meta.generated ? ` (${m.meta.generated})` : ''} · dosya data/processed/ankara_olculer.json
              </Text>
            )}
          </Stack>
        </Spoiler>
      </InfoSection>

      {ac && (
        <InfoSection id="olcu-alan" kicker="Yüzölçümü kaynakları çelişkili" title="Kaynaklardaki yüzölçümü ile karşılaştırma">
          <Prose>
            Referans: bu çalışmanın jeodezik il alanı <N>{fmtKm2(ac.reference_km2)}</N>. Göller hariç verilen değerler (soldaki mavi çizgi) poligon − kalıcı su
            {m.water?.land_area_km2 ? <> (<N>{m.water.land_area_km2.text}</N>)</> : null} ile kıyaslanmalıdır.
          </Prose>
          <Table.ScrollContainer minWidth={860}>
            <Table className="info-table" striped={false} highlightOnHover verticalSpacing={6} fz="sm" data-testid="area-table">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Kaynak</Table.Th>
                  <Table.Th className="num">Değer</Table.Th>
                  <Table.Th>Kapsam</Table.Th>
                  <Table.Th className="num">Fark (jeodezik alana göre)</Table.Th>
                  <Table.Th className="num">Fark (kara alanına göre)</Table.Th>
                  <Table.Th>Durum</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {ownRows.map((r) => (
                  <Table.Tr key={`own-${r.source}`} data-own>
                    <Table.Td>
                      <Text size="sm" fw={600}>
                        {r.source}
                      </Text>
                    </Table.Td>
                    <Table.Td className="num">{r.text}</Table.Td>
                    <Table.Td>
                      <Text size="xs">{r.scope}</Text>
                    </Table.Td>
                    <Table.Td className="num">{r.reference ? 'referans' : `${signedKm2(r.diff_km2)} (${signedPct(r.diff_pct, 3)})`}</Table.Td>
                    <Table.Td className="num">—</Table.Td>
                    <Table.Td>
                      <Badge size="sm" variant="light" color="blue" tt="none">
                        bu çalışma
                      </Badge>
                    </Table.Td>
                  </Table.Tr>
                ))}
                {ac.rows.map((r, i) => (
                  <Table.Tr key={`${r.source}-${i}`} data-flag={r.lakes_excluded ? 'lakes' : undefined}>
                    <Table.Td>
                      <Tooltip label={r.source_full ?? r.source} multiline maw={380} openDelay={300}>
                        <Text size="sm">{r.source}</Text>
                      </Tooltip>
                      {r.source_id && <SourceButton id={r.source_id} title={r.source_full ?? r.source} compact />}
                    </Table.Td>
                    <Table.Td className="num">{r.text}</Table.Td>
                    <Table.Td>
                      <Group gap={4} wrap="wrap">
                        {r.lakes_excluded && (
                          <Badge size="xs" variant="light" color="blue" tt="none">
                            göller hariç
                          </Badge>
                        )}
                        <Text size="xs">{r.scope}</Text>
                      </Group>
                    </Table.Td>
                    <Table.Td className="num">
                      {signedKm2(r.diff_km2)} ({signedPct(r.diff_pct)})
                    </Table.Td>
                    <Table.Td className="num">{r.lakes_excluded ? `${signedKm2(r.diff_vs_land_km2)} (${signedPct(r.diff_vs_land_pct)})` : '—'}</Table.Td>
                    <Table.Td>
                      <StatusTag status={r.status} />
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {ac.note && <Prose size="xs">{ac.note}</Prose>}
          {ac.method && (
            <Text size="xs" c="dimmed">
              {ac.method}
            </Text>
          )}
        </InfoSection>
      )}

      {hl && (
        <InfoSection id="olcu-arazi" kicker="Hocanın 3a. maddesi · hastanenin inşaat / arsa alanı" title="Hastane arsası ve kapalı alanı × hücre alanı">
          <Alert color="teal" variant="light" title="Arazi bağlayıcı değil" data-testid="land-conclusion">
            <Text size="sm" className="info-prose">
              {hl.conclusion.replace(/^Arazi bağlayıcı değil\.\s*/, '')}
            </Text>
          </Alert>
          {hl.cases && hl.cases.length > 0 && (
            <Table.ScrollContainer minWidth={820}>
              <Table className="info-table" highlightOnHover verticalSpacing={6} fz="sm" data-testid="land-table">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Durum</Table.Th>
                    <Table.Th className="num">Arsa / yatak</Table.Th>
                    <Table.Th className="num">Arsa</Table.Th>
                    <Table.Th className="num">Kapalı alan</Table.Th>
                    {cellKms.map((km) => (
                      <Table.Th key={km} className="num" style={km === gridKm ? { color: 'var(--mantine-primary-color-filled)' } : undefined}>
                        {fmtNumber(km, 0)} km hücrenin payı{km === gridKm ? ' (seçili)' : ''}
                      </Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {hl.cases.map((c) => (
                    <Table.Tr key={c.key}>
                      <Table.Td>
                        <Text size="sm">{c.label}</Text>
                        {c.land_basis && (
                          <Text size="xs" c="dimmed">
                            {c.land_basis}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td className="num">{fmtNumber(c.land_m2_per_bed, 0)} m²</Table.Td>
                      <Table.Td className="num">
                        {fmtInt(c.land_m2)} m²
                        <Text size="xs" c="dimmed">
                          ≈{fmtNumber(c.land_km2, 3)} km²
                        </Text>
                      </Table.Td>
                      <Table.Td className="num">
                        {c.floor_area_m2 != null ? `${fmtInt(c.floor_area_m2)} m²` : '—'}
                        {c.floor_to_land_ratio != null && (
                          <Text size="xs" c="dimmed">
                            kapalı / arsa {fmtNumber(c.floor_to_land_ratio, 2)}
                          </Text>
                        )}
                      </Table.Td>
                      {cellKms.map((km) => {
                        const s = c.shares.find((x) => x.cell_km === km);
                        return (
                          <Table.Td key={km} className="num" style={km === gridKm ? { fontWeight: 700 } : undefined}>
                            {s ? `%${fmtNumber(s.share_pct, 2)}` : '—'}
                          </Table.Td>
                        );
                      })}
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
          <Prose size="xs">
            {hl.min_parcel && (
              <>
                En küçük hastanenin ({fmtInt(hl.min_parcel.beds)} yatak) asgari parseli {fmtInt(hl.min_parcel.m2)} m² — en küçük hücrenin %
                {fmtNumber(hl.min_parcel.share_pct_smallest_cell, 2)}’i.{' '}
              </>
            )}
            {hl.province_total && (
              <>
                Tüm ilin yatak talebi ({fmtNumber(hl.province_total.beds, 1)} yatak) için gereken arsa ≈{fmtNumber(hl.province_total.land_km2, 2)} km², il alanının %
                {fmtNumber(hl.province_total.share_of_province_pct, 3)}’i.{' '}
              </>
            )}
            {hl.caveat}
          </Prose>
        </InfoSection>
      )}
    </Stack>
  );
}
