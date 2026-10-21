"""Faz 1 — idari sınırlar: Ankara il sınırı + 25 ilçe.

Ana küme: geoBoundaries gbOpen TUR ADM1 (shapeISO TR-06) + ADM2 (ilçeler il poligonuna mekânsal olarak bağlanır;
öznitelikte il bağı yok, 'Gölbaşı' Adıyaman'da da var).
Karşılaştırma: Kontur Boundaries TR 2023-06-28 (osm_admin_level 4/6) ve HDX COD-AB Türkiye (HGK kaynaklı).

Çıktılar:
  data/processed/ankara_il.geojson, data/processed/ankara_ilceler.geojson  (tam çözünürlük, WGS84)
  data/processed/web/ankara_il.geojson, data/processed/web/ankara_ilceler.geojson  (sadeleştirilmiş, 5 ondalık,
      ortak sınırlar tek yay olarak sadeleştirildiği için komşu ilçeler arasında boşluk/bindirme yok)
  data/processed/sinir_karsilastirma.csv  (ilçe bazında alan ve geoBoundaries'e göre IoU)
  data/raw/sinirlar/sinir_ozet.json  (doğrulama ve karşılaştırma özeti)

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/11_boundaries.py
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

RAW_S = common.RAW / "sinirlar"
GB_DIR = RAW_S / "geoboundaries"
KONTUR_DIR = RAW_S / "kontur"
HDX_DIR = RAW_S / "hdx"
CODAB_DIR = RAW_S / "hdx_codab"
OUT_IL = common.PROCESSED / "ankara_il.geojson"
OUT_ILCE = common.PROCESSED / "ankara_ilceler.geojson"
WEB_DIR = common.PROCESSED / "web"
OUT_CMP = common.PROCESSED / "sinir_karsilastirma.csv"
SUMMARY_JSON = RAW_S / "sinir_ozet.json"

GB_RAW = "https://raw.githubusercontent.com/wmgeolab/geoBoundaries/main/releaseData/gbOpen/TUR/{lvl}/{name}"
GB_MEDIA = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/TUR/{lvl}/{name}"
GB_FILES = ["geoBoundaries-TUR-{lvl}.geojson", "geoBoundaries-TUR-{lvl}_simplified.geojson",
            "geoBoundaries-TUR-{lvl}-metaData.json", "geoBoundaries-TUR-{lvl}-metaData.txt",
            "CITATION-AND-USE-geoBoundaries.txt"]

KONTUR_S3 = "https://geodata-eu-central-1-kontur-public.s3.amazonaws.com/kontur_datasets/{}"
KONTUR_BND = "kontur_boundaries_TR_20230628.gpkg"
HDX_API = "https://data.humdata.org/api/3/action/package_show?id={}"
HDX_PACKAGES = {  # yerel ad -> HDX paket kimliği
    "hdx_kontur_boundaries_tur.json": "ee13e01d-edee-47d2-a5fc-1a658227d879",
    "hdx_kontur_population_tur.json": "a241c6ba-5a15-4f02-aae3-74212859ad85",
    "hdx_cod_ab_tur.json": "cod-ab-tur",
}
CODAB_URL = ("https://data.humdata.org/dataset/d74086a0-f398-4474-9e12-1b9a70907bd0/resource/"
             "470bd810-2240-4ce0-b5c4-17434112ce41/download/tur_admin_boundaries.geojson.zip")
CODAB_ZIP = CODAB_DIR / "tur_admin_boundaries.geojson.zip"

ANKARA_ISO = "TR-06"
N_DISTRICTS = 25
# Eski/kısa adlar -> TÜİK'teki güncel ad anahtarı (Kazan ilçesinin adı 2016'da Kahramankazan oldu)
NAME_ALIASES = {"kazan": "kahramankazan"}
WEB_TOL_DEG = 0.0004   # web sadeleştirme toleransı (≈35–45 m)
WEB_GRID = 1e-5        # 5 ondalık basamak


# ---------------------------------------------------------------- indirme yardımcıları
def _manifest_record(dest: Path, url: str) -> dict | None:
    man = dest.parent / "_manifest.jsonl"
    if not (dest.exists() and man.exists()):
        return None
    rel = str(dest.relative_to(common.ROOT))
    for line in man.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("url") == url and rec.get("file") == rel:
            return rec
    return None


def ensure(url: str, dest: Path, *, timeout: int = 300, overwrite: bool = False) -> dict:
    """Dosya yoksa indirir; manifestte (url, dosya) kaydı varsa tekrar yazmaz."""
    rec = None if overwrite else _manifest_record(dest, url)
    if rec is None:
        rec = common.download(url, dest, timeout=timeout, overwrite=overwrite)
        print(f"  kayıt: {rec['file']} ({rec['bytes']:,} B)")
    return rec


def _parse_lfs_pointer(path: Path) -> tuple[str, int]:
    oid, size = None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("oid sha256:"):
            oid = line.split(":", 1)[1].strip()
        elif line.startswith("size "):
            size = int(line.split()[1])
    if not oid or size is None:
        raise ValueError(f"LFS işaretçisi okunamadı: {path}")
    return oid, size


def fetch_geoboundaries() -> list[dict]:
    """geoBoundaries dosyalarını git-LFS işaretçisindeki sha256 ile doğrulayarak kaydeder."""
    out = []
    for lvl in ("ADM1", "ADM2"):
        for tpl in GB_FILES:
            name = tpl.format(lvl=lvl)
            ptr = GB_DIR / "lfs" / f"{lvl}--{name}.lfs"
            ensure(GB_RAW.format(lvl=lvl, name=name), ptr, timeout=60)
            oid, size = _parse_lfs_pointer(ptr)
            dest = GB_DIR / f"{lvl}--{name}"
            if dest.exists() and common.sha256(dest) != oid:
                print(f"  UYARI: {dest.name} LFS özetiyle uyuşmuyor, yeniden indiriliyor")
                rec = ensure(GB_MEDIA.format(lvl=lvl, name=name), dest, overwrite=True)
            else:
                rec = ensure(GB_MEDIA.format(lvl=lvl, name=name), dest)
            ok = rec["sha256"] == oid and rec["bytes"] == size
            if not ok:
                raise RuntimeError(f"{dest} sha256/boyut LFS işaretçisiyle uyuşmuyor")
            out.append({"file": rec["file"], "url": rec["url"], "sha256": rec["sha256"],
                        "lfs_oid_match": ok})
    return out


def fetch_kontur_boundaries() -> dict:
    """Kontur sınırlarının .gz arşivini indirir; açılmış gpkg'yi (Faz 0 kopyası) özetle doğrular."""
    gz = KONTUR_DIR / (KONTUR_BND + ".gz")
    rec = ensure(KONTUR_S3.format(KONTUR_BND + ".gz"), gz)
    return {"gz": rec["file"], "gz_sha256": rec["sha256"], **_check_gunzip(gz, KONTUR_DIR / KONTUR_BND)}


