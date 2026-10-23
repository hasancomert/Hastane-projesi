/**
 * Altlık harita stili: yerel Protomaps v4 PMTiles kesiti (data/basemap/ankara.pmtiles, OSM 2026-03-31, z0–14),
 * yerel Noto Sans glifleri ve simgeler — hepsi FastAPI'nin /basemap/ yolundan, aynı kökenden. Türkçe adlar
 * (`lang: 'tr'`), açık/koyu tema `namedFlavor`.
 * Altlık yüklenemezse (dosya yok, bozuk) `fallbackStyle` kullanılır: düz arka plan; il/ilçe sınırları deck.gl
 * katmanı, ilçe adları bu stilin kendi sembol katmanı olduğu için harita yine anlamlı kalır (beyaz ekran yok).
 *
 * İki stile de iki katman eklenir:
 *   - FILL_ANCHOR: görünmez çapa; deck.gl hücre dolguları `beforeId: FILL_ANCHOR` ile bunun önüne (altlığın
 *     yollarının üstüne, YAZILARININ altına) yerleşir → yer adları dolguların üstünde okunur kalır;
 *   - LABEL_LAYER: ilçe adları (labels.ts), en üstteki sembol katmanı (çakışmada önceliği var).
 */
import { layers, namedFlavor } from '@protomaps/basemaps';
import type { FeatureCollection } from 'geojson';
import type { LayerSpecification, StyleSpecification } from 'maplibre-gl';

import { basemapUrls } from '../api/client.ts';
import { CHROME, type Scheme } from '../theme/palette.ts';
import { EMPTY_LABELS, type LabelCollection } from './labels.ts';

export const BASEMAP_SOURCE = 'protomaps';
export const FILL_ANCHOR = 'hastane-dolgu-capasi';
export const LABEL_SOURCE = 'hastane-ilce-adlari';
export const LABEL_LAYER = 'hastane-ilce-adlari';

/** Ankara il sınırının kutusu (WGS84) — /api/measures extent'ten, küçük payla. */
export const ANKARA_BOUNDS: [[number, number], [number, number]] = [
  [30.8, 38.62],
  [33.92, 40.79],
];

/** PMTiles kesitinin kapsamı (başlıktan). */
export const BASEMAP_EXTENT: [[number, number], [number, number]] = [
  [30.3, 38.2],
  [34.7, 41.2],
];

/**
 * Kaydırma sınırı: kesitten geniş tutulur — dikey (telefon) ekranda Ankara'nın tamamı sığabilsin (MapLibre
 * maxBounds görünür alanı sınırlar ve dar-uzun ekranda yakınlaştırmaya zorlar). Kesit dışı düz arka plan.
 */
export const PAN_LIMIT: [[number, number], [number, number]] = [
  [28.3, 36.4],
  [36.7, 43.0],
];

/**
 * Dar (telefon) haritada kaydırma sınırı: altlık kesitine yakın — dikey ekranda kesit dışındaki boş (gri) şerit
 * görünmesin. Ankara yine tamamen sığar (harita yüksekliği telefonda en-boy oranıyla sınırlı, global.css).
 */
export const PHONE_LIMIT: [[number, number], [number, number]] = [
  [29.5, 37.7],
  [35.5, 41.7],
];

export const BASEMAP_ATTRIBUTION = '© OpenStreetMap katkıcıları · Protomaps';

/**
 * Ankara'nın 25 ilçesinin adı (+ OSM'deki yazım çeşitleri). Altlığın yerleşim adlarından bunlar düşülür: ilçe
 * adlarını kendi katmanımız (LABEL_LAYER, büyük harf) zaten yazıyor — iki kez görünmesin ("ÇUBUK" + "Çubuk").
 */
export const DISTRICT_NAMES: readonly string[] = [
  'Akyurt', 'Altındağ', 'Ayaş', 'Bala', 'Balâ', 'Beypazarı', 'Elmadağ', 'Etimesgut', 'Evren', 'Gölbaşı', 'Güdül',
  'Haymana', 'Kahramankazan', 'Kazan', 'Kalecik', 'Keçiören', 'Kızılcahamam', 'Mamak', 'Nallıhan', 'Polatlı',
  'Pursaklar', 'Sincan', 'Yenimahalle', 'Çamlıdere', 'Çankaya', 'Çubuk', 'Şereflikoçhisar',
];

