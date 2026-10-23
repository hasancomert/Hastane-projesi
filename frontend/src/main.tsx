/**
 * Giriş: yazı tipleri ve stiller (hepsi yerel), Mantine sağlayıcısı (tema depodaki colorScheme'i izler),
 * uygulama yan etkileri (açılış, otomatik çözüm) — React dışında bir kez.
 */
import '@fontsource-variable/inter';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import './theme/global.css';

import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import App from './App.tsx';
import { ErrorBoundary } from './components/ErrorBoundary.tsx';
import { startAppEffects } from './store/bootstrap.ts';
import { useApp } from './store/appStore.ts';
import { cssVariablesResolver, theme } from './theme/theme.ts';

function Root() {
  const scheme = useApp((s) => s.colorScheme);
  return (
    <MantineProvider theme={theme} forceColorScheme={scheme} cssVariablesResolver={cssVariablesResolver}>
      <Notifications position="top-right" limit={3} />
      <ErrorBoundary area="Sayfa">
        <App />
      </ErrorBoundary>
    </MantineProvider>
  );
}

startAppEffects();

// Uçtan uca testler ve geliştirme için depoya erişim (?e2e adres parametresiyle; sunumda görünmez).
try {
  if (import.meta.env.DEV || new URLSearchParams(window.location.search).has('e2e')) {
    (window as unknown as { __hastane?: unknown }).__hastane = { store: useApp };
  }
} catch {
  /* yok say */
}

/**
 * Servis çalışanı (yalnız derlemede; build/offline.ts üretir): sayfa kabuğunu ve gömülü yedek çözümü önbelleğe alır,
 * sunucu tamamen kapalıyken sayfa yeniden yüklenince de varsayılan çözüm "çevrimdışı mod" bandıyla görünür.
 * İlk açılışı yavaşlatmasın diye `load` olayından sonra kaydedilir. `?nosw` ile kapatılır (hata ayıklama).
 */
function registerServiceWorker() {
  try {
    if (!import.meta.env.PROD || !('serviceWorker' in navigator) || new URLSearchParams(window.location.search).has('nosw')) return;
    const register = () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
        /* desteklenmiyor / engellendi: çevrimdışı yedek yalnız sunucu ayaktayken çalışır */
      });
    };
    if (document.readyState === 'complete') register();
    else window.addEventListener('load', register, { once: true });
  } catch {
    /* yok say */
  }
}
registerServiceWorker();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
