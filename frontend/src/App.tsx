/**
 * Sayfa düzeni (şartname 6.1):
 *
 *   [Başlık + otomatik tek cümlelik sonuç]                    [▶ Sunum turu] [⛶ Sunum modu]
 *   [ Parametre paneli ] [ Harita (geniş) …………………… ] [ Sonuç / açıklama paneli ]
 *   [ Grafik sekmeleri: U eğrisi · Pareto · Tornado · Dal-sınır · Maliyet kırılımı ]
 *   [ Hocanın Listesi ✓ ] [ Veri ] [ Model ] [ Sınırlılıklar ] [ Kaynakça ]
 *
 * Duyarlı: ≥ 1200 px üç sütun; 900–1199 px parametreler çekmecede; < 900 px parametre ve sonuç çekmecede,
 * harita tam genişlik. Her bölüm ErrorBoundary içinde (beyaz ekran yok).
 */
import { Badge, Box, Button, Drawer, Group, Text, Title, Tooltip, UnstyledButton } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useEffect } from 'react';

import { ChartTabs } from './charts/ChartTabs.tsx';
import { ErrorBoundary } from './components/ErrorBoundary.tsx';
import { ConnectionBadge } from './components/StatusBadges.tsx';
import { ThemeToggle } from './components/ThemeToggle.tsx';
import { InfoTabs } from './info/InfoTabs.tsx';
import { MapView } from './map/MapView.tsx';
import { ParameterPanel } from './panels/ParameterPanel.tsx';
import { ResultPanel } from './panels/ResultPanel.tsx';
import { fmtNumber, fmtPercent } from './format/number.ts';
import { optimalityLabel, summarySentence } from './panels/optimality.ts';
import { selectOkResult, selectShowStale, selectUiTargetGap, useApp } from './store/appStore.ts';
import { STATUS_MANTINE } from './theme/palette.ts';
import { LAYOUT } from './theme/theme.ts';
import { startTour, TourCard } from './tour/Tour.tsx';

const FALLBACK_SOURCE_TR: Record<string, string> = {
  'sunucu-onceden': 'sunucudaki önceden çözülmüş sonuç',
  gomulu: 'sayfaya gömülü varsayılan çözüm (3 km)',
};

/**
 * Çevrimdışı bandı (şartname 5): başlığın hemen altında tam genişlik — haritanın denetimlerini örtmez. Ne
 * gösterildiğini ve sunucu dönünce ne olacağını söyler.
 */
function OfflineStrip() {
  const connection = useApp((s) => s.connection);
  if (connection.mode !== 'offline') return null;
  const what = connection.fallbackSource ? FALLBACK_SOURCE_TR[connection.fallbackSource] ?? 'önceden çözülmüş sonuç' : 'önceden çözülmüş sonuç';
  return (
    <div className="offline-strip" role="status" aria-live="polite" data-testid="offline-banner">
      <span className="offline-strip-dot" aria-hidden />
      <Text component="span" fw={700} size="sm">
        Çevrimdışı mod — canlı çözüm kapalı.
      </Text>
      <Text component="span" size="sm" className="offline-strip-detail">
        Gösterilen: {what}. Sunucu açılınca sayfa kendiliğinden bağlanır.
      </Text>
    </div>
  );
}

/**
 * Başlıktaki tek cümlelik sonuç (şartname 6.1, 7.1). Başı optimallik kuralından gelir (panels/optimality.ts: başlık,
 * sonuç özeti ve rozetler aynı etiketi kullanır). Parametre değişip yeni çözüm beklenirken ya da son istek hata
 * verdiyse cümle soluk ve "(önceki sonuç)" ile başlar. İki satırlık yer baştan ayrılır (sonuç gelince düzen kaymasın).
 */
function HeaderSummary() {
  const result = useApp((s) => s.result);
  const ok = useApp(selectOkResult);
  const info = useApp((s) => s.resultInfo);
  const stale = useApp(selectShowStale);
  const target = useApp(selectUiTargetGap);
  const metaError = useApp((s) => s.metaError);
  const mode = useApp((s) => s.connection.mode);
  const opt = ok ? optimalityLabel(ok, target, !!info?.stopped) : null;
  const text = ok && opt
    ? `${stale ? '(önceki sonuç) ' : ''}${summarySentence(ok, opt)}`
    : result && !result.ok
      ? result.message_tr
      : metaError
        ? `Veri yüklenemedi: ${metaError}`
        : mode === 'connecting'
          ? 'Yerel sunucuya bağlanılıyor…'
          : 'Sonuç bekleniyor…';
  return (
    <Tooltip
      label={
        opt ? (
          <span>
            {stale ? 'Parametreler değişti; bu cümle önceki parametrelerin sonucudur. ' : ''}
            {opt.tooltip}
          </span>
        ) : (
          text
        )
      }
      disabled={!ok}
      openDelay={500}
    >
      <Text className="header-summary" size="sm" lineClamp={2} data-testid="header-summary" data-stale={stale || undefined}>
        {text}
      </Text>
    </Tooltip>
  );
}