/** Altlığın yerleşim katmanları (protomaps v4 `places`): ilçe adıyla aynı olanlar gösterilmez. */
const PLACE_KINDS: Record<string, string[]> = {
  places_locality: ['locality'],
  places_subplace: ['neighbourhood', 'macrohood'],
};

function withoutDistrictNames(l: LayerSpecification): LayerSpecification {
  const kinds = PLACE_KINDS[l.id];
  if (!kinds || l.type !== 'symbol') return l;
  const names = ['literal', DISTRICT_NAMES];
  // protomaps eski (legacy) süzgeç sözdizimi kullanıyor; ifade sözdizimiyle yeniden yazılır (ikisi karıştırılamaz)
  const filter = [
    'all',
    ['in', ['get', 'kind'], ['literal', kinds]],
    ['!', ['in', ['coalesce', ['get', 'name'], ''], names]],
    ['!', ['in', ['coalesce', ['get', 'name:tr'], ''], names]],
  ];
  return { ...l, filter } as unknown as LayerSpecification;
}

export interface OverlayStyleOptions {
  labels?: LabelCollection | null;
  labelsVisible?: boolean;
}

function anchorLayer(): LayerSpecification {
  return { id: FILL_ANCHOR, type: 'background', layout: { visibility: 'none' }, paint: { 'background-opacity': 0 } };
}

function labelLayer(scheme: Scheme, visible: boolean): LayerSpecification {
  const c = CHROME[scheme];
  return {
    id: LABEL_LAYER,
    type: 'symbol',
    source: LABEL_SOURCE,
    layout: {
      visibility: visible ? 'visible' : 'none',
      'text-field': ['get', 'ad'],
      'text-font': ['Noto Sans Medium'],
      'text-size': ['interpolate', ['linear'], ['zoom'], 7, 11.5, 9, 13.5, 11, 16],
      'text-letter-spacing': 0.08,
      'text-max-width': 9,
      'text-padding': 3,
      // büyük ilçe önce yerleşir (çakışmada küçük merkez ilçeler yakınlaşınca görünür)
      'symbol-sort-key': ['-', 0, ['get', 'alan']],
    },
    paint: {
      'text-color': c.inkSecondary,
      'text-halo-color': c.surface,
      'text-halo-width': 1.6,
      'text-halo-blur': 0.4,
      'text-opacity': 0.95,
    },
  };
}

function labelSource(labels: LabelCollection | null | undefined) {
  return { type: 'geojson' as const, data: (labels ?? EMPTY_LABELS) as unknown as FeatureCollection };
}

export function basemapStyle(scheme: Scheme, opts: OverlayStyleOptions = {}): StyleSpecification {
  const u = basemapUrls();
  const flavor = namedFlavor(scheme);
  const base = (layers(BASEMAP_SOURCE, flavor, { lang: 'tr' }) as LayerSpecification[]).map((l) =>
    // kesit dışı (telefonda Ankara sığdırılınca görünen kenarlar) kara rengiyle aynı olsun, gri bant görünmesin
    l.type === 'background' ? ({ ...l, paint: { ...(l.paint ?? {}), 'background-color': flavor.earth } } as LayerSpecification) : withoutDistrictNames(l),
  );
  // çapa: ilk sembol (yazı) katmanının hemen önü
  const firstSymbol = base.findIndex((l) => l.type === 'symbol');
  const at = firstSymbol < 0 ? base.length : firstSymbol;
  const all = [...base.slice(0, at), anchorLayer(), ...base.slice(at), labelLayer(scheme, opts.labelsVisible ?? true)];
  return {
    version: 8,
    glyphs: u.glyphs,
    sprite: u.sprite(scheme),
    sources: {
      [BASEMAP_SOURCE]: {
        type: 'vector',
        url: u.pmtiles,
        attribution: BASEMAP_ATTRIBUTION,
      },
      [LABEL_SOURCE]: labelSource(opts.labels),
    },
    layers: all,
  };
}

/** Altlık yoksa: yalnız arka plan rengi (sınırlar ve veri deck.gl katmanlarıyla çizilir) + çapa + ilçe adları. */
export function fallbackStyle(scheme: Scheme, opts: OverlayStyleOptions = {}): StyleSpecification {
  return {
    version: 8,
    glyphs: basemapUrls().glyphs,
    sources: { [LABEL_SOURCE]: labelSource(opts.labels) },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': scheme === 'dark' ? '#202226' : '#eef0f2' } },
      anchorLayer(),
      labelLayer(scheme, opts.labelsVisible ?? true),
    ],
  };
}
