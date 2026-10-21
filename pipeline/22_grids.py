"""Faz 2 — 2/3/5 km ızgaralar, TÜİK 2025'e kalibre hücre nüfusu, 2030 senaryosu, arazi örtüsü, aday hücreler,
kapsama denetimi ve seyrek mesafe listeleri (şartname 4.3 madde 4–7).

Girdiler (hepsi git'te)
  data/processed/ankara_il.geojson, ankara_ilceler.geojson      il ve 25 ilçe sınırı (WGS84)
  data/reference/tuik_adnks_ankara.csv                          TÜİK ADNKS 2025 ilçe nüfusları (TÜİK sırası)
  data/processed/pop/worldpop_{2025,2030}_100m.tif              WorldPop R2025A (WGS84 3")
  data/processed/pop/ghs_pop_e{2025,2030}_100m.tif              GHS-POP R2023A (ESRI:54009 100 m)
  data/processed/landcover/worldcover_ankara_100m.tif           ESA WorldCover % su / sulak alan / yapılaşma
  data/processed/ilce_nufus_kaynaklar.csv                       Faz 1 ilçe toplamları (birebir yeniden üretilir)
  data/reference/parameters.json                                il nüfusları, aday kuralı, R, k, dolambaç

Yöntem
  1. Hücreler: EPSG:32636'da kenarları s'nin katlarına hizalı kareler (id '<s>km_<ix>_<iy>', hücre
     [ix·s, (ix+1)·s) × [iy·s, (iy+1)·s) m); il poligonuyla kesişen (alanı > 0) her hücre tutulur.
     inside_frac = alan(hücre ∩ il) / alan(hücre) (UTM).
  2. Nüfus: her kaynak pikseli MERKEZİYLE ilçeye (Faz 1 ile aynı kural: rasterize, all_touched=False, ilce_key
     sırası) ve hücreye (merkez UTM'ye dönüştürülür) atanır. 2025: ilçe katsayısı TÜİK2025_d / Σkaynak_d; (hücre, ilçe)
     parçaları ilçe içinde en büyük kalan yöntemiyle tam sayıya çevrilir → her ilçe TÜİK'e, il 5.910.320'ye birebir.
     2030 senaryosu (hesap): T30_d = TÜİK2025_d × S30_d / S25_d, sonra Σ = population.province_2030 olacak şekilde
     ölçek; ilçe tam sayıları en büyük kalanla; ilçe içi dağılım kaynağın 2030 katmanından.
     Çoğunluk ilçesi: varsayılan kaynağın 2025 tam sayı nüfusu en büyük olan ilçe; eşitlikte yuvarlanmamış kalibre
     nüfus, sonra hücredeki ilçe alanı; nüfussuz hücrede alana göre.
  3. Arazi örtüsü: hücreye merkezi düşen, il içindeki WorldCover 100 m piksellerinin ortalaması.
  4. Adaylar: backend/model/grid_io.py kuralı (yakınlık eşiği grid.candidate_near_density × s² kişi/hücre); |J|
     tablosu, kapsama denetimi ve onarımı.
  5. Mesafe: backend/model/distance.py (yol km = kuş uçuşu × k(e) × ölçek), en yakın k aday CSR (k'ıncı mesafeye eşit
     adaylar da tutulur).

Çıktılar (data/processed/grid/)
  grid_{2,3,5}km.npz, grid_{2,3,5}km_pieces.csv, grid_{2,3,5}km_cells.geojson, grid_meta.json, grid_summary.json,
  neighbors_3km_default.npz, ilce_kalibrasyon.csv
  (statik haritalar: pipeline/24_static_maps.py → docs/screenshots/)

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/22_grids.py
Deterministiktir: aynı girdilerle aynı dosyalar (grid_summary.json'daki süre ölçümleri hariç).
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402
from backend.model import distance, grid_io  # noqa: E402

OUT = common.PROCESSED / "grid"
POP_DIR = common.PROCESSED / "pop"
WC_TIF = common.PROCESSED / "landcover" / "worldcover_ankara_100m.tif"
TUIK_CSV = common.REFERENCE / "tuik_adnks_ankara.csv"
PHASE1_CSV = common.PROCESSED / "ilce_nufus_kaynaklar.csv"
PARAMS_JSON = common.REFERENCE / "parameters.json"
IL_GEOJSON = common.PROCESSED / "ankara_il.geojson"
ILCE_GEOJSON = common.PROCESSED / "ankara_ilceler.geojson"

CELL_SIZES_KM = (2, 3, 5)
P_MIN_TABLE = (0, 100, 250, 500, 1000, 2500)
NBR_OFFSETS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))
PHASE1_TOL = 0.06          # Faz 1 CSV'si 0,1'e yuvarlı (± 0,05) + float32 payı
NEIGHBOR_TIMING_RUNS = 3
# 2030 büyüme bayrakları (yalnız raporlama; model_secimi): senaryonun 2025→2030 yıllık hızı, TÜİK'in en son yıllık
# değişimiyle (2024→2025) karşılaştırılır.
FLAG_NEAR_ZERO = 0.0025    # |yıllık hız| < %0,25 → yön yok sayılır
FLAG_MAX_GAP = 0.02        # |senaryo yıllık hızı − TÜİK yıllık hızı| > 2 puan → bayrak

SOURCES = {
    "wp": {"name": "WorldPop R2025A (constrained, 100 m)", "registry": "worldpop_r2025a",
           "f25": POP_DIR / "worldpop_2025_100m.tif", "f30": POP_DIR / "worldpop_2030_100m.tif",
           "col25": "worldpop_2025_100m", "col30": "worldpop_2030_100m",
           "source_ids": ["nufus_izgara_worldpop_2025", "nufus_izgara_worldpop_2030",
                          "nufus_izgara_worldpop_release_statement"]},
    "ghs": {"name": "GHS-POP R2023A E2025/E2030 (100 m)", "registry": "ghs_pop_r2023a",
            "f25": POP_DIR / "ghs_pop_e2025_100m.tif", "f30": POP_DIR / "ghs_pop_e2030_100m.tif",
            "col25": "ghs_e2025_100m", "col30": "ghs_e2030_100m",
            "source_ids": ["nufus_izgara_ghs_pop_e2025_100m", "nufus_izgara_ghs_pop_e2030_100m"]},
}


# ---------------------------------------------------------------- yardımcılar
def pval(params: dict, key: str):
    return params[key]["value"]


def largest_remainder(values: np.ndarray, groups: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """Grup içinde en büyük kalan yöntemi: tam sayılar, her grubun toplamı = hedef (deterministik).

    Önce taban değerler; eksik kalan birimler kesir kısmı en büyük olanlara (eşitlikte küçük indis) birer birer verilir.
    """
    values = np.asarray(values, dtype=np.float64)
    groups = np.asarray(groups, dtype=np.int64)
    targets = np.asarray(targets, dtype=np.int64)
    G = len(targets)
    gsum = np.bincount(groups, weights=values, minlength=G)
    if not np.allclose(gsum, targets, rtol=1e-9, atol=1e-6):
        raise ValueError("grup değer toplamları hedeflerle uyuşmuyor")
    fl = np.floor(values)
    rem = values - fl
    fl = fl.astype(np.int64)
    deficit = targets - np.bincount(groups, weights=fl, minlength=G).round().astype(np.int64)
    if (deficit < 0).any():
        raise ValueError("taban toplamı hedefi aşıyor")
    order = np.lexsort((np.arange(len(values)), -rem, groups))
    gs = groups[order]
    start = np.searchsorted(gs, np.arange(G))
    rank = np.arange(len(values)) - start[gs]
    plus = order[rank < deficit[gs]]
    if not (rem[plus] > 0).all():
        raise ValueError("kalanı sıfır olan parçaya birim verilmek istendi")
    out = fl.copy()
    out[plus] += 1
    got = np.bincount(groups, weights=out, minlength=G).round().astype(np.int64)
    assert np.array_equal(got, targets), "en büyük kalan hedefleri tutmadı"
    return out


def majority_district(pop_int: np.ndarray, pop_raw: np.ndarray, area: np.ndarray) -> tuple[np.ndarray, dict]:
    """Hücre başına çoğunluk ilçesi (n × ilçe dizilerinden): en büyük tam sayı nüfus; eşitlikte yuvarlanmamış kalibre
    nüfus, sonra hücredeki ilçe alanı (hepsi eşitse küçük TÜİK indisi). İkinci değer: eşitlik sayaçları."""
    best = pop_int == pop_int.max(axis=1, keepdims=True)
    tie_int = best.sum(axis=1) > 1
    r = np.where(best, pop_raw, -np.inf)
    best &= r == r.max(axis=1, keepdims=True)
    tie_raw = best.sum(axis=1) > 1
    a = np.where(best, area, -np.inf)
    best &= a == a.max(axis=1, keepdims=True)
    tie_area = best.sum(axis=1) > 1
    has = pop_int.sum(axis=1) > 0
    return np.argmax(best, axis=1), {"integer_ties": int((tie_int & has).sum()),
                                     "still_tied_after_raw": int((tie_raw & has).sum()),
                                     "still_tied_after_area": int((tie_area & has).sum())}


def density_stats(g: grid_io.Grid, pop: np.ndarray, mask: np.ndarray) -> dict:
    """Hücre nüfus yoğunluğu (kişi/km², hücrenin il içi alanına göre) dağılımı, maskedeki hücreler üzerinden."""
    d = pop[mask] / g.area_km2[mask]
    med = float(np.median(d))
    return {"n_cells": int(mask.sum()), "min": float(d.min()), "median": med, "p90": float(np.percentile(d, 90)),
            "max": float(d.max()), "max_over_median": float(d.max() / med) if med > 0 else None}


def write_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    """np.savez_compressed eşdeğeri, ama sabit zaman damgalı (aynı girdi → aynı bayt)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(arrays):
            buf = io.BytesIO()
            np.lib.format.write_array(buf, np.asarray(arrays[name]), allow_pickle=False)
            zi = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, buf.getvalue(), compresslevel=9)


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel(p: Path) -> str:
    return str(Path(p).relative_to(common.ROOT))


