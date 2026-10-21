"""Ankara için OSM yol ağından dolambaç katsayısı (circuity = ağ mesafesi / kuş uçuşu mesafe) ve Kızılay noktası.

Adımlar
1. Türkiye OSM PBF (Geofabrik; erişilemezse Hugging Face aynası) -> Ankara kutusundaki (common.ANKARA_BBOX)
   araç yolları (motorway…residential, living_street, unclassified, *_link). Tek yön kuralları uygulanır.
2. Yönlü graf (kenar uzunluğu EPSG:32636'da metre), en büyük güçlü bağlı bileşen; derece-2 düğümler daraltılır.
3. GHS-POP 2025 1 km (R2023A) Ankara içi dolu hücrelerinden örneklem:
   köken ∝ nüfus; her köken için her mesafe bandından (0–5, 5–10, 10–20, 20–40, 40–80, 80+ km) bir hedef ∝ nüfus.
   Ağırlık w = kökenin o banttaki toplam nüfusu (ters olasılık) -> "rastgele iki kişi arası yolculuk" temsili.
4. En yakın graf düğümüne bağlama (motorway/trunk düğümleri hariç; >1 km ise çift atılır),
   Dijkstra ağ mesafesi / düğümler arası kuş uçuşu.
5. Kızılay Meydanı (OSM place=square) koordinatı.

Çıktılar: data/reference/circuity_ankara.csv, data/processed/circuity_ankara.png,
          data/reference/params/dolambac.yaml, data/reference/kizilay.yaml
Çalıştırma (repo kökünden):  .venv/bin/python pipeline/15_circuity.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

RAW_OSM = common.RAW / "osm"
PBF = RAW_OSM / "turkey-latest.osm.pbf"
PBF_URLS = [
    "https://download.geofabrik.de/europe/turkey-latest.osm.pbf",
    "https://huggingface.co/datasets/NoeFlandre/osm-geofabrik-raw-pbf-extracts/resolve/main/turkey-latest.osm.pbf",
]
GHS_1KM_URL = ("https://jrc-ghsl.s3.amazonaws.com/ghs-pop/r2023a/54009/1000m/2025/"
               "GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0.tif")
GHS_1KM_LOCAL = RAW_OSM / "GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0_ankara_window.tif"
CROSSREF = {
    "boscoe_2012": "10.1080/00330124.2011.583586",
    "ballou_2002": "10.1016/S0965-8564(01)00044-1",
}

ROADS_NPZ = RAW_OSM / "ankara_drivable_ways.npz"
PAIRS_CSV = RAW_OSM / "circuity_pairs.csv"
OUT_CSV = common.REFERENCE / "circuity_ankara.csv"
OUT_PNG = common.PROCESSED / "circuity_ankara.png"
OUT_PARAM = common.PARAMS_DIR / "dolambac.yaml"
OUT_KIZILAY = common.REFERENCE / "kizilay.yaml"

DRIVABLE = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
NO_ACCESS = {"no", "private"}
# Bağlama (snap) bu sınıflara yapılmaz: erişim kontrollü/bölünmüş yollara bağlanınca yapay dolambaç oluşuyor
NO_SNAP = {"motorway", "motorway_link", "trunk", "trunk_link"}
YES_ACCESS = {"yes", "designated", "permissive", "destination"}

# Ankara'nın bitişik kent dokusunu oluşturan 9 merkez ilçe (model seçimi; bkz. rapor)
CORE_DISTRICTS = ["Altındağ", "Çankaya", "Etimesgut", "Gölbaşı", "Keçiören", "Mamak", "Pursaklar",
                  "Sincan", "Yenimahalle"]
BANDS = [(0, 5), (5, 10), (10, 20), (20, 40), (40, 80), (80, np.inf)]
N_ORIGINS = 800
SEED = 20261021
MAX_SNAP_M = 1000.0
MIN_EUCLID_M = 500.0
SRC_PREFIX = "arazi_osm_"


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


def ensure(url: str, dest: Path, timeout: int = 120) -> Path:
    if not _manifest_has(dest, url):
        common.download(url, dest, timeout=timeout)
    return dest


def ensure_pbf() -> str:
    """PBF'i getirir; kullanılan URL'yi döndürür."""
    for url in PBF_URLS:
        if _manifest_has(PBF, url):
            return url
    last = None
    for url in PBF_URLS:
        try:
            common.download(url, PBF, timeout=600, overwrite=True)
            return url
        except Exception as exc:  # Geofabrik bağlantıyı sıfırlayabiliyor
            print(f"  PBF indirilemedi ({url}): {exc}")
            last = exc
    raise RuntimeError(f"OSM PBF indirilemedi: {last}")


def pbf_header() -> dict:
    import osmium
    r = osmium.io.Reader(str(PBF), osmium.osm.osm_entity_bits.NOTHING)
    h = r.header()
    out = {k: h.get(k) for k in ("generator", "osmosis_replication_timestamp",
                                 "osmosis_replication_sequence_number", "osmosis_replication_base_url")}
    r.close()
    return out


def ghs_pop_window():
    """GHS-POP 2025 1 km'yi /vsicurl ile Ankara penceresinde okur; yerel kopyayı manifestle saklar."""
    import rasterio
    from rasterio.windows import from_bounds
    if not GHS_1KM_LOCAL.exists():
        env = dict(GDAL_CURL_CA_BUNDLE=os.environ.get("CURL_CA_BUNDLE", "/root/.ccr/ca-bundle.crt"),
                   GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR")
        with rasterio.Env(**env), rasterio.open("/vsicurl/" + GHS_1KM_URL) as ds:
            win = from_bounds(*common.GHS_WINDOW, transform=ds.transform).round_offsets().round_lengths()
            arr = ds.read(1, window=win)
            prof = ds.profile.copy()
            prof.update(width=arr.shape[1], height=arr.shape[0], transform=ds.window_transform(win),
                        compress="deflate", tiled=False)
            prof.pop("blockxsize", None), prof.pop("blockysize", None)
            win_desc = {"col_off": int(win.col_off), "row_off": int(win.row_off),
                        "width": int(win.width), "height": int(win.height)}
        GHS_1KM_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(GHS_1KM_LOCAL, "w", **prof) as dst:
            dst.write(arr, 1)
        rec = {"url": GHS_1KM_URL, "file": str(GHS_1KM_LOCAL.relative_to(common.ROOT)),
               "accessed": common.today(), "bytes": GHS_1KM_LOCAL.stat().st_size,
               "sha256": common.sha256(GHS_1KM_LOCAL), "window": win_desc,
               "note": "/vsicurl pencereli okuma (common.GHS_WINDOW); tüm dosya indirilmedi"}
        with open(GHS_1KM_LOCAL.parent / "_manifest.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with rasterio.open(GHS_1KM_LOCAL) as ds:
        arr = ds.read(1).astype(np.float64)
        nod = ds.nodata
        tr = ds.transform
    arr[arr == nod] = 0.0
    arr[arr < 0] = 0.0
    return arr, tr


# ---------------------------------------------------------------- OSM okuma
def read_osm():
    """Araç yollarını ve Kızılay meydanını tek geçişte okur (önbellek: ROADS_NPZ)."""
    if ROADS_NPZ.exists():
        z = np.load(ROADS_NPZ, allow_pickle=False)
        kiz = json.loads(str(z["kizilay"]))
        return z["node_id"], z["lon"], z["lat"], z["way_start"], z["way_dir"], z["way_hw"], kiz
    import osmium
    lon0, lat0, lon1, lat1 = common.ANKARA_BBOX
    hw_codes = {h: i for i, h in enumerate(sorted(DRIVABLE))}
    node_id, lon, lat, way_start, way_dir, way_hw = [], [], [], [], [], []
    kizilay = []
    fp = (osmium.FileProcessor(str(PBF), osmium.osm.NODE | osmium.osm.WAY)
          .with_locations()
          .with_filter(osmium.filter.KeyFilter("highway", "place")))
    t0 = time.time()
    for o in fp:
        if o.is_node():
            if o.tags.get("place") == "square" and "kızılay" in o.tags.get("name", "").lower():
                kizilay.append({"osm_type": "node", "osm_id": int(o.id), "name": o.tags.get("name"),
                                "lon": o.location.lon, "lat": o.location.lat,
                                "tags": {k: v for k, v in o.tags}})
            continue
        hw = o.tags.get("highway")
        if hw not in DRIVABLE or o.tags.get("area") == "yes":
            continue
        mv = o.tags.get("motor_vehicle") or o.tags.get("motorcar") or o.tags.get("vehicle")
        if mv in NO_ACCESS or (o.tags.get("access") in NO_ACCESS and mv not in YES_ACCESS):
            continue
        try:
            ids = [n.ref for n in o.nodes]
            xs = [n.lon for n in o.nodes]
            ys = [n.lat for n in o.nodes]
        except Exception:  # konumu eksik düğüm
            continue
        if len(ids) < 2:
            continue
        if not any(lon0 <= x <= lon1 and lat0 <= y <= lat1 for x, y in zip(xs, ys)):
            continue
        ow = o.tags.get("oneway")
        if ow in ("yes", "true", "1"):
            d = 1
        elif ow in ("-1", "reverse"):
            d = -1
        elif ow in ("no", "false", "0"):
            d = 0
        elif hw == "motorway" or o.tags.get("junction") in ("roundabout", "circular"):
            d = 1  # OSM'de örtük tek yön
        else:
            d = 0
        way_start.append(len(node_id))
        node_id.extend(ids)
        lon.extend(xs)
        lat.extend(ys)
        way_dir.append(d)
        way_hw.append(hw_codes[hw])
    print(f"  OSM okundu: {len(way_start):,} yol, {len(node_id):,} düğüm referansı ({time.time() - t0:.0f} sn)")
    arrs = dict(node_id=np.asarray(node_id, dtype=np.int64), lon=np.asarray(lon), lat=np.asarray(lat),
                way_start=np.asarray(way_start, dtype=np.int64), way_dir=np.asarray(way_dir, dtype=np.int8),
                way_hw=np.asarray(way_hw, dtype=np.int8))
    np.savez_compressed(ROADS_NPZ, kizilay=json.dumps(kizilay, ensure_ascii=False), **arrs)
    return arrs["node_id"], arrs["lon"], arrs["lat"], arrs["way_start"], arrs["way_dir"], arrs["way_hw"], kizilay


# ---------------------------------------------------------------- graf
def utm(lon, lat):
    from pyproj import Transformer
    t = Transformer.from_crs(common.CRS_WGS84, common.CRS_UTM36N, always_xy=True)
    return t.transform(lon, lat)


def build_edges(nidx, x, y, way_start, way_dir, keep_node):
    """Yolları 'keep_node' düğümlerinde böler; yönlü kenarlar (u, v, uzunluk m) döndürür."""
    M = len(nidx)
    way_end = np.r_[way_start[1:], M] - 1
    way_of = np.repeat(np.arange(len(way_start)), np.diff(np.r_[way_start, M]))
    seg = np.hypot(np.diff(x[nidx]), np.diff(y[nidx]))
    seg = np.r_[seg, 0.0]
    seg[way_end] = 0.0                      # yol sonundan sonrakine geçiş yok
    cs = np.r_[0.0, np.cumsum(seg)[:-1]]    # konum p'ye kadar birikimli uzunluk
    is_kept = keep_node[nidx].copy()
    is_kept[way_start] = True
    is_kept[way_end] = True
    kp = np.flatnonzero(is_kept)
    a, b = kp[:-1], kp[1:]
    same = way_of[a] == way_of[b]
    a, b = a[same], b[same]
    length = cs[b] - cs[a]
    d = way_dir[way_of[a]]
    u = np.r_[nidx[a][d >= 0], nidx[b][d <= 0]]
    v = np.r_[nidx[b][d >= 0], nidx[a][d <= 0]]
    L = np.r_[length[d >= 0], length[d <= 0]]
    ok = u != v
    return u[ok], v[ok], L[ok]


def to_csr(u, v, L, n):
    """Yinelenen kenarlardan en kısasını tutar."""
    from scipy.sparse import csr_matrix
    key = u.astype(np.int64) * n + v
    order = np.lexsort((L, key))
    key, L2 = key[order], L[order]
    first = np.r_[True, key[1:] != key[:-1]]
    key, L2 = key[first], L2[first]
    L2 = np.maximum(L2, 1e-3)  # sıfır uzunluklu kenar seyrek matriste kaybolmasın
    return csr_matrix((L2, (key // n, key % n)), shape=(n, n))


def largest_scc(G):
    from scipy.sparse.csgraph import connected_components
    _, lab = connected_components(G, directed=True, connection="strong")
    big = np.bincount(lab).argmax()
    return lab == big


# ---------------------------------------------------------------- örneklem
def populated_cells(arr, tr):
    """Ankara içindeki (merkezi il poligonunda) nüfuslu 1 km hücreler."""
    import geopandas as gpd
    from shapely import points
    from pyproj import Transformer
    gb = common.RAW / "sinirlar" / "geoboundaries"
    adm1 = gpd.read_file(gb / "ADM1--geoBoundaries-TUR-ADM1.geojson")
    ank = adm1[adm1["shapeName"].map(common.norm_name) == "ankara"].to_crs(common.CRS_MOLLWEIDE)
    adm2 = gpd.read_file(gb / "ADM2--geoBoundaries-TUR-ADM2.geojson").to_crs(common.CRS_MOLLWEIDE)
    ank_geom = ank.geometry.iloc[0]
    adm2 = adm2[adm2.representative_point().within(ank_geom)][["shapeName", "geometry"]]
    assert len(adm2) == 25, f"Ankara ilçe sayısı 25 değil: {len(adm2)}"
    rows, cols = np.nonzero(arr > 0)
    xs = tr.c + (cols + 0.5) * tr.a
    ys = tr.f + (rows + 0.5) * tr.e
    pts = gpd.GeoDataFrame({"pop": arr[rows, cols], "mx": xs, "my": ys},
                           geometry=points(xs, ys), crs=common.CRS_MOLLWEIDE)
    pts = pts[pts.within(ank_geom)]
    pts = gpd.sjoin(pts, adm2, how="left", predicate="within").drop(columns="index_right")
    pts = pts[~pts.index.duplicated(keep="first")]
    if pts["shapeName"].isna().any():  # sınır boşluğu: en yakın ilçe
        miss = pts["shapeName"].isna()
        near = gpd.sjoin_nearest(pts.loc[miss, ["geometry"]], adm2, how="left")
        pts.loc[miss, "shapeName"] = near.groupby(level=0)["shapeName"].first()
    pts = pts.rename(columns={"shapeName": "district"}).reset_index(drop=True)
    core = {common.norm_name(d) for d in CORE_DISTRICTS}
    pts["urban"] = pts["district"].map(common.norm_name).isin(core)
    t = Transformer.from_crs(common.CRS_MOLLWEIDE, common.CRS_UTM36N, always_xy=True)
    pts["ux"], pts["uy"] = t.transform(pts["mx"].to_numpy(), pts["my"].to_numpy())
    return pts


def sample_pairs(cells):
    rng = np.random.default_rng(SEED)
    pop = cells["pop"].to_numpy()
    ux, uy = cells["ux"].to_numpy(), cells["uy"].to_numpy()
    orig = rng.choice(len(cells), size=N_ORIGINS, replace=True, p=pop / pop.sum())
    recs = []
    for o in orig:
        dkm = np.hypot(ux - ux[o], uy - uy[o]) / 1000.0
        for bi, (lo, hi) in enumerate(BANDS):
            cand = np.flatnonzero((dkm >= lo) & (dkm < hi) & (dkm > 0))
            if cand.size == 0:
                continue
            w = pop[cand]
            s_b = w.sum()
            j = cand[rng.choice(cand.size, p=w / s_b)]
            recs.append((o, j, bi, s_b, dkm[j]))
    import pandas as pd
    return pd.DataFrame(recs, columns=["o", "d", "band", "w", "euclid_cell_km"])


# ---------------------------------------------------------------- istatistik
def band_label(bi):
    lo, hi = BANDS[bi]
    return f"{lo}–{int(hi)} km" if np.isfinite(hi) else f"{lo}+ km"


def summarize(df, group_type, group, label):
    r = df["ratio"].to_numpy()
    w = df["w"].to_numpy()
    return {
        "group_type": group_type, "group": group, "label_tr": label, "n_pairs": len(df),
        "mean": r.mean(), "median": np.median(r), "p10": np.percentile(r, 10), "p90": np.percentile(r, 90),
        "pw_mean": np.average(r, weights=w),
        "pw_ratio_of_sums": (w * df["net_km"]).sum() / (w * df["euclid_node_km"]).sum(),
        "pw_mean_door_to_door": np.average(df["ratio_d2d"], weights=w),
        "mean_euclid_km": df["euclid_node_km"].mean(), "mean_network_km": df["net_km"].mean(),
    }


def main() -> None:
    import pandas as pd
    from scipy.sparse.csgraph import dijkstra
    from scipy.spatial import cKDTree

    print("1) OSM PBF")
    pbf_url = ensure_pbf()
    hdr = pbf_header()
    print(f"  kaynak: {pbf_url}\n  çoğaltma zaman damgası: {hdr['osmosis_replication_timestamp']}")
    for k, doi in CROSSREF.items():  # kaynakça doğrulaması (Crossref üst verisi)
        ensure(f"https://api.crossref.org/works/{doi}", RAW_OSM / f"crossref_{k}.json", timeout=60)

    print("2) Yol ağı")
    node_id, lon, lat, way_start, way_dir, way_hw, kizilay = read_osm()
    uniq, nidx = np.unique(node_id, return_inverse=True)
    n = len(uniq)
    first = np.full(n, -1, dtype=np.int64)
    first[nidx[::-1]] = np.arange(len(nidx))[::-1]
    nlon, nlat = lon[first], lat[first]
    x, y = utm(nlon, nlat)
    x, y = np.asarray(x), np.asarray(y)
    u, v, L = build_edges(nidx, x, y, way_start, way_dir, np.ones(n, bool))  # tüm düğümler
    G = to_csr(u, v, L, n)
    in_scc = largest_scc(G)
    print(f"  tam graf: {n:,} düğüm; en büyük güçlü bileşen {in_scc.sum():,} düğüm ({in_scc.mean():.1%})")

    print("3) Nüfus hücreleri ve örneklem")
    arr, tr = ghs_pop_window()
    cells = populated_cells(arr, tr)
    print(f"  Ankara içi nüfuslu 1 km hücre: {len(cells):,}; GHS-POP 2025 toplamı {cells['pop'].sum():,.0f}")
    hw_sorted = sorted(DRIVABLE)
    snap_way = ~np.isin(way_hw, [hw_sorted.index(h) for h in NO_SNAP])
    way_of = np.repeat(np.arange(len(way_start)), np.diff(np.r_[way_start, len(nidx)]))
    snappable = np.zeros(n, bool)
    snappable[nidx[snap_way[way_of]]] = True
    scc_ids = np.flatnonzero(in_scc & snappable)
    print(f"  bağlanabilir düğüm (otoyol/ana arter hariç, güçlü bileşende): {len(scc_ids):,}")
    tree = cKDTree(np.c_[x[scc_ids], y[scc_ids]])
    snap_d, snap_i = tree.query(np.c_[cells["ux"], cells["uy"]])
    cells["node"] = scc_ids[snap_i]
    cells["snap_m"] = snap_d
    far = cells["snap_m"] > MAX_SNAP_M
    print(f"  >1 km bağlanamayan hücre: {far.sum():,} ({far.mean():.1%}); "
          f"nüfus payı {cells.loc[far, 'pop'].sum() / cells['pop'].sum():.2%}")
    pairs = sample_pairs(cells)
    n_drawn = len(pairs)

    # bağlanan düğümleri koruyarak daralt
    keep = np.zeros(n, bool)
    keep[cells["node"].to_numpy()] = True
    deg_ref = np.bincount(nidx, minlength=n) >= 2  # birden fazla yol referansı = kavşak
    u, v, L = build_edges(nidx, x, y, way_start, way_dir, keep | deg_ref)
    used = np.unique(np.r_[u, v])
    remap = np.full(n, -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    Gc = to_csr(remap[u], remap[v], L, len(used))
    scc_c = largest_scc(Gc)
    print(f"  daraltılmış graf: {len(used):,} düğüm, {Gc.nnz:,} kenar; güçlü bileşen {scc_c.sum():,}")

    P = pairs.merge(cells[["node", "snap_m", "ux", "uy", "urban", "district"]].add_prefix("o_"),
                    left_on="o", right_index=True)
    P = P.merge(cells[["node", "snap_m", "ux", "uy", "urban", "district"]].add_prefix("d_"),
                left_on="d", right_index=True)
    P = P[(P["o_snap_m"] <= MAX_SNAP_M) & (P["d_snap_m"] <= MAX_SNAP_M)].copy()
    P["o_c"], P["d_c"] = remap[P["o_node"]], remap[P["d_node"]]
    assert (P["o_c"] >= 0).all() and scc_c[P["o_c"]].all() and scc_c[P["d_c"]].all()
    P["euclid_node_km"] = np.hypot(x[P["o_node"]] - x[P["d_node"]], y[P["o_node"]] - y[P["d_node"]]) / 1000
    P = P[P["euclid_node_km"] * 1000 >= MIN_EUCLID_M].copy()

    print(f"4) Dijkstra: {P['o_c'].nunique()} köken düğümü, {len(P):,} çift")
    t0 = time.time()
    net = np.empty(len(P))
    src = P["o_c"].to_numpy()
    dst = P["d_c"].to_numpy()
    uo = np.unique(src)
    for i in range(0, len(uo), 32):
        batch = uo[i:i + 32]
        D = dijkstra(Gc, directed=True, indices=batch)
        pos = {s: k for k, s in enumerate(batch)}
        sel = np.flatnonzero(np.isin(src, batch))
        net[sel] = D[[pos[s] for s in src[sel]], dst[sel]]
    print(f"  {time.time() - t0:.0f} sn")
    assert np.isfinite(net).all()
    P["net_km"] = net / 1000
    P["ratio"] = P["net_km"] / P["euclid_node_km"]
    P["ratio_d2d"] = (P["net_km"] + (P["o_snap_m"] + P["d_snap_m"]) / 1000) / P["euclid_cell_km"]
    P["band_label"] = P["band"].map(band_label)
    P["pair_type"] = np.where(P["o_urban"] & P["d_urban"], "kentsel-kentsel",
                              np.where(~P["o_urban"] & ~P["d_urban"], "kırsal-kırsal", "karma"))
    P.to_csv(PAIRS_CSV, index=False, float_format="%.4f")
    print(f"  çift: çekilen {n_drawn:,}, geçerli {len(P):,} ({len(P) / n_drawn:.1%})")

    # özet tablo
    rows = [summarize(P, "all", "all", "Tüm çiftler (bantlar eşit sayıda örneklendi)")]
    hosp = P[P["band"].isin([1, 2, 3])]
    rows.append(summarize(hosp, "band_group", "5-40", "5–40 km (hastane yolculuğu bandı)"))
    for bi in range(len(BANDS)):
        sub = P[P["band"] == bi]
        if len(sub):
            rows.append(summarize(sub, "band", band_label(bi).replace("–", "-").replace(" km", ""),
                                  f"Kuş uçuşu {band_label(bi)}"))
    for flag, nm in ((True, "kentsel"), (False, "kırsal")):
        sub = P[P["o_urban"] == flag]
        rows.append(summarize(sub, "origin", nm, f"Köken {nm} ({'9 merkez ilçe' if flag else 'diğer 16 ilçe'})"))
        sub2 = hosp[hosp["o_urban"] == flag]
        rows.append(summarize(sub2, "origin_5-40", nm, f"Köken {nm}, 5–40 km"))
        for bi in range(len(BANDS)):
            s3 = sub[sub["band"] == bi]
            if len(s3) >= 10:
                rows.append(summarize(s3, f"origin_band_{nm}", band_label(bi).replace("–", "-").replace(" km", ""),
                                      f"Köken {nm}, {band_label(bi)}"))
    for pt in ("kentsel-kentsel", "karma", "kırsal-kırsal"):
        sub = P[P["pair_type"] == pt]
        if len(sub):
            rows.append(summarize(sub, "pair_type", pt, f"Çift türü {pt}"))
    tab = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        f.write(f"# Ankara dolambaç katsayısı (ağ / kuş uçuşu), OSM {hdr['osmosis_replication_timestamp']}, "
                f"GHS-POP 2025 1 km; tohum {SEED}; üretim pipeline/15_circuity.py, {common.today()}\n")
        f.write("# pw_* = nüfus ağırlıklı (w = kökenin o banttaki nüfusu); ratio = ağ mesafesi / bağlanan "
                "düğümler arası kuş uçuşu; door_to_door = (bağlantılar + ağ) / hücre merkezleri arası\n")
        tab.to_csv(f, index=False, float_format="%.4f")
    with pd.option_context("display.width", 220, "display.max_columns", 20):
        print(tab.drop(columns="label_tr").round(3).to_string(index=False))

    plot(P, tab, hdr)
    write_param(tab, P, hdr, pbf_url, n_drawn, in_scc, cells, far)
    write_kizilay(kizilay, hdr)


# ---------------------------------------------------------------- çıktılar
def plot(P, tab, hdr):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker  # noqa: F401
    surf, ink, ink2, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
    c_urb, c_rur = "#2a78d6", "#eb6834"
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150, facecolor=surf)
    ax.set_facecolor(surf)
    for flag, col, nm in ((True, c_urb, "Köken kentsel (9 merkez ilçe)"), (False, c_rur, "Köken kırsal (16 ilçe)")):
        s = P[P["o_urban"] == flag]
        ax.scatter(s["euclid_node_km"], s["ratio"], s=9, color=col, alpha=0.45, linewidths=0, label=nm)
    bands = tab[tab["group_type"] == "band"].reset_index(drop=True)
    for bi, r in bands.iterrows():
        lo, hi = BANDS[bi]
        hi = min(hi, P["euclid_node_km"].max())
        ax.hlines(r["pw_mean"], max(lo, 0.5), hi, color=ink, lw=2)
        ax.text(np.sqrt(max(lo, 0.5) * hi), r["pw_mean"] + 0.03, common.fmt_tr(r["pw_mean"], 2),
                ha="center", va="bottom", fontsize=9, color=ink,
                bbox=dict(facecolor=surf, edgecolor="none", pad=1.0, alpha=0.85))
    ax.axhline(1.3, color=ink2, lw=1, ls=(0, (4, 3)))
    ax.text(0.55, 1.3 - 0.03, "Şartname varsayılanı 1,3", fontsize=8, color=ink2, va="top",
            bbox=dict(facecolor=surf, edgecolor="none", pad=1.0, alpha=0.85))
    ax.set_xscale("log")
    ax.set_xlim(0.5, P["euclid_node_km"].max() * 1.05)
    ax.set_ylim(0.95, min(3.0, P["ratio"].quantile(0.995) + 0.1))
    ax.set_xticks([1, 2, 5, 10, 20, 40, 80, 160])
    ax.set_xticklabels(["1", "2", "5", "10", "20", "40", "80", "160"])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: common.fmt_tr(v, 1)))
    ax.set_xlabel("Kuş uçuşu mesafe (km, log ölçek)", color=ink2)
    ax.set_ylabel("Dolambaç katsayısı = yol ağı / kuş uçuşu", color=ink2)
    ax.grid(True, color=grid, lw=0.6)
    ax.tick_params(colors=ink2)
    for sp in ax.spines.values():
        sp.set_color(grid)
    pw = tab.loc[tab["group"] == "5-40", "pw_mean"].iloc[0]
    ax.set_title(f"Ankara yol ağı dolambaç katsayısı — {common.fmt_tr(len(P))} çift; "
                 f"5–40 km nüfus ağırlıklı ortalama {common.fmt_tr(pw, 2)}",
                 color=ink, fontsize=11, loc="left")
    ax.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=ink2,
              title="Siyah çizgi: bant içi nüfus ağırlıklı ortalama", title_fontsize=8)
    fig.text(0.01, 0.01, f"Kaynak: OpenStreetMap katkıcıları (ODbL), {hdr['osmosis_replication_timestamp'][:10]}; "
             "GHS-POP R2023A 2025 1 km (JRC, CC BY 4.0). Hesap: pipeline/15_circuity.py",
             fontsize=7, color=ink2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PNG, facecolor=surf)
    plt.close(fig)


def _f(x, d=3):
    return float(round(float(x), d))


def write_param(tab, P, hdr, pbf_url, n_drawn, in_scc, cells, far):
    import yaml
    g = tab.set_index(["group_type", "group"])
    h = g.loc[("band_group", "5-40")]
    alts = [{"label": "Şartname varsayılanı (Bölüm 4.3/4.4; kaynaksız)", "value": 1.3, "status": "varsayim",
             "source_ids": []}]
    for key, lab in ((("all", "all"), "OSM ölçümü, tüm çiftler (nüfus ağırlıklı)"),
                     (("origin_5-40", "kentsel"), "OSM ölçümü, 5–40 km, köken kentsel (9 merkez ilçe)"),
                     (("origin_5-40", "kırsal"), "OSM ölçümü, 5–40 km, köken kırsal (16 ilçe)")):
        alts.append({"label": lab, "value": _f(g.loc[key, "pw_mean"]), "status": "hesap",
                     "source_ids": [f"{SRC_PREFIX}circuity_hesap"]})
    for bi in range(len(BANDS)):
        k = ("band", band_label(bi).replace("–", "-").replace(" km", ""))
        if k in g.index:
            alts.append({"label": f"OSM ölçümü, {band_label(bi)} (nüfus ağırlıklı)",
                         "value": _f(g.loc[k, "pw_mean"]), "status": "hesap",
                         "source_ids": [f"{SRC_PREFIX}circuity_hesap"]})
    band_max = max(g.loc[k, "pw_mean"] for k in g.index if k[0] == "band")
    hi = float(np.ceil((band_max + 0.05) * 10) / 10)
    param = [{
        "key": "access.circuity",
        "label": "Dolambaç katsayısı (yol ağı mesafesi / kuş uçuşu mesafe)",
        "value": _f(h["pw_mean"]),
        "unit": "oran (boyutsuz)",
        "status": "hesap",
        "source_ids": [f"{SRC_PREFIX}circuity_hesap", f"{SRC_PREFIX}osm_turkey_pbf",
                       f"{SRC_PREFIX}ghs_pop_e2025_1km", f"{SRC_PREFIX}geoboundaries_tur"],
        "derivation": (
            f"OSM ({hdr['osmosis_replication_timestamp']}) araç yolu ağında {common.fmt_tr(len(P))} köken–hedef çifti; "
            "köken ∝ GHS-POP 2025 nüfusu, her mesafe bandından bir hedef ∝ nüfus; ağ mesafesi (Dijkstra, yönlü, "
            "tek yön kuralları) / bağlanan düğümler arası kuş uçuşu (EPSG:32636); hücreler en yakın "
            "motorway/trunk dışı yol düğümüne bağlandı. Değer = kuş uçuşu 5–40 km "
            f"çiftlerinin nüfus ağırlıklı ortalaması (n = {common.fmt_tr(h['n_pairs'])}, w = kökenin bant içi "
            f"nüfusu). Aynı bantta ortanca {common.fmt_tr(h['median'], 3)}, ağırlıksız ortalama "
            f"{common.fmt_tr(h['mean'], 3)}, nüfus ağırlıklı oranlar toplamı Σw·ağ/Σw·kuş = "
            f"{common.fmt_tr(h['pw_ratio_of_sums'], 3)}, kapıdan kapıya (bağlantı mesafeleri dahil, hücre "
            f"merkezine göre) {common.fmt_tr(h['pw_mean_door_to_door'], 3)}."
        ),
        "rationale": (
            "Hastane yolculuklarının çoğu hücreden en yakın hastaneye 5–40 km aralığında (R varsayılanı 30 km); "
            "çok kısa (<5 km) çiftlerde katsayı kent içi blok yapısı nedeniyle yüksek, uzun çiftlerde düşük. "
            "Model d_ij = kuş uçuşu × k kullandığı için bu banttaki nüfus ağırlıklı ortalama seçildi."
        ),
        "range": [1.0, hi],
        "alternatives": alts,
        "notes": (
            "Aralık: 1,0 = kuş uçuşu (fiziksel alt sınır); üst sınır = ölçülen en yüksek bant ortalaması + pay. "
            f"Tablo: data/reference/circuity_ankara.csv; grafik: data/processed/circuity_ankara.png. "
            f"Çekilen {common.fmt_tr(n_drawn)} çiftten {common.fmt_tr(len(P))} geçerli (bağlantı > {int(MAX_SNAP_M)} m veya düğümler arası "
            f"< {int(MIN_EUCLID_M)} m olanlar atıldı). Nüfuslu hücrelerin %{common.fmt_tr(100 * far.mean(), 1)}'i "
            f"(nüfusun %{common.fmt_tr(100 * cells.loc[far, 'pop'].sum() / cells['pop'].sum(), 2)}'i) 1 km içinde araç yoluna bağlanamadı. "
            "Literatür: Boscoe, Henry & Zdeb 2012 (alan_literatur_lit_boscoe2012) ve Ballou, Rahardja & Sakai 2002 "
            "(alan_literatur_lit_ballou2002) künyeleri Crossref'ten doğrulandı; "
            "tam metinlere erişilemediği (vekil sunucu 403) için katsayı değerleri kaydedilmedi."
        ),
    }]
    OUT_PARAM.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PARAM, "w", encoding="utf-8") as f:
        f.write("# Alan: arazi-osm — dolambaç katsayısı. Üreten: pipeline/15_circuity.py (elle düzenlemeyin)\n")
        yaml.safe_dump(param, f, allow_unicode=True, sort_keys=False, width=110)


def write_kizilay(kizilay, hdr):
    import yaml
    cand = [k for k in kizilay if "meydan" in k["name"].lower()]
    assert len(cand) == 1, f"Kızılay Meydanı için tek aday beklenirdi: {cand}"
    k = cand[0]
    x, y = utm(k["lon"], k["lat"])
    rec = {
        "name": "Kızılay Meydanı",
        "osm_name": k["name"],
        "lon": round(k["lon"], 7),
        "lat": round(k["lat"], 7),
        "x_utm36n": round(float(x), 1),
        "y_utm36n": round(float(y), 1),
        "osm_type": k["osm_type"],
        "osm_id": k["osm_id"],
        "osm_tags": {t: k["tags"][t] for t in ("place", "wikidata") if t in k["tags"]},
        "source_id": f"{SRC_PREFIX}osm_turkey_pbf",
        "osm_snapshot": hdr["osmosis_replication_timestamp"],
        "notes": "OSM'de place=square düğümü; 'Kızılay'dan en uzak nokta' hesabının referansı. "
                 "https://www.openstreetmap.org/node/" + str(k["osm_id"]),
    }
    with open(OUT_KIZILAY, "w", encoding="utf-8") as f:
        f.write("# Kızılay referans noktası. Üreten: pipeline/15_circuity.py\n")
        yaml.safe_dump(rec, f, allow_unicode=True, sort_keys=False)
    print(f"  Kızılay: {rec['lon']}, {rec['lat']} (node/{rec['osm_id']})")


if __name__ == "__main__":
    main()
