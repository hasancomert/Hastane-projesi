"""ESA WorldCover 2021 v200 (10 m) -> Ankara 100 m arazi maskesi (GHS-POP ızgarasına hizalı).

Çıktı: data/processed/landcover/worldcover_ankara_100m.tif — 3 bant, uint8, hücre alanının yüzdesi (0–100):
  bant 1 = sınıf 80 kalıcı su, bant 2 = sınıf 90 otsu sulak alan, bant 3 = sınıf 50 yapılaşmış alan; 255 = veri yok.
Izgara: ESRI:54009 (Mollweide, eşit alanlı), 100 m, common.GHS_WINDOW -> her hücre tam 1 ha.
Yöntem: her sınıf için 10 m ikili maske -> GDAL 'average' (kısmi piksel ağırlıklı) ile 100 m'ye.
Sağlama: OSM göl poligonlarında 100 m ürünün su alanı, 10 m piksellerin jeodezik alanıyla karşılaştırılır.

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/13_landcover.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

RAW_ARAZI = common.RAW / "arazi"
RAW_OSM = common.RAW / "osm"
OUT_DIR = common.PROCESSED / "landcover"
OUT_TIF = OUT_DIR / "worldcover_ankara_100m.tif"
LAKES_GEOJSON = RAW_ARAZI / "osm_lakes.geojson"
LAKE_CHECK_CSV = RAW_ARAZI / "worldcover_lake_check.csv"
PROVINCE_JSON = RAW_ARAZI / "worldcover_province_summary.json"

WC_TILES = ["N39E030", "N39E033", "N36E033", "N36E030"]
WC_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{}_Map.tif"
ZENODO_API = "https://zenodo.org/api/records/7254221"   # DOI 10.5281/zenodo.7254221
WC_RES_DEG = 1.0 / 12000.0                               # 10 m ürünün piksel boyu (derece)

CLASSES = [(80, "Kalıcı su (WorldCover 80)"),
           (90, "Otsu sulak alan (WorldCover 90)"),
           (50, "Yapılaşmış alan (WorldCover 50)")]
CELL = 100.0
BLOCK = 400  # hedef blok boyu (hücre)

PBF = RAW_OSM / "turkey-latest.osm.pbf"
PBF_URLS = [
    "https://download.geofabrik.de/europe/turkey-latest.osm.pbf",
    "https://huggingface.co/datasets/NoeFlandre/osm-geofabrik-raw-pbf-extracts/resolve/main/turkey-latest.osm.pbf",
]

# Sağlama gölleri: OSM nesne kimlikleri (2026-06-26 PBF'te ad ve natural=water ile doğrulandı)
LAKES = [
    ("Tuz Gölü", "r", 2411676),
    ("Hirfanlı Baraj Gölü", "r", 1028112),
    ("Sarıyar Baraj Gölü", "r", 385113),
    ("Kesikköprü Baraj Gölü", "r", 2280078),
    ("Mogan Gölü", "r", 2280277),
    ("Eymir Gölü", "w", 729243281),
]
LAKE_BUFFER_M = 300.0  # kıyı çizgisi farkları için poligon tamponu


# ---------------------------------------------------------------- indirme
def _manifest_has(dest: Path, url: str) -> bool:
    man = dest.parent / "_manifest.jsonl"
    if not (dest.exists() and man.exists()):
        return False
    rel = str(dest.relative_to(common.ROOT))
    for line in man.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("url") == url and rec.get("file") == rel:
            return True
    return False


def ensure(url: str, dest: Path, timeout: int = 300) -> Path:
    """Dosya yoksa indirir (manifest kaydıyla); varsa ve manifestte kayıtlıysa atlar."""
    if not _manifest_has(dest, url):
        rec = common.download(url, dest, timeout=timeout)
        print(f"  indirildi: {rec['file']} ({rec['bytes']:,} B)")
    return dest


def ensure_pbf() -> Path:
    """Türkiye OSM PBF: önce Geofabrik, olmazsa Hugging Face aynası."""
    if PBF.exists():
        return PBF
    last = None
    for url in PBF_URLS:
        try:
            common.download(url, PBF, timeout=600)
            return PBF
        except Exception as exc:  # bağlantı sıfırlanabiliyor
            print(f"  PBF indirilemedi ({url}): {exc}")
            last = exc
    raise RuntimeError(f"OSM PBF indirilemedi: {last}")


# ---------------------------------------------------------------- ızgara
def target_grid():
    from rasterio.transform import from_origin
    xmin, ymin, xmax, ymax = common.GHS_WINDOW
    width = int(round((xmax - xmin) / CELL))
    height = int(round((ymax - ymin) / CELL))
    # kökene hizalı mı?
    ox, oy = common.GHS_ORIGIN
    assert (xmin - ox) % CELL == 0 and (oy - ymax) % CELL == 0
    return from_origin(xmin, ymax, CELL, CELL), width, height


def _snap(v: float, fn) -> float:
    return fn(v / WC_RES_DEG) * WC_RES_DEG


def read_wc(srcs, west, south, east, north):
    """4 karodan verilen lon/lat kutusunu 10 m ızgaraya hizalı okur (0 = veri yok)."""
    from rasterio.merge import merge
    w, s = _snap(west, math.floor), _snap(south, math.floor)
    e, n = _snap(east, math.ceil), _snap(north, math.ceil)
    arr, tr = merge(srcs, bounds=(w, s, e, n), res=WC_RES_DEG, nodata=0)
    return arr[0], tr


def build_landcover() -> None:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import Affine
    from rasterio.warp import reproject, transform_bounds

    dst_crs = CRS.from_user_input(common.CRS_MOLLWEIDE)
    transform, width, height = target_grid()
    out = np.full((len(CLASSES), height, width), np.nan, dtype=np.float32)
    srcs = [rasterio.open(RAW_ARAZI / f"ESA_WorldCover_10m_2021_v200_{t}_Map.tif") for t in WC_TILES]
    try:
        nby, nbx = math.ceil(height / BLOCK), math.ceil(width / BLOCK)
        for by in range(nby):
            for bx in range(nbx):
                r0, c0 = by * BLOCK, bx * BLOCK
                h, w = min(BLOCK, height - r0), min(BLOCK, width - c0)
                btr = transform * Affine.translation(c0, r0)
                left, top = btr.c, btr.f
                right, bottom = left + w * CELL, top - h * CELL
                lon0, lat0, lon1, lat1 = transform_bounds(dst_crs, "EPSG:4326", left, bottom, right, top,
                                                          densify_pts=51)
                pad = 0.01
                src_arr, src_tr = read_wc(srcs, lon0 - pad, lat0 - pad, lon1 + pad, lat1 + pad)
                valid = src_arr != 0
                for k, (cls, _) in enumerate(CLASSES):
                    mask = np.where(valid, (src_arr == cls).astype(np.float32), np.float32(np.nan))
                    dst = np.full((h, w), np.nan, dtype=np.float32)
                    reproject(mask, dst, src_transform=src_tr, src_crs="EPSG:4326", src_nodata=np.nan,
                              dst_transform=btr, dst_crs=dst_crs, dst_nodata=np.nan,
                              resampling=Resampling.average, num_threads=2)
                    out[k, r0:r0 + h, c0:c0 + w] = dst
                del src_arr, valid
                print(f"  blok {by * nbx + bx + 1}/{nby * nbx}", end="\r", flush=True)
        print()
    finally:
        for s in srcs:
            s.close()

    pct = np.where(np.isnan(out), 255, np.clip(np.rint(out * 100.0), 0, 100)).astype(np.uint8)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    profile = dict(driver="GTiff", dtype="uint8", count=len(CLASSES), width=width, height=height,
                   crs=dst_crs, transform=transform, nodata=255, compress="deflate", predictor=2,
                   tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(OUT_TIF, "w", **profile) as dst:
        dst.write(pct)
        for k, (cls, label) in enumerate(CLASSES, start=1):
            dst.set_band_description(k, f"% {label}")
        dst.update_tags(
            source="ESA WorldCover 10 m 2021 v200 (Zanaga et al. 2022, doi:10.5281/zenodo.7254221), CC BY 4.0",
            method="10 m sınıf maskesi -> GDAL average (alan ağırlıklı) -> 100 m Mollweide; değer = hücre alanının %'si",
            grid="ESRI:54009, 100 m, GHS-POP R2023A ızgarasına hizalı (common.GHS_WINDOW)",
            bands="1=sınıf 80 kalıcı su; 2=sınıf 90 otsu sulak alan; 3=sınıf 50 yapılaşmış alan; 255=veri yok",
            created=common.today(),
        )
    print(f"  yazıldı: {OUT_TIF.relative_to(common.ROOT)} ({OUT_TIF.stat().st_size / 1e6:.1f} MB)")


# ---------------------------------------------------------------- sınır ve göller
def ankara_polygon():
    """Ankara il poligonu (geoBoundaries ADM1, WGS84)."""
    import geopandas as gpd
    g = gpd.read_file(common.RAW / "sinirlar" / "geoboundaries" / "ADM1--geoBoundaries-TUR-ADM1.geojson")
    g = g[g["shapeName"].map(common.norm_name) == "ankara"].to_crs(common.CRS_WGS84)
    assert len(g) == 1, "Ankara ADM1 bulunamadı"
    return g.geometry.iloc[0]


def osm_lakes():
    """Sağlama göllerinin OSM poligonları (önbellek: data/raw/arazi/osm_lakes.geojson)."""
    import geopandas as gpd
    if LAKES_GEOJSON.exists():
        return gpd.read_file(LAKES_GEOJSON)
    import osmium
    import shapely
    ensure_pbf()
    want = {(t, i): name for name, t, i in LAKES}
    fp = (osmium.FileProcessor(str(PBF))
          .with_areas(osmium.filter.TagFilter(("natural", "water")))
          .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
          .with_filter(osmium.filter.TagFilter(("natural", "water"))))
    wkb = osmium.geom.WKBFactory()
    rows = []
    for a in fp:
        key = ("w" if a.from_way() else "r", a.orig_id())
        if key in want:
            geom = shapely.from_wkb(bytes.fromhex(wkb.create_multipolygon(a)))
            rows.append({"name": want[key], "osm_type": "way" if key[0] == "w" else "relation",
                         "osm_id": key[1], "osm_name": a.tags.get("name"), "geometry": geom})
    gdf = gpd.GeoDataFrame(rows, crs=common.CRS_WGS84)
    missing = set(want.values()) - set(gdf["name"])
    assert not missing, f"OSM'de bulunamayan göller: {missing}"
    gdf.to_file(LAKES_GEOJSON, driver="GeoJSON")
    return gdf


def geodesic_row_areas(lat_top: float, nrows: int) -> np.ndarray:
    """10 m ızgarada her satırdaki bir pikselin WGS84 elipsoit üzerindeki alanı (m²)."""
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    d = WC_RES_DEG
    areas = np.empty(nrows)
    for r in range(nrows):
        n = lat_top - r * d
        s = n - d
        a, _ = geod.polygon_area_perimeter([0, d, d, 0], [s, s, n, n])
        areas[r] = abs(a)
    return areas


def checks() -> None:
    import geopandas as gpd
    import pandas as pd
    import rasterio
    from rasterio.features import geometry_mask
    import shapely
    from shapely.geometry import box
    from pyproj import Transformer

    ank = ankara_polygon()
    ank_m = gpd.GeoSeries([ank], crs=common.CRS_WGS84).to_crs(common.CRS_MOLLWEIDE).iloc[0]
    window_box = box(*common.GHS_WINDOW)  # 100 m ürünün kapsamı
    with rasterio.open(OUT_TIF) as ds:
        arr = ds.read().astype(np.float64)  # %
        tr, shape = ds.transform, (ds.height, ds.width)
    cell_km2 = CELL * CELL / 1e6
    frac = np.where(arr == 255, 0.0, arr / 100.0)

    # il geneli
    in_ank = ~geometry_mask([ank_m], out_shape=shape, transform=tr, all_touched=False)
    prov = {"il_alani_km2_hucre_merkezi": float(in_ank.sum() * cell_km2)}
    for k, (cls, label) in enumerate(CLASSES):
        prov[f"sinif_{cls}_km2"] = float((frac[k] * in_ank).sum() * cell_km2)
    prov["veri_yok_hucre"] = int(((arr[0] == 255) & in_ank).sum())
    for k, (cls, _) in enumerate(CLASSES):
        prov[f"sinif_{cls}_yuzde"] = 100.0 * prov[f"sinif_{cls}_km2"] / prov["il_alani_km2_hucre_merkezi"]
    PROVINCE_JSON.write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  il geneli:", {k: round(v, 2) for k, v in prov.items()})

    # göller: 100 m ürün vs 10 m jeodezik alan
    lakes = osm_lakes().to_crs(common.CRS_MOLLWEIDE)
    to_ll = Transformer.from_crs(common.CRS_MOLLWEIDE, common.CRS_WGS84, always_xy=True).transform
    eq = "EPSG:6933"
    srcs = [rasterio.open(RAW_ARAZI / f"ESA_WorldCover_10m_2021_v200_{t}_Map.tif") for t in WC_TILES]
    rows = []
    try:
        for _, lk in lakes.iterrows():
            poly = lk.geometry
            region = poly.buffer(LAKE_BUFFER_M).intersection(window_box)
            region_ank = region.intersection(ank_m)
            rec = {"gol": lk["name"], "osm": f"{lk['osm_type']}/{lk['osm_id']}",
                   "osm_alan_km2": gpd.GeoSeries([poly], crs=common.CRS_MOLLWEIDE).to_crs(eq).area.iloc[0] / 1e6,
                   "osm_alan_ankara_km2": gpd.GeoSeries([poly.intersection(ank_m)], crs=common.CRS_MOLLWEIDE)
                   .to_crs(eq).area.iloc[0] / 1e6}
            for tag, geom in (("pencere", region), ("ankara", region_ank)):
                if geom.is_empty:
                    rec[f"wc100_su_{tag}_km2"] = 0.0
                    rec[f"wc10_su_{tag}_km2"] = 0.0
                    continue
                m = ~geometry_mask([geom], out_shape=shape, transform=tr, all_touched=False)
                rec[f"wc100_su_{tag}_km2"] = float((frac[0] * m).sum() * cell_km2)
                # 10 m piksellerle doğrudan (piksel merkezi bölgede ise)
                g_ll = shapely.transform(geom, lambda xx, yy: to_ll(xx, yy), interleaved=False)
                w, s, e, n = g_ll.bounds
                src, str_ = read_wc(srcs, w, s, e, n)
                m10 = ~geometry_mask([g_ll], out_shape=src.shape, transform=str_, all_touched=False)
                row_area = geodesic_row_areas(str_.f, src.shape[0])
                water_rows = ((src == 80) & m10).sum(axis=1)
                rec[f"wc10_su_{tag}_km2"] = float((water_rows * row_area).sum() / 1e6)
            rows.append(rec)
    finally:
        for s in srcs:
            s.close()
    df = pd.DataFrame(rows)
    df.to_csv(LAKE_CHECK_CSV, index=False, float_format="%.2f")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(df.round(2).to_string(index=False))


def main() -> None:
    print("1) WorldCover karoları + Zenodo kaydı")
    for t in WC_TILES:
        ensure(WC_URL.format(t), RAW_ARAZI / f"ESA_WorldCover_10m_2021_v200_{t}_Map.tif")
    ensure(ZENODO_API, RAW_ARAZI / "zenodo_7254221_record.json", timeout=60)
    if not OUT_TIF.exists():
        print("2) 100 m su / sulak alan / yapılaşma yüzdeleri")
        build_landcover()
    else:
        print(f"2) mevcut: {OUT_TIF.relative_to(common.ROOT)} (yeniden üretmek için silin)")
    print("3) sağlama: il geneli ve göller")
    checks()


if __name__ == "__main__":
    main()