/**
 * Telefonda (sonuç paneli çekmecede) başlığın altında tek satırlık özet: hastane sayısı, toplam maliyet, 10 km
 * içindeki nüfus ve optimallik. Dokununca sonuç çekmecesi açılır.
 */
function MobileSummary() {
  const ok = useApp(selectOkResult);
  const info = useApp((s) => s.resultInfo);
  const stale = useApp(selectShowStale);
  const target = useApp(selectUiTargetGap);
  const solving = useApp((s) => !!s.job);
  const setUi = useApp((s) => s.setUi);
  const opt = ok ? optimalityLabel(ok, target, !!info?.stopped) : null;
  return (
    <UnstyledButton className="mobile-summary" onClick={() => setUi({ resultDrawerOpen: true })} data-testid="mobile-summary" data-stale={stale || undefined} aria-label="Sonucu aç">
      {ok && opt ? (
        <>
          <Text component="span" size="sm" fw={700} style={{ whiteSpace: 'nowrap' }}>
            {ok.kpis.n_hospitals} hastane
          </Text>
          <Text component="span" size="sm" style={{ whiteSpace: 'nowrap' }}>
            · {fmtNumber(ok.costs.toplam / 1e9, 1)} milyar TL
          </Text>
          <Text component="span" size="sm" className="mobile-summary-opt" style={{ whiteSpace: 'nowrap' }}>
            · 10 km içinde {fmtPercent(ok.kpis.share_pop_within_km['10'])}
          </Text>
          <Badge size="sm" variant="light" color={STATUS_MANTINE[stale ? 'neutral' : opt.tone]} tt="none" style={{ flexShrink: 0, marginLeft: 'auto' }}>
            {solving ? 'çözülüyor…' : stale ? 'önceki sonuç' : opt.kind === 'optimum' ? 'optimum' : opt.kind === 'stopped' ? 'durduruldu' : 'en iyi bulunan'}
          </Badge>
        </>
      ) : (
        <Text component="span" size="sm">
          {solving ? 'Çözülüyor…' : 'Sonuç bekleniyor…'}
        </Text>
      )}
    </UnstyledButton>
  );
}

/** Odağı bir bölgeye taşı (atlama bağlantıları; adres çubuğundaki # değişmez — orada problem durumu tutulur). */
function focusRegion(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  const canvas = id === 'harita' ? el.querySelector<HTMLElement>('.maplibregl-canvas') : null;
  if (canvas) {
    canvas.focus();
    return;
  }
  // bölge yalnız bu odak için geçici olarak odaklanabilir (kalıcı tabindex fare tıklamalarında odağı panele çekip
  // kaydırıcı sürüklemesini bozuyordu)
  el.setAttribute('tabindex', '-1');
  el.addEventListener('blur', () => el.removeAttribute('tabindex'), { once: true });
  el.focus();
  el.scrollIntoView({ block: 'nearest' });
}