def pixel_centres(transform, rows: np.ndarray, cols: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    c = cols.astype(np.float64) + 0.5
    r = rows.astype(np.float64) + 0.5
    return (transform.c + c * transform.a + r * transform.b, transform.f + c * transform.d + r * transform.e)


# ---------------------------------------------------------------- girdiler
def load_inputs():
    import geopandas as gpd

    tuik = pd.read_csv(TUIK_CSV, dtype={"ilce_key": str})
    assert len(tuik) == 25 and tuik["ilce_key"].is_unique
    # Faz 1 ile aynı sıra (ilce_key) — rasterize çakışmalarında aynı sonuç için
    dist = gpd.read_file(ILCE_GEOJSON).sort_values("ilce_key").reset_index(drop=True)
    il = gpd.read_file(IL_GEOJSON)
    assert set(dist["ilce_key"]) == set(tuik["ilce_key"]) and len(il) == 1
    return tuik, dist, il


def source_pixels(key: str, dist, tuik: pd.DataFrame, phase1: pd.DataFrame) -> dict:
    """Kaynağın 2025/2030 piksellerini ilçeye (merkez kuralı) atar; UTM merkezleri ve ilçe toplamları."""
    import rasterio
    from pyproj import Transformer
    from rasterio.features import rasterize

    s = SOURCES[key]
    with rasterio.open(s["f25"]) as a, rasterio.open(s["f30"]) as b:
        assert a.transform == b.transform and a.shape == b.shape and a.crs == b.crs
        v25 = a.read(1).astype(np.float64)
        v30 = b.read(1).astype(np.float64)
        tr, crs = a.transform, a.crs
    dd = dist.to_crs(crs)
    lab = rasterize([(g, i + 1) for i, g in enumerate(dd.geometry)], out_shape=v25.shape, transform=tr,
                    fill=0, dtype="int16", all_touched=False)
    tidx = {k: i for i, k in enumerate(tuik["ilce_key"])}
    lab_to_t = np.array([-1] + [tidx[k] for k in dd["ilce_key"]], dtype=np.int64)

    # ilçe toplamları (TÜİK sırası) ve Faz 1 ile karşılaştırma
    def by_district(v):
        sums = np.bincount(lab.ravel(), weights=v.ravel(), minlength=26)[1:]
        out = np.zeros(25)
        out[lab_to_t[1:]] = sums
        return out

    S25, S30 = by_district(v25), by_district(v30)
    p1 = phase1.set_index("ilce_key").loc[tuik["ilce_key"]]
    d25 = np.abs(S25 - p1[s["col25"]].to_numpy())
    d30 = np.abs(S30 - p1[s["col30"]].to_numpy())
    if d25.max() > PHASE1_TOL or d30.max() > PHASE1_TOL:
        raise SystemExit(f"{key}: ilçe toplamları Faz 1 ile uyuşmuyor (en büyük fark {d25.max():.3f} / {d30.max():.3f})")
    outside = float(v25[lab == 0].sum())

    m = (lab > 0) & ((v25 > 0) | (v30 > 0))
    rows, cols = np.nonzero(m)
    xs, ys = pixel_centres(tr, rows, cols)
    t = Transformer.from_crs(crs, common.CRS_UTM36N, always_xy=True)
    ux, uy = t.transform(xs, ys)
    print(f"  {key}: {m.sum():,} nüfuslu piksel; ilçe toplamı 2025 {S25.sum():,.1f}, 2030 {S30.sum():,.1f}; "
          f"Faz 1 farkı en çok {max(d25.max(), d30.max()):.4f}; ilçe dışı pencere nüfusu {outside:,.0f}")
    return {"x": np.asarray(ux), "y": np.asarray(uy), "d": lab_to_t[lab[m]], "v25": v25[m], "v30": v30[m],
            "S25": S25, "S30": S30, "phase1_maxdiff": float(max(d25.max(), d30.max())),
            "window_outside_districts_2025": outside}


def landcover_pixels(il) -> dict:
    """İl içindeki (merkez kuralı) geçerli WorldCover pikselleri: UTM merkezleri ve 3 bant (%)."""
    import rasterio
    from pyproj import Transformer
    from rasterio.features import rasterize

    with rasterio.open(WC_TIF) as src:
        arr = src.read()
        tr, crs, nod = src.transform, src.crs, src.nodata
    pg = il.to_crs(crs).geometry.iloc[0]
    inside = rasterize([(pg, 1)], out_shape=arr.shape[1:], transform=tr, fill=0, dtype="uint8",
                       all_touched=False).astype(bool)
    valid = inside & (arr != nod).all(axis=0)
    rows, cols = np.nonzero(valid)
    xs, ys = pixel_centres(tr, rows, cols)
    ux, uy = Transformer.from_crs(crs, common.CRS_UTM36N, always_xy=True).transform(xs, ys)
    print(f"  WorldCover: il içinde {inside.sum():,} piksel, geçerli {valid.sum():,}")
    return {"x": np.asarray(ux), "y": np.asarray(uy), "water": arr[0][valid].astype(np.float64),
            "wetland": arr[1][valid].astype(np.float64), "built": arr[2][valid].astype(np.float64),
            "n_inside": int(inside.sum()), "n_valid": int(valid.sum())}


# ---------------------------------------------------------------- nüfus kalibrasyonu (ilçe düzeyi)
def calibrate(tuik: pd.DataFrame, px: dict, p2030: int) -> dict:
    T25 = tuik["nufus_2025"].to_numpy(np.int64)
    out = {}
    for key, p in px.items():
        factor = T25 / p["S25"]
        g_raw = p["S30"] / p["S25"]
        t30_raw = T25 * g_raw
        t30 = t30_raw * (p2030 / t30_raw.sum())
        T30 = largest_remainder(t30, np.zeros(25, dtype=np.int64), np.array([p2030]))
        out[key] = {"factor": factor, "g_raw": g_raw, "T30": T30, "g_final": T30 / T25,
                    "rescale": float(p2030 / t30_raw.sum())}
    return out


def growth_flags(tuik: pd.DataFrame, cal: dict) -> dict:
    """2030 ilçe büyümesini TÜİK'in son yıllık değişimiyle (2024→2025) karşılaştırır (raporlama bayrağı).

    kuculuyor     : senaryoda ilçe 2030'da 2025'ten küçük (il büyürken)
    yon_celiskili : senaryo yıllık hızının işareti TÜİK 2024→2025 değişiminin tersi (ikisi de |r| ≥ %0,25)
    buyuk_fark    : |senaryo yıllık hızı − TÜİK 2024→2025| > 2 puan
    """
    T25 = tuik["nufus_2025"].to_numpy(float)
    T24 = tuik["nufus_2024"].to_numpy(float)
    r_tuik = T25 / T24 - 1
    res = {}
    for key, c in cal.items():
        r_scn = c["g_final"] ** 0.2 - 1
        flags = []
        for i in range(25):
            f = []
            if c["g_final"][i] < 1:
                f.append("kuculuyor")
            if (abs(r_scn[i]) >= FLAG_NEAR_ZERO and abs(r_tuik[i]) >= FLAG_NEAR_ZERO
                    and np.sign(r_scn[i]) != np.sign(r_tuik[i])):
                f.append("yon_celiskili")
            if abs(r_scn[i] - r_tuik[i]) > FLAG_MAX_GAP:
                f.append("buyuk_fark")
            flags.append(f)
        res[key] = {"r_scn": r_scn, "flags": flags}
    res["r_tuik"] = r_tuik
    return res


# ---------------------------------------------------------------- hücreler
def build_cells(s_km: int, prov, dists) -> dict:
    """İl poligonuyla kesişen hizalı kare hücreler; il ve ilçe kesişim alanları."""
    import shapely

    s = s_km * 1000.0
    xmin, ymin, xmax, ymax = prov.bounds
    ixs = np.arange(math.floor(xmin / s), math.floor(xmax / s) + 1)
    iys = np.arange(math.floor(ymin / s), math.floor(ymax / s) + 1)
    IY, IX = np.meshgrid(iys, ixs, indexing="ij")          # sıra: güneyden kuzeye, batıdan doğuya
    ix, iy = IX.ravel(), IY.ravel()
    boxes = shapely.box(ix * s, iy * s, (ix + 1) * s, (iy + 1) * s)
    shapely.prepare(prov)
    hit = shapely.intersects(prov, boxes)
    full = shapely.contains_properly(prov, boxes)
    area = np.where(full, s * s, 0.0)
    part = hit & ~full
    area[part] = shapely.area(shapely.intersection(boxes[part], prov))
    keep = area > 0
    ix, iy, boxes, area = ix[keep], iy[keep], boxes[keep], area[keep]
    n = len(ix)
    # ilçe kesişim alanları (n × 25, TÜİK sırası)
    tree = shapely.STRtree(dists)
    pb, pd_ = tree.query(boxes, predicate="intersects")
    for g in dists:
        shapely.prepare(g)
    inside_d = shapely.contains_properly(dists[pd_], boxes[pb])
    a = np.where(inside_d, s * s, 0.0)
    rest = ~inside_d
    a[rest] = shapely.area(shapely.intersection(boxes[pb[rest]], dists[pd_[rest]]))
    darea = np.zeros((n, len(dists)))
    np.add.at(darea, (pb, pd_), a)
    lut = np.full((len(iys), len(ixs)), -1, dtype=np.int64)
    lut[iy - iys[0], ix - ixs[0]] = np.arange(n)
    return {"s_km": s_km, "s": s, "ix": ix, "iy": iy, "area": area, "inside_frac": area / (s * s),
            "darea": darea, "lut": lut, "ix0": int(ixs[0]), "iy0": int(iys[0])}


def cell_index(cells: dict, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    s, lut = cells["s"], cells["lut"]
    cx = np.floor(x / s).astype(np.int64) - cells["ix0"]
    cy = np.floor(y / s).astype(np.int64) - cells["iy0"]
    ok = (cx >= 0) & (cy >= 0) & (cx < lut.shape[1]) & (cy < lut.shape[0])
    idx = np.full(len(x), -1, dtype=np.int64)
    idx[ok] = lut[cy[ok], cx[ok]]
    return idx


def neighbors8(cells: dict) -> np.ndarray:
    lut = cells["lut"]
    ix = cells["ix"] - cells["ix0"]
    iy = cells["iy"] - cells["iy0"]
    nb = np.full((len(ix), 8), -1, dtype=np.int32)
    for k, (dx, dy) in enumerate(NBR_OFFSETS):
        jx, jy = ix + dx, iy + dy
        ok = (jx >= 0) & (jy >= 0) & (jx < lut.shape[1]) & (jy < lut.shape[0])
        nb[ok, k] = lut[jy[ok], jx[ok]]
    return nb


def grid_arrays(cells: dict, px: dict, cal: dict, lc: dict, tuik: pd.DataFrame, params: dict) -> tuple[dict, pd.DataFrame, dict]:
    """Bir hücre boyutunun tüm dizileri + parça tablosu + ara bilgiler."""
    from pyproj import Transformer

    n, s, s_km = len(cells["ix"]), cells["s"], cells["s_km"]
    T25 = tuik["nufus_2025"].to_numpy(np.int64)
    ix, iy = cells["ix"], cells["iy"]
    x, y = (ix + 0.5) * s, (iy + 0.5) * s
    to_ll = Transformer.from_crs(common.CRS_UTM36N, common.CRS_WGS84, always_xy=True)
    lon, lat = to_ll.transform(x, y)
    A: dict[str, np.ndarray] = {
        "id": np.array([f"{s_km}km_{a}_{b}" for a, b in zip(ix, iy)]),
        "ix": ix.astype(np.int32), "iy": iy.astype(np.int32), "x": x, "y": y,
        "lon": np.asarray(lon), "lat": np.asarray(lat),
        "inside_frac": cells["inside_frac"], "area_km2": cells["area"] / 1e6,
    }
    pieces, pieces_raw = {}, {}
    info = {"pixels_outside_cells": {}}
    for key, p in px.items():
        cidx = cell_index(cells, p["x"], p["y"])
        bad = cidx < 0
        info["pixels_outside_cells"][key] = int(bad.sum())
        if bad.any():
            raise SystemExit(f"{key}: {bad.sum()} piksel merkezi il ile kesişen hücrelerin dışında")
        kk = cidx * 25 + p["d"]
        raw25 = np.bincount(kk, weights=p["v25"], minlength=n * 25)
        raw30 = np.bincount(kk, weights=p["v30"], minlength=n * 25)
        grp = np.tile(np.arange(25), n)
        c = cal[key]
        pieces_raw[key] = raw25 * c["factor"][grp]          # yuvarlanmamış kalibre 2025 parçaları
        p25 = largest_remainder(pieces_raw[key], grp, T25)
        p30 = largest_remainder(raw30 * (c["T30"] / p["S30"])[grp], grp, c["T30"])
        pieces[f"pop2025_{key}"] = p25
        pieces[f"pop2030_{key}"] = p30
        A[f"pop2025_{key}"] = p25.reshape(n, 25).sum(axis=1)
        A[f"pop2030_{key}"] = p30.reshape(n, 25).sum(axis=1)
        # 2025 nüfus ağırlık merkezi (kalibre ağırlıklarla)
        w = p["v25"] * c["factor"][p["d"]]
        sw = np.bincount(cidx, weights=w, minlength=n)
        with np.errstate(invalid="ignore", divide="ignore"):
            mx = np.bincount(cidx, weights=w * p["x"], minlength=n) / sw
            my = np.bincount(cidx, weights=w * p["y"], minlength=n) / sw
        A[f"popx2025_{key}"] = np.where(sw > 0, mx, x)
        A[f"popy2025_{key}"] = np.where(sw > 0, my, y)

    # çoğunluk ilçesi: varsayılan kaynağın 2025 tam sayı nüfusuna göre (eşitlikte yuvarlanmamış nüfus, sonra ilçe
    # alanı), nüfus yoksa alana göre
    default = grid_io.source_key(pval(params, "population.grid_source"))
    pm = pieces[f"pop2025_{default}"].reshape(n, 25)
    by_area = np.argmax(cells["darea"], axis=1)
    by_pop, info["majority_ties"] = majority_district(pm, pieces_raw[default].reshape(n, 25), cells["darea"])
    A["district"] = np.where(pm.sum(axis=1) > 0, by_pop, by_area).astype(np.int16)
    A["district_area"] = by_area.astype(np.int16)

    # arazi örtüsü
    lci = cell_index(cells, lc["x"], lc["y"])
    ok = lci >= 0
    info["landcover_pixels_outside_cells"] = int((~ok).sum())
    cnt = np.bincount(lci[ok], minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        for b in ("water", "wetland", "built"):
            A[f"{b}_pct"] = np.where(cnt > 0, np.bincount(lci[ok], weights=lc[b][ok], minlength=n) / cnt, np.nan)
    A["water_frac"] = (A["water_pct"] + A["wetland_pct"]) / 100.0
    A["lc_pixels"] = cnt.astype(np.int32)

    # komşular ve komşu en büyük nüfusu
    A["nbr8"] = neighbors8(cells)
    g = grid_io.Grid(cell_km=float(s_km), arrays=A)
    for key in px:
        for yr in (2025, 2030):
            A[f"nbr_max_pop{yr}_{key}"] = g.neighbor_max(A[f"pop{yr}_{key}"]).astype(np.int64)

    # varsayılan kural (kapsama onarımı hariç)
    rule = grid_io.RuleParams.from_params(params)
    A["admissible"] = grid_io.admissible_mask(g, rule.min_inside, rule.max_water)
    A["candidate"] = grid_io.candidate_mask(g, rule, default, 2025)
    A["cell_km"] = np.array(float(s_km))
    A["district_keys"] = tuik["ilce_key"].to_numpy(str)
    A["district_names"] = tuik["ilce"].to_numpy(str)
    A["crs"] = np.array(common.CRS_UTM36N)

    # parça tablosu
    cols = ["pop2025_wp", "pop2025_ghs", "pop2030_wp", "pop2030_ghs"]
    P = np.column_stack([pieces[c] for c in cols])
    nz = P.any(axis=1)
    kk = np.flatnonzero(nz)
    df = pd.DataFrame({"cell_id": A["id"][kk // 25], "ilce_key": tuik["ilce_key"].to_numpy()[kk % 25]})
    for j, c in enumerate(cols):
        df[c] = P[nz, j]
    info["n_pieces"] = int(nz.sum())
    info["n_cells_multi_district"] = int((np.bincount(kk // 25, minlength=n) > 1).sum())
    return A, df, info


# ---------------------------------------------------------------- aday ve kapsama özetleri
def candidate_tables(g: grid_io.Grid, params: dict, source: str, year: int) -> list[dict]:
    rule = grid_io.RuleParams.from_params(params)
    adm = grid_io.admissible_mask(g, rule.min_inside, rule.max_water)
    pop = g.pop(source, year)
    nmax = g.neighbor_max(pop)
    thr = rule.near_threshold(g.cell_km)
    rows = []
    for pmin in P_MIN_TABLE:
        base = adm & (pop >= pmin)
        near = base | (adm & (nmax >= thr))
        rows.append({"P_min": pmin, "J_without_near": int(base.sum()), "J_with_near": int(near.sum()),
                     "added_by_near": int(near.sum() - base.sum()),
                     "pop_in_candidates_without_near": int(pop[base].sum()),
                     "pop_in_candidates_with_near": int(pop[near].sum())})
    return rows


def coverage_block(g: grid_io.Grid, params: dict, circ: distance.Circuity, source: str, year: int) -> tuple[dict, np.ndarray]:
    rule = grid_io.RuleParams.from_params(params)
    R = float(pval(params, "access.max_distance_km"))
    cand = grid_io.candidate_mask(g, rule, source, year)
    rep = grid_io.coverage_repair(g, cand, rule, R, circ, source, year)
    pop = g.pop(source, year)
    out = {"source": source, "year": year, "J_rule": int(cand.sum()), "J_repaired": int(rep.mask.sum()),
           **rep.summary(pop),
           "added_cell_ids": [str(g.id[i]) for i in rep.added],
           "added_self": int(sum(1 for i in rep.added if i in set(rep.uncovered_before.tolist()))),
           "uncovered_before_ids": [str(g.id[i]) for i in rep.uncovered_before],
           "uncovered_before_pop_by_cell": [int(pop[i]) for i in rep.uncovered_before]}
    return out, rep.mask


def neighbor_stats(g: grid_io.Grid, cand: np.ndarray, params: dict, circ: distance.Circuity, source: str,
                   year: int) -> tuple[dict, distance.NeighborCSR, np.ndarray, np.ndarray]:
    R = float(pval(params, "access.max_distance_km"))
    k = int(pval(params, "grid.k_nearest"))
    pop = g.pop(source, year)
    dem = np.flatnonzero(pop > 0)
    cid = np.flatnonzero(cand)
    xy = g.xy
    allr = distance.neighbor_lists(xy[dem], xy[cid], g.cell_km, R, None, circ)
    cnt = allr.counts
    times = []
    for _ in range(NEIGHBOR_TIMING_RUNS):
        t0 = time.perf_counter()
        nl = distance.neighbor_lists(xy[dem], xy[cid], g.cell_km, R, k, circ)
        times.append(time.perf_counter() - t0)
    # k-en-yakın listesi = tüm R içi listenin başı (tutarlılık); k'dan uzun satırların fazlası k'ıncıya eşit mesafede
    cnt_k = nl.counts
    assert (cnt_k >= np.minimum(cnt, k)).all() and (cnt_k <= cnt).all()
    for i in np.flatnonzero(cnt_k > k):
        jj, dd = nl.row(i)
        ja, _ = allr.row(i)
        assert np.array_equal(jj, ja[:len(jj)]) and dd[-1] - dd[k - 1] <= distance.TIE_TOL_KM
    extra = cnt_k - np.minimum(cnt, k)
    first = nl.dist_km[nl.indptr[:-1][nl.counts > 0]]
    w = pop[dem]
    stats = {"n_demand": int(len(dem)), "n_candidates": int(len(cid)), "R_km": R, "k": k,
             "candidates_within_R_mean": float(cnt.mean()), "candidates_within_R_median": float(np.median(cnt)),
             "candidates_within_R_min": int(cnt.min()), "candidates_within_R_max": int(cnt.max()),
             "share_demand_cells_truncated_by_k": float((cnt > k).mean()),
             "nnz_all_within_R": int(cnt.sum()), "nnz_k_nearest": int(cnt_k.sum()),
             "k_list_tie_extra_pairs": int(extra.sum()), "k_list_rows_longer_than_k": int((cnt_k > k).sum()),
             "k_list_row_len_mean": float(cnt_k.mean()), "k_list_row_len_max": int(cnt_k.max()),
             "nearest_candidate_road_km_popweighted_mean": float((first * w[nl.counts > 0]).sum() / w[nl.counts > 0].sum()),
             "nearest_candidate_road_km_max": float(first.max())}
    timing = {"neighbor_lists_s_median": float(np.median(times)), "neighbor_lists_s_min": float(min(times)),
              "runs": NEIGHBOR_TIMING_RUNS}
    return stats | {"timing": timing}, nl, dem, cid


# ---------------------------------------------------------------- web çıktısı
def write_cells_geojson(path: Path, g: grid_io.Grid, default: str) -> int:
    from pyproj import Transformer

    s = g.cell_km * 1000.0
    ix, iy = g.ix.astype(np.float64), g.iy.astype(np.float64)
    t = Transformer.from_crs(common.CRS_UTM36N, common.CRS_WGS84, always_xy=True)
    corners = []
    for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1)):
        lo, la = t.transform((ix + dx) * s, (iy + dy) * s)
        corners.append((np.round(lo, 5), np.round(la, 5)))
    alt = "ghs" if default == "wp" else "wp"
    pop = g.pop(default, 2025)
    with np.errstate(divide="ignore", invalid="ignore"):
        dens = np.where(g.area_km2 > 0, pop / g.area_km2, 0.0)
    names = g.district_names
    feats = []
    for i in range(g.n):
        ring = [[float(corners[k][0][i]), float(corners[k][1][i])] for k in (0, 1, 2, 3, 0)]
        wpct = g.water_pct[i] + g.wetland_pct[i]
        props = {"id": str(g.id[i]), "pop": int(pop[i]), "pop_ghs" if alt == "ghs" else "pop_wp": int(g.pop(alt, 2025)[i]),
                 "pop2030": int(g.pop(default, 2030)[i]), "dens": round(float(dens[i]), 1),
                 "water": None if not np.isfinite(wpct) else round(float(wpct), 1),
                 "built": None if not np.isfinite(g.built_pct[i]) else round(float(g.built_pct[i]), 1),
                 "ilce": names[int(g.district[i])], "aday": bool(g.candidate[i])}
        feats.append('{"type":"Feature","geometry":{"type":"Polygon","coordinates":[' +
                     json.dumps(ring, separators=(",", ":")) + ']},"properties":' +
                     json.dumps(props, ensure_ascii=False, separators=(",", ":")) + "}")
    text = '{"type":"FeatureCollection","name":"' + path.stem + '","features":[\n' + ",\n".join(feats) + "\n]}\n"
    path.write_text(text, encoding="utf-8")
    return path.stat().st_size


# ---------------------------------------------------------------- ana akış
ARRAY_DOC = {
    "id": "hücre kimliği '<s>km_<ix>_<iy>'",
    "ix, iy": "hücre indisleri: hücre = [ix·s, (ix+1)·s) × [iy·s, (iy+1)·s) m (EPSG:32636)",
    "x, y": "hücre merkezi, UTM 36N (m)", "lon, lat": "hücre merkezi, WGS84",
    "inside_frac": "alan(hücre ∩ il) / alan(hücre), UTM", "area_km2": "hücrenin il içindeki alanı (km², UTM)",
    "district": ("çoğunluk ilçesi (TÜİK sırasında indis): varsayılan kaynağın 2025 tam sayı nüfusuna göre; eşitlikte "
                 "yuvarlanmamış kalibre nüfus, sonra hücredeki ilçe alanı; nüfus yoksa alana göre"),
    "district_area": "alana göre çoğunluk ilçesi",
    "pop2025_wp, pop2025_ghs": "TÜİK 2025'e kalibre tam sayı nüfus (WorldPop / GHS-POP dağılımı)",
    "pop2030_wp, pop2030_ghs": "2030 senaryosu tam sayı nüfus (il = population.province_2030)",
    "popx2025_*, popy2025_*": "2025 nüfus ağırlık merkezi (UTM m; kaynak pikseli yoksa hücre merkezi)",
    "water_pct, wetland_pct, built_pct": "WorldCover %: hücreye merkezi düşen il içi 100 m piksellerin ortalaması",
    "water_frac": "(water_pct + wetland_pct) / 100", "lc_pixels": "ortalamaya giren WorldCover piksel sayısı",
    "nbr8": "8 komşu hücre indisi (sıra: GB, G, GD, B, D, KB, K, KD; yoksa −1)",
    "nbr_max_pop<yıl>_<kaynak>": "8 komşudaki en büyük nüfus (yakınlık kuralı: ≥ grid.candidate_near_density × s²)",
    "admissible": "inside_frac ≥ grid.candidate_min_inside ve water_frac < grid.candidate_max_water",
    "candidate": "varsayılan aday kuralı (varsayılan kaynak 2025; kapsama onarımı HARİÇ)",
    "cell_km, crs, district_keys, district_names": "üst veri",
}


def main() -> None:
    import shapely

    params = json.loads(PARAMS_JSON.read_text(encoding="utf-8"))
    P25 = int(pval(params, "population.province_2025"))
    P30 = int(pval(params, "population.province_2030"))
    default = grid_io.source_key(pval(params, "population.grid_source"))
    circ = distance.Circuity.from_params(params)
    R = float(pval(params, "access.max_distance_km"))
    OUT.mkdir(parents=True, exist_ok=True)

    print("1) Girdiler")
    tuik, dist, il = load_inputs()
    assert int(tuik["nufus_2025"].sum()) == P25
    phase1 = pd.read_csv(PHASE1_CSV, dtype={"ilce_key": str})
    phase1 = phase1[phase1["satir_turu"] == "ilce"]
    px = {k: source_pixels(k, dist, tuik, phase1) for k in SOURCES}
    lc = landcover_pixels(il)

    print("2) İlçe kalibrasyonu ve 2030 senaryosu")
    cal = calibrate(tuik, px, P30)
    fl = growth_flags(tuik, cal)
    T25 = tuik["nufus_2025"].to_numpy(np.int64)
    calib_rows = []
    for i, r in tuik.reset_index(drop=True).iterrows():
        row = {"ilce": r["ilce"], "ilce_key": r["ilce_key"], "tuik_2024": int(r["nufus_2024"]),
               "tuik_2025": int(r["nufus_2025"]), "tuik_degisim_2024_2025": round(float(fl["r_tuik"][i]), 5)}
        for k in SOURCES:
            c = cal[k]
            row |= {f"{k}_kaynak_2025": round(float(px[k]["S25"][i]), 1),
                    f"{k}_kaynak_2030": round(float(px[k]["S30"][i]), 1),
                    f"{k}_kalibrasyon_katsayisi": round(float(c["factor"][i]), 5),
                    f"{k}_kaynak_buyume_2030_2025": round(float(c["g_raw"][i]), 5),
                    f"{k}_senaryo_2030": int(c["T30"][i]),
                    f"{k}_senaryo_buyume_2030_2025": round(float(c["g_final"][i]), 5),
                    f"{k}_senaryo_yillik_hiz": round(float(fl[k]["r_scn"][i]), 5),
                    f"{k}_bayrak": ";".join(fl[k]["flags"][i])}
        calib_rows.append(row)
    calib = pd.DataFrame(calib_rows)
    calib.to_csv(OUT / "ilce_kalibrasyon.csv", index=False, encoding="utf-8")
    for k in SOURCES:
        print(f"  {k}: katsayı {cal[k]['factor'].min():.3f}–{cal[k]['factor'].max():.3f}; 2030 ölçek "
              f"{cal[k]['rescale']:.5f}; bayraklı ilçe: "
              + ", ".join(f"{tuik['ilce'][i]} ({'/'.join(f)})" for i, f in enumerate(fl[k]['flags']) if f))

    print("3) Izgaralar")
    prov = shapely.union_all(il.to_crs(common.CRS_UTM36N).geometry.values)
    du = dist.to_crs(common.CRS_UTM36N).set_index("ilce_key")
    dists = np.array([shapely.union_all(du.loc[[k]].geometry.values) for k in tuik["ilce_key"]], dtype=object)
    summary_sizes, timings, grids, files = {}, {}, {}, {}
    for s_km in CELL_SIZES_KM:
        cells = build_cells(s_km, prov, dists)
        A, pieces, info = grid_arrays(cells, px, cal, lc, tuik, params)
        g = grid_io.Grid(cell_km=float(s_km), arrays=A)
        grids[s_km] = g
        # denetimler: ilçe ve il toplamları birebir
        for k in SOURCES:
            for yr, tgt in ((2025, T25), (2030, cal[k]["T30"])):
                col = f"pop{yr}_{k}"
                byd = pieces.groupby("ilce_key")[col].sum().reindex(tuik["ilce_key"], fill_value=0).to_numpy()
                assert np.array_equal(byd, tgt), (s_km, col)
                assert int(A[col].sum()) == int(tgt.sum()) == (P25 if yr == 2025 else P30), (s_km, col)
        assert A["id"].size == np.unique(A["id"]).size
        write_npz(OUT / f"grid_{s_km}km.npz", A)
        pieces.to_csv(OUT / f"grid_{s_km}km_pieces.csv", index=False, encoding="utf-8")

        cov, cand_rep = coverage_block(g, params, circ, default, 2025)
        cov_alt = {f"{k}_{yr}": {kk: v for kk, v in coverage_block(g, params, circ, k, yr)[0].items()
                                 if not kk.endswith(("_ids", "_by_cell"))}
                   for k in SOURCES for yr in (2025, 2030) if (k, yr) != (default, 2025)}
        nstats, nl, dem, cid = neighbor_stats(g, cand_rep, params, circ, default, 2025)
        timings[f"{s_km}km"] = nstats.pop("timing")
        if s_km == 3:
            write_npz(OUT / "neighbors_3km_default.npz", {
                "indptr": nl.indptr, "indices": nl.indices, "dist_km": nl.dist_km,
                "demand_idx": dem.astype(np.int64), "cand_idx": cid.astype(np.int64),
                "cell_km": np.array(3.0), "R_km": np.array(R), "k": np.array(int(pval(params, "grid.k_nearest"))),
                "circuity_mode": np.array(circ.mode), "circuity_scale": np.array(circ.scale),
                "source": np.array(default), "year": np.array(2025),
                "candidate_rule": np.array("varsayılan kural + kapsama onarımı (grid_io.coverage_repair)"),
                "tie_rule": np.array("k'ıncı yol mesafesine eşit (distance.TIE_TOL_KM içinde) adayların hepsi tutulur")})
        size = write_cells_geojson(OUT / f"grid_{s_km}km_cells.geojson", g, default)
        assert size < 3_000_000, f"geojson {size} B"
        files[f"{s_km}km"] = {"npz": rel(OUT / f"grid_{s_km}km.npz"), "pieces": rel(OUT / f"grid_{s_km}km_pieces.csv"),
                              "cells_geojson": rel(OUT / f"grid_{s_km}km_cells.geojson"), "geojson_bytes": size}
        pops = {f"{k}_{yr}": g.pop(k, yr) for k in SOURCES for yr in (2025, 2030)}
        adm = A["admissible"]
        summary_sizes[f"{s_km}km"] = {
            "cell_km": s_km, "n_cells": g.n, "n_cells_fully_inside": int((A["inside_frac"] >= 1 - 1e-12).sum()),
            "sum_inside_area_km2": round(float(A["area_km2"].sum()), 3),
            "n_admissible": int(adm.sum()),
            "n_excluded_inside_frac": int((A["inside_frac"] < float(pval(params, "grid.candidate_min_inside"))).sum()),
            "n_excluded_water": int(((A["inside_frac"] >= float(pval(params, "grid.candidate_min_inside")))
                                     & ~(A["water_frac"] < float(pval(params, "grid.candidate_max_water")))).sum()),
            "n_demand_cells": {kk: int((v > 0).sum()) for kk, v in pops.items()},
            "sum_pop": {kk: int(v.sum()) for kk, v in pops.items()},
            "max_cell_pop": {kk: int(v.max()) for kk, v in pops.items()},
            "n_pieces": info["n_pieces"], "n_cells_multi_district": info["n_cells_multi_district"],
            "majority_district_ties": info["majority_ties"],
            "J_table": {"source": default, "year": 2025,
                        "near_density_per_km2": float(pval(params, "grid.candidate_near_density")),
                        "near_threshold_per_cell": grid_io.RuleParams.from_params(params).near_threshold(s_km),
                        "rows": candidate_tables(g, params, default, 2025)},
            "J_table_ghs_2025": candidate_tables(g, params, "ghs", 2025) if default == "wp" else None,
            "coverage_default": cov,
            "coverage_other_scenarios": cov_alt,
            "neighbors_default": nstats,
            # arsa fiyatı vekili (land.price_*) için yoğunluk dağılımı: varsayılan kaynak 2025, il içi alana göre
            "density_per_km2": {"demand_cells": density_stats(g, pops[f"{default}_2025"], pops[f"{default}_2025"] > 0),
                                "candidate_cells": density_stats(g, pops[f"{default}_2025"], cand_rep)},
        }
        r = summary_sizes[f"{s_km}km"]
        print(f"  {s_km} km: {g.n:,} hücre; talep {r['n_demand_cells'][f'{default}_2025']:,}; uygun {adm.sum():,}; "
              f"|J| kural {cov['J_rule']:,} → onarım {cov['J_repaired']:,} (+{cov['added_cells']}); "
              f"kapsanmayan önce {cov['uncovered_before_cells']} hücre / {cov['uncovered_before_pop']:,} kişi, "
              f"sonra {cov['uncovered_after_cells']}; R içinde uygun yok {cov['no_admissible_within_R_cells']}; "
              f"R içi aday ort. {nstats['candidates_within_R_mean']:.1f}, en çok {nstats['candidates_within_R_max']}; "
              f"k-liste eşitlik fazlası {nstats['k_list_tie_extra_pairs']} çift (satır ort. "
              f"{nstats['k_list_row_len_mean']:.2f}, en çok {nstats['k_list_row_len_max']}); "
              f"k-liste {timings[f'{s_km}km']['neighbor_lists_s_median'] * 1000:.0f} ms; geojson {size / 1e6:.2f} MB")

    print("4) Üst veri ve özet")
    from pipeline.common import sha256
    inputs = [IL_GEOJSON, ILCE_GEOJSON, TUIK_CSV, PHASE1_CSV, WC_TIF, *(SOURCES[k][f] for k in SOURCES for f in ("f25", "f30"))]
    meta = {
        "generated_by": "pipeline/22_grids.py",
        "crs": common.CRS_UTM36N, "crs_web": common.CRS_WGS84,
        "cell_sizes_km": list(CELL_SIZES_KM),
        "origin_utm_m": [0.0, 0.0],
        "alignment": "hücre kenarları UTM 36N koordinatlarında s'nin katları: hücre (ix, iy) = [ix·s, (ix+1)·s) × [iy·s, (iy+1)·s) m",
        "id_format": "<s>km_<ix>_<iy>",
        "cell_selection": "il poligonuyla kesişim alanı > 0 olan her hücre",
        "default_source": default, "source_keys": grid_io.SOURCE_KEYS,
        "sources": {k: {"name": v["name"], "registry_value": v["registry"], "files": [rel(v["f25"]), rel(v["f30"])],
                        "source_ids": v["source_ids"]} for k, v in SOURCES.items()},
        "landcover": {"file": rel(WC_TIF), "source_ids": ["alan_literatur_lit_zanaga2022"],
                      "bands": "1 = % su (80), 2 = % sulak alan (90), 3 = % yapılaşma (50)"},
        "boundaries": {"il": rel(IL_GEOJSON), "ilceler": rel(ILCE_GEOJSON)},
        "assignment_rule": "piksel merkezi poligonda (rasterio.features.rasterize, all_touched=False; ilçeler ilce_key "
                           "sırasıyla, Faz 1 ile aynı) ve piksel merkezinin UTM 36N'deki hücresi",
        "calibration_2025": "factor_d = TÜİK2025_d / Σ kaynak_d; (hücre, ilçe) parçaları ilçe içinde en büyük kalanla tam sayı",
        "scenario_2030": ("T30_d = TÜİK2025_d × S30_d / S25_d; Σ T30 = population.province_2030 olacak şekilde ölçek; "
                          "ilçe tam sayıları en büyük kalanla; ilçe içi dağılım kaynağın 2030 katmanı"),
        "province_2025": P25, "province_2030": P30,
        "districts": [{"idx": i, "ilce": r["ilce"], "ilce_key": r["ilce_key"], "tuik_2025": int(r["nufus_2025"]),
                       **{f"pop2030_{k}": int(cal[k]["T30"][i]) for k in SOURCES}}
                      for i, r in tuik.reset_index(drop=True).iterrows()],
        "arrays": ARRAY_DOC,
        "geojson_properties": {"id": "hücre kimliği", "pop": f"2025 kalibre nüfus ({default})",
                               "pop_ghs": "2025 kalibre nüfus (GHS-POP dağılımı)", "pop2030": f"2030 senaryosu ({default})",
                               "dens": "pop / il içi alan (kişi/km²)", "water": "% su + sulak alan",
                               "built": "% yapılaşma", "ilce": "çoğunluk ilçesi", "aday": "varsayılan kural (onarım hariç)"},
        "files": files | {"neighbors_3km_default": rel(OUT / "neighbors_3km_default.npz"),
                          "calibration_table": rel(OUT / "ilce_kalibrasyon.csv"),
                          "summary": rel(OUT / "grid_summary.json")},
        "inputs_sha256": {rel(p): sha256(p) for p in inputs},
        "params_used": {k: pval(params, k) for k in (
            "population.province_2025", "population.province_2030", "population.grid_source", "grid.cell_km",
            "grid.candidate_min_pop", "grid.candidate_near_density", "grid.candidate_min_inside",
            "grid.candidate_max_water", "grid.k_nearest", "access.max_distance_km", "access.circuity_mode",
            "access.circuity_profile", "access.circuity_scale", "access.circuity")},
    }
    write_json(OUT / "grid_meta.json", meta)

    calib_summary = {}
    for k in SOURCES:
        c = cal[k]
        calib_summary[k] = {
            "factor_min": float(c["factor"].min()), "factor_max": float(c["factor"].max()),
            "source_province_2025": float(px[k]["S25"].sum()), "source_province_2030": float(px[k]["S30"].sum()),
            "rescale_2030": c["rescale"],
            "phase1_max_abs_diff": px[k]["phase1_maxdiff"],
            "districts": {tuik["ilce_key"][i]: {"factor": float(c["factor"][i]), "growth_source": float(c["g_raw"][i]),
                                                "pop2030": int(c["T30"][i]), "growth_final": float(c["g_final"][i]),
                                                "annual_rate_2025_2030": float(fl[k]["r_scn"][i]),
                                                "flags": fl[k]["flags"][i]} for i in range(25)},
            "flagged": [tuik["ilce"][i] for i in range(25) if fl[k]["flags"][i]],
        }
    summary = {
        "generated_by": "pipeline/22_grids.py", "generated": common.today(),
        "province_2025": P25, "province_2030": P30, "default_source": default,
        "rule_params": {k: pval(params, k) for k in ("grid.candidate_min_pop", "grid.candidate_near_density",
                                                     "grid.candidate_min_inside", "grid.candidate_max_water")},
        "near_threshold_per_cell": {f"{s}km": grid_io.RuleParams.from_params(params).near_threshold(s)
                                    for s in CELL_SIZES_KM},
        "R_km": R, "k_nearest": int(pval(params, "grid.k_nearest")), "k_tie_tol_km": distance.TIE_TOL_KM,
        "circuity": {"mode": circ.mode, "scale": circ.scale, "min_factor": circ.min_factor,
                     "prefilter_euclid_km": R / circ.min_factor,
                     "R_euclid_km": circ.euclid_for_road_km(R),
                     "R_euclid_km_note": "road_km(e) = R olan kuş uçuşu e (varsayılan dolambaç modu ve profiliyle)",
                     "R_euclid_km_constant_mode": distance.Circuity.from_params(params, mode="sabit").euclid_for_road_km(R)},
        "intra_cell_euclid_km": {f"{s}km": distance.intra_cell_euclid_km(s) for s in CELL_SIZES_KM},
        "intra_cell_road_km": {f"{s}km": float(circ.road_km(distance.intra_cell_euclid_km(s))) for s in CELL_SIZES_KM},
        "calibration": calib_summary,
        "growth_flag_rule": {"near_zero_annual": FLAG_NEAR_ZERO, "max_gap_annual": FLAG_MAX_GAP,
                             "basis": "TÜİK ADNKS 2024→2025 ilçe değişimi",
                             "note": "kuculuyor: senaryoda 2030 < 2025; yon_celiskili: senaryo 2025→2030 yıllık hızının "
                                     "işareti TÜİK 2024→2025 değişiminin tersi (ikisi de |r| ≥ %0,25); buyuk_fark: "
                                     "fark > 2 puan/yıl. Yalnız raporlama (model_secimi eşikler)."},
        "landcover": {"pixels_inside_province": lc["n_inside"], "pixels_valid": lc["n_valid"]},
        "sizes": summary_sizes,
        "timings_note": "makineye bağlı; deterministik değildir (geliştirme kabı, Linux, 4 çekirdek)",
        "timings": timings,
    }
    write_json(OUT / "grid_summary.json", summary)
    print("  yazıldı:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
