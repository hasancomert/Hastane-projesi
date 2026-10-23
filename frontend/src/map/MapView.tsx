/**
 * Harita (MapLibre GL 6 + deck.gl 9, MapLibreOverlay interleaved). Şartname 6.2, 6.5.
 *
 *   - Altlık: yerel Protomaps PMTiles (Türkçe adlar, açık/koyu tema); yüklenemezse düz arka plan + sınırlar.
 *   - Katmanlar: layers/*.ts (LAYER_ORDER); hücre dolguları altlık yazılarının altında (FILL_ANCHOR), işaretler üstte.
 *     Katman nesneleri her durum değişikliğinde yeniden oluşturulur ama veri dizileri not alınır (aynı referans →
 *     deck.gl yeniden mozaiklemez); renkler updateTriggers / ikili özniteliklerle güncellenir.
 *   - Etkileşim (merkezî): tıklama → seçim aracında hastane/hücre kartı, kilit araçlarında 'aç'/'yasak' kilidi;
 *     sağ tık → kilit menüsü; imleç → ipucu + hizmet bölgesi vurgusu; Esc → seçim/araç sıfırla. Hücre, tıklanan
 *     koordinattan geometrik olarak bulunur (gridIndex.cellAt) — hiçbir hücre katmanı açık değilken de çalışır.
 *   - Lejant ve katman paneli (MapLegend.tsx), araç çubuğu (MapToolbar.tsx), ipucu (MapTooltip.tsx), menü
 *     (MapContextMenu.tsx). Dışa: `MapApi` (mapApi.ts) — sığdır, uç, PNG, eğim, araç.
 *
 * Test kancaları: kapsayıcıda `data-testid="map"`, `data-map-state` ∈ loading | idle | error,
 * `data-basemap` ∈ pmtiles | fallback; `?e2e` adres parametresiyle `window.__hastaneMap` (MapApi).
 */
import 'maplibre-gl/dist/maplibre-gl.css';

import { MapLibreOverlay } from '@deck.gl/maplibre';
import type { PickingInfo } from '@deck.gl/core';
import type { FeatureCollection } from 'geojson';
import { notifications } from '@mantine/notifications';
import { Map as MapLibreMap, NavigationControl, ScaleControl, type ErrorEvent as MapErrorEvent, type GeoJSONSource } from 'maplibre-gl';
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';

import { optimalityLabel, summarySentence } from '../panels/optimality.ts';
import { selectCanSolve, selectGrid, selectOkResult, selectUiTargetGap, useApp, type Selection } from '../store/appStore.ts';
import { ANKARA_BOUNDS, BASEMAP_SOURCE, FILL_ANCHOR, LABEL_LAYER, LABEL_SOURCE, PAN_LIMIT, PHONE_LIMIT, basemapStyle, fallbackStyle } from './basemap.ts';
import { gridIndexOf } from './gridIndex.ts';
import { districtLabels, type LabelCollection } from './labels.ts';
import { demandPosByCell } from './layers/cellData.ts';
import { flowArcIndex } from './layers/flows.ts';
import { DECK_IDS, buildDeckLayers, type LayerContext, type MapTool } from './layers/index.ts';
import { MapContextMenu } from './MapContextMenu.tsx';
import { MapLayerPanel, MapLegend } from './MapLegend.tsx';
import { setMapApi, type MapApi } from './mapApi.ts';
import { MapToolbar } from './MapToolbar.tsx';
import { MapTooltip } from './MapTooltip.tsx';
import { useMapUi, type HoverTarget } from './mapUiStore.ts';
import { setupMapLibre } from './setup.ts';
import { useShownSolution } from './useShownSolution.ts';

export interface MapViewProps {
  className?: string;
  style?: CSSProperties;
  /** false → yalnız gösterim (sunum küçük resmi vb.) */
  interactive?: boolean;
}

type MapState = 'loading' | 'idle' | 'error';

const TILT_PITCH = 50;
const TILT_BEARING = -15;
/** Ankara kent merkezi (Kızılay yakını) ve 3B görünümde yaklaşılan ölçek */
const CITY_CENTER: [number, number] = [32.86, 39.9];
const CITY_ZOOM = 8.9;
/** bu genişliğin altında lejant ve panel sıkışık düzende; açılışta lejant kapalı */
const COMPACT_BELOW = 640;
/** harita bu yükseklikten alçaksa lejant kısa metinli (1280×720'de ≈590 px) */
const DENSE_BELOW_H = 700;
/** harita bu yükseklikten alçaksa lejant açılışta kapalı başlar (1280×720: Ankara'nın güneybatısını örtmesin) */
const LEGEND_COLLAPSE_BELOW_H = 650;