def _check_gunzip(gz: Path, out: Path) -> dict:
    """gz içeriğinin sha256'sını akışta hesaplar; açılmış dosya yoksa yazar, varsa karşılaştırır."""
    import hashlib
    h = hashlib.sha256()
    tmp = out.with_suffix(out.suffix + ".part")
    write = not out.exists()
    with gzip.open(gz, "rb") as fi:
        fo = open(tmp, "wb") if write else None
        for block in iter(lambda: fi.read(1 << 20), b""):
            h.update(block)
            if fo:
                fo.write(block)
        if fo:
            fo.close()
            tmp.replace(out)
    h_out = common.sha256(out)
    return {"file": str(out.relative_to(common.ROOT)), "sha256": h_out,
            "matches_gz_content": h_out == h.hexdigest()}


def fetch_hdx() -> dict:
    recs = {}
    for fname, pid in HDX_PACKAGES.items():
        recs[fname] = ensure(HDX_API.format(pid), HDX_DIR / fname, timeout=60)
    recs["codab"] = ensure(CODAB_URL, CODAB_ZIP)
    return recs


# ---------------------------------------------------------------- geometri yardımcıları
def _key(name: str) -> str:
    k = common.norm_name(name)
    return NAME_ALIASES.get(k, k)


def _ea(gdf):
    return gdf.to_crs(common.CRS_EQUAL_AREA)


def _area_km2(geom_ea) -> float:
    return float(geom_ea.area) / 1e6


def _iou(a, b) -> float:
    inter = a.intersection(b).area
    union = a.union(b).area
    return float(inter / union) if union > 0 else float("nan")


