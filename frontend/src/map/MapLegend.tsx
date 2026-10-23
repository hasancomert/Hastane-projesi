/**
 * Harita katman paneli ve lejant (şartname 6.2 "aç/kapa, lejantlı").
 *
 *   MapLayerPanel (sol üst): "Hücre rengi" tek seçimli grup — hizmet bölgeleri · nüfus yoğunluğu · erişim ·
 *     gölge fiyat · yok (iki sıralı renk ölçeği üst üste okunamaz; aynı ton = aynı anda tek anlam) — ve bağımsız
 *     katmanlar: sınırlar, hastaneler, aday hücreler, akışlar, R halkaları, 3B sütunlar. Kullanılamayan katman
 *     (çözüm yok, dual yok) devre dışı ve nedenini söyler.
 *   MapLegend (sol alt): yalnız GÖRÜNEN katmanların anahtarı — sınıf renkleri (sayılarıyla), yatak boyut anahtarı
 *     (o anki yakınlaştırmada gerçek piksel boyutu), etkin hücre katmanının renk çubuğu (birim + tikler), akış,
 *     halka, aday, kilit anahtarları. Metin mürekkep tonunda; renk yalnız işarette (dataviz kuralı).
 */
import { ActionIcon, Badge, Checkbox, Divider, Paper, Radio, ScrollArea, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core';
import { useMemo, type ReactNode } from 'react';

import { fmtInt, fmtNumber, fmtPeople } from '../format/number.ts';
import { LAYER_DEFS, selectGrid, useApp, type LayerId } from '../store/appStore.ts';
import { CHROME, CLASS_NAME_TR, CLASS_ORDER, classColor, type Scheme } from '../theme/palette.ts';
import { gridIndexOf } from './gridIndex.ts';
import { IconChevron, IconLayers, IconLegend } from './icons.tsx';
import { currentR } from './layers/access.ts';
import { bedsToPx } from './layers/hospitals.ts';
import { COLUMN_M_PER_DENSITY } from './layers/population.ts';
import { ringRadiusKm } from './layers/rings.ts';
import { shadowScaleFor } from './layers/shadowPrices.ts';
import { BEYOND_R_HEX, LOCK_CLOSED_HEX, LOCK_OPEN_HEX, accessScale, densityScale, type SequentialScale } from './mapColors.ts';
import { useMapUi } from './mapUiStore.ts';
import { useShownSolution } from './useShownSolution.ts';

/* ------------------------------------------------------------------------------------------------ hücre rengi grubu */

export const CELL_LAYERS = ['serviceAreas', 'population', 'access', 'shadowPrices'] as const;
export type CellLayer = (typeof CELL_LAYERS)[number];

export function activeCellLayer(visible: Record<LayerId, boolean>): CellLayer | 'none' {
  return CELL_LAYERS.find((id) => visible[id]) ?? 'none';
}

/** Hücre rengi grubunda tek seçim: seçileni açar, diğerlerini kapatır. */
export function setCellLayer(id: CellLayer | 'none') {
  const st = useApp.getState();
  for (const c of CELL_LAYERS) if (st.layers[c] !== (c === id)) st.setLayerVisible(c, c === id);
}

const def = (id: LayerId) => LAYER_DEFS.find((d) => d.id === id)!;

function useAvailability() {
  const { solution: sol } = useShownSolution();
  const hasDuals = !!sol?.duals?.marginal_cost_per_person_tl?.length;
  const noSol = 'Çözüm bekleniyor — bu katman çözümden türetilir.';
  const reason: Partial<Record<LayerId, string | null>> = {
    serviceAreas: sol ? null : noSol,
    access: sol?.layers?.access ? null : noSol,
    flows: sol ? null : noSol,
    rings: sol ? null : noSol,
    hospitals: sol ? null : noSol,
    shadowPrices: hasDuals ? null : sol ? 'Bu çözümde dual değer yok (gölge fiyatlar yalnız dual değerli çözümlerde).' : noSol,
  };
  return { sol, reason };
}

/* ------------------------------------------------------------------------------------------------ katman paneli */

export function MapLayerPanel({ compact, top = 10 }: { compact?: boolean; top?: number }) {
  const open = useMapUi((s) => s.layersOpen);
  const setOpen = useMapUi((s) => s.setLayersOpen);
  const visible = useApp((s) => s.layers);
  const setLayerVisible = useApp((s) => s.setLayerVisible);
  const { reason } = useAvailability();
  const active = activeCellLayer(visible);
  const nOn = (Object.keys(visible) as LayerId[]).filter((k) => visible[k]).length;

  const cellOptions: { id: CellLayer | 'none'; label: string; description: string }[] = [
    ...CELL_LAYERS.map((id) => ({ id, label: def(id).label, description: def(id).description })),
    { id: 'none', label: 'Yok', description: 'Yalnız altlık harita' },
  ];
  const overlays: LayerId[] = ['boundaries', 'hospitals', 'candidates', 'flows', 'rings', 'population3d'];

  return (
    <div className="map-layer-panel" data-export="skip" style={{ position: 'absolute', top, left: 10, zIndex: 5, maxWidth: 'calc(100% - 70px)' }}>
      <Paper shadow="sm" radius="md" withBorder style={{ overflow: 'hidden' }}>
        <UnstyledButton
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-controls="harita-katmanlari"
          data-testid="map-layers-button"
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: compact ? '6px 10px' : '7px 12px', width: '100%' }}
        >
          <IconLayers size={18} />
          <Text fw={650} size="sm">
            Katmanlar
          </Text>
          <Badge size="sm" variant="light" color="gray" radius="sm">
            {nOn}
          </Badge>
          <span style={{ marginLeft: 'auto', display: 'inline-flex' }}>
            <IconChevron size={16} open={open} />
          </span>
        </UnstyledButton>
        {open && (
          <ScrollArea.Autosize mah="min(60vh, 30rem)" type="auto" id="harita-katmanlari">
            <Stack gap={7} px="sm" pb="sm" pt={2} w="15.5rem" data-testid="map-layers-panel">
              <Text size="xs" fw={700} c="dimmed" tt="uppercase" style={{ letterSpacing: '0.04em' }}>
                Hücre rengi
              </Text>
              <Radio.Group value={active} onChange={(v) => setCellLayer(v as CellLayer | 'none')} aria-label="Hücre rengi">
                <Stack gap={7}>
                  {cellOptions.map((o) => {
                    const why = o.id === 'none' ? null : (reason[o.id] ?? null);
                    return (
                      <Tooltip key={o.id} label={why ?? o.description} position="right" openDelay={why ? 0 : 450}>
                        <div>
                          <Radio value={o.id} size="xs" disabled={!!why} label={<Text size="sm">{o.label}</Text>} data-testid={`layer-radio-${o.id}`} />
                        </div>
                      </Tooltip>
                    );
                  })}
                </Stack>
              </Radio.Group>
              <Divider my={4} />
              <Text size="xs" fw={700} c="dimmed" tt="uppercase" style={{ letterSpacing: '0.04em' }}>
                Katmanlar
              </Text>
              {overlays.map((id) => {
                const d = def(id);
                const why = reason[id] ?? null;
                const hint = id === 'population3d' ? `${d.description}; harita eğilir (sağ tıkla sürükleyerek döndürün)` : d.description;
                return (
                  <Tooltip key={id} label={why ?? hint} position="right" openDelay={why ? 0 : 450}>
                    <div>
                      <Checkbox
                        size="xs"
                        checked={visible[id]}
                        disabled={!!why}
                        onChange={(e) => setLayerVisible(id, e.currentTarget.checked)}
                        label={<Text size="sm">{d.label}</Text>}
                        data-testid={`layer-toggle-${id}`}
                      />
                    </div>
                  </Tooltip>
                );
              })}
            </Stack>
          </ScrollArea.Autosize>
        )}
      </Paper>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ lejant */

function RampBar({ scale, title, unit, extra }: { scale: SequentialScale; title: string; unit: string; extra?: ReactNode }) {
  return (
    <div>
      <Text size="xs" fw={650} mb={2}>
        {title} <span style={{ fontWeight: 400, color: 'var(--mantine-color-dimmed)' }}>({unit})</span>
      </Text>
      <div style={{ position: 'relative', height: 10, borderRadius: 3, background: scale.gradient, boxShadow: 'inset 0 0 0 1px var(--mantine-color-default-border)' }} />
      <div style={{ position: 'relative', height: 18, marginTop: 1 }}>
        {scale.ticks.map((t, i) => (
          <span
            key={t.label}
            style={{
              position: 'absolute',
              left: `${t.at * 100}%`,
              transform: i === 0 && t.at < 0.02 ? 'none' : t.at > 0.98 ? 'translateX(-100%)' : 'translateX(-50%)',
              fontSize: '0.8125rem',
              color: 'var(--mantine-color-dimmed)',
              whiteSpace: 'nowrap',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {t.label}
          </span>
        ))}
      </div>
      {extra}
    </div>
  );
}

function Swatch({ color, border, children, shape = 'square' }: { color: string; border?: string; children?: ReactNode; shape?: 'square' | 'circle' }) {
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-grid',
        placeItems: 'center',
        width: 14,
        height: 14,
        flex: '0 0 auto',
        borderRadius: shape === 'circle' ? 7 : 3,
        background: color,
        boxShadow: border ? `inset 0 0 0 2px ${border}` : undefined,
        fontSize: 12,
        fontWeight: 800,
        lineHeight: 1,
        color: border ?? 'inherit',
      }}
    >
      {children}
    </span>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div style={{ display: 'flex', alignItems: 'center', gap: 6, minHeight: 18 }}>{children}</div>;
}

function SizeKey({ zoom, maxBeds, scheme }: { zoom: number; maxBeds: number; scheme: Scheme }) {
  const values = [50, 200, 400];
  if (maxBeds > 600) values.push(Math.round(maxBeds / 50) * 50);
  const radii = values.map((b) => Math.min(30, bedsToPx(b, zoom)));
  const h = Math.max(...radii) * 2 + 4;
  const ring = CHROME[scheme].inkMuted;
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, flexWrap: 'wrap' }}>
      {values.map((b, i) => {
        const r = radii[i];
        const last = i === values.length - 1;
        return (
          <span key={b} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}>
            <svg width={r * 2 + 3} height={h} aria-hidden>
              <circle cx={r + 1.5} cy={h - r - 1.5} r={r} fill="none" stroke={ring} strokeWidth={1.5} />
            </svg>
            <Text size="xs" c="dimmed" style={{ fontVariantNumeric: 'tabular-nums' }} title={last ? 'Daire alanı yatak sayısıyla orantılı' : undefined}>
              {fmtInt(b)}
              {last ? ' yatak' : ''}
            </Text>
          </span>
        );
      })}
    </div>
  );
}

