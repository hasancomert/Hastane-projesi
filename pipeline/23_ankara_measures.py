"""Faz 2 — "Toplam mesafe" kartı: Ankara'nın büyüklük ölçüleri ve hastane arsası / hücre alanı karşılaştırması.

Spec 4.3 madde 3 (hocanın 4. maddesi) ve madde 6. Girdilerin hepsi repoda:
  data/processed/ankara_il.geojson            il sınırı (geoBoundaries ADM1, tam çözünürlük, WGS84)
  data/processed/web/ankara_il.geojson        web için sadeleştirilmiş il sınırı (çevre karşılaştırması)
  data/processed/ankara_ilceler.geojson       25 ilçe
  data/processed/landcover/worldcover_ankara_100m.tif   %kalıcı su (bant 1), %otsu sulak alan (bant 2)
  data/reference/tuik_adnks_ankara.csv        TÜİK ADNKS 2025 ilçe nüfusları
  data/reference/kizilay.yaml                 Kızılay referans noktası (OSM)
  data/reference/ankara_alan_kaynaklari.csv   kaynaklardaki yüzölçümü değerleri
  data/reference/parameters.json              arsa/alan/ızgara/boyut parametreleri

Çıktılar:
  data/processed/ankara_olculer.json   sayılar + birimler + Türkçe yöntem metinleri (arayüz kartı dahil)
  data/processed/ankara_olculer.png    il + ilçeler, en uzak iki nokta, Kızılay → en uzak nokta, UTM kutusu

Yöntem özeti:
  * Alan ve çevre: WGS84 elipsoidinde jeodezik (pyproj.Geod.geometry_area_perimeter); alan ayrıca EPSG:6933'te.
  * Genişlikler: UTM 36N sınırlayıcı kutusu ve en batı/doğu, en kuzey/güney sınır köşeleri arası jeodezik mesafe.
  * En uzak iki nokta: UTM 36N'de dışbükey örtü köşeleri arasında Geod.inv ile kaba kuvvet; tüm köşe çiftleri
    (≈11 milyon) taranarak doğrulanır.
  * Kızılay: tüm sınır köşelerine Geod.inv; en yakın nokta Kızılay merkezli eşit uzaklıklı azimutal projeksiyonda
    (aeqd; merkezden uzaklık = jeodezik uzaklık) sınır çizgisi üzerinde aranır.
  * Ağırlık merkezi: il merkezli Lambert azimutal eşit alanlı projeksiyonda (CRS_LAEA_ANKARA) alan ağırlık merkezi.
    Düzlemdeki ağırlık merkezi projeksiyona bağlıdır; il merkezli LAEA'da şekil bozulması en azdır. Küresel silindirik
    EPSG:6933'teki ağırlık merkezi karşılaştırma için ayrıca yazılır.
  * Su: WorldCover %su × hücrenin il içinde kalan payı (10 × 10 alt örnekleme) × hücrenin gerçek (jeodezik) alanı.
    100 m Mollweide hücresi elipsoit üzerinde tam 1 ha değildir (bkz. pipeline/13_landcover.py).

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/23_ankara_measures.py
"""
from __future__ import annotations

import csv
import json
import math
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

IL_GEOJSON = common.PROCESSED / "ankara_il.geojson"
IL_WEB_GEOJSON = common.PROCESSED / "web" / "ankara_il.geojson"
ILCE_GEOJSON = common.PROCESSED / "ankara_ilceler.geojson"
LANDCOVER_TIF = common.PROCESSED / "landcover" / "worldcover_ankara_100m.tif"
TUIK_CSV = common.REFERENCE / "tuik_adnks_ankara.csv"
KIZILAY_YAML = common.REFERENCE / "kizilay.yaml"
AREA_CSV = common.REFERENCE / "ankara_alan_kaynaklari.csv"
PARAMS_JSON = common.REFERENCE / "parameters.json"
OUT_JSON = common.PROCESSED / "ankara_olculer.json"
OUT_PNG = common.PROCESSED / "ankara_olculer.png"

SUPERSAMPLE = 10          # kısmi hücre payı için alt örnekleme (100 m hücre → 10 m alt piksel)
BLOCK_ROWS = 50           # su hesabında blok yüksekliği (100 m satır)
SEGMENT_DEG = 0.0005      # aeqd/Mollweide'e geçmeden önce kenarları sıklaştırma (≈50 m)
# Çevrenin çözünürlüğe bağlılığını göstermek için sadeleştirme toleransları (m) — gösterim tercihi
SIMPLIFY_TOL_M = [50, 100, 250, 500, 1000, 2500, 5000]
SPEC_EXAMPLE_BEDS = 300   # spec 4.3 madde 6'daki örnek hastane
# İl merkezli Lambert azimutal eşit alanlı projeksiyon (ağırlık merkezi için; merkez il sınır kutusunun ortası civarı)
CRS_LAEA_ANKARA = "+proj=laea +lat_0=39.7 +lon_0=32.8 +datum=WGS84 +units=m +no_defs"

# Alan kaynakları tablosu için kısa adlar: (source_id, değer) → etiket. Eşleşmeyen satırda tam ad kısaltılır.
SHORT_LABELS = {
    ("alan_literatur_csb_ref4_altindag", 24521.0): "ÇŞB plan raporu [4] — göller hariç",
    ("alan_literatur_csb_ref4_altindag", 25706.0): "ÇŞB plan raporu [4]",
    ("alan_literatur_csb_ref5_beypazari", 25632.0): "ÇŞB plan raporu [5]",
    ("alan_literatur_csb_ref6_cankaya", 25978.0): "ÇŞB plan raporu [6]",
    ("alan_literatur_csb_polatli_2025", 26897.0): "ÇŞB Polatlı plan raporu 2025",
    ("alan_literatur_csb_karakusunlar_2020", 24521.0): "ÇŞB Karakusunlar 2020 — göller hariç",
    ("alan_literatur_valilik_cdr_2018", 25632.0): "Valilik ÇDR 2018, Çizelge E.85",
    ("alan_literatur_valilik_cdr_2018", 25476.252): "CORINE 1990 (ÇDR 2018, E.86)",
    ("alan_literatur_valilik_cdr_2018", 25654.748): "CORINE 2006/2012 (ÇDR 2018, E.86)",
    ("alan_literatur_valilik_cdr_2018", 26897.0): "Biyoçeşitlilik Atlası çalışma alanı",
    ("tuik_bt_adnks2023_yogunluk", 24521.0): "TÜİK 2023 (nüfus ÷ yoğunluk) — göller hariç",
    ("tuik_bt_adnks2025_yogunluk", 24842.63): "TÜİK 2025 (nüfus ÷ yoğunluk) — göller hariç",
    ("sinirlar_hdx_codab_tur", 25631.758): "HDX COD-AB (HGK sınırı) özniteliği",
    ("alan_literatur_hgm_ikincil_snrylmz", 25632.0): "HGM tablosu — ikincil kopya (snrylmz)",
    ("alan_literatur_hgm_ikincil_brkunver", 25632.0): "HGM tablosu — ikincil kopya (brkunver)",
}

# Renkler (diğer Faz 1 şekilleriyle aynı)
SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#1baf7a"
FILL, EDGE = "#efeeea", "#c9c8c2"


# ----------------------------------------------------------------------------- yardımcılar
def rel(p: Path) -> str:
    return str(p.relative_to(common.ROOT))


def tr(x: float, d: int = 1) -> str:
    """Türkçe sayı metni; ikili kayan nokta yerine ondalık yarım-yukarı yuvarlama (0,975 → 0,98)."""
    q = Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    txt = common.fmt_tr(float(q), d)
    return "−" + txt[1:] if txt.startswith("-") else txt   # tipografik eksi


def pct(x: float, d: int = 2) -> str:
    """Türkçe yüzde metni: %0,43 · −%0,07."""
    return ("−%" if x < 0 else "%") + tr(abs(x), d)


def az360(a: float) -> float:
    """Azimut (derece, kuzeyden saat yönünde) 0–360 aralığında."""
    return round(float(a) % 360.0, 2)


def measure(value: float, unit: str, method: str, d: int = 1, nd: int = 3) -> dict:
    """Sayı + birim + yöntem + Türkçe biçimli metin."""
    return {"value": round(float(value), nd), "unit": unit, "method": method, "text": f"{tr(value, d)} {unit}"}


def load_features(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def transformer(src: str, dst: str):
    from pyproj import Transformer
    return Transformer.from_crs(src, dst, always_xy=True)


def project(geom, src: str, dst: str):
    import shapely
    t = transformer(src, dst)
    return shapely.transform(geom, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))


def geod():
    from pyproj import Geod
    return Geod(ellps="WGS84")


def geodesic_area_perimeter(geom) -> tuple[float, float]:
    """(alan km², çevre km) — WGS84 elipsoidi, kenarlar jeodezik."""
    a, p = geod().geometry_area_perimeter(geom)
    return abs(a) / 1e6, p / 1e3


def point(lon: float, lat: float, **extra) -> dict:
    return {"lon": round(float(lon), 6), "lat": round(float(lat), 6), **extra}


def ring_vertices(poly) -> np.ndarray:
    """Dış halkanın köşeleri (kapanış noktası hariç), WGS84 (lon, lat)."""
    xy = np.asarray(poly.exterior.coords)
    return xy[:-1] if np.allclose(xy[0], xy[-1]) else xy


def vertex_count(geom) -> int:
    """Kapanış noktaları hariç köşe sayısı (tüm halkalar)."""
    import shapely
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    return int(sum(shapely.get_num_coordinates(r) - 1 for p in polys for r in [p.exterior, *p.interiors]))