def select_districts(adm2, province_geom_ea, label: str):
    """İl poligonuyla alanının yarısından fazlası kesişen ADM2 birimlerini seçer."""
    a2 = _ea(adm2)
    cand = a2[a2.intersects(province_geom_ea)].copy()
    frac = cand.geometry.intersection(province_geom_ea).area / cand.geometry.area
    sel = cand[frac > 0.5].copy()
    border = cand[(frac > 0.01) & (frac <= 0.5)]
    if len(border):
        print(f"  {label}: sınırda kısmen kesişen {len(border)} birim dışarıda bırakıldı")
    sel["inside_frac"] = frac[frac > 0.5]
    return sel


def load_geoboundaries():
    import geopandas as gpd
    a1 = gpd.read_file(GB_DIR / "ADM1--geoBoundaries-TUR-ADM1.geojson")
    a2 = gpd.read_file(GB_DIR / "ADM2--geoBoundaries-TUR-ADM2.geojson")
    il = a1[a1["shapeISO"] == ANKARA_ISO]
    assert len(il) == 1, "geoBoundaries ADM1'de TR-06 bulunamadı"
    il_ea = _ea(il).geometry.iloc[0]
    d = select_districts(a2, il_ea, "geoBoundaries")
    d["ilce"] = d["shapeName"]
    return il, d


def load_kontur():
    import geopandas as gpd
    k = gpd.read_file(KONTUR_DIR / KONTUR_BND)
    il = k[(k["osm_admin_level"] == "4") & (k["name"] == "Ankara")]
    assert len(il) == 1, "Kontur'da Ankara (osm_admin_level 4) bulunamadı"
    il_ea = _ea(il).geometry.iloc[0]
    d = select_districts(k[k["osm_admin_level"] == "6"], il_ea, "Kontur")
    d["ilce"] = d["name"]
    return il, d


def load_codab():
    """COD-AB: il/ilçe bağı öznitelikte var (adm1_pcode); yine de il poligonuyla mekânsal olarak sağlanır."""
    import geopandas as gpd
    a1 = gpd.read_file(f"/vsizip/{CODAB_ZIP}/tur_admin1.geojson")
    a2 = gpd.read_file(f"/vsizip/{CODAB_ZIP}/tur_admin2.geojson")
    il = a1[a1["adm1_name"] == "Ankara"]
    assert len(il) == 1, "COD-AB'de Ankara bulunamadı"
    pcode = il["adm1_pcode"].iloc[0]
    d = _ea(a2[a2["adm1_pcode"] == pcode]).copy()
    sp = select_districts(a2, _ea(il).geometry.iloc[0], "COD-AB")
    assert set(sp["adm2_pcode"]) == set(d["adm2_pcode"]), "COD-AB pcode ile mekânsal seçim farklı"
    d["ilce"] = d["adm2_name1"]   # Türkçe ad (kaynakta i/ı karakterleri bozuk; anahtar norm_name ile)
    return il, d


# ---------------------------------------------------------------- karşılaştırma
def _match(ref, other, label: str) -> dict:
    """Her referans ilçeye en çok örtüşen ilçeyi eşler; ad anahtarı uyuşmazlığını bildirir."""
    out = {}
    for _, r in ref.iterrows():
        inter = other.geometry.intersection(r.geometry).area
        j = int(np.argmax(inter.values))
        o = other.iloc[j]
        if _key(o["ilce"]) != r["ilce_key"]:
            print(f"  UYARI {label}: {r['ilce']} en çok '{o['ilce']}' ile örtüşüyor (ad uyuşmuyor)")
        out[r["ilce_key"]] = {"name": o["ilce"], "key": _key(o["ilce"]),
                              "area_km2": _area_km2(o.geometry), "iou": _iou(r.geometry, o.geometry)}
    return out


