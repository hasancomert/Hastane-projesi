/**
 * Haritaya özgü arayüz durumu (uygulama deposundan ayrı; yalnız harita bileşenleri abone olur, imleç hareketleri
 * sayfanın geri kalanını yeniden çizdirmez): araç (seç / zorunlu kıl / yasakla), imleç ipucu, sağ tık menüsü,
 * lejant ve katman panelinin açıklığı, eğim, yakınlaştırma (lejanttaki boyut anahtarı için).
 */
import { create } from 'zustand';

import { readPref, writePref } from '../store/prefs.ts';
import type { MapTool } from './layers/types.ts';

export type HoverTarget =
  | { kind: 'hospital'; index: number }
  | { kind: 'flow'; arc: number }
  | { kind: 'cell'; gridIndex: number };

export interface HoverState {
  /** harita kapsayıcısına göre piksel */
  x: number;
  y: number;
  target: HoverTarget;
}

export interface MenuState {
  x: number;
  y: number;
  gridIndex: number;
  cellId: string;
  /** bu hücredeki hastane (hospitals sırası) */
  hospital: number | null;
}

export interface MapUiState {
  tool: MapTool;
  hover: HoverState | null;
  menu: MenuState | null;
  legendOpen: boolean;
  layersOpen: boolean;
  tilted: boolean;
  zoom: number;
  setTool(t: MapTool): void;
  setHover(h: HoverState | null): void;
  setMenu(m: MenuState | null): void;
  setLegendOpen(v: boolean): void;
  setLayersOpen(v: boolean): void;
  setTilted(v: boolean): void;
  setZoom(z: number): void;
}

export const useMapUi = create<MapUiState>()((set) => ({
  tool: 'select',
  hover: null,
  menu: null,
  legendOpen: readPref<boolean>('map.legend') ?? true,
  layersOpen: false,
  tilted: false,
  zoom: 8.5,
  setTool: (tool) => set({ tool }),
  setHover: (hover) => set({ hover }),
  setMenu: (menu) => set({ menu }),
  setLegendOpen: (legendOpen) => {
    writePref('map.legend', legendOpen);
    set({ legendOpen });
  },
  setLayersOpen: (layersOpen) => set({ layersOpen }),
  setTilted: (tilted) => set({ tilted }),
  setZoom: (zoom) => set({ zoom }),
}));