# ----------------------------------------------------------------------------- girdiler
def load_inputs() -> dict:
    import yaml
    from shapely.geometry import shape

    il_f = load_features(IL_GEOJSON)
    assert len(il_f) == 1
    il = shape(il_f[0]["geometry"])
    web = shape(load_features(IL_WEB_GEOJSON)[0]["geometry"])
    ilce = [(f["properties"], shape(f["geometry"])) for f in load_features(ILCE_GEOJSON)]
    with open(TUIK_CSV, encoding="utf-8") as f:
        tuik = {r["ilce_key"]: r for r in csv.DictReader(f)}
    with open(AREA_CSV, encoding="utf-8") as f:
        area_rows = list(csv.DictReader(f))
    kiz = yaml.safe_load(KIZILAY_YAML.read_text(encoding="utf-8"))
    params = json.loads(PARAMS_JSON.read_text(encoding="utf-8"))
    assert il.geom_type == "Polygon" and len(il.interiors) == 0, "il poligonu tek parçalı ve deliksiz bekleniyor"
    assert len(ilce) == 25 and set(tuik) == {p["ilce_key"] for p, _ in ilce}
    return {"il": il, "il_props": il_f[0]["properties"], "web": web, "ilce": ilce, "tuik": tuik,
            "area_rows": area_rows, "kizilay": kiz, "params": params}


def district_at(lon: float, lat: float, ilce, ilce_utm, to_utm) -> dict:
    """Noktanın bulunduğu (sınırdaysa en yakın) ilçe."""
    from shapely.geometry import Point
    x, y = to_utm.transform(lon, lat)
    p = Point(x, y)
    d = [g.distance(p) for g in ilce_utm]
    k = int(np.argmin(d))
    return {"ilce": ilce[k][0]["ilce"], "ilce_key": ilce[k][0]["ilce_key"],
            "inside": bool(ilce_utm[k].contains(p)), "distance_to_district_m": round(float(d[k]), 1)}


# ----------------------------------------------------------------------------- 1. alan ve çevre
def area_perimeter(inp: dict) -> tuple[dict, dict, dict]:
    import shapely

    il, web = inp["il"], inp["web"]
    a_geo, p_geo = geodesic_area_perimeter(il)
    il_ea = project(il, common.CRS_WGS84, common.CRS_EQUAL_AREA)
    il_utm = project(il, common.CRS_WGS84, common.CRS_UTM36N)
    a_ea = il_ea.area / 1e6
    a_utm, p_utm = il_utm.area / 1e6, il_utm.length / 1e3
    n = vertex_count(il)

    # kenar uzunlukları (jeodezik)
    v = ring_vertices(il)
    v2 = np.vstack([v, v[:1]])
    _, _, seg = geod().inv(v2[:-1, 0], v2[:-1, 1], v2[1:, 0], v2[1:, 1])
    boundary = {
        "file": rel(IL_GEOJSON), "source": inp["il_props"].get("source"),
        "geometry_type": il.geom_type, "parts": 1, "holes": len(il.interiors),
        "vertex_count": n,
        "segment_km": {"mean": round(float(seg.mean()) / 1e3, 4), "median": round(float(np.median(seg)) / 1e3, 4),
                       "max": round(float(seg.max()) / 1e3, 3), "min_m": round(float(seg.min()), 2)},
        "note": (f"Tam çözünürlüklü il sınırı tek parçalı, deliksiz bir çokgen: {tr(n, 0)} köşe (kapanış noktası "
                 f"hariç), kenar uzunluğu medyanı {tr(np.median(seg), 0)} m, en uzun kenar {tr(seg.max() / 1e3, 2)} km."),
    }
    area = {
        "geodesic_km2": measure(a_geo, "km²", "WGS84 elipsoidinde jeodezik alan (pyproj.Geod.geometry_area_perimeter; "
                                "kenarlar jeodezik, Karney 2013)."),
        "epsg6933_km2": measure(a_ea, "km²", "EPSG:6933 (WGS 84 / NSIDC EASE-Grid 2.0 Global, silindirik eşit alanlı) "
                                "projeksiyonunda düzlem alanı (shapely)."),
        "utm36n_km2": measure(a_utm, "km²", "UTM 36N (EPSG:32636) düzlem alanı — eşit alanlı değil, yalnız karşılaştırma."),
        "rel_diff_epsg6933_vs_geodesic_pct": round(100 * (a_ea / a_geo - 1), 6),
        "rel_diff_utm36n_vs_geodesic_pct": round(100 * (a_utm / a_geo - 1), 6),
        "phase1_property_area_km2_epsg6933": inp["il_props"].get("area_km2"),
        "note": (f"Jeodezik alan ile EPSG:6933 alanı arasındaki fark {tr(abs(a_ea - a_geo), 3)} km² "
                 f"({pct(100 * abs(a_ea / a_geo - 1), 5)}); UTM 36N eşit alanlı olmadığı için "
                 f"{pct(100 * (a_utm / a_geo - 1), 3)} sapar."),
    }

    # çözünürlüğe bağlılık: web sürümü + sadeleştirme dizisi
    a_web, p_web = geodesic_area_perimeter(web)
    series = []
    for tol in SIMPLIFY_TOL_M:
        s_utm = shapely.simplify(il_utm, tol, preserve_topology=True)
        s = project(s_utm, common.CRS_UTM36N, common.CRS_WGS84)
        a_s, p_s = geodesic_area_perimeter(s)
        series.append({"tolerance_m": tol, "vertex_count": vertex_count(s), "perimeter_km": round(p_s, 3),
                       "area_km2": round(a_s, 3), "perimeter_diff_pct": round(100 * (p_s / p_geo - 1), 3),
                       "area_diff_pct": round(100 * (a_s / a_geo - 1), 4)})
    s5 = next(r for r in series if r["tolerance_m"] == 5000)
    perim = {
        "geodesic_km": measure(p_geo, "km", "WGS84 elipsoidinde jeodezik çevre (pyproj.Geod.geometry_area_perimeter)."),
        "utm36n_km": round(p_utm, 3),
        "web": {"file": rel(IL_WEB_GEOJSON), "vertex_count": vertex_count(web), "perimeter_km": round(p_web, 3),
                "area_km2": round(a_web, 3), "perimeter_diff_km": round(p_web - p_geo, 3),
                "perimeter_diff_pct": round(100 * (p_web / p_geo - 1), 3), "area_diff_km2": round(a_web - a_geo, 3),
                "area_diff_pct": round(100 * (a_web / a_geo - 1), 4),
                "method": "pipeline/11_boundaries.py: ortak sınırlar tek yay olarak 0,0004° (≈35–45 m) toleransla "
                          "sadeleştirilmiş, koordinatlar 5 ondalığa yuvarlanmış."},
        "simplification_series": series,
        "simplification_method": "Tam sınır UTM 36N'de Douglas–Peucker ile (shapely.simplify, topoloji korunarak) "
                                 "verilen toleranslarla sadeleştirilip jeodezik çevre ve alan yeniden hesaplandı. "
                                 "Tolerans dizisi gösterim tercihidir (model_secimi).",
        "note": (f"Çevre sınırın çözünürlüğüne bağlıdır (kıyı çizgisi etkisi): web sürümünde köşe sayısı "
                 f"{tr(n, 0)} → {tr(vertex_count(web), 0)}, çevre {tr(p_geo, 1)} → {tr(p_web, 1)} km "
                 f"({pct(100 * (p_web / p_geo - 1), 2)}); 5 km toleransla {tr(s5['vertex_count'], 0)} köşede çevre "
                 f"{tr(s5['perimeter_km'], 1)} km ({pct(s5['perimeter_diff_pct'], 1)}). Alan ise neredeyse hiç değişmez "
                 f"(web {pct(100 * (a_web / a_geo - 1), 4)}; 5 km {pct(s5['area_diff_pct'], 3)}). Daha ayrıntılı bir "
                 f"sınır (ör. OSM'nin tam çözünürlüğü) daha uzun bir çevre verir; çevre için 'tek doğru' değer yoktur."),
    }
    return boundary, area, perim