def compare(gb_il, gb_d, k_il, k_d, c_il, c_d) -> tuple[pd.DataFrame, dict]:
    ref = gb_d.copy()
    ref["ilce_key"] = ref["ilce"].map(_key)
    km = _match(ref, k_d, "Kontur")
    cm = _match(ref, c_d, "COD-AB")
    c_attr = dict(zip(c_d["ilce"].map(_key), c_d["area_sqkm"]))
    rows = []
    for _, r in ref.sort_values("ilce_key").iterrows():
        k, c = km[r["ilce_key"]], cm[r["ilce_key"]]
        rows.append({
            "ilce": r["ilce"], "ilce_key": r["ilce_key"],
            "alan_km2_geoboundaries": _area_km2(r.geometry),
            "alan_km2_kontur": k["area_km2"], "iou_kontur": k["iou"], "ad_kontur": k["name"],
            "alan_km2_codab": c["area_km2"], "iou_codab": c["iou"], "ad_codab": c["name"],
            "alan_km2_codab_ozniteligi": c_attr.get(r["ilce_key"], np.nan),
        })
    g, k, c = (_ea(x).geometry.iloc[0] for x in (gb_il, k_il, c_il))
    rows.append({
        "ilce": "ANKARA (il poligonu)", "ilce_key": "ankara",
        "alan_km2_geoboundaries": _area_km2(g),
        "alan_km2_kontur": _area_km2(k), "iou_kontur": _iou(g, k), "ad_kontur": "Ankara",
        "alan_km2_codab": _area_km2(c), "iou_codab": _iou(g, c), "ad_codab": "Ankara",
        "alan_km2_codab_ozniteligi": float(c_il["area_sqkm"].iloc[0]),
    })
    df = pd.DataFrame(rows)
    dist = df.iloc[:-1]
    stats = {
        "kontur_iou_min": float(dist["iou_kontur"].min()), "kontur_iou_median": float(dist["iou_kontur"].median()),
        "codab_iou_min": float(dist["iou_codab"].min()), "codab_iou_median": float(dist["iou_codab"].median()),
        "kontur_iou_min_ilce": dist.loc[dist["iou_kontur"].idxmin(), "ilce"],
        "codab_iou_min_ilce": dist.loc[dist["iou_codab"].idxmin(), "ilce"],
        "il_iou_kontur": float(df.iloc[-1]["iou_kontur"]), "il_iou_codab": float(df.iloc[-1]["iou_codab"]),
    }
    return df, stats


def boundary_deviation(ref_ea, other_ea, n: int = 4000) -> dict:
    """other sınırından örneklenen noktaların ref sınırına uzaklığı (m) ve çevre uzunlukları (km)."""
    import shapely
    rb, ob = ref_ea.boundary, other_ea.boundary
    pts = shapely.line_interpolate_point(ob, np.linspace(0, 1, n, endpoint=False), normalized=True)
    d = shapely.distance(rb, pts)
    return {"median_m": float(np.median(d)), "p90_m": float(np.percentile(d, 90)),
            "max_m": float(d.max()), "perimeter_km_ref": rb.length / 1000, "perimeter_km_other": ob.length / 1000}


def _vertex_count(geoms) -> int:
    import shapely
    return int(sum(shapely.get_num_coordinates(g) for g in geoms))


def _consistency(il_ea, d_ea) -> dict:
    """İl poligonu ile ilçelerin birleşimi arasındaki boşluk/bindirme (km²)."""
    from itertools import combinations
    u = d_ea.geometry.union_all()
    overlap = 0.0
    geoms = list(d_ea.geometry)
    for a, b in combinations(geoms, 2):
        if a.intersects(b):
            overlap += a.intersection(b).area
    return {"il_minus_ilceler_km2": il_ea.difference(u).area / 1e6,
            "ilceler_minus_il_km2": u.difference(il_ea).area / 1e6,
            "ilce_ciftleri_bindirme_km2": overlap / 1e6,
            "iou_il_vs_ilce_birlesimi": _iou(il_ea, u)}


# ---------------------------------------------------------------- web için sadeleştirme
def simplify_shared(gdf, tol: float, grid: float):
    """Ortak sınırları tek yay olarak sadeleştirir (topolojiyi korur), sonra poligonları yeniden kurar.

    gdf: WGS84, 'ilce' sütunlu, aralarında boşluk/bindirme olmayan poligonlar.
    """
    import geopandas as gpd
    import shapely
    noded = shapely.union_all([g.boundary for g in gdf.geometry])
    arcs = shapely.line_merge(noded)
    arcs = list(arcs.geoms) if hasattr(arcs, "geoms") else [arcs]
    simp = [shapely.set_precision(shapely.simplify(a, tol, preserve_topology=True), grid) for a in arcs]
    faces = list(shapely.polygonize(simp).geoms)
    owner = []
    for f in faces:
        p = f.point_on_surface()
        hit = [i for i, g in enumerate(gdf.geometry) if g.contains(p)]
        owner.append(hit[0] if hit else -1)
    out_geoms = []
    for i in range(len(gdf)):
        parts = [f for f, o in zip(faces, owner) if o == i]
        g = shapely.union_all(parts)
        g = shapely.set_precision(g, grid)
        out_geoms.append(g)
    out = gpd.GeoDataFrame(gdf.drop(columns="geometry").reset_index(drop=True),
                           geometry=out_geoms, crs=gdf.crs)
    info = {"arcs": len(arcs), "faces": len(faces), "unassigned_faces": int(sum(o < 0 for o in owner)),
            "vertices_before": _vertex_count(gdf.geometry), "vertices_after": _vertex_count(out.geometry)}
    return out, info