export default function App() {
  const wide = useMediaQuery(`(min-width: ${LAYOUT.paramsDrawerBelow}px)`, true, { getInitialValueInEffect: false });
  const medium = useMediaQuery(`(min-width: ${LAYOUT.resultDrawerBelow}px)`, true, { getInitialValueInEffect: false });
  const ui = useApp((s) => s.ui);
  const setUi = useApp((s) => s.setUi);
  // yalnız bağlantı KİPİ: sağlık yoklaması (10 sn'de bir health nesnesi) bütün sayfayı yeniden çizdirmesin
  const connMode = useApp((s) => s.connection.mode);
  const selection = useApp((s) => s.selection);

  // dar ekranda haritada bir şey seçilince sonuç çekmecesini aç
  useEffect(() => {
    if (!medium && selection) useApp.getState().setUi({ resultDrawerOpen: true });
  }, [medium, selection]);

  const layout = wide ? 'wide' : medium ? 'medium' : 'narrow';

  const skip = (id: 'harita' | 'sonuc' | 'parametreler') => () => {
    if (id === 'sonuc' && !medium) setUi({ resultDrawerOpen: true });
    else if (id === 'parametreler' && !wide) setUi({ paramsDrawerOpen: true });
    else focusRegion(id);
  };

  return (
    <div className="app" data-layout={layout} data-offline={connMode === 'offline' || undefined} data-testid="app">
      <nav className="skip-links" aria-label="Hızlı geçiş">
        <button type="button" onClick={skip('harita')}>
          Haritaya geç
        </button>
        <button type="button" onClick={skip('sonuc')}>
          Sonuçlara geç
        </button>
        <button type="button" onClick={skip('parametreler')}>
          Parametrelere geç
        </button>
      </nav>
      <header className="app-header">
        <div className="header-title">
          <Title order={1} className="app-title">
            Ankara Hastane Yerleşimi
          </Title>
          <HeaderSummary />
        </div>
        <Group gap="xs" wrap="nowrap" className="header-actions">
          <ConnectionBadge />
          {!wide && (
            <Button variant="default" size={medium ? 'sm' : 'compact-xs'} onClick={() => setUi({ paramsDrawerOpen: true })}>
              Parametreler
            </Button>
          )}
          {!medium && (
            <Button variant="default" size="compact-xs" onClick={() => setUi({ resultDrawerOpen: true })}>
              Sonuç
            </Button>
          )}
          <Tooltip label="Hocanın 9 maddesini sırayla anlatan tur (← → ile gezin, Esc kapatır)">
            <span>
              <Button variant="filled" size="sm" visibleFrom="sm" data-testid="tour-button" onClick={startTour}>
                ▶ Sunum turu
              </Button>
            </span>
          </Tooltip>
          <TourCard />
          <Tooltip label="Tam ekran, büyük yazı, sade panel (hazırlanıyor)">
            <span>
              <Button variant="light" size="sm" disabled visibleFrom="sm" data-testid="presentation-button">
                ⛶ Sunum modu
              </Button>
            </span>
          </Tooltip>
          <ThemeToggle />
        </Group>
      </header>

      <OfflineStrip />
      {layout === 'narrow' && <MobileSummary />}

      <main className="app-main">
        {wide && (
          <aside className="panel panel-left" aria-label="Parametreler" id="parametreler">
            <ErrorBoundary area="Parametre paneli">
              <ParameterPanel />
            </ErrorBoundary>
          </aside>
        )}
        <section className="map-area" aria-label="Harita" id="harita">
          <ErrorBoundary area="Harita">
            <MapView />
          </ErrorBoundary>
        </section>
        {medium && (
          <aside className="panel panel-right" aria-label="Sonuç ve açıklama" id="sonuc">
            <ErrorBoundary area="Sonuç paneli">
              <ResultPanel />
            </ErrorBoundary>
          </aside>
        )}
      </main>

      <section className="app-section" aria-label="Grafikler">
        <ErrorBoundary area="Grafikler">
          <ChartTabs />
        </ErrorBoundary>
      </section>
      <section className="app-section" aria-label="Bilgi">
        <ErrorBoundary area="Bilgi sekmeleri">
          <InfoTabs />
        </ErrorBoundary>
      </section>
      <Box component="footer" className="app-footer">
        <Text size="xs" c="dimmed">
          Optimizasyon dersi projesi · Veriler: TÜİK ADNKS 2025, WorldPop R2025A, Sağlık Bakanlığı, 2026 Yapı Yaklaşık Birim Maliyetleri ·
          Harita: © OpenStreetMap katkıcıları, Protomaps · Her sayının kaynağı ya da “varsayım” etiketi parametre panelinde.
        </Text>
      </Box>

      <Drawer
        opened={!wide && ui.paramsDrawerOpen}
        onClose={() => setUi({ paramsDrawerOpen: false })}
        position="left"
        size={medium ? 'md' : '100%'}
        title={<Text fw={700}>Parametreler</Text>}
      >
        <ErrorBoundary area="Parametre paneli">
          <ParameterPanel inDrawer />
        </ErrorBoundary>
      </Drawer>
      <Drawer
        opened={!medium && ui.resultDrawerOpen}
        onClose={() => setUi({ resultDrawerOpen: false })}
        position="bottom"
        size="75%"
        title={<Text fw={700}>Sonuç</Text>}
      >
        <ErrorBoundary area="Sonuç paneli">
          <ResultPanel inDrawer />
        </ErrorBoundary>
      </Drawer>
    </div>
  );
}
