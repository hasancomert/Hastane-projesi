/**
 * Grafik sekmeleri (şartname 6.1 ve 7.7–7.10): U eğrisi · Pareto · Tornado · Dal-sınır · Maliyet kırılımı.
 *
 * İSKELET — Faz 6 her sekmeye bir bileşen bağlar. ECharts YALNIZ tembel yüklenen (React.lazy) grafik
 * bileşenlerinden içe aktarılmalı: ilk açılışta ≈1 MB'lık ECharts parçası yüklenmez (şartname 6.6 "< 3 sn").
 *   { id: 'u_egrisi', …, load: () => import('./UCurveChart.tsx') }
 * Veri: U eğrisi GET /api/precomputed/p_sweep_{3,5}km (PSweep), dal-sınır `job.trace` / `resultInfo.liveTrace` /
 * `result.trace` (lb, ub, gap, t), maliyet kırılımı `result.costs`. Pareto ve tornado Faz 6 iş türleri.
 * Renkler theme/palette.ts (sınıf rengi = haritadakiyle aynı), sayı biçimleri format/number.ts.
 */
import { Paper, Tabs, Text } from '@mantine/core';
import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from 'react';

import { ErrorBoundary } from '../components/ErrorBoundary.tsx';
import { useApp, type ChartTabId } from '../store/appStore.ts';
import type { Scheme } from '../theme/palette.ts';

/** Her grafik bileşeninin alacağı ortak özellikler. */
export interface ChartPanelProps {
  scheme: Scheme;
  /** piksel yükseklik (kapsayıcı verir) */
  height: number;
}

export interface ChartTabDef {
  id: ChartTabId;
  label: string;
  description: string;
  /** Faz 6'da: () => import('./XChart.tsx') */
  load?: () => Promise<{ default: ComponentType<ChartPanelProps> }>;
}

export const CHART_TABS: ChartTabDef[] = [
  { id: 'u_egrisi', label: 'U eğrisi', description: 'Hastane sayısı p taraması: toplam = açılış + erişim; optimumda dikey çizgi; bir p’ye tıklayınca harita o çözümü gösterir.' },
  { id: 'pareto', label: 'Pareto', description: 'Açılış maliyeti – ortalama mesafe ödünleşimi (λ taraması); mevcut çözüm eğri üzerinde işaretli.' },
  { id: 'tornado', label: 'Tornado', description: 'Önemli parametreler ±%20 → toplam maliyet ve hastane sayısı değişimi.' },
  { id: 'dal_sinir', label: 'Dal-sınır', description: 'Çözüm sırasında bulunan çözüm (üst sınır) ve alt sınır eğrileri; aralarındaki fark optimallik garantisidir.' },
  { id: 'maliyet', label: 'Maliyet kırılımı', description: 'İnşaat · donanım · altyapı · KDV · sabit · arsa · işletme · erişim.' },
];

const lazyCache = new Map<ChartTabId, LazyExoticComponent<ComponentType<ChartPanelProps>>>();
function lazyChart(def: ChartTabDef) {
  if (!def.load) return null;
  let c = lazyCache.get(def.id);
  if (!c) {
    c = lazy(def.load);
    lazyCache.set(def.id, c);
  }
  return c;
}

export function ChartTabs({ height = 320 }: { height?: number }) {
  const tab = useApp((s) => s.ui.chartTab);
  const setUi = useApp((s) => s.setUi);
  const scheme = useApp((s) => s.colorScheme);
  return (
    <Tabs value={tab} onChange={(v) => v && setUi({ chartTab: v as ChartTabId })} keepMounted={false} data-testid="chart-tabs">
      <Tabs.List>
        {CHART_TABS.map((t) => (
          <Tabs.Tab key={t.id} value={t.id}>
            {t.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {CHART_TABS.map((t) => {
        const C = lazyChart(t);
        return (
          <Tabs.Panel key={t.id} value={t.id} pt="sm">
            <ErrorBoundary area={t.label}>
              {C ? (
                <Suspense fallback={<Text c="dimmed">Grafik yükleniyor…</Text>}>
                  <C scheme={scheme} height={height} />
                </Suspense>
              ) : (
                <Paper withBorder p="md" mih={height / 2} radius="md">
                  <Text fw={600}>{t.label}</Text>
                  <Text size="sm" c="dimmed">
                    {t.description}
                  </Text>
                  <Text size="xs" c="dimmed" mt="xs">
                    Bu grafik hazırlanıyor.
                  </Text>
                </Paper>
              )}
            </ErrorBoundary>
          </Tabs.Panel>
        );
      })}
    </Tabs>
  );
}

export default ChartTabs;