def _round_coords(obj, nd: int = 5):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(v), nd) for v in obj]
        return [_round_coords(o, nd) for o in obj]
    return obj


def _orient_ccw(g):
    """RFC 7946 yönü: dış halka saat yönünün tersine, delikler saat yönünde."""
    from shapely.geometry import MultiPolygon, polygon
    if g.geom_type == "Polygon":
        return polygon.orient(g, 1.0)
    if g.geom_type == "MultiPolygon":
        return MultiPolygon([polygon.orient(p, 1.0) for p in g.geoms])
    return g


def write_geojson_compact(gdf, path: Path, nd: int = 5) -> int:
    """Küçük GeoJSON yazar: nd ondalık, boşluksuz, RFC 7946 halka yönü."""
    from shapely.geometry import mapping
    feats = []
    for _, r in gdf.iterrows():
        m = mapping(_orient_ccw(r.geometry))
        feats.append({"type": "Feature",
                      "properties": {k: (None if (isinstance(v, float) and np.isnan(v)) else v)
                                     for k, v in r.drop(labels="geometry").items()},
                      "geometry": {"type": m["type"], "coordinates": _round_coords(m["coordinates"], nd)}})
    fc = {"type": "FeatureCollection", "features": feats}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path.stat().st_size


def write_geojson_full(gdf, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    gdf.to_file(path, driver="GeoJSON", engine="pyogrio",
                layer_options={"RFC7946": "YES", "WRITE_BBOX": "NO", "COORDINATE_PRECISION": "7"})


# ---------------------------------------------------------------- ana akış
def main() -> None:
    import geopandas as gpd
    import shapely

    print("1) Kaynaklar")
    gb_recs = fetch_geoboundaries()
    kb = fetch_kontur_boundaries()
    hdx = fetch_hdx()
    assert all(r["lfs_oid_match"] for r in gb_recs)
    print("  geoBoundaries dosyaları LFS sha256 özetleriyle doğrulandı")
    print("  Kontur gpkg = gz içeriği:", kb["matches_gz_content"])

    print("2) Sınırlar")
    gb_il, gb_d = load_geoboundaries()
    k_il, k_d = load_kontur()
    c_il, c_d = load_codab()
    for lbl, d in (("geoBoundaries", gb_d), ("Kontur", k_d), ("COD-AB", c_d)):
        print(f"  {lbl}: {len(d)} ilçe")
        assert len(d) == N_DISTRICTS, f"{lbl}: {len(d)} ilçe (beklenen {N_DISTRICTS})"
    keys = {lbl: sorted(d["ilce"].map(_key)) for lbl, d in
            (("geoBoundaries", gb_d), ("Kontur", k_d), ("COD-AB", c_d))}
    names_agree = keys["geoBoundaries"] == keys["Kontur"] == keys["COD-AB"]
    print("  ilçe ad anahtarları üç kaynakta aynı:", names_agree)

    cons = {"geoboundaries": _consistency(_ea(gb_il).geometry.iloc[0], gb_d),
            "kontur": _consistency(_ea(k_il).geometry.iloc[0], k_d),
            "codab": _consistency(_ea(c_il).geometry.iloc[0], c_d)}
    for k_, v in cons.items():
        print(f"  {k_}: il−ilçeler {v['il_minus_ilceler_km2']:.4f} km², ilçeler−il {v['ilceler_minus_il_km2']:.4f} km², "
              f"bindirme {v['ilce_ciftleri_bindirme_km2']:.4f} km²")

    print("3) Karşılaştırma")
    df, stats = compare(gb_il, gb_d, k_il, k_d, c_il, c_d)
    gb_u, k_u, c_u = (x.to_crs(common.CRS_UTM36N).geometry.iloc[0] for x in (gb_il, k_il, c_il))
    stats["sinir_sapmasi_utm36n"] = {"kontur": boundary_deviation(gb_u, k_u), "codab": boundary_deviation(gb_u, c_u)}
    for k_, v in stats["sinir_sapmasi_utm36n"].items():
        print(f"  il sınırı sapması {k_}: medyan {v['median_m']:.0f} m, p90 {v['p90_m']:.0f} m; "
              f"çevre {v['perimeter_km_other']:.0f} km (geoBoundaries {v['perimeter_km_ref']:.0f} km)")
    out = df.copy()
    for c in out.columns:
        if c.startswith("alan_km2"):
            out[c] = out[c].round(3)
        if c.startswith("iou"):
            out[c] = out[c].round(5)
    OUT_CMP.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CMP, index=False, encoding="utf-8")
    print(out[["ilce", "alan_km2_geoboundaries", "alan_km2_kontur", "iou_kontur",
               "alan_km2_codab", "iou_codab"]].to_string(index=False))

    print("4) Çıktılar (ana küme: geoBoundaries gbOpen)")
    src = "geoBoundaries gbOpen TUR ADM2 (OSM, 2023-01-19; build 2023-12-12)"
    d = gb_d.copy()
    d["ilce_key"] = d["ilce"].map(_key)
    d["area_km2"] = (d.geometry.area / 1e6).round(3)
    d["source"] = src
    d = d.to_crs(common.CRS_WGS84).sort_values("ilce_key")
    d = gpd.GeoDataFrame(d[["ilce", "ilce_key", "area_km2", "source", "geometry"]].reset_index(drop=True),
                         crs=common.CRS_WGS84)
    il = gb_il.copy()
    il["il"] = "Ankara"
    il["il_key"] = "ankara"
    il["iso"] = ANKARA_ISO
    il["area_km2"] = round(_area_km2(_ea(gb_il).geometry.iloc[0]), 3)
    il["source"] = "geoBoundaries gbOpen TUR ADM1 (OSM, 2023-08-31; build 2023-12-12)"
    il = gpd.GeoDataFrame(il[["il", "il_key", "iso", "area_km2", "source", "geometry"]].reset_index(drop=True),
                          crs=common.CRS_WGS84)
    write_geojson_full(il, OUT_IL)
    write_geojson_full(d, OUT_ILCE)

    web_d, sinfo = simplify_shared(d, WEB_TOL_DEG, WEB_GRID)
    web_il = gpd.GeoDataFrame(il.drop(columns="geometry"),
                              geometry=[shapely.set_precision(web_d.geometry.union_all(), WEB_GRID)],
                              crs=common.CRS_WGS84)
    web_ea = _ea(web_d)
    full_ea = _ea(d)
    web_iou = [_iou(a, b) for a, b in zip(full_ea.geometry, web_ea.geometry)]
    web_cons = _consistency(_ea(web_il).geometry.iloc[0], web_ea)
    s1 = write_geojson_compact(web_il, WEB_DIR / "ankara_il.geojson")
    s2 = write_geojson_compact(web_d, WEB_DIR / "ankara_ilceler.geojson")
    print(f"  web: {s1 + s2:,} B (il {s1:,} + ilçeler {s2:,}); köşe {sinfo['vertices_before']:,} → "
          f"{sinfo['vertices_after']:,}; IoU min {min(web_iou):.4f}; geçerli: {bool(web_d.is_valid.all())}")
    assert s1 + s2 < 1_000_000, "web sınırları 1 MB'ı aşıyor"
    assert sinfo["unassigned_faces"] == 0 and web_d.is_valid.all()

    summary = {
        "generated": common.today(),
        "primary": "geoboundaries",
        "geoboundaries_files": gb_recs, "kontur_boundaries": kb,
        "hdx": {k_: {"file": v["file"], "sha256": v["sha256"]} for k_, v in hdx.items()},
        "district_counts": {"geoboundaries": len(gb_d), "kontur": len(k_d), "codab": len(c_d)},
        "names_agree": names_agree, "consistency_km2": cons, "comparison": stats,
        "kontur_ankara_population_attr": float(k_il["population"].iloc[0]),
        "province_area_km2_ea6933": {"geoboundaries": float(df.iloc[-1]["alan_km2_geoboundaries"]),
                                     "kontur": float(df.iloc[-1]["alan_km2_kontur"]),
                                     "codab": float(df.iloc[-1]["alan_km2_codab"]),
                                     "codab_attr_area_sqkm": float(df.iloc[-1]["alan_km2_codab_ozniteligi"])},
        "web": {**sinfo, "bytes_il": s1, "bytes_ilceler": s2, "tolerance_deg": WEB_TOL_DEG,
                "iou_min": float(min(web_iou)), "iou_median": float(np.median(web_iou)),
                "consistency_km2": web_cons},
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  özet:", SUMMARY_JSON.relative_to(common.ROOT))


if __name__ == "__main__":
    main()