/**
 * `dense`: harita alanı alçakken (1280×720 gibi) lejant kısa metinlerle çizilir — Ankara'nın güneybatısını
 * (Polatlı/Haymana) daha az örter; uzun açıklamalar ipucunda.
 */
export function MapLegend({ compact, dense }: { compact?: boolean; dense?: boolean }) {
  const open = useMapUi((s) => s.legendOpen);
  const setOpen = useMapUi((s) => s.setLegendOpen);
  const zoom = useMapUi((s) => s.zoom);
  const scheme = useApp((s) => s.colorScheme);
  const visible = useApp((s) => s.layers);
  const meta = useApp((s) => s.meta);
  const locks = useApp((s) => s.problem.locks);
  const grid = useApp(selectGrid);
  const gridKm = useApp((s) => s.problem.gridKm);
  const { sol } = useAvailability();
  const failed = useApp((s) => !!s.result && !s.result.ok);
  const active = activeCellLayer(visible);

  const gi = useMemo(() => (grid ? gridIndexOf(grid, gridKm) : null), [grid, gridKm]);
  const R = currentR({ solution: sol, meta });
  const shadow = useMemo(() => (sol ? shadowScaleFor(gi, sol.duals, scheme) : null), [gi, sol, scheme]);

  if (!open) {
    return (
      <div className="map-legend" style={{ position: 'absolute', left: 10, bottom: 10, zIndex: 4 }} data-export="skip">
        <Tooltip label="Lejantı göster">
          <ActionIcon variant="default" size="lg" radius="md" onClick={() => setOpen(true)} aria-label="Lejantı göster" data-testid="map-legend-open">
            <IconLegend />
          </ActionIcon>
        </Tooltip>
      </div>
    );
  }

  const counts = sol?.kpis?.count_by_class;
  const maxBeds = sol?.hospitals?.reduce((m, h) => Math.max(m, h.beds), 0) ?? 0;
  const nCand = sol?.layers?.candidates?.cell_id.length ?? (grid ? grid.features.filter((f) => f.properties.aday).length : null);
  const access = sol?.layers?.access;

  // en çok haritanın %35'i (H3): uzun lejant kendi içinde kayar; Ankara lejantın üstüne sığdırılır (MapView)
  return (
    <div
      className="map-legend"
      style={{ position: 'absolute', left: 10, bottom: 10, zIndex: 4, maxWidth: 'calc(100% - 70px)', maxHeight: '35%', display: 'flex', flexDirection: 'column' }}
      data-testid="map-legend"
    >
      <Paper shadow="sm" radius="md" withBorder p={compact ? 8 : 10} style={{ width: compact ? '15rem' : '16.5rem', minHeight: 0, overflow: 'auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase" style={{ letterSpacing: '0.04em' }}>
            Lejant
          </Text>
          <UnstyledButton onClick={() => setOpen(false)} aria-label="Lejantı gizle" data-export="skip" style={{ display: 'inline-flex', color: 'var(--mantine-color-dimmed)' }}>
            <IconChevron size={16} open />
          </UnstyledButton>
        </div>
        <Stack gap={8}>
          {visible.hospitals && sol && (
            <div>
              <Text size="xs" fw={650} mb={2}>
                Hastaneler ({fmtInt(sol.hospitals.length)}) · renk = sınıf{dense ? '' : ', alan ∝ yatak'}
              </Text>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 8, rowGap: 1 }}>
                {CLASS_ORDER.map((c) => {
                  const n = counts?.[c] ?? 0;
                  return (
                    <Row key={c}>
                      <Swatch color={classColor(c, scheme)} shape="circle" border={undefined} />
                      <Text size="xs" c={n ? undefined : 'dimmed'} style={{ whiteSpace: 'nowrap' }}>
                        {CLASS_NAME_TR[c]} <span style={{ color: 'var(--mantine-color-dimmed)', fontVariantNumeric: 'tabular-nums' }}>{fmtInt(n)}</span>
                      </Text>
                    </Row>
                  );
                })}
              </div>
              <div style={{ marginTop: 4 }}>
                <SizeKey zoom={zoom} maxBeds={maxBeds} scheme={scheme} />
              </div>
            </div>
          )}

          {active === 'serviceAreas' && sol && dense && (
            <Text
              size="xs"
              c="dimmed"
              lh={1.3}
              title="Hücre, hizmet veren hastanenin sınıf renginde; ince ayırıcı çizgi = bölge sınırı; soluk = talebi bölünmüş hücre."
            >
              <b style={{ color: 'var(--mantine-color-text)' }}>Hizmet bölgesi:</b> hücre rengi = hastanenin sınıfı
            </Text>
          )}
          {active === 'serviceAreas' && sol && !dense && (
            <Text size="xs" c="dimmed" lh={1.35}>
              <b style={{ color: 'var(--mantine-color-text)' }}>Hizmet bölgesi:</b> hücre, hizmet veren hastanenin sınıf renginde; ince ayırıcı çizgi = bölge sınırı;
              soluk = talebi bölünmüş hücre.
            </Text>
          )}
          {(active === 'population' || visible.population3d) && <RampBar scale={densityScale(scheme)} title="Nüfus yoğunluğu" unit="kişi/km², log ölçek" />}
          {active === 'access' && access && (
            <RampBar
              scale={accessScale(scheme, R)}
              title="En yakın açık hastane"
              unit="yol km"
              extra={
                <Row>
                  <Swatch color={BEYOND_R_HEX} />
                  <Text size="xs">
                    ⚠ R dışı (&gt; {fmtNumber(R, 0)} km): {fmtInt(access.beyond_R_cells)} hücre
                    {access.beyond_R_population !== undefined ? `, ${fmtPeople(access.beyond_R_population)}` : ''}
                  </Text>
                </Row>
              }
            />
          )}
          {active === 'shadowPrices' && shadow && (
            <RampBar
              scale={shadow}
              title="Marjinal hizmet maliyeti"
              unit="bin TL/kişi"
              extra={
                <Text size="xs" c="dimmed" lh={1.3}>
                  Hücrede 1 kişi daha olsaydı toplam maliyet artışı (atama LP'sinin dual değeri; açık hastaneler sabit). Renk %2–%98 aralığında.
                </Text>
              }
            />
          )}
          {visible.population3d && (
            <Text size="xs" c="dimmed" lh={1.3}>
              <b style={{ color: 'var(--mantine-color-text)' }}>3B sütun:</b> yükseklik ∝ nüfus yoğunluğu (1.000 kişi/km² = {fmtNumber(1000 * COLUMN_M_PER_DENSITY, 0)} m), renk
              yoğunluk ölçeğinde.
            </Text>
          )}
          {visible.flows && sol && (
            <Row>
              <svg width={28} height={14} aria-hidden>
                <path d="M2 12 Q14 0 26 12" fill="none" stroke={CHROME[scheme].inkSecondary} strokeWidth={2} />
              </svg>
              <Text size="xs" c="dimmed">
                Hasta akışı: hücre → hastane, kalınlık ∝ √nüfus
              </Text>
            </Row>
          )}
          {visible.rings && sol && (
            <Row>
              <Swatch color="transparent" border={CHROME[scheme].inkSecondary} shape="circle" />
              <Text size="xs" c="dimmed">
                R = {fmtNumber(R, 0)} km yol ≈ {fmtNumber(ringRadiusKm({ solution: sol, meta }), 1)} km kuş uçuşu
              </Text>
            </Row>
          )}
          {visible.candidates && nCand !== null && (
            <Row>
              <Swatch color="transparent" border={CHROME[scheme].inkMuted} />
              <Text size="xs" c="dimmed">
                Aday hücre: {fmtInt(nCand)} (kalın çerçeve: kapsama onarımı)
              </Text>
            </Row>
          )}
          {(locks.open.length > 0 || locks.closed.length > 0) && (
            <Row>
              {locks.open.length > 0 && (
                <>
                  <Swatch color="transparent" border={LOCK_OPEN_HEX}>
                    +
                  </Swatch>
                  <Text size="xs">zorunlu {fmtInt(locks.open.length)}</Text>
                </>
              )}
              {locks.closed.length > 0 && (
                <>
                  <Swatch color="transparent" border={LOCK_CLOSED_HEX}>
                    ×
                  </Swatch>
                  <Text size="xs">yasak {fmtInt(locks.closed.length)}</Text>
                </>
              )}
            </Row>
          )}
          {failed && (
            <Text size="xs" lh={1.3} c="var(--mantine-color-red-text)" data-testid="map-legend-infeasible">
              ⚠ Son istek olurlu değil — haritada önceki olurlu çözüm soluk gösteriliyor (neden: sonuç paneli).
            </Text>
          )}
          {!sol && !failed && (
            <Text size="xs" c="dimmed">
              Çözüm bekleniyor…
            </Text>
          )}
        </Stack>
      </Paper>
    </div>
  );
}
