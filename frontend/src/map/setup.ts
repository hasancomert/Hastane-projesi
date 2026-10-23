/**
 * MapLibre 6 küresel kurulumu (bir kez): işçi adresi ve PMTiles protokolü.
 *
 * MapLibre 6 yalnız ESM adlı dışa aktarımlar sunar; Vite'ta işçi dosyasının adresi açıkça verilmelidir
 * (`?url` ile derlemeye ayrı dosya olarak kopyalanır, internetten yüklenmez).
 */
import { addProtocol, setWorkerUrl } from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { Protocol } from 'pmtiles';

let done = false;

export function setupMapLibre(): void {
  if (done) return;
  done = true;
  setWorkerUrl(workerUrl);
  const protocol = new Protocol();
  addProtocol('pmtiles', protocol.tile);
}