# ----------------------------------------------------------------------------- 2. genişlikler
def extents(inp: dict, ctx: dict) -> dict:
    from pyproj import Proj

    v = ring_vertices(inp["il"])
    to_utm = transformer(common.CRS_WGS84, common.CRS_UTM36N)
    x, y = to_utm.transform(v[:, 0], v[:, 1])
    g = geod()
    iw, ie, i_n, i_s = int(np.argmin(v[:, 0])), int(np.argmax(v[:, 0])), int(np.argmax(v[:, 1])), int(np.argmin(v[:, 1]))

    def pt(i, label):
        return point(v[i, 0], v[i, 1], label=label, x_utm=round(float(x[i]), 1), y_utm=round(float(y[i]), 1),
                     district=ctx["district_at"](v[i, 0], v[i, 1]))

    az_we, _, d_we = g.inv(v[iw, 0], v[iw, 1], v[ie, 0], v[ie, 1])
    az_ns, _, d_ns = g.inv(v[i_n, 0], v[i_n, 1], v[i_s, 0], v[i_s, 1])
    fac = Proj(common.CRS_UTM36N).get_factors(v[:, 0], v[:, 1])
    k = np.asarray(fac.meridional_scale)
    conv = np.asarray(fac.meridian_convergence)
    w_km, h_km = (x.max() - x.min()) / 1e3, (y.max() - y.min()) / 1e3
    return {
        "utm_bbox": {
            "crs": common.CRS_UTM36N,
            "xmin_m": round(float(x.min()), 1), "ymin_m": round(float(y.min()), 1),
            "xmax_m": round(float(x.max()), 1), "ymax_m": round(float(y.max()), 1),
            "width_km": measure(w_km, "km", "UTM 36N (EPSG:32636) sınırlayıcı kutusunun doğu–batı kenarı "
                                "(sınır köşelerinin en büyük − en küçük x'i)."),
            "height_km": measure(h_km, "km", "UTM 36N sınırlayıcı kutusunun kuzey–güney kenarı "
                                 "(sınır köşelerinin en büyük − en küçük y'si)."),
            "note": (f"Kutu UTM ızgara kuzeyine hizalıdır; il sınırında ızgara ile coğrafi kuzey arasındaki açı "
                     f"(meridyen yakınsaması) {tr(conv.min(), 2)}° … {tr(conv.max(), 2)}°, UTM ölçek çarpanı "
                     f"{tr(k.min(), 5)} … {tr(k.max(), 5)}; bu yüzden kutu kenarları gerçek uzunluklardan en çok "
                     f"{pct(100 * max(abs(k.min() - 1), abs(k.max() - 1)), 3)} sapar."),
        },
        "west": pt(iw, "en batı"), "east": pt(ie, "en doğu"), "north": pt(i_n, "en kuzey"), "south": pt(i_s, "en güney"),
        "west_east_geodesic_km": measure(d_we / 1e3, "km", "En batı ve en doğu sınır köşeleri (en küçük / en büyük "
                                         "boylam) arasındaki jeodezik mesafe (pyproj.Geod.inv, WGS84)."),
        "north_south_geodesic_km": measure(d_ns / 1e3, "km", "En kuzey ve en güney sınır köşeleri (en büyük / en küçük "
                                           "enlem) arasındaki jeodezik mesafe (pyproj.Geod.inv, WGS84)."),
        "west_east_azimuth_deg": az360(az_we), "north_south_azimuth_deg": az360(az_ns),
        "lon_span_deg": round(float(v[ie, 0] - v[iw, 0]), 5), "lat_span_deg": round(float(v[i_n, 1] - v[i_s, 1]), 5),
        "utm_scale_factor_min": round(float(k.min()), 6), "utm_scale_factor_max": round(float(k.max()), 6),
        "meridian_convergence_deg_min": round(float(conv.min()), 3),
        "meridian_convergence_deg_max": round(float(conv.max()), 3),
        "note": ("En batı ve en doğu noktalar aynı enlemde değildir; aralarındaki jeodezik mesafe kuzey–güney bileşeni de "
                 "içerir (en kuzey/en güney için tersi). Kenarlar GeoJSON'da boylam–enlem doğrusu olduğundan uç noktalar "
                 "köşelerdedir."),
    }


