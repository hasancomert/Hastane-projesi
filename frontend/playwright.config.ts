/**
 * Uçtan uca testler: DERLENMİŞ arayüz FastAPI tarafından sunulurken (npm run build → backend/static).
 * Sunucu çalışmıyorsa Playwright başlatır (`python -m backend.run --no-browser --port <E2E_PORT>`), çalışıyorsa
 * onu kullanır. Tarayıcı: önceden kurulu Chromium (PLAYWRIGHT_BROWSERS_PATH; `playwright install` GEREKMEZ).
 * WebGL başsız Chromium'da SwiftShader ile çalışır.
 *
 *   npm run build && npm run test:e2e
 *   E2E_SCREENSHOT_DIR=/tmp/ekran npm run test:e2e     # ekran görüntülerini de kaydet
 */
import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.E2E_PORT ?? 8000);
const baseURL = `http://127.0.0.1:${port}`;
const python = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python';

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL,
    locale: 'tr-TR',
    timezoneId: 'Europe/Istanbul',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    // servis çalışanı (çevrimdışı yedek) yalnız onu sınayan testte açık: diğer testlerin istek yönlendirmeleri
    // (page.route) servis çalışanının yanıtladığı istekleri göremez
    serviceWorkers: 'block',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1280, height: 720 },
        launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] },
      },
    },
  ],
  webServer: {
    command: `${python} -m backend.run --no-browser --port ${port}`,
    cwd: '..',
    url: `${baseURL}/api/health`,
    reuseExistingServer: true,
    timeout: 90_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
