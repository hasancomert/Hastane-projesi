/**
 * Haritanın dışarıya açılan arayüzü (sunum turu — Faz 6, dışa aktarım ve sunum modu — Faz 7). MapView harita
 * hazır olunca kaydeder; diğer bileşenler `getMapApi()` ya da `onMapApi(cb)` ile ulaşır.
 */
import type { Map as MapLibreMap } from 'maplibre-gl';

import type { ExportOptions } from './exportPng.ts';
import type { MapTool } from './layers/types.ts';

export interface MapApi {
  map: MapLibreMap;
  /** Ankara il sınırına sığdır */
  /** Ankara'ya sığdır; padding verilmezse haritanın üstündeki denetimlerin (lejant, araç çubuğu) arasına */
  fitAnkara(opts?: { animate?: boolean; padding?: number | { top: number; right: number; bottom: number; left: number } }): void;
  /** bir noktaya uç */
  flyTo(lon: number, lat: number, zoom?: number): void;
  /** gösterilen çözümdeki hastaneye uç (index = hospitals sırası) */
  flyToHospital(index: number, zoom?: number): void;
  /** bir ızgara hücresine uç (grid_index) */
  flyToCell(gridIndex: number, zoom?: number): void;
  /** yalnız tuvalin PNG'si (data URL; canvas preserveDrawingBuffer açık; lejant yok) */
  toPng(): string;
  /** harita + lejant (HTML katmanları dahil) PNG — html-to-image; data URL */
  exportPng(opts?: ExportOptions): Promise<string>;
  /** exportPng + dosya olarak indir */
  downloadPng(opts?: ExportOptions & { filename?: string }): Promise<void>;
  /** harita boşta olana dek bekle (karo ve katmanlar çizildi) */
  whenIdle(): Promise<void>;
  /** 'pmtiles' ya da 'fallback' (altlık yüklenemedi) */
  basemap(): 'pmtiles' | 'fallback';
  /** 3B eğim (true: 50°, döndürme açık; false: düz, kuzey yukarı) */
  setTilt(on: boolean): void;
  /** harita aracı (seç / zorunlu kıl / yasakla) */
  setTool(tool: MapTool): void;
  /** harita kapsayıcısı (sunum modu, dışa aktarım) */
  container: HTMLElement;
}

let current: MapApi | null = null;
const listeners = new Set<(api: MapApi | null) => void>();

export function setMapApi(api: MapApi | null): void {
  current = api;
  listeners.forEach((l) => l(api));
}

export function getMapApi(): MapApi | null {
  return current;
}

/** Harita hazır olunca (ya da zaten hazırsa hemen) çağrılır; dönen fonksiyon aboneliği kaldırır. */
export function onMapApi(cb: (api: MapApi | null) => void): () => void {
  listeners.add(cb);
  if (current) cb(current);
  return () => listeners.delete(cb);
}
