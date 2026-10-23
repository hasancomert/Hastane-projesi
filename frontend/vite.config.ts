/**
 * Vite yapılandırması (Faz 5).
 *
 * - Geliştirme: `npm run dev` → http://127.0.0.1:5173; /api ve /basemap istekleri yerel FastAPI sunucusuna
 *   (varsayılan http://127.0.0.1:8000, HASTANE_BACKEND ile değişir) aktarılır. Sunucunun Host denetimi yalnız
 *   127.0.0.1 / localhost kabul ettiği için changeOrigin açık.
 * - Derleme: `npm run build` → ../backend/static (FastAPI aynı kökenden sunar; sunum dizüstünde Node gerekmez).
 *   Klasör her derlemede boşaltılır; git'teki backend/static/.gitkeep derleme sonunda yeniden yazılır.
 * - Büyük kütüphaneler ayrı parçalara bölünür (maplibre, deck, echarts, katex, mantine, react): tarayıcı önbelleği
 *   ve ilk açılış süresi için. ECharts ve KaTeX yalnız tembel yüklenen bileşenlerden çağrılmalıdır.
 * - Hiçbir dosya internetten yüklenmez: yazı tipleri (@fontsource-variable/inter, KaTeX) derlemeye gömülür.
 * - Çevrimdışı yedek (build/offline.ts): varsayılan 3 km çözümü, ızgara, sınırlar ve /api/meta kopyası
 *   backend/static/offline/'a yazılır; servis çalışanı (sw.js) sayfa kabuğunu ve bunları önbelleğe alır — sunucu
 *   tamamen kapalıyken sayfa yeniden yüklenince de varsayılan çözüm görünür.
 */
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

import { offlineBundle } from './build/offline.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..');
const outDir = path.resolve(repoRoot, 'backend/static');
const backend = process.env.HASTANE_BACKEND ?? 'http://127.0.0.1:8000';

/** emptyOutDir backend/static/.gitkeep'i de siler; derleme bitince geri yaz (klasör git'te kalsın). */
function keepGitkeep(): Plugin {
  return {
    name: 'hastane-keep-gitkeep',
    apply: 'build',
    closeBundle() {
      mkdirSync(outDir, { recursive: true });
      const keep = path.join(outDir, '.gitkeep');
      if (!existsSync(keep)) writeFileSync(keep, '');
    },
  };
}

const proxy = {
  '/api': { target: backend, changeOrigin: true },
  '/basemap': { target: backend, changeOrigin: true },
};

/** node_modules paketi → parça adı (yol ayırıcısı Windows'ta '\\'). */
const nm = (names: string) => new RegExp(`[\\\\/]node_modules[\\\\/](?:${names})[\\\\/]`);

export default defineConfig({
  plugins: [react(), keepGitkeep(), offlineBundle({ repoRoot, outDir })],
  base: '/',
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy,
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
    proxy,
  },
  build: {
    outDir,
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
    assetsInlineLimit: 4096,
    chunkSizeWarningLimit: 1800,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'maplibre', test: nm('maplibre-gl|pmtiles|@protomaps|fflate'), priority: 50 },
            {
              name: 'deck',
              test: nm('@deck\\.gl|@luma\\.gl|@loaders\\.gl|@math\\.gl|@probe\\.gl|mjolnir\\.js|gl-matrix|earcut|h3-js|@turf|@mapbox'),
              priority: 40,
            },
            { name: 'echarts', test: nm('echarts|zrender|echarts-for-react|size-sensor'), priority: 40 },
            { name: 'katex', test: nm('katex'), priority: 40 },
            { name: 'mantine', test: nm('@mantine|@floating-ui|react-remove-scroll[^\\\\/]*|react-transition-group|react-number-format|react-textarea-autosize|clsx'), priority: 30 },
            { name: 'react', test: nm('react|react-dom|scheduler|zustand|use-sync-external-store'), priority: 20 },
          ],
        },
      },
    },
  },
});