/**
 * Haritanın üstündeki denetimlerin kapladığı kenarlar (H3): Ankara bunların ARASINA sığdırılır — sol üst katman
 * düğmesi, sağ araç çubuğu, sol alt lejant, sağ alt ölçek/atıf. Lejant için iki yerleşim denenir (alttan ya da
 * soldan boşluk) ve Ankara'yı daha büyük gösteren seçilir. Kenarlar haritanın %60'ını geçmez (küçük ekran).
 */
type Pad = { top: number; right: number; bottom: number; left: number };

function overlayPadding(map: MapLibreMap, wrap: HTMLElement): Pad {
  const r = wrap.getBoundingClientRect();
  const W = r.width;
  const H = r.height;
  const fallback = { top: 24, right: 24, bottom: 24, left: 24 };
  if (W <= 0 || H <= 0) return fallback;
  const rect = (sel: string) => {
    const el = wrap.querySelector(sel);
    const b = el?.getBoundingClientRect();
    return b && b.width > 0 && b.height > 0 ? b : null;
  };
  const gap = 12;
  const layersBtn = rect('[data-testid="map-layers-button"]');
  const toolbar = rect('.map-toolbar');
  const legend = rect('.map-legend');
  const scale = rect('.maplibregl-ctrl-scale');
  const attrib = rect('.maplibregl-ctrl-attrib');
  const top = layersBtn ? layersBtn.bottom - r.top + gap : 24;
  const right = toolbar ? r.right - toolbar.left + gap : 24;
  // sağ alt: ölçek çubuğu + atıf (ilk çizimde henüz boyutlanmamış olabilir → en az 60 px ayrılır)
  const brTop = Math.min(scale?.top ?? r.bottom, attrib?.top ?? r.bottom);
  const bottomRight = Math.max(60, r.bottom - brTop + 6);
  const legendBottom = legend ? r.bottom - legend.top + gap : 24;
  const legendLeft = legend ? legend.right - r.left + gap : 24;
  // Ankara kutusunun o anki yakınlaştırmadaki piksel boyutu → iki seçenekten hangisi daha büyük ölçek veriyor
  const sw = map.project(ANKARA_BOUNDS[0]);
  const ne = map.project(ANKARA_BOUNDS[1]);
  const bw = Math.abs(ne.x - sw.x) || 1;
  const bh = Math.abs(ne.y - sw.y) || 1;
  const fit = (p: Pad) => Math.min((W - p.left - p.right) / bw, (H - p.top - p.bottom) / bh);
  const a: Pad = { top, right, bottom: Math.max(legendBottom, bottomRight), left: 16 };
  const b: Pad = { top, right, bottom: bottomRight, left: Math.max(16, legendLeft) };
  const best = fit(b) > fit(a) ? b : a;
  // en az %40 alan kalsın
  const sx = Math.min(1, (0.6 * W) / Math.max(1, best.left + best.right));
  const sy = Math.min(1, (0.6 * H) / Math.max(1, best.top + best.bottom));
  return { top: best.top * sy, bottom: best.bottom * sy, left: best.left * sx, right: best.right * sx };
}

function anyCellLayerOn(v: Record<string, boolean>): boolean {
  return !!(v.population || v.access || v.serviceAreas || v.shadowPrices || v.candidates);
}

/** PNG başlığı: senaryo, ızgara, kilitler ve tek cümlelik sonuç (optimallik etiketiyle). */
function exportCaption(): { title: string; lines: string[] } | null {
  const st = useApp.getState();
  const r = selectOkResult(st);
  const sc = st.meta?.scenarios.find((x) => x.id === st.problem.scenarioId);
  const nChanged = Object.keys(st.problem.overrides).filter((k) => !(sc && k in sc.overrides)).length;
  const nLocks = st.problem.locks.open.length + st.problem.locks.closed.length;
  const parts = [`${sc?.title ?? 'Varsayılan'} senaryosu`, `${st.problem.gridKm} km ızgara`];
  if (nChanged) parts.push(`${nChanged} parametre değişikliği`);
  if (nLocks) parts.push(`${nLocks} kilit`);
  const lines: string[] = [];
  if (r) {
    const opt = optimalityLabel(r, selectUiTargetGap(st), !!st.resultInfo?.stopped);
    lines.push(summarySentence(r, opt));
  }
  const d = new Date();
  lines.push(`${parts.join(' · ')} · ${d.toLocaleDateString('tr-TR')} ${d.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })}`);
  return { title: 'Ankara Hastane Yerleşimi', lines };
}