# ----------------------------------------------------------------------------- 3. en uzak iki nokta
def diameter(inp: dict, ctx: dict) -> dict:
    from scipy.spatial import ConvexHull

    v = ring_vertices(inp["il"])
    n = len(v)
    to_utm = transformer(common.CRS_WGS84, common.CRS_UTM36N)
    x, y = to_utm.transform(v[:, 0], v[:, 1])
    hull = ConvexHull(np.column_stack([x, y]))
    hv = np.asarray(hull.vertices)
    g = geod()
    # örtü köşe çiftleri (kaba kuvvet)
    ii, jj = np.triu_indices(len(hv), k=1)
    a, b = hv[ii], hv[jj]
    _, _, d = g.inv(v[a, 0], v[a, 1], v[b, 0], v[b, 1])
    k = int(np.argmax(d))
    i1, i2, dmax = int(a[k]), int(b[k]), float(d[k])
    # doğrulama: tüm köşe çiftleri
    best = (0.0, -1, -1)
    for i in range(n - 1):
        m = n - i - 1
        _, _, di = g.inv(np.full(m, v[i, 0]), np.full(m, v[i, 1]), v[i + 1:, 0], v[i + 1:, 1])
        j = int(np.argmax(di))
        if di[j] > best[0]:
            best = (float(di[j]), i, i + 1 + j)
    same = {best[1], best[2]} == {i1, i2}
    assert same and abs(best[0] - dmax) < 1e-6, "örtü sonucu kaba kuvvetle eşleşmiyor"
    az12, _, _ = g.inv(v[i1, 0], v[i1, 1], v[i2, 0], v[i2, 1])
    e_utm = math.hypot(x[i1] - x[i2], y[i1] - y[i2]) / 1e3
    # UTM'deki en uzak çift (Öklid) — karşılaştırma
    du = np.hypot(x[a] - x[b], y[a] - y[b])
    ku = int(np.argmax(du))
    p1 = point(v[i1, 0], v[i1, 1], x_utm=round(float(x[i1]), 1), y_utm=round(float(y[i1]), 1),
               district=ctx["district_at"](v[i1, 0], v[i1, 1]))
    p2 = point(v[i2, 0], v[i2, 1], x_utm=round(float(x[i2]), 1), y_utm=round(float(y[i2]), 1),
               district=ctx["district_at"](v[i2, 0], v[i2, 1]))
    return {
        "distance_km": measure(dmax / 1e3, "km", "Sınır köşelerinin UTM 36N'deki dışbükey örtüsünün köşe çiftleri "
                               "arasında en büyük jeodezik mesafe (pyproj.Geod.inv, WGS84); tüm köşe çiftleriyle "
                               "doğrulandı."),
        "p1": p1, "p2": p2, "azimuth_deg": az360(az12),
        "vertex_count": n, "hull_vertex_count": int(len(hv)), "hull_pairs": int(len(a)),
        "bruteforce_pairs": int(n * (n - 1) // 2), "bruteforce_distance_km": round(best[0] / 1e3, 6),
        "bruteforce_same_pair": bool(same),
        "utm36n_euclid_km": round(e_utm, 3),
        "utm36n_max_pair_same": bool({int(a[ku]), int(b[ku])} == {i1, i2}),
        "proof": ("Düzlemde (UTM 36N) sabit bir a noktası için f(b) = |a − b| dışbükey bir fonksiyondur; dışbükey bir "
                  "fonksiyonun dışbükey bir çokgen üzerindeki en büyük değeri çokgenin bir köşesinde alınır. Bu yüzden "
                  "önce b, sonra a için uygulanınca bir nokta kümesinin en uzak iki noktasının ikisi de dışbükey örtünün "
                  f"köşeleridir. Örtüde {len(hv)} köşe ({len(a)} çift) kaldı ve hepsi Geod.inv ile tarandı. Jeodezik "
                  "mesafe UTM Öklid mesafesiyle birebir aynı olmadığından (ölçek çarpanı ≈1, bkz. extent) sonuç ayrıca "
                  f"tüm {tr(n, 0)} köşenin {tr(n * (n - 1) // 2, 0)} çiftinin jeodezik kaba kuvvet taramasıyla "
                  "doğrulandı: aynı çift, aynı mesafe."),
    }


# ----------------------------------------------------------------------------- 4. Kızılay ve ağırlık merkezi
def kizilay(inp: dict, ctx: dict) -> tuple[dict, dict]:
    import shapely
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    kz = inp["kizilay"]
    lon0, lat0 = float(kz["lon"]), float(kz["lat"])
    il = inp["il"]
    v = ring_vertices(il)
    g = geod()
    az, _, d = g.inv(np.full(len(v), lon0), np.full(len(v), lat0), v[:, 0], v[:, 1])
    i_far, i_near = int(np.argmax(d)), int(np.argmin(d))

    # en yakın sınır noktası: Kızılay merkezli aeqd'de sınır çizgisi üzerinde
    aeqd = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs"
    ring = shapely.segmentize(il.exterior, SEGMENT_DEG)
    ring_a = project(ring, common.CRS_WGS84, aeqd)
    q = nearest_points(ring_a, Point(0.0, 0.0))[0]
    to_ll = transformer(aeqd, common.CRS_WGS84)
    qlon, qlat = to_ll.transform(q.x, q.y)
    az_q, _, d_q = g.inv(lon0, lat0, qlon, qlat)
    # aeqd sağlaması: merkezden uzaklık = jeodezik uzaklık
    to_a = transformer(common.CRS_WGS84, aeqd)
    ax_, ay_ = to_a.transform(v[:, 0], v[:, 1])
    aeqd_err = float(np.max(np.abs(np.hypot(ax_, ay_) - d)))
    inside = bool(il.contains(Point(lon0, lat0)))

    kiz = {
        "point": point(lon0, lat0, name=kz.get("name"), osm=f"{kz.get('osm_type')}/{kz.get('osm_id')}",
                       source_id=kz.get("source_id"), district=ctx["district_at"](lon0, lat0), inside_province=inside),
        "farthest": {
            **point(v[i_far, 0], v[i_far, 1], district=ctx["district_at"](v[i_far, 0], v[i_far, 1])),
            "distance_km": measure(d[i_far] / 1e3, "km", "Kızılay'dan tüm il sınırı köşelerine jeodezik mesafelerin en "
                                   "büyüğü (pyproj.Geod.inv, WGS84). Uzaklık aeqd düzleminde dışbükey olduğundan sınır "
                                   "üzerindeki en uzak nokta bir köşedir."),
            "azimuth_deg": az360(az[i_far]),
        },
        "nearest": {
            **point(qlon, qlat, district=ctx["district_at"](qlon, qlat)),
            "distance_km": measure(d_q / 1e3, "km", "Kızılay merkezli eşit uzaklıklı azimutal projeksiyonda (aeqd, "
                                   f"WGS84) {SEGMENT_DEG}° aralıkla sıklaştırılmış il sınırı çizgisinin Kızılay'a en "
                                   "yakın noktası; mesafe Geod.inv ile."),
            "azimuth_deg": az360(az_q),
        },
        "nearest_vertex": {**point(v[i_near, 0], v[i_near, 1]), "distance_km": round(float(d[i_near]) / 1e3, 3)},
        "aeqd_check_max_abs_diff_m": round(aeqd_err, 4),
    }

    # eşit alanlı ağırlık merkezi: il merkezli LAEA (karşılaştırma: EPSG:6933)
    def area_centroid(crs: str) -> tuple[float, float]:
        c = project(il, common.CRS_WGS84, crs).centroid
        return transformer(crs, common.CRS_WGS84).transform(c.x, c.y)

    clon, clat = area_centroid(CRS_LAEA_ANKARA)
    az_c, _, d_c = g.inv(lon0, lat0, clon, clat)
    elon, elat = area_centroid(common.CRS_EQUAL_AREA)
    _, _, d_e = g.inv(lon0, lat0, elon, elat)
    _, _, shift = g.inv(clon, clat, elon, elat)
    cen = {
        "point": point(clon, clat, district=ctx["district_at"](clon, clat),
                       inside_province=bool(il.contains(Point(clon, clat)))),
        "projection": CRS_LAEA_ANKARA,
        "method": ("İl poligonunun il merkezli Lambert azimutal eşit alanlı projeksiyondaki (LAEA, merkez 39,7° K, 32,8° D, "
                   "WGS84) alan ağırlık merkezi, WGS84'e geri çevrildi."),
        "distance_to_kizilay_km": measure(d_c / 1e3, "km", "Kızılay ile eşit alanlı (il merkezli LAEA) ağırlık merkezi "
                                          "arasındaki jeodezik mesafe (pyproj.Geod.inv)."),
        "azimuth_from_kizilay_deg": az360(az_c),
        "comparison_epsg6933": {
            **point(elon, elat),
            "distance_to_kizilay_km": round(float(d_e) / 1e3, 3),
            "shift_from_laea_km": round(float(shift) / 1e3, 3),
            "note": ("Küresel silindirik eşit alanlı EPSG:6933 düzleminde bulunan ağırlık merkezi. Alanlar her iki "
                     "projeksiyonda korunur ama düzlemdeki ağırlık merkezi projeksiyonun şekil bozulmasına bağlıdır; "
                     "il merkezli LAEA'da bozulma en azdır, bu yüzden o kullanılır."),
        },
    }
    return kiz, cen


# ----------------------------------------------------------------------------- 5–6. su ve ilçeler
def row_cell_areas(transform, nrows: int, densify: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Mollweide 100 m ızgarasında satır başına bir hücrenin gerçek alanı (m²): (jeodezik, EPSG:6933).

    Mollweide'de bir satırdaki hücrelerin alanı aynıdır. Kenarlar sıklaştırılıp coğrafi koordinata çevrilir; alan
    pyproj.Geod ile (jeodezik) ve EPSG:6933 düzleminde (sağlama) bulunur. Yöntem pipeline/13_landcover.py ile aynı.
    """
    from shapely.geometry import Polygon

    to_ll = transformer(common.CRS_MOLLWEIDE, common.CRS_WGS84)
    to_ea = transformer(common.CRS_WGS84, common.CRS_EQUAL_AREA)
    g = geod()
    t = np.linspace(0.0, 1.0, densify, endpoint=False)
    x0, dx, dy = transform.c, transform.a, transform.e   # dy < 0
    ring = np.concatenate([np.c_[x0 + t * dx, np.zeros_like(t)], np.c_[np.full_like(t, x0 + dx), t * dy],
                           np.c_[x0 + dx - t * dx, np.full_like(t, dy)], np.c_[np.full_like(t, x0), dy - t * dy]])
    geo, ea = np.empty(nrows), np.empty(nrows)
    for r in range(nrows):
        lon, lat = to_ll.transform(ring[:, 0], transform.f + r * dy + ring[:, 1])
        geo[r] = abs(g.polygon_area_perimeter(lon, lat)[0])
        ex, ey = to_ea.transform(lon, lat)
        ea[r] = Polygon(np.c_[ex, ey]).area
    return geo, ea


def water_and_districts(inp: dict) -> tuple[dict, list[dict]]:
    import rasterio
    import shapely
    from rasterio.features import rasterize
    from rasterio.transform import Affine

    with rasterio.open(LANDCOVER_TIF) as ds:
        water = ds.read(1)
        wet = ds.read(2)
        tf, (H, W) = ds.transform, (ds.height, ds.width)
    nodata = (water == 255)
    wf = np.where(nodata, 0.0, water / 100.0)
    tf_ = np.where(wet == 255, 0.0, wet / 100.0)
    row_geo, row_ea = row_cell_areas(tf, H)
    row_km2 = row_geo / 1e6

    def to_moll(geom):
        return project(shapely.segmentize(geom, SEGMENT_DEG), common.CRS_WGS84, common.CRS_MOLLWEIDE)

    il_m = to_moll(inp["il"])
    ilce = inp["ilce"]
    ilce_m = [to_moll(gm) for _, gm in ilce]
    nk = len(ilce) + 1  # 0 = il dışı
    S = SUPERSAMPLE
    # alt piksel → hücre sütunu
    colpart = (np.arange(W * S, dtype=np.int64) // S)
    acc = {"prov_cov": np.zeros((H, W), np.float32)}
    d_area = np.zeros(nk)
    d_water = np.zeros(nk)
    d_wet = np.zeros(nk)
    for r0 in range(0, H, BLOCK_ROWS):
        h = min(BLOCK_ROWS, H - r0)
        btf = tf * Affine.translation(0, r0) * Affine.scale(1.0 / S)
        shape = (h * S, W * S)
        pm = rasterize([(il_m, 1)], out_shape=shape, transform=btf, fill=0, dtype="uint8")
        cov = pm.reshape(h, S, W, S).sum(axis=(1, 3), dtype=np.int32) / float(S * S)
        acc["prov_cov"][r0:r0 + h] = cov
        lab = rasterize([(gm, k + 1) for k, gm in enumerate(ilce_m)], out_shape=shape, transform=btf, fill=0,
                        dtype="uint8")
        rowpart = (np.arange(h * S, dtype=np.int64) // S) * W
        idx = (rowpart[:, None] + colpart[None, :]) * nk + lab
        cnt = np.bincount(idx.ravel(), minlength=h * W * nk).reshape(h, W, nk) / float(S * S)
        area_c = row_km2[r0:r0 + h, None, None] * cnt               # km² (hücre × ilçe)
        d_area += area_c.sum(axis=(0, 1))
        d_water += (area_c * wf[r0:r0 + h, :, None]).sum(axis=(0, 1))
        d_wet += (area_c * tf_[r0:r0 + h, :, None]).sum(axis=(0, 1))
        del pm, lab, idx, cnt, area_c
    cov = acc["prov_cov"].astype(np.float64)
    cell_km2 = row_km2[:, None]
    prov_area_r = float((cov * cell_km2).sum())
    prov_water = float((cov * wf * cell_km2).sum())
    prov_wet = float((cov * tf_ * cell_km2).sum())
    # karşılaştırma: hücre merkezi poligonda (Faz 1 yöntemi) ve 'hücre sayısı × 1 ha' (yanlış yöntem)
    centre = rasterize([(il_m, 1)], out_shape=(H, W), transform=tf, fill=0, dtype="uint8").astype(bool)
    centre_water = float((wf * centre * cell_km2).sum())
    nominal_water = float((wf * centre).sum() * 0.01)
    nominal_area = float(centre.sum() * 0.01)
    a_geo, _ = geodesic_area_perimeter(inp["il"])
    nodata_in = int((nodata & (cov > 0)).sum())
    land = a_geo - prov_water
    pop_total = sum(int(r["nufus_2025"]) for r in inp["tuik"].values())

    water = {
        "permanent_water_km2": measure(prov_water, "km²", (
            "ESA WorldCover 2021 sınıf 80 (kalıcı su) payı, 100 m Mollweide hücrelerinde: Σ (hücrenin %su'yu × hücrenin "
            f"il içinde kalan payı × hücrenin gerçek alanı). İl içi pay {S} × {S} alt örneklemeyle (10 m alt piksel "
            "merkezi poligonda), hücre alanı satır başına WGS84 jeodezik alan (EPSG:6933 ile sağlandı); Mollweide hücre "
            "sayısı × 1 ha kullanılmadı."), d=1),
        "wetland_km2": measure(prov_wet, "km²", "Aynı yöntemle WorldCover sınıf 90 (otsu sulak alan).", d=1, nd=3),
        "water_share_pct": round(100 * prov_water / a_geo, 4),
        "land_area_km2": measure(land, "km²", "Jeodezik il alanı − kalıcı su (WorldCover 80): göller hariç kaynak "
                                 "değerleriyle karşılaştırma içindir.", d=1),
        "land_area_excl_wetland_km2": measure(land - prov_wet, "km²", "Jeodezik il alanı − kalıcı su − otsu sulak alan "
                                              "(WorldCover 80 + 90).", d=1),
        "province_density_per_km2": round(pop_total / a_geo, 2),
        "province_density_land_per_km2": round(pop_total / land, 2),
        "checks": {
            "raster_province_area_km2": round(prov_area_r, 3),
            "raster_vs_polygon_area_pct": round(100 * (prov_area_r / a_geo - 1), 5),
            "pixel_centre_water_km2": round(centre_water, 3),
            "pixel_centre_method": "Faz 1 yöntemi (pipeline/13_landcover.py): yalnız merkezi il poligonunda olan "
                                   "hücreler, jeodezik hücre alanıyla.",
            "nominal_1ha_water_km2": round(nominal_water, 3),
            "nominal_1ha_province_area_km2": round(nominal_area, 3),
            "nominal_1ha_note": ("Yanlış yöntem (yalnız karşılaştırma): Mollweide hücre sayısı × 1 ha. PROJ'un "
                                 "Mollweide'i küre formülüyle çalıştığından 100 m hücre elipsoit üzerinde "
                                 f"{tr(row_geo.min(), 1)}–{tr(row_geo.max(), 1)} m²dir; bu yöntem su alanını "
                                 f"{pct(100 * (nominal_water / prov_water - 1), 3)} fazla verir."),
            "cell_area_m2_min": round(float(row_geo.min()), 3), "cell_area_m2_max": round(float(row_geo.max()), 3),
            "cell_area_geodesic_vs_epsg6933_max_rel_diff": float(np.max(np.abs(row_ea / row_geo - 1))),
            "nodata_cells_in_province": nodata_in,
            "rounding_note": "Raster bant değerleri tam sayı yüzdedir (0–100); hücre başına ±0,5 puan yuvarlama içerir.",
        },
        "source_ids": ["arazi_osm_worldcover_tiles_s3", "alan_literatur_lit_zanaga2022"],
    }

    rows = []
    for k, (props, gm) in enumerate(ilce):
        a, p = geodesic_area_perimeter(gm)
        key = props["ilce_key"]
        pop = int(inp["tuik"][key]["nufus_2025"])
        wk = float(d_water[k + 1])
        rows.append({
            "ilce": props["ilce"], "ilce_key": key,
            "area_geodesic_km2": round(a, 3), "area_epsg6933_km2_phase1": props.get("area_km2"),
            "area_raster_km2": round(float(d_area[k + 1]), 3),
            "perimeter_km": round(p, 3), "vertex_count": vertex_count(gm),
            "pop_2025": pop, "density_per_km2": round(pop / a, 2),
            "water_km2": round(wk, 3), "wetland_km2": round(float(d_wet[k + 1]), 3),
            "water_share_pct": round(100 * wk / a, 3),
            "density_land_per_km2": round(pop / (a - wk), 2),
        })
    rows.sort(key=lambda r: -r["density_per_km2"])
    water["districts_water_sum_km2"] = round(float(d_water[1:].sum()), 3)
    return water, rows


# ----------------------------------------------------------------------------- 5. alan kaynakları tablosu
def area_comparison(inp: dict, area: dict, water: dict) -> dict:
    a_geo = area["geodesic_km2"]["value"]
    land = water["land_area_km2"]["value"]
    rows = []
    for r in inp["area_rows"]:
        val = float(r["deger_km2"])
        lakes = "göller hariç" in r["kapsam"].lower() or "gölleri hariç" in r["kapsam"].lower()
        short = SHORT_LABELS.get((r["source_id"], round(val, 3)))
        row = {
            "source": short or (r["kaynak"][:60] + ("…" if len(r["kaynak"]) > 60 else "")),
            "source_full": r["kaynak"], "value_km2": val, "scope": r["kapsam"], "source_id": r["source_id"],
            "status": r["status"], "lakes_excluded": lakes,
            "diff_km2": round(val - a_geo, 3), "diff_pct": round(100 * (val / a_geo - 1), 3),
            "text": f"{tr(val, 2 if val % 1 else 0)} km²",
        }
        if lakes:
            row["diff_vs_land_km2"] = round(val - land, 3)
            row["diff_vs_land_pct"] = round(100 * (val / land - 1), 3)
        rows.append(row)
    own = [
        {"source": "Bu çalışma — il poligonu, jeodezik (WGS84)", "value_km2": round(a_geo, 3),
         "scope": "geoBoundaries ADM1 il poligonu (göller dahil)", "reference": True},
        {"source": "Bu çalışma — il poligonu, EPSG:6933", "value_km2": area["epsg6933_km2"]["value"],
         "scope": "aynı poligon, eşit alanlı projeksiyon"},
        {"source": "Bu çalışma — poligon − kalıcı su (WorldCover 80)", "value_km2": round(land, 3),
         "scope": "göller ve barajlar hariç (göller hariç değerlerle karşılaştırılır)"},
        {"source": "Bu çalışma — poligon − kalıcı su − sulak alan (80 + 90)",
         "value_km2": water["land_area_excl_wetland_km2"]["value"], "scope": "su ve otsu sulak alan hariç"},
    ]
    for o in own:
        o["diff_km2"] = round(o["value_km2"] - a_geo, 3)
        o["diff_pct"] = round(100 * (o["value_km2"] / a_geo - 1), 4)
        o["text"] = f"{tr(o['value_km2'], 1)} km²"
    lk = [r for r in rows if r["lakes_excluded"]]
    nl = [r for r in rows if not r["lakes_excluded"]]
    tuik25 = next((r for r in lk if r["source_id"] == "tuik_bt_adnks2025_yogunluk"), None)
    note = (f"Kaynaklar {tr(min(r['value_km2'] for r in rows), 0)}–{tr(max(r['value_km2'] for r in rows), 0)} km² "
            f"arasında. Göller hariç değerler ({', '.join(sorted({tr(r['value_km2'], 2 if r['value_km2'] % 1 else 0) for r in lk}))} km²) "
            f"poligon − kalıcı su ({tr(land, 1)} km²) ile karşılaştırılmalıdır")
    if tuik25:
        note += (f"; TÜİK 2025'in örtük alanı bundan {tr(abs(tuik25['diff_vs_land_km2']), 1)} km² "
                 f"({pct(abs(tuik25['diff_vs_land_pct']), 2)}) {'küçük' if tuik25['diff_vs_land_km2'] < 0 else 'büyük'}")
    note += ". Göl ayrımı belirtilmeyen değerler poligon alanıyla karşılaştırılır"
    counts: dict[float, int] = {}
    for r in nl:
        counts[r["value_km2"]] = counts.get(r["value_km2"], 0) + 1
    mode_v, mode_n = max(counts.items(), key=lambda kv: (kv[1], -abs(kv[0] - a_geo)))
    if mode_n > 1:
        note += (f"; en sık değer {tr(mode_v, 0)} km² ({mode_n} kaynak) poligondan {tr(abs(mode_v - a_geo), 1)} km² "
                 f"({pct(abs(100 * (mode_v / a_geo - 1)), 2)}) {'küçük' if mode_v < a_geo else 'büyük'}")
    note += "."
    return {"reference_km2": round(a_geo, 3), "reference": "Bu çalışmanın jeodezik il alanı", "rows": rows,
            "own_rows": own, "note": note,
            "method": "fark = kaynak değeri − bu çalışmanın jeodezik alanı; % = fark / jeodezik alan. Göller hariç "
                      "satırlarda ayrıca poligon − kalıcı su ile fark verilir. Kaynak tablosu: "
                      + rel(AREA_CSV)}


# ----------------------------------------------------------------------------- 7. hastane arsası / hücre
def hospital_land(inp: dict, a_geo: float) -> dict:
    P = inp["params"]
    land_std = float(P["land.m2_per_bed"]["value"])
    camp = next(a for a in P["land.m2_per_bed"]["alternatives"] if "kampüs" in a["label"].lower())
    land_camp = float(camp["value"])
    a0 = float(P["cost.core_area_m2"]["value"])
    a_gen = float(P["cost.m2_per_bed.general"]["value"])
    a_city = float(P["cost.m2_per_bed.city"]["value"])
    city_min = int(P["size.city_min_beds"]["value"])
    city_max = int(P["size.city_max_beds"]["value"])
    gen_min = int(P["size.general_min_beds"]["value"])
    min_parcel = float(P["land.min_parcel_m2"]["value"])
    cells = sorted({float(P["grid.cell_km"]["value"])} | {float(a["value"]) for a in P["grid.cell_km"]["alternatives"]})
    cell_rows = [{"cell_km": c, "area_km2": c * c, "default": c == float(P["grid.cell_km"]["value"])} for c in cells]
    others = [float(a["value"]) for a in P["land.m2_per_bed"]["alternatives"]]

    def case(key, label, beds, cls, m2b, land_label, floor_m2, floor_method, beds_basis):
        land_m2 = beds * m2b
        sh = [{"cell_km": c["cell_km"], "cell_area_km2": c["area_km2"],
               "share_pct": round(100 * land_m2 / 1e6 / c["area_km2"], 4),
               "fits_per_cell": int(c["area_km2"] * 1e6 // land_m2)} for c in cell_rows]
        return {"key": key, "label": label, "beds": beds, "beds_basis": beds_basis, "class": cls,
                "land_m2_per_bed": m2b, "land_basis": land_label, "land_m2": round(land_m2, 1),
                "land_km2": round(land_m2 / 1e6, 6), "floor_area_m2": round(floor_m2, 1),
                "floor_area_method": floor_method, "floor_to_land_ratio": round(floor_m2 / land_m2, 3),
                "shares": sh,
                "text": (f"{label}: arsa {tr(beds, 0)} × {tr(m2b, 0)} m² = {tr(land_m2, 0)} m² "
                         f"(≈{tr(land_m2 / 1e6, 3)} km²); " + "; ".join(
                             f"{tr(s['cell_km'], 0)} km hücre ({tr(s['cell_area_km2'], 0)} km²) {pct(s['share_pct'], 2)}"
                             for s in sh))}

    std_lbl = "MPYY EK-2: devlet / eğitim-araştırma hastaneleri, yatak başına 130 m² (land.m2_per_bed)"
    camp_lbl = f"MPYY EK-2: {camp['label']}, yatak başına {tr(land_camp, 0)} m² (land.m2_per_bed alternatifi)"
    gen_floor = f"Kapalı alan = A0 + a·yatak = {tr(a0, 0)} + yatak × {tr(a_gen, 2)} m² (cost.core_area_m2, cost.m2_per_bed.general)"
    city_floor = f"Kapalı alan = yatak × {tr(a_city, 1)} m² (cost.m2_per_bed.city; KYP şehir hastaneleri medyan oranı)"
    b = SPEC_EXAMPLE_BEDS
    cases = [
        case("general_300", f"{b} yataklı genel hastane", b, "genel", land_std, std_lbl, a0 + b * a_gen, gen_floor,
             "spec 4.3 madde 6 örneği"),
        case(f"city_{city_min}_std", f"{tr(city_min, 0)} yataklı şehir hastanesi (130 m²/yatak)", city_min, "sehir",
             land_std, std_lbl, city_min * a_city, city_floor, "size.city_min_beds"),
        case(f"city_{city_min}_campus", f"{tr(city_min, 0)} yataklı şehir hastanesi (kampüs, {tr(land_camp, 0)} m²/yatak)",
             city_min, "sehir", land_camp, camp_lbl, city_min * a_city, city_floor, "size.city_min_beds"),
        case(f"city_{city_max}_std", f"{tr(city_max, 0)} yataklı şehir hastanesi (130 m²/yatak)", city_max, "sehir",
             land_std, std_lbl, city_max * a_city, city_floor, "size.city_max_beds"),
        case(f"city_{city_max}_campus", f"{tr(city_max, 0)} yataklı şehir hastanesi (kampüs, {tr(land_camp, 0)} m²/yatak)",
             city_max, "sehir", land_camp, camp_lbl, city_max * a_city, city_floor, "size.city_max_beds"),
    ]
    worst = max(cases, key=lambda c: c["land_m2"])
    assert worst["class"] == "sehir" and worst["land_m2_per_bed"] == land_camp  # sonuç metni bunu varsayar
    smallest = cell_rows[0]
    default = next(c for c in cell_rows if c["default"])
    w_small = next(s for s in worst["shares"] if s["cell_km"] == smallest["cell_km"])["share_pct"]
    w_def = next(s for s in worst["shares"] if s["cell_km"] == default["cell_km"])["share_pct"]
    g = cases[0]
    g_def = next(s for s in g["shares"] if s["cell_km"] == default["cell_km"])

    # il geneli: tüm yatak talebi (oran modu, varsayılan parametreler) × 130 m²
    b1000 = float(P["demand.beds_per_1000"]["value"])
    margin = float(P["demand.safety_margin"]["value"])
    pop = int(P["population.province_2025"]["value"])
    beds_total = pop * b1000 / 1000 * (1 + margin)
    land_total_km2 = beds_total * land_std / 1e6
    conclusion = (
        f"Arazi bağlayıcı değil. {b} yataklı genel hastanenin arsası {b} × {tr(land_std, 0)} m² = "
        f"{tr(g['land_m2'], 0)} m² (≈{tr(g['land_km2'], 2)} km²); bu, varsayılan {tr(default['cell_km'], 0)} km "
        f"hücrenin ({tr(default['area_km2'], 0)} km²) yalnız {pct(g_def['share_pct'], 2)} kadarıdır. En büyük durumda "
        f"bile — {tr(worst['beds'], 0)} yataklı şehir hastanesi, kampüs standardı {tr(worst['land_m2_per_bed'], 0)} "
        f"m²/yatak, {tr(worst['land_km2'], 2)} km² — arsa {tr(smallest['cell_km'], 0)} km hücrenin "
        f"{pct(w_small, 1)}, {tr(default['cell_km'], 0)} km hücrenin {pct(w_def, 1)} kadarını kaplar. Genel "
        f"hastanenin kapalı alanı ({tr(g['floor_area_m2'], 0)} m²) arsasından büyüktür, yani bina çok katlıdır "
        f"(kapalı alan / arsa ≈ {tr(g['floor_to_land_ratio'], 2)}). Bu yüzden 'her hücrede en fazla 1 hastane' "
        f"kısıtı arazi yetersizliğinden değil modelleme tercihinden gelir; bağlayıcı olan talep, maliyet ve erişimdir.")
    return {
        "cells": cell_rows, "cases": cases,
        "min_parcel": {"m2": min_parcel, "beds": gen_min,
                       "share_pct_smallest_cell": round(100 * min_parcel / 1e6 / smallest["area_km2"], 4),
                       "method": "land.min_parcel_m2 = land.m2_per_bed × size.general_min_beds"},
        "province_total": {
            "beds": round(beds_total, 1), "land_km2": round(land_total_km2, 4),
            "share_of_province_pct": round(100 * land_total_km2 / a_geo, 5),
            "method": (f"Tüm il yatak talebi (oran modu, varsayılan parametreler): nüfus 2025 × demand.beds_per_1000 / "
                       f"1000 × (1 + demand.safety_margin) = {tr(pop, 0)} × {tr(b1000, 2)} / 1000 × "
                       f"{tr(1 + margin, 2)}; arsa = yatak × {tr(land_std, 0)} m². Yalnız ölçek göstergesidir; "
                       f"modelin talebi Faz 3'te hücre bazında hesaplanır."),
        },
        "land_m2_per_bed_registry_max": max([land_std, *others]),
        "conclusion": conclusion,
        "caveat": ("Hücrenin tamamı uygun arazi değildir (su, mevcut yapılaşma, eğim, koruma alanları); aday filtresi "
                   "suyu büyük oranda kaplanan hücreleri eler. Bu karşılaştırma yalnız alan ölçeğini gösterir."),
        "source_ids": ["maliyet_mpyy_ek2", "maliyet_hesap_kyp_alan", "yatirim_kyp2026"],
        "params_used": ["land.m2_per_bed", "land.min_parcel_m2", "cost.core_area_m2", "cost.m2_per_bed.general",
                        "cost.m2_per_bed.city", "size.general_min_beds", "size.city_min_beds", "size.city_max_beds",
                        "grid.cell_km", "demand.beds_per_1000", "demand.safety_margin", "population.province_2025"],
    }


# ----------------------------------------------------------------------------- arayüz kartı
def card(out: dict) -> dict:
    a, p, e, d, k, c, w = (out["area"], out["perimeter"], out["extent"], out["diameter"], out["kizilay"],
                           out["centroid"], out["water"])
    items = [
        ("area", "Alan (jeodezik)", a["geodesic_km2"]["text"],
         f"WGS84 elipsoidinde; EPSG:6933 ile fark {pct(abs(a['rel_diff_epsg6933_vs_geodesic_pct']), 5)}."),
        ("land_area", "Alan, kalıcı su hariç", w["land_area_km2"]["text"],
         f"Kalıcı su (WorldCover) {w['permanent_water_km2']['text']}; göller hariç kaynak değerleriyle karşılaştırılır."),
        ("perimeter", "Çevre (jeodezik)", p["geodesic_km"]["text"],
         f"{tr(out['boundary']['vertex_count'], 0)} köşeli sınırla; çevre çözünürlüğe bağlıdır "
         f"(web sürümü {tr(p['web']['perimeter_km'], 1)} km)."),
        ("width_ew", "Doğu–batı genişlik (UTM kutusu)", e["utm_bbox"]["width_km"]["text"],
         f"En batı ↔ en doğu nokta jeodezik: {e['west_east_geodesic_km']['text']}."),
        ("height_ns", "Kuzey–güney genişlik (UTM kutusu)", e["utm_bbox"]["height_km"]["text"],
         f"En kuzey ↔ en güney nokta jeodezik: {e['north_south_geodesic_km']['text']}."),
        ("diameter", "En uzak iki sınır noktası", d["distance_km"]["text"],
         f"{d['p1']['district']['ilce']} ↔ {d['p2']['district']['ilce']} (dışbükey örtü + kaba kuvvet)."),
        ("kizilay_far", "Kızılay → en uzak sınır noktası", k["farthest"]["distance_km"]["text"],
         f"{k['farthest']['district']['ilce']} sınırında."),
        ("kizilay_near", "Kızılay → en yakın il sınırı", k["nearest"]["distance_km"]["text"],
         f"{k['nearest']['district']['ilce']} sınırında."),
        ("centroid", "Kızılay → ilin ağırlık merkezi", c["distance_to_kizilay_km"]["text"],
         f"İl merkezli eşit alanlı (LAEA) projeksiyonda alan ağırlık merkezi; {c['point']['district']['ilce']} "
         "ilçesinde."),
    ]
    return {"title": "Toplam mesafe — Ankara'nın büyüklüğü",
            "items": [{"key": i, "label": lbl, "value_text": val, "tooltip": tip} for i, lbl, val, tip in items],
            "hospital_land_conclusion": out["hospital_land"]["conclusion"]}


# ----------------------------------------------------------------------------- şekil
def plot(inp: dict, out: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    halo = [pe.withStroke(linewidth=3.2, foreground=SURF)]
    to_utm = transformer(common.CRS_WGS84, common.CRS_UTM36N)
    g = geod()

    def xy(lon, lat):
        x, y = to_utm.transform(lon, lat)
        return np.asarray(x) / 1e3, np.asarray(y) / 1e3

    def geodesic_line(p, q, n=80):
        pts = g.npts(p["lon"], p["lat"], q["lon"], q["lat"], n)
        lon = [p["lon"], *[a for a, _ in pts], q["lon"]]
        lat = [p["lat"], *[b for _, b in pts], q["lat"]]
        return xy(lon, lat)

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig = plt.figure(figsize=(16, 10.4), dpi=150)
    fig.patch.set_facecolor(SURF)
    ax = fig.add_axes((0.015, 0.06, 0.60, 0.85))
    ax.set_facecolor(SURF)

    # ilçeler
    for props, gm in inp["ilce"]:
        polys = list(gm.geoms) if gm.geom_type == "MultiPolygon" else [gm]
        for pg in polys:
            x, y = xy(*np.asarray(pg.exterior.coords).T)
            ax.fill(x, y, color=FILL, ec=EDGE, lw=0.6, zorder=1)
    small = {"altindag", "cankaya", "kecioren", "mamak", "yenimahalle", "etimesgut", "pursaklar"}
    for props, gm in inp["ilce"]:
        rp = gm.representative_point()
        x, y = xy(rp.x, rp.y)
        if props["ilce_key"] in small:
            continue
        ax.text(x, y, props["ilce"], ha="center", va="center", fontsize=7.2, color=INK2, zorder=3, path_effects=halo)
    # il sınırı
    x, y = xy(*np.asarray(inp["il"].exterior.coords).T)
    ax.plot(x, y, color=INK, lw=1.3, zorder=4)

    # UTM kutusu
    bb = out["extent"]["utm_bbox"]
    x0, y0, x1, y1 = bb["xmin_m"] / 1e3, bb["ymin_m"] / 1e3, bb["xmax_m"] / 1e3, bb["ymax_m"] / 1e3
    ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec=INK2, lw=0.9, ls=(0, (5, 3)), zorder=2))
    ax.annotate("", xy=(x1, y1 + 6), xytext=(x0, y1 + 6),
                arrowprops=dict(arrowstyle="<->", color=INK2, lw=0.9, shrinkA=0, shrinkB=0))
    ax.text((x0 + x1) / 2, y1 + 8, f"Doğu–batı (UTM 36N kutusu): {bb['width_km']['text']}", ha="center",
            va="bottom", fontsize=9, color=INK2)
    ax.annotate("", xy=(x1 + 6, y1), xytext=(x1 + 6, y0),
                arrowprops=dict(arrowstyle="<->", color=INK2, lw=0.9, shrinkA=0, shrinkB=0))
    ax.text(x1 + 9, (y0 + y1) / 2, f"Kuzey–güney (UTM 36N kutusu): {bb['height_km']['text']}", ha="left",
            va="center", rotation=90, fontsize=9, color=INK2)

    # uç noktalar
    e = out["extent"]
    for key, dx, dy, ha, va in (("west", 4, -6, "left", "top"), ("east", -4, 6, "right", "bottom"),
                                ("north", 5, 2, "left", "bottom"), ("south", 5, -2, "left", "top")):
        p = e[key]
        px, py = xy(p["lon"], p["lat"])
        ax.plot(px, py, marker="o", ms=4.5, mfc=SURF, mec=INK, mew=1.1, zorder=6)
        ax.text(px + dx, py + dy, p["label"], fontsize=7.5, color=INK, ha=ha, va=va, zorder=6, path_effects=halo)

    # en uzak iki nokta
    d = out["diameter"]
    lx, ly = geodesic_line(d["p1"], d["p2"])
    ax.plot(lx, ly, color=BLUE, lw=2.2, zorder=5, solid_capstyle="round")
    for p in (d["p1"], d["p2"]):
        px, py = xy(p["lon"], p["lat"])
        ax.plot(px, py, marker="o", ms=6.5, color=BLUE, mec=SURF, mew=1.3, zorder=7)
    mi = len(lx) // 2 + 12
    ang = math.degrees(math.atan2(ly[-1] - ly[0], lx[-1] - lx[0]))
    if ang > 90:
        ang -= 180
    if ang < -90:
        ang += 180
    ax.text(lx[mi], ly[mi] + 4, f"En uzak iki sınır noktası: {d['distance_km']['text']}", color=BLUE, fontsize=9.5,
            fontweight="bold", rotation=ang, rotation_mode="anchor", ha="center", va="bottom", zorder=8,
            path_effects=halo)

    # Kızılay
    k = out["kizilay"]
    kx, ky = xy(k["point"]["lon"], k["point"]["lat"])
    fx, fy = geodesic_line(k["point"], k["farthest"])
    ax.plot(fx, fy, color=ORANGE, lw=2.0, zorder=5, solid_capstyle="round")
    fpx, fpy = xy(k["farthest"]["lon"], k["farthest"]["lat"])
    ax.plot(fpx, fpy, marker="o", ms=6.5, color=ORANGE, mec=SURF, mew=1.3, zorder=7)
    nx, ny = geodesic_line(k["point"], k["nearest"])
    ax.plot(nx, ny, color=ORANGE, lw=1.3, ls=(0, (2, 2)), zorder=5)
    npx, npy = xy(k["nearest"]["lon"], k["nearest"]["lat"])
    ax.plot(npx, npy, marker="o", ms=5, mfc=SURF, mec=ORANGE, mew=1.4, zorder=7)
    ax.plot(kx, ky, marker="*", ms=14, color=ORANGE, mec=INK, mew=0.8, zorder=9)
    ax.text(kx + 4, ky + 4, "Kızılay", fontsize=9.5, fontweight="bold", color=INK, ha="left", va="bottom", zorder=9,
            path_effects=halo)
    mi = int(len(fx) * 0.6)
    ang = math.degrees(math.atan2(fy[-1] - fy[0], fx[-1] - fx[0]))
    if ang > 90:
        ang -= 180
    if ang < -90:
        ang += 180
    ax.text(fx[mi], fy[mi] - 4, f"Kızılay → en uzak sınır noktası: {k['farthest']['distance_km']['text']}",
            color=ORANGE, fontsize=9.5, fontweight="bold", rotation=ang, rotation_mode="anchor", ha="center",
            va="top", zorder=8, path_effects=halo)
    ax.text(npx + 3, npy - 2, f"en yakın il sınırı\n{k['nearest']['distance_km']['text']}", color=ORANGE,
            fontsize=7.8, fontweight="bold", ha="left", va="top", zorder=8, path_effects=halo)

    # ağırlık merkezi
    c = out["centroid"]
    cx, cy = xy(c["point"]["lon"], c["point"]["lat"])
    ax.plot(cx, cy, marker="P", ms=8, color=GREEN, mec=SURF, mew=1.0, zorder=8)
    ax.text(cx - 3, cy - 3, f"ağırlık merkezi\n(Kızılay'a {c['distance_to_kizilay_km']['text']})", color=GREEN,
            fontsize=7.5, ha="right", va="top", zorder=8, fontweight="bold", path_effects=halo)

    # ölçek çubuğu (50 km) ve kuzey oku
    sx, sy = x0 + 4, y0 - 14
    for i, col in enumerate([INK, SURF, INK, SURF]):
        ax.add_patch(Rectangle((sx + i * 12.5, sy), 12.5, 2.2, fc=col, ec=INK, lw=0.8, zorder=9))
    for t in (0, 25, 50):
        ax.text(sx + t, sy - 1.5, f"{t}", ha="center", va="top", fontsize=7.5, color=INK)
    ax.text(sx + 52.5, sy + 1.1, "km", ha="left", va="center", fontsize=7.5, color=INK)
    ax.annotate("K", xy=(x1 - 2, y0 + 22), xytext=(x1 - 2, y0 + 6), ha="center", va="center", fontsize=9,
                fontweight="bold", color=INK, arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.2))
    ax.set_aspect("equal")
    ax.set_xlim(x0 - 8, x1 + 22)
    ax.set_ylim(y0 - 22, y1 + 20)
    ax.axis("off")

    # merkez ilçe adları: ayrı küçük not
    ax.text(x0 + 2, y0 + 2, "Merkez ilçeler (adları yazılmadı): Altındağ, Çankaya, Etimesgut, Keçiören, Mamak, "
            "Pursaklar, Yenimahalle", fontsize=6.8, color=INK2, ha="left", va="bottom")

    # sağ panel: ölçüler
    tx = fig.add_axes((0.635, 0.465, 0.35, 0.445))
    tx.axis("off")
    tx.set_xlim(0, 1)
    tx.set_ylim(0, 1)
    rows = [(it["label"], it["value_text"]) for it in out["card"]["items"]]
    rows.insert(2, ("Alan (EPSG:6933)", out["area"]["epsg6933_km2"]["text"]))
    rows.insert(4, ("Çevre, web sürümü", f"{tr(out['perimeter']['web']['perimeter_km'], 1)} km "
                                         f"({tr(out['perimeter']['web']['vertex_count'], 0)} köşe)"))
    rows.insert(7, ("En batı ↔ en doğu (jeodezik)", e["west_east_geodesic_km"]["text"]))
    rows.insert(8, ("En kuzey ↔ en güney (jeodezik)", e["north_south_geodesic_km"]["text"]))
    tx.text(0, 0.995, "Ölçüler", fontsize=11, fontweight="bold", color=INK, va="top")
    n = len(rows)
    step = 0.9 / n
    for i, (lbl, val) in enumerate(rows):
        yy = 0.93 - i * step
        tx.text(0.0, yy, lbl, fontsize=8.8, color=INK2, va="center")
        tx.text(1.0, yy, val, fontsize=8.8, color=INK, va="center", ha="right", fontweight="bold")
        tx.plot([0, 1], [yy - step / 2] * 2, color=GRID, lw=0.6)

    # hastane arsası / hücre (spec 4.3 madde 6) — tek satırlık özet
    h = out["hospital_land"]
    g0, w0 = h["cases"][0], max(h["cases"], key=lambda c: c["land_m2"])
    dc = next(c for c in h["cells"] if c["default"])
    sh = lambda c: next(s for s in c["shares"] if s["cell_km"] == dc["cell_km"])["share_pct"]  # noqa: E731
    fig.text(0.635, 0.462, "Hastane arsası / hücre alanı: arazi bağlayıcı değil", fontsize=9.5, fontweight="bold",
             color=GREEN, va="top")
    fig.text(0.635, 0.441, f"{g0['beds']} yataklı genel hastane: {g0['beds']} × {tr(g0['land_m2_per_bed'], 0)} m² = "
             f"{tr(g0['land_m2'], 0)} m² arsa → {tr(dc['cell_km'], 0)} km hücrenin ({tr(dc['area_km2'], 0)} km²) "
             f"{pct(sh(g0), 2)} kadarı\n{tr(w0['beds'], 0)} yataklı şehir hastanesi, kampüs standardı: "
             f"{tr(w0['beds'], 0)} × {tr(w0['land_m2_per_bed'], 0)} m² = {tr(w0['land_m2'], 0)} m² → "
             f"{pct(sh(w0), 1)} kadarı", fontsize=8.2, color=INK2, va="top", linespacing=1.5)

    # sağ panel: yüzölçümü kaynakları
    bx = fig.add_axes((0.80, 0.07, 0.185, 0.255))
    ac = out["area_comparison"]
    groups: dict[float, list] = {}
    for r in ac["rows"]:
        groups.setdefault(r["value_km2"], []).append(r)
    vals = sorted(groups)
    ref = ac["reference_km2"]
    land = out["water"]["land_area_km2"]["value"]
    ys = np.arange(len(vals))
    for yv, v in zip(ys, vals):
        lakes = any(r["lakes_excluded"] for r in groups[v])
        col = ORANGE if lakes else BLUE
        bx.plot([0, v - ref], [yv, yv], color=col, lw=1.4, zorder=2)
        bx.plot(v - ref, yv, "o", color=col, ms=5, zorder=3)
        lbl = groups[v][0]["source"]
        if len(groups[v]) > 1:
            lbl += f" (+{len(groups[v]) - 1})"
        bx.text(-1500 - 60, yv, f"{lbl}  {groups[v][0]['text']}", ha="right", va="center", fontsize=6.9, color=INK)
    bx.axvline(0, color=INK, lw=1.0, zorder=1)
    bx.axvline(land - ref, color=ORANGE, lw=1.0, ls=(0, (3, 2)), zorder=1)
    bx.set_yticks([])
    bx.set_xlim(-1500, 1500)
    bx.set_ylim(-0.7, len(vals) - 0.3)
    bx.set_xticks([-1000, 0, 1000])
    bx.set_xticklabels(["−1.000", "0", "+1.000"], fontsize=7.5, color=INK2)
    bx.set_xlabel("fark, km² (kaynak − poligon)", fontsize=7.5, color=INK2)
    for s in ("top", "right", "left"):
        bx.spines[s].set_visible(False)
    bx.spines["bottom"].set_color(INK2)
    bx.set_facecolor(SURF)
    bx.text(0, len(vals) - 0.15, f"poligon\n{tr(ref, 1)}", fontsize=6.8, color=INK, ha="center", va="bottom")
    bx.text(land - ref, len(vals) - 0.15, f"− su\n{tr(land, 1)}", fontsize=6.8, color=ORANGE, ha="center",
            va="bottom")
    fig.text(0.635, 0.385, "Kaynaklardaki yüzölçümü (km²)", fontsize=11, fontweight="bold", color=INK)
    fig.text(0.635, 0.368, "turuncu: göller hariç değerler → 'poligon − kalıcı su' ile karşılaştırılır · "
             "(+n): aynı değeri veren n kaynak daha", fontsize=7.5, color=INK2)

    fig.text(0.015, 0.965, "Ankara'nın büyüklük ölçüleri — 'Toplam mesafe' kartı", fontsize=15, fontweight="bold",
             color=INK)
    fig.text(0.015, 0.94, "Harita UTM 36N (EPSG:32636); mesafeler WGS84 elipsoidinde jeodezik. Mavi: en uzak iki sınır "
             "noktası · turuncu: Kızılay → en uzak / en yakın il sınırı · kesikli kutu: UTM sınırlayıcı kutusu.",
             fontsize=9, color=INK2)
    fig.text(0.015, 0.012, "Kaynak: geoBoundaries gbOpen TUR ADM1/ADM2 (OSM); Kızılay: OSM node 9456598160; su: ESA "
             "WorldCover 2021 v200; nüfus: TÜİK ADNKS 2025. Hesap: pipeline/23_ankara_measures.py",
             fontsize=7, color=INK2)
    fig.savefig(OUT_PNG, facecolor=SURF)
    plt.close(fig)


# ----------------------------------------------------------------------------- ana akış
def main() -> None:
    inp = load_inputs()
    to_utm = transformer(common.CRS_WGS84, common.CRS_UTM36N)
    ilce_utm = [project(gm, common.CRS_WGS84, common.CRS_UTM36N) for _, gm in inp["ilce"]]
    ctx = {"district_at": lambda lon, lat: district_at(lon, lat, inp["ilce"], ilce_utm, to_utm)}

    print("1) alan ve çevre")
    boundary, area, perim = area_perimeter(inp)
    print("2) genişlikler")
    ext = extents(inp, ctx)
    print("3) en uzak iki nokta (dışbükey örtü + kaba kuvvet)")
    dia = diameter(inp, ctx)
    print("4) Kızılay ve ağırlık merkezi")
    kiz, cen = kizilay(inp, ctx)
    print("5–6) su ve ilçeler")
    water, districts = water_and_districts(inp)
    a_geo = area["geodesic_km2"]["value"]
    print("5) alan kaynakları tablosu")
    comp = area_comparison(inp, area, water)
    print("7) hastane arsası / hücre")
    hosp = hospital_land(inp, a_geo)

    sum_area = sum(r["area_geodesic_km2"] for r in districts)
    out = {
        "meta": {
            "title": "Ankara'nın büyüklük ölçüleri ('Toplam mesafe' kartı) ve hastane arsası / hücre karşılaştırması",
            "spec": "Bölüm 4.3 madde 3 ve 6; hocanın 4. maddesi",
            "generated_by": "pipeline/23_ankara_measures.py", "generated": common.today(),
            "ellipsoid": "WGS84", "crs": {"map": common.CRS_WGS84, "metric": common.CRS_UTM36N,
                                          "equal_area": common.CRS_EQUAL_AREA, "centroid": CRS_LAEA_ANKARA,
                                          "raster": common.CRS_MOLLWEIDE},
            "inputs": [{"file": rel(p), "sha256": common.sha256(p)} for p in
                       (IL_GEOJSON, IL_WEB_GEOJSON, ILCE_GEOJSON, LANDCOVER_TIF, TUIK_CSV, KIZILAY_YAML, AREA_CSV,
                        PARAMS_JSON)],
            "source_ids": ["sinirlar_geoboundaries_tur_adm1", "sinirlar_geoboundaries_tur_adm2",
                           "arazi_osm_osm_turkey_pbf", "arazi_osm_worldcover_tiles_s3", "tuik_st_il_ilce_2025",
                           "maliyet_mpyy_ek2"],
            "number_format_note": "JSON sayıları nokta ondalıklı; 'text' alanları Türkçe biçimde (1.234,5).",
        },
        "boundary": boundary, "area": area, "perimeter": perim, "extent": ext, "diameter": dia,
        "kizilay": kiz, "centroid": cen, "water": water, "area_comparison": comp,
        "districts": {
            "rows": districts, "count": len(districts),
            "sum_area_geodesic_km2": round(sum_area, 3), "province_area_geodesic_km2": a_geo,
            "sum_minus_province_km2": round(sum_area - a_geo, 3),
            "sum_pop_2025": sum(r["pop_2025"] for r in districts),
            "method": ("Alan: her ilçe poligonunun WGS84 jeodezik alanı (pyproj.Geod). Yoğunluk = TÜİK ADNKS 2025 "
                       "nüfusu / jeodezik alan (kişi/km²); kara yoğunluğu = nüfus / (alan − kalıcı su). Su: il ile "
                       "aynı yöntem (10 × 10 alt örneklemeli ilçe payı × jeodezik hücre alanı). Satırlar yoğunluğa "
                       "göre azalan sırada."),
            "source_ids": ["sinirlar_geoboundaries_tur_adm2", "tuik_st_il_ilce_2025", "arazi_osm_worldcover_tiles_s3"],
        },
        "hospital_land": hosp,
    }
    out["card"] = card(out)
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  yazıldı: {rel(OUT_JSON)}")
    plot(inp, out)
    print(f"  yazıldı: {rel(OUT_PNG)}")

    # özet
    print(f"  alan jeodezik {tr(a_geo, 3)} km² · EPSG:6933 {area['epsg6933_km2']['text']} "
          f"(fark {pct(area['rel_diff_epsg6933_vs_geodesic_pct'], 6)}) · çevre {perim['geodesic_km']['text']} "
          f"(web {tr(perim['web']['perimeter_km'], 1)} km)")
    print(f"  UTM kutusu {ext['utm_bbox']['width_km']['text']} × {ext['utm_bbox']['height_km']['text']} · "
          f"B–D {ext['west_east_geodesic_km']['text']} · K–G {ext['north_south_geodesic_km']['text']}")
    print(f"  çap {dia['distance_km']['text']} ({dia['p1']['district']['ilce']} ↔ {dia['p2']['district']['ilce']}; "
          f"örtü {dia['hull_vertex_count']} köşe; kaba kuvvet {tr(dia['bruteforce_pairs'], 0)} çift: aynı)")
    print(f"  Kızılay → en uzak {kiz['farthest']['distance_km']['text']} ({kiz['farthest']['district']['ilce']}), "
          f"en yakın {kiz['nearest']['distance_km']['text']} ({kiz['nearest']['district']['ilce']}), "
          f"ağırlık merkezi {cen['distance_to_kizilay_km']['text']} ({cen['point']['district']['ilce']})")
    print(f"  kalıcı su {water['permanent_water_km2']['text']} (hücre merkezi "
          f"{tr(water['checks']['pixel_centre_water_km2'], 1)}) · poligon − su {water['land_area_km2']['text']}")
    print("  " + hosp["conclusion"])


if __name__ == "__main__":
    main()