export function MapView({ className, style, interactive = true }: MapViewProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const overlayRef = useRef<MapLibreOverlay | null>(null);
  const basemapRef = useRef<'pmtiles' | 'fallback'>('pmtiles');
  const styleSchemeRef = useRef<string | null>(null);
  const ctxRef = useRef<LayerContext | null>(null);
  const zoomRef = useRef(8.5);
  const pushedZoomRef = useRef(-1);
  const hoveredCellRef = useRef(-1);
  const userMovedRef = useRef(false);
  const labelsRef = useRef<LabelCollection | null>(null);
  const [mapState, setMapState] = useState<MapState>('loading');
  const [basemap, setBasemap] = useState<'pmtiles' | 'fallback'>('pmtiles');
  const [fatal, setFatal] = useState<string | null>(null);
  const [compact, setCompact] = useState(false);
  const [dense, setDense] = useState(false);
  const [exporting, setExporting] = useState(false);

  const scheme = useApp((s) => s.colorScheme);
  const gridKm = useApp((s) => s.problem.gridKm);
  const grid = useApp(selectGrid);
  const boundaries = useApp((s) => s.boundaries);
  const meta = useApp((s) => s.meta);
  // son sonuç olursuzsa haritada son OLURLU çözüm soluk kalır (useShownSolution)
  const { solution: result, stale } = useShownSolution();
  const locks = useApp((s) => s.problem.locks);
  const selection = useApp((s) => s.selection);
  const hoveredHospital = useApp((s) => s.hoveredHospital);
  const visible = useApp((s) => s.layers);
  const select = useApp((s) => s.select);
  const hoverHospital = useApp((s) => s.hoverHospital);
  const canLock = useApp(selectCanSolve);
  const tool = useMapUi((s) => s.tool);
  const tilted = useMapUi((s) => s.tilted);

  /* ---- deck.gl katmanlarını gönder (React dışı durum: yakınlaştırma, imleç hücresi, araç) ---- */
  const pushLayers = useCallback(() => {
    const ctx = ctxRef.current;
    const overlay = overlayRef.current;
    if (!ctx || !overlay) return;
    pushedZoomRef.current = zoomRef.current;
    overlay.setProps({
      layers: buildDeckLayers({
        ...ctx,
        zoom: zoomRef.current,
        hoveredCell: hoveredCellRef.current,
        tool: useMapUi.getState().tool,
        beforeId: FILL_ANCHOR,
      }),
    });
  }, []);

  const setTilt = useCallback((on: boolean, focusCity = false) => {
    const map = mapRef.current;
    if (!map) return;
    if (on) {
      map.dragRotate.enable();
      map.touchPitch.enable();
      map.touchZoomRotate.enableRotation();
      // 3B nüfus sütunları için: kent merkezine biraz yaklaş (sütunların en yüksek olduğu yer)
      const focus = focusCity && map.getZoom() < CITY_ZOOM ? { center: CITY_CENTER, zoom: CITY_ZOOM } : {};
      map.easeTo({ pitch: TILT_PITCH, bearing: TILT_BEARING, duration: 900, ...focus });
    } else {
      map.easeTo({ pitch: 0, bearing: 0, duration: 700 });
      map.dragRotate.disable();
      map.touchPitch.disable();
      map.touchZoomRotate.disableRotation();
    }
    useMapUi.getState().setTilted(on);
  }, []);

  const fitAnkara = useCallback((animate = true) => {
    const map = mapRef.current;
    const wrap = wrapRef.current;
    if (!map || !wrap) return;
    userMovedRef.current = false;
    map.fitBounds(ANKARA_BOUNDS, { padding: overlayPadding(map, wrap), animate, duration: animate ? 450 : 0, pitch: map.getPitch(), bearing: map.getBearing() });
  }, []);

  const exportPng = useCallback(async (opts?: Parameters<MapApi['exportPng']>[0]) => {
    const el = wrapRef.current;
    const map = mapRef.current;
    if (!el || !map) throw new Error('Harita hazır değil');
    // ipucu/menü kapalı, son kare çizilmiş olsun
    useMapUi.getState().setHover(null);
    useMapUi.getState().setMenu(null);
    await new Promise<void>((resolve) => {
      const t = window.setTimeout(resolve, 1500); // gizli sekmede kare gelmeyebilir: son kareyle devam
      map.once('render', () => {
        window.clearTimeout(t);
        resolve();
      });
      map.triggerRepaint();
    });
    // html-to-image yalnız dışa aktarımda yüklenir (ilk açılış paketine girmez)
    const { exportElementPng } = await import('./exportPng.ts');
    return exportElementPng(el, { caption: exportCaption(), ...opts });
  }, []);

  const downloadPng = useCallback(
    async (opts?: Parameters<MapApi['downloadPng']>[0]) => {
      setExporting(true);
      try {
        const url = await exportPng(opts);
        const { downloadDataUrl, mapPngFilename } = await import('./exportPng.ts');
        downloadDataUrl(url, opts?.filename ?? mapPngFilename(useApp.getState().problem.gridKm));
      } catch (ex) {
        notifications.show({ color: 'red', title: 'PNG oluşturulamadı', message: ex instanceof Error ? ex.message : String(ex) });
      } finally {
        setExporting(false);
      }
    },
    [exportPng],
  );

  /* ---- haritayı bir kez kur ---- */
  useEffect(() => {
    const el = containerRef.current;
    const wrap = wrapRef.current;
    if (!el || !wrap) return;
    setupMapLibre();
    const initialScheme = useApp.getState().colorScheme;
    styleSchemeRef.current = initialScheme;
    const labelsVisible = () => useApp.getState().layers.boundaries;
    let map: MapLibreMap;
    try {
      map = new MapLibreMap({
        container: el,
        style: basemapStyle(initialScheme, { labels: labelsRef.current, labelsVisible: labelsVisible() }),
        bounds: ANKARA_BOUNDS,
        fitBoundsOptions: { padding: 24 },
        maxBounds: PAN_LIMIT,
        minZoom: 6,
        maxZoom: 16,
        maxPitch: 70,
        attributionControl: { compact: true },
        canvasContextAttributes: { preserveDrawingBuffer: true, antialias: true },
        interactive,
        // döndürme/eğim kapalı (sunumda yön karışmasın); 3B görünümde (setTilt) açılır
        dragRotate: false,
        pitchWithRotate: true,
        touchPitch: false,
        locale: {
          'NavigationControl.ZoomIn': 'Yakınlaştır',
          'NavigationControl.ZoomOut': 'Uzaklaştır',
          'NavigationControl.ResetBearing': 'Kuzeyi yukarı al',
          'NavigationControl.ResetBearingAndPitch': 'Kuzeyi yukarı al, eğimi sıfırla',
          'ScaleControl.Meters': 'm',
          'ScaleControl.Kilometers': 'km',
          'AttributionControl.ToggleAttribution': 'Atıfları göster/gizle',
        },
      });
    } catch (ex) {
      // WebGL yok ya da bağlam açılamadı: sayfa çalışmaya devam etsin (paneller, sonuç)
      console.warn('Harita oluşturulamadı:', ex);
      setFatal(ex instanceof Error ? ex.message : String(ex));
      setMapState('error');
      return;
    }
    mapRef.current = map;
    map.touchZoomRotate.disableRotation();
    if (interactive) {
      // sağ alt: (alttan üste) atıf, ölçek, yakınlaştırma
      map.addControl(new ScaleControl({ unit: 'metric', maxWidth: 120 }), 'bottom-right');
      map.addControl(new NavigationControl({ showCompass: true, visualizePitch: true }), 'bottom-right');
    }

    const gridOf = () => ctxRef.current?.gridIndex ?? null;
    const solutionOf = () => ctxRef.current?.solution ?? null;
    const hospitalOfInfo = (info: PickingInfo): number => {
      if (info.layer?.id === DECK_IDS.hospitals && info.index >= 0) return info.index;
      const sol = solutionOf();
      const gi = gridOf();
      if (info.layer?.id === DECK_IDS.flows && info.index >= 0 && sol && gi) {
        const k = flowArcIndex(gi, sol.assignment, sol.hospitals, info.index);
        return k >= 0 ? sol.assignment.hospital[k] : -1;
      }
      return -1;
    };
    const toggleLock = (cellId: string, t: MapTool) => {
      const st = useApp.getState();
      if (!selectCanSolve(st)) {
        notifications.show({ color: 'orange', title: 'Kilit eklenemedi', message: 'Çevrimdışı modda kilitler kullanılamaz (canlı çözüm kapalı).' });
        return;
      }
      const want = t === 'lockOpen' ? 'open' : 'closed';
      const cur = st.problem.locks.open.includes(cellId) ? 'open' : st.problem.locks.closed.includes(cellId) ? 'closed' : null;
      st.setLock(cellId, cur === want ? null : want);
    };

    const onHover = (info: PickingInfo) => {
      const st = useApp.getState();
      const ui = useMapUi.getState();
      const gi = gridOf();
      const sol = solutionOf();
      let target: HoverTarget | null = null;
      let cell = -1;
      let hh: number | null = null;
      if (info.layer?.id === DECK_IDS.hospitals && info.index >= 0) {
        target = { kind: 'hospital', index: info.index };
        hh = info.index;
      } else if (info.layer?.id === DECK_IDS.flows && info.index >= 0) {
        target = { kind: 'flow', arc: info.index };
        const h = hospitalOfInfo(info);
        hh = h >= 0 ? h : null;
      } else if (info.coordinate && gi) {
        cell = gi.cellAt(info.coordinate[0], info.coordinate[1]);
        if (cell >= 0) {
          if (anyCellLayerOn(st.layers) || st.layers.population3d || ui.tool !== 'select') target = { kind: 'cell', gridIndex: cell };
          const demand = sol?.layers?.demand;
          if (demand && st.layers.serviceAreas) {
            // hücrenin bölgesini vurgula
            const k = demandPosByCell(gi, demand)[cell];
            if (k >= 0 && demand.service_hospital[k] >= 0) hh = demand.service_hospital[k];
          }
        }
      }
      if (st.hoveredHospital !== hh) st.hoverHospital(hh);
      ui.setHover(target ? { x: info.x, y: info.y, target } : null);
      if (cell !== hoveredCellRef.current) {
        hoveredCellRef.current = cell;
        if (ui.tool !== 'select' || anyCellLayerOn(st.layers)) pushLayers();
      }
    };

    /**
     * Tıklama MapLibre'nin `click` olayından (sürükleme eşiği mesafeyle; deck.gl'in "dokunma" tanıyıcısının süre
     * sınırı yavaş makinede — ya da yazılım GPU'da — tıklamayı yutabiliyordu). Hastane/akış deck.gl'den eşzamanlı
     * toplanır (pickObject), hücre tıklanan koordinattan geometrik olarak bulunur.
     */
    const onClickAt = (x: number, y: number, lon: number, lat: number) => {
      const st = useApp.getState();
      const ui = useMapUi.getState();
      const t = ui.tool;
      ui.setMenu(null);
      const sol = solutionOf();
      const gi = gridOf();
      let hi = -1;
      try {
        const picked = overlayRef.current?.pickObject({ x, y, radius: 5, layerIds: [DECK_IDS.hospitals, DECK_IDS.flows] });
        if (picked) hi = hospitalOfInfo(picked);
      } catch {
        /* toplama başarısız: yalnız hücre */
      }
      if (hi >= 0 && sol?.hospitals[hi]) {
        const h = sol.hospitals[hi];
        if (t === 'select') st.select({ kind: 'hospital', index: hi, cellId: h.cell_id });
        else toggleLock(h.cell_id, t);
        return;
      }
      const cell = gi ? gi.cellAt(lon, lat) : -1;
      const grid = ctxRef.current?.grid;
      if (cell < 0 || !grid) {
        if (t === 'select') st.select(null);
        return;
      }
      const cellId = grid.features[cell].properties.id;
      if (t === 'select') st.select({ kind: 'cell', gridIndex: cell, cellId });
      else toggleLock(cellId, t);
    };

    const overlay = new MapLibreOverlay({
      interleaved: true,
      layers: [],
      pickingRadius: 5,
      // düz (z = 0) katmanlar arasında derinlik testi yok: aynı düzlemdeki dolgu/işaret çakışmasın (z-fighting);
      // çizim sırası = katman sırası. Yalnız 3B sütunlar kendi derinlik testini açar (population.ts).
      parameters: { depthCompare: 'always', depthWriteEnabled: false },
      onHover,
      getCursor: ({ isDragging, isHovering }) =>
        isDragging ? 'grabbing' : useMapUi.getState().tool !== 'select' ? 'crosshair' : isHovering ? 'pointer' : 'grab',
    });
    overlayRef.current = overlay;
    map.addControl(overlay);

    const styleOpts = () => ({ labels: labelsRef.current, labelsVisible: labelsVisible() });
    const switchToFallback = () => {
      if (basemapRef.current === 'fallback') return;
      basemapRef.current = 'fallback';
      setBasemap('fallback');
      map.setStyle(fallbackStyle(useApp.getState().colorScheme, styleOpts()));
    };
    const onError = (e: MapErrorEvent & { sourceId?: string }) => {
      const message = e.error?.message ?? '';
      if (e.sourceId === BASEMAP_SOURCE || /pmtiles/i.test(message)) {
        console.warn('Altlık harita yüklenemedi, düz arka plana geçiliyor:', message);
        switchToFallback();
      } else {
        console.warn('Harita uyarısı:', message);
      }
    };
    map.on('error', onError);
    map.on('idle', () => setMapState('idle'));
    map.on('dataloading', () => setMapState((s) => (s === 'idle' ? 'loading' : s)));

    // yakınlaştırma: sembol ölçeği ve etiketler (React'i yeniden çizdirmeden; ≈0,1 adımda bir)
    let zoomRaf = 0;
    map.on('zoom', () => {
      zoomRef.current = map.getZoom();
      if (Math.abs(zoomRef.current - pushedZoomRef.current) < 0.08 || zoomRaf) return;
      zoomRaf = requestAnimationFrame(() => {
        zoomRaf = 0;
        pushLayers();
      });
    });
    map.on('zoomend', () => {
      zoomRef.current = map.getZoom();
      useMapUi.getState().setZoom(zoomRef.current);
      pushLayers();
    });
    zoomRef.current = map.getZoom();
    useMapUi.getState().setZoom(zoomRef.current);
    map.on('pitchend', () => {
      const t = map.getPitch() > 5;
      if (useMapUi.getState().tilted !== t) {
        useMapUi.getState().setTilted(t);
        if (!t) {
          map.dragRotate.disable();
          map.touchPitch.disable();
        }
      }
    });
    map.on('movestart', (e) => {
      if ((e as { originalEvent?: unknown }).originalEvent) userMovedRef.current = true;
    });
    map.on('mouseout', () => {
      useMapUi.getState().setHover(null);
      if (useApp.getState().hoveredHospital !== null) useApp.getState().hoverHospital(null);
      if (hoveredCellRef.current !== -1) {
        hoveredCellRef.current = -1;
        pushLayers();
      }
    });
    map.on('click', (e) => {
      if (interactive) onClickAt(e.point.x, e.point.y, e.lngLat.lng, e.lngLat.lat);
    });
    map.on('contextmenu', (e) => {
      e.originalEvent?.preventDefault();
      if (!interactive) return;
      const gi = gridOf();
      const sol = solutionOf();
      const grid = ctxRef.current?.grid;
      if (!gi || !grid) return;
      let hospital: number | null = null;
      try {
        const picked = overlay.pickObject({ x: e.point.x, y: e.point.y, radius: 5, layerIds: [DECK_IDS.hospitals] });
        if (picked && picked.index >= 0) hospital = picked.index;
      } catch {
        /* toplama başarısız: yalnız hücre */
      }
      const cell = hospital !== null && sol?.hospitals[hospital] ? sol.hospitals[hospital].grid_index : gi.cellAt(e.lngLat.lng, e.lngLat.lat);
      if (cell < 0 || !grid.features[cell]) return;
      useMapUi.getState().setHover(null);
      useMapUi.getState().setMenu({ x: e.point.x, y: e.point.y, gridIndex: cell, cellId: grid.features[cell].properties.id, hospital });
    });

    // dar ya da alçak haritada lejant kapalı başlar (Ankara'yı örtmesin; düğmeyle açılır, açılınca harita yeniden
    // sığdırılır). Kullanıcının kayıtlı tercihi geniş ekranda geçerli.
    if ((wrap.clientWidth > 0 && wrap.clientWidth < 520) || (wrap.clientHeight > 0 && wrap.clientHeight < LEGEND_COLLAPSE_BELOW_H)) {
      useMapUi.setState({ legendOpen: false });
    }

    // boyut değişince yeniden çiz; kullanıcı haritayı oynatmadıysa Ankara'ya yeniden sığdır (denetimlerin arasına)
    let refitRaf = 0;
    // ilk çizimden önce canlandırma yok (açılışta lejant kapanınca harita zıplamasın)
    let settled = false;
    map.once('idle', () => {
      settled = true;
      // denetimler (ölçek, atıf) artık boyutlandı: son yerleşimle bir kez daha sığdır
      refit(false);
    });
    const refit = (animate: boolean) => {
      if (refitRaf) cancelAnimationFrame(refitRaf);
      refitRaf = requestAnimationFrame(() => {
        refitRaf = 0;
        if (!userMovedRef.current && mapRef.current) fitAnkara(animate && settled);
      });
    };
    let phone: boolean | null = null;
    const ro = new ResizeObserver(() => {
      map.resize();
      const isPhone = wrap.clientWidth > 0 && wrap.clientWidth < 520;
      if (isPhone !== phone) {
        phone = isPhone;
        map.setMaxBounds(isPhone ? PHONE_LIMIT : PAN_LIMIT);
      }
      refit(false);
      const w = wrap.clientWidth;
      setCompact(w > 0 && w < COMPACT_BELOW);
      setDense(wrap.clientHeight > 0 && wrap.clientHeight < DENSE_BELOW_H);
    });
    ro.observe(wrap);
    // lejant açılıp kapanınca ya da içeriği büyüyünce (yeni katman anahtarı) Ankara yeniden sığdırılır
    let legendEl: Element | null = null;
    const legendRo = new ResizeObserver(() => refit(true));
    const watchLegend = () => {
      const el = wrap.querySelector('.map-legend');
      if (el === legendEl) return;
      if (legendEl) legendRo.unobserve(legendEl);
      legendEl = el;
      if (el) legendRo.observe(el);
    };
    const legendMo = new MutationObserver(watchLegend);
    legendMo.observe(wrap, { childList: true });
    watchLegend();

    // Esc (sayfanın her yerinde; açık iletişim kutusu, menü ya da yazı alanı yoksa): menü → araç → seçim
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest?.('input, textarea, select, [contenteditable="true"], [role="dialog"], [role="menu"], [role="listbox"]')) return;
      if (document.querySelector('[role="dialog"]')) return;
      const ui = useMapUi.getState();
      if (ui.menu) ui.setMenu(null);
      else if (ui.tool !== 'select') ui.setTool('select');
      else if (useApp.getState().selection) useApp.getState().select(null);
    };
    window.addEventListener('keydown', onKey);

    const api: MapApi = {
      map,
      container: wrap,
      fitAnkara: (o) => {
        userMovedRef.current = false;
        map.fitBounds(ANKARA_BOUNDS, { padding: o?.padding ?? overlayPadding(map, wrap), animate: o?.animate ?? true });
      },
      flyTo: (lon, lat, z) => map.flyTo({ center: [lon, lat], zoom: z ?? Math.max(map.getZoom(), 10), essential: true }),
      flyToHospital: (index, z) => {
        const r = selectOkResult(useApp.getState());
        const h = r?.hospitals[index];
        if (h) map.flyTo({ center: [h.lon, h.lat], zoom: z ?? Math.max(map.getZoom(), 11), essential: true });
      },
      flyToCell: (gridIndex, z) => {
        const gi = gridOf();
        if (gi && gridIndex >= 0 && gridIndex < gi.n) map.flyTo({ center: gi.centroid(gridIndex), zoom: z ?? Math.max(map.getZoom(), 11), essential: true });
      },
      toPng: () => map.getCanvas().toDataURL('image/png'),
      exportPng: (o) => exportPng(o),
      downloadPng: (o) => downloadPng(o),
      // bir sonraki boşta karesini bekle (son katman değişikliği de çizilmiş olsun)
      whenIdle: () =>
        new Promise<void>((resolve) => {
          map.once('idle', () => resolve());
          map.triggerRepaint();
        }),
      basemap: () => basemapRef.current,
      setTilt: (on) => setTilt(on),
      setTool: (t) => useMapUi.getState().setTool(t),
    };
    setMapApi(api);
    try {
      if (new URLSearchParams(window.location.search).has('e2e')) {
        const w = window as unknown as { __hastaneMap?: MapApi; __hastaneMapDebug?: unknown };
        w.__hastaneMap = api;
        // yalnız test: deck.gl ölçümleri (kare süresi, öznitelik güncelleme süresi)
        w.__hastaneMapDebug = { overlay, metrics: () => (overlay as unknown as { _deck?: { metrics?: unknown } })._deck?.metrics };
      }
    } catch {
      /* yok say */
    }

    return () => {
      setMapApi(null);
      ro.disconnect();
      legendRo.disconnect();
      legendMo.disconnect();
      if (refitRaf) cancelAnimationFrame(refitRaf);
      window.removeEventListener('keydown', onKey);
      if (zoomRaf) cancelAnimationFrame(zoomRaf);
      overlayRef.current = null;
      mapRef.current = null;
      map.remove();
    };
  }, [interactive, pushLayers, setTilt, exportPng, downloadPng, fitAnkara]);

  /* ---- tema değişimi: altlık stilini değiştir (harita hangi temayla kurulduysa ondan farklıysa) ---- */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || styleSchemeRef.current === scheme) return;
    styleSchemeRef.current = scheme;
    const opts = { labels: labelsRef.current, labelsVisible: useApp.getState().layers.boundaries };
    map.setStyle(basemapRef.current === 'fallback' ? fallbackStyle(scheme, opts) : basemapStyle(scheme, opts));
  }, [scheme]);

  /* ---- ilçe adları: sınırlar gelince kaynağa yaz; görünürlük sınır katmanıyla birlikte ---- */
  useEffect(() => {
    if (!boundaries) return;
    labelsRef.current = districtLabels(boundaries);
    const map = mapRef.current;
    const apply = () => {
      const src = map?.getSource(LABEL_SOURCE) as GeoJSONSource | undefined;
      if (src && labelsRef.current) void src.setData(labelsRef.current as unknown as FeatureCollection);
    };
    if (!map) return;
    if (map.isStyleLoaded()) apply();
    else map.once('idle', apply);
  }, [boundaries]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer(LABEL_LAYER)) map.setLayoutProperty(LABEL_LAYER, 'visibility', visible.boundaries ? 'visible' : 'none');
    };
    if (map.isStyleLoaded()) apply();
    else map.once('idle', apply);
  }, [visible.boundaries]);

  /* ---- 3B nüfus sütunları açılınca haritayı eğ ---- */
  const prev3d = useRef(visible.population3d);
  useEffect(() => {
    const was = prev3d.current;
    prev3d.current = visible.population3d;
    if (visible.population3d && !was && !useMapUi.getState().tilted) setTilt(true, true);
    // sütunlar kapanınca eğim de kalksın (düz katmanlar eğik görünümde okunaksız)
    else if (!visible.population3d && was && useMapUi.getState().tilted) setTilt(false);
  }, [visible.population3d, setTilt]);
  // tercihlerden 3B açık gelirse ilk yüklemede eğ
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !useApp.getState().layers.population3d) return;
    map.once('load', () => setTilt(true, true));
  }, [setTilt]);

  /* ---- araç: kilit araçlarında çift tıklama yakınlaştırması kapalı (iki kez kilit değişmesin) ---- */
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (tool === 'select') map.doubleClickZoom.enable();
    else map.doubleClickZoom.disable();
    pushLayers();
  }, [tool, pushLayers]);

  /* ---- deck.gl katmanları ---- */
  const gridIndex = useMemo(() => (grid ? gridIndexOf(grid, gridKm) : null), [grid, gridKm]);
  const solutionGridOk = !!result;
  const ctx: LayerContext = useMemo(
    () => ({
      scheme,
      gridKm,
      grid,
      gridIndex,
      boundaries,
      meta,
      solution: solutionGridOk ? result : null,
      stale,
      locks,
      selection,
      hoveredHospital,
      visible,
      zoom: zoomRef.current,
      actions: {
        select: (sel: Selection) => select(sel),
        hoverHospital: (i: number | null) => hoverHospital(i),
      },
    }),
    [scheme, gridKm, grid, gridIndex, boundaries, meta, solutionGridOk, result, stale, locks, selection, hoveredHospital, visible, select, hoverHospital],
  );

  useEffect(() => {
    ctxRef.current = ctx;
    pushLayers();
  }, [ctx, pushLayers]);

  // ızgara değişince imleç hücresi geçersiz
  useEffect(() => {
    hoveredCellRef.current = -1;
    useMapUi.getState().setHover(null);
    useMapUi.getState().setMenu(null);
  }, [gridKm]);

  return (
    <div
      ref={wrapRef}
      className={className}
      style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden', ...style }}
      data-testid="map"
      data-map-state={mapState}
      data-basemap={basemap}
      data-tool={tool}
      role="region"
      aria-label="Ankara haritası: hastaneler ve katmanlar"
    >
      <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />
      {fatal && (
        <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', padding: '1rem', textAlign: 'center' }}>
          Harita çizilemedi (tarayıcıda WebGL kullanılamıyor). Sonuçlar ve parametreler panellerde görünmeye devam eder.
        </div>
      )}
      {interactive && !fatal && (
        <>
          <MapLayerPanel compact={compact} top={10} />
          <MapLegend compact={compact} dense={dense} />
          <MapToolbar
            tool={tool}
            onTool={(t) => useMapUi.getState().setTool(t)}
            canLock={canLock}
            tilted={tilted}
            onTilt={() => setTilt(!tilted)}
            onFit={() => fitAnkara(true)}
            onExport={() => void downloadPng()}
            exporting={exporting}
            top={10}
          />
          <MapTooltip containerRef={wrapRef} />
          <MapContextMenu />
        </>
      )}
    </div>
  );
}

export default MapView;
