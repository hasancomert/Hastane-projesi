"""Faz 1 veri kontrolleri: TÜİK tablosu, sınırlar, kayıt doğrulaması, işlenmiş rasterler, kaynak tekilliği.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase1_data.py
Önkoşul: pipeline/build_registry.py ve pipeline/16_district_check.py çalıştırılmış olmalı.
"""
from __future__ import annotations

import json
import re
import warnings

import numpy as np
import pandas as pd
import pytest

from pipeline import build_registry, common

TUIK_CSV = common.REFERENCE / "tuik_adnks_ankara.csv"
PARAMS_JSON = common.REFERENCE / "parameters.json"
CHECK_CSV = common.PROCESSED / "ilce_kontrol_tablosu.csv"
BOUNDARIES = {
    "ilceler": common.PROCESSED / "ankara_ilceler.geojson",
    "il": common.PROCESSED / "ankara_il.geojson",
    "web_ilceler": common.PROCESSED / "web" / "ankara_ilceler.geojson",
    "web_il": common.PROCESSED / "web" / "ankara_il.geojson",
}
AREA_CSV = common.REFERENCE / "ankara_alan_kaynaklari.csv"
GHS_100M = (2920, 2390)   # (genişlik, yükseklik) — common.GHS_WINDOW, 100 m
GHS_1KM = (292, 239)


@pytest.fixture(scope="module")
def tuik() -> pd.DataFrame:
    return pd.read_csv(TUIK_CSV, dtype={"ilce_key": str})


@pytest.fixture(scope="module")
def params() -> dict:
    return json.loads(PARAMS_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def registry():
    # data/raw git dışı: sha256 denetimi build_registry.py çalıştırılırken yapılır
    return build_registry.validate(verify_sha=False)


# ---------------------------------------------------------------- TÜİK
def test_tuik_25_districts_sum_to_province(tuik, params):
    assert len(tuik) == 25
    assert tuik["ilce_key"].is_unique and tuik["ilce"].is_unique
    total = params["population.province_2025"]["value"]
    assert total == 5_910_320 == common.TUIK_PROVINCE_POP_2025
    assert int(tuik["nufus_2025"].sum()) == total


def test_tuik_cankaya_kecioren(tuik):
    v = tuik.set_index("ilce")["nufus_2025"]
    assert v["Çankaya"] == 952_198
    assert v["Keçiören"] == 931_722


def test_tuik_keys_are_normalized_names(tuik):
    assert all(common.norm_name(n) == k for n, k in zip(tuik["ilce"], tuik["ilce_key"]))


def test_tuik_older_years_sum_to_official_totals(tuik):
    # 2024 resmî (DF_ADNKS_T22), 2023 hesap; il toplamları TÜİK bülten tablolarıyla aynı olmalı
    assert int(tuik["nufus_2024"].sum()) == 5_864_049
    assert int(tuik["nufus_2023"].sum()) == 5_803_482


# ---------------------------------------------------------------- sınırlar
@pytest.mark.parametrize("name", list(BOUNDARIES))
def test_boundary_files_valid(name):
    import geopandas as gpd

    g = gpd.read_file(BOUNDARIES[name])
    assert g.crs is not None and g.crs.to_epsg() == 4326
    assert g.geometry.notna().all() and g.is_valid.all() and not g.is_empty.any()
    assert set(g.geom_type) <= {"Polygon", "MultiPolygon"}
    assert len(g) == (25 if name.endswith("ilceler") else 1)


@pytest.mark.parametrize("name", ["ilceler", "web_ilceler"])
def test_tuik_boundary_key_join_one_to_one(tuik, name):
    import geopandas as gpd

    g = gpd.read_file(BOUNDARIES[name])
    assert g["ilce_key"].is_unique
    assert set(g["ilce_key"]) == set(tuik["ilce_key"])
    m = tuik.merge(g[["ilce_key"]], on="ilce_key", how="inner", validate="one_to_one")
    assert len(m) == 25


def test_district_areas_sum_to_province():
    import geopandas as gpd

    d = gpd.read_file(BOUNDARIES["ilceler"])
    p = gpd.read_file(BOUNDARIES["il"])
    assert d["area_km2"].sum() == pytest.approx(p["area_km2"].iloc[0], rel=1e-4)


# ---------------------------------------------------------------- kayıt (registry)
def test_registry_validation_passes(registry):
    assert not registry.errors, "\n".join(map(str, registry.errors))


def test_all_canonical_keys_present(registry, params):
    canonical = list(registry.schema.canonical)
    assert len(canonical) == 58  # 54 + population.grid_source + 3 dolambaç profili anahtarı (Faz 1 kararları)
    missing = [k for k in canonical if k not in params]
    assert missing == registry.missing_canonical
    assert not missing, f"eksik kanonik anahtarlar: {missing}"
    assert not registry.unknown_keys


def test_parameters_json_matches_fragments(registry, params):
    assert set(params) == set(registry.params)
    for key, p in registry.params.items():
        assert params[key]["value"] == p["value"], key
        assert params[key]["status"] == p["status"], key
        assert params[key]["source_ids"] == list(p.get("source_ids") or []), key


def test_sourced_params_have_existing_sources(registry, params):
    for key, p in params.items():
        if p["status"] in ("kaynakli", "hesap"):
            assert p["source_ids"], key
        if p["status"] == "hesap":
            assert p["derivation"], key
        if p["status"] in ("varsayim", "model_secimi"):
            assert p["rationale"], key
        for sid in p["source_ids"]:
            assert sid in registry.sources, (key, sid)


def test_sources_md_generated():
    text = (common.DATA / "SOURCES.md").read_text(encoding="utf-8")
    for heading in ("## 2. Parametreler", "## 3. Varsayımlar", "## 4. Bölüm 3 ile karşılaştırma"):
        assert heading in text
    assert "5.910.320" in text


def test_range_note_in_outputs(params):
    # kaydırıcı aralıkları arayüz tercihi (varsayım): parameters.json'da alan, SOURCES.md'de genel not
    note = build_registry.RANGE_NOTE
    assert "varsayım" in note
    for key, p in params.items():
        assert "range_note" in p, key
        assert p["range_note"] == (note if p["range"] is not None else None), key
    assert any(p["range"] is not None for p in params.values())
    assert note in (common.DATA / "SOURCES.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- kaynak tekilliği ve betik listesi
# Alanlar arası birleştirilen kayıtlar: kaldırılan kimlik -> korunan kimlik (alan_literatur)
MERGED_SOURCES = {
    "arazi_osm_worldcover_2021_v200": "alan_literatur_lit_zanaga2022",
    "arazi_osm_boscoe_2012": "alan_literatur_lit_boscoe2012",
    "arazi_osm_ballou_2002": "alan_literatur_lit_ballou2002",
}
# Birleştirilen belgelerin URL'de geçen DOI/kayıt parçaları
MERGED_DOI_KEYS = {
    "alan_literatur_lit_zanaga2022": ("zenodo.7254221", "zenodo.org/api/records/7254221"),
    "alan_literatur_lit_boscoe2012": ("10.1080/00330124.2011.583586",),
    "alan_literatur_lit_ballou2002": ("10.1016/s0965-8564(01)00044-1",),
}


def _cited_ids(registry) -> set[str]:
    ids = set()
    for p in registry.params.values():
        ids |= set(p.get("source_ids") or [])
        for a in p.get("alternatives") or []:
            ids |= set(a.get("source_ids") or [])
    for row in build_registry.SPEC_SECTION3:
        ids |= set(row["source_ids"])
    return ids


def test_merged_sources_are_single_records(registry):
    for old, new in MERGED_SOURCES.items():
        assert old not in registry.sources, old
        assert new in registry.sources, new
    assert not _cited_ids(registry) & set(MERGED_SOURCES)
    for new, keys in MERGED_DOI_KEYS.items():
        same = {sid for sid, s in registry.sources.items()
                if any(k in str(s.get("url", "")).lower() for k in keys)
                or s.get("sha256") == registry.sources[new].get("sha256")}
        assert same == {new}, (new, same)


def test_no_cross_domain_duplicate_urls(registry):
    # birleştirilen belgeler için yinelenme HATA; diğer alanlar arası yinelenmeler yalnız uyarı
    dups = build_registry.cross_domain_duplicates(registry)
    merged = set(MERGED_SOURCES) | set(MERGED_SOURCES.values())
    bad = [d for d in dups if {d[1], d[2]} & merged]
    assert not bad, bad
    for crit, first, sid in dups:
        warnings.warn(f"alanlar arası yinelenen {crit}: {first} / {sid}", UserWarning)
    reported = [i for i in registry.warnings if i.where.startswith("yinelenen")]
    assert len(reported) == len(dups)


SCRIPT_RE = re.compile(r"pipeline/[A-Za-z0-9_./-]+?\.py")


def test_listed_pipeline_scripts_exist():
    files = [*common.SOURCES_DIR.glob("*.yaml"), common.SOURCES_DIR / "README.md", *common.PARAMS_DIR.glob("*.yaml"),
             *(f for f in common.REFERENCE.iterdir() if f.suffix in (".yaml", ".md", ".csv", ".json")),
             common.DATA / "SOURCES.md", *(common.ROOT / "pipeline").glob("*.py")]
    listed: dict[str, set[str]] = {}
    for f in files:
        for m in SCRIPT_RE.findall(f.read_text(encoding="utf-8")):
            listed.setdefault(m, set()).add(str(f.relative_to(common.ROOT)))
    assert "pipeline/20_area_literature.py" in listed
    missing = {m: sorted(w) for m, w in listed.items() if not (common.ROOT / m).is_file()}
    assert not missing, missing


def test_area_25632_primary_sources():
    # 25.632 km²: birincil dayanak resmî belgeler ([5] ve Valilik ÇDR 2018 Çizelge E.85); HGM yalnız ikincil
    df = pd.read_csv(AREA_CSV)
    rows = df[df["deger_km2"].astype(float) == 25632]
    official = rows[rows["status"].isin(["kaynakli", "hesap"])]
    assert set(official["source_id"]) == {"alan_literatur_csb_ref5_beypazari", "alan_literatur_valilik_cdr_2018"}
    hgm = rows[rows["source_id"].str.startswith("alan_literatur_hgm_")]
    assert len(hgm) == 2 and (hgm["status"] == "kaynakli_ikincil").all()
    assert official["notlar"].str.contains("kopyalara göre HGM ile aynı", regex=False).all()   # 'İ' küçültmesi güvenilmez


# ---------------------------------------------------------------- rasterler
def _raster(path):
    import rasterio

    with rasterio.open(path) as src:
        return src.width, src.height, src.count, src.crs, src.transform, src.read()


@pytest.mark.parametrize("name,shape", [
    ("ghs_pop_e2020_100m.tif", GHS_100M), ("ghs_pop_e2025_100m.tif", GHS_100M),
    ("ghs_pop_e2030_100m.tif", GHS_100M), ("ghs_pop_e2025_1km.tif", GHS_1KM), ("ghs_pop_e2030_1km.tif", GHS_1KM),
])
def test_ghs_rasters_shape_crs(name, shape):
    from pyproj import CRS

    w, h, n, crs, tr, arr = _raster(common.PROCESSED / "pop" / name)
    assert (w, h, n) == (*shape, 1)
    assert CRS.from_user_input(crs.to_wkt()).equals(CRS.from_user_input(common.CRS_MOLLWEIDE)) \
        or crs.to_string() == common.CRS_MOLLWEIDE
    xmin, ymin, xmax, ymax = common.GHS_WINDOW
    assert (tr.c, tr.f) == (xmin, ymax)
    assert tr.a == (xmax - xmin) / w and -tr.e == (ymax - ymin) / h
    assert np.isfinite(arr).all() and (arr >= 0).all()


def test_ghs_1km_equals_100m_total():
    a = _raster(common.PROCESSED / "pop" / "ghs_pop_e2025_100m.tif")[-1].astype("float64").sum()
    b = _raster(common.PROCESSED / "pop" / "ghs_pop_e2025_1km.tif")[-1].astype("float64").sum()
    assert a == pytest.approx(b, rel=1e-5)


@pytest.mark.parametrize("name", ["worldpop_2025_100m.tif", "worldpop_2030_100m.tif"])
def test_worldpop_rasters(name):
    w, h, n, crs, tr, arr = _raster(common.PROCESSED / "pop" / name)
    assert (w, h, n) == (4081, 2641, 1)
    assert crs.to_epsg() == 4326
    assert (arr[np.isfinite(arr)] >= 0).all()


def test_worldcover_fractions():
    w, h, n, crs, tr, arr = _raster(common.PROCESSED / "landcover" / "worldcover_ankara_100m.tif")
    assert (w, h, n) == (*GHS_100M, 3)
    assert crs.to_string() == common.CRS_MOLLWEIDE
    valid = arr[arr != 255]
    assert valid.size > 0
    assert valid.min() >= 0 and valid.max() <= 100
    # su + sulak alan + yapılaşma payları bir hücrede %100'ü aşamaz
    s = arr.astype("int16")
    ok = (arr != 255).all(axis=0)
    assert (s[:, ok].sum(axis=0) <= 100).all()


# ---------------------------------------------------------------- ilçe kontrol tablosu
def test_district_check_table(tuik):
    df = pd.read_csv(CHECK_CSV, dtype={"ilce_key": str}, keep_default_na=False)
    body = df[df["ilce_key"] != "toplam"]
    assert len(body) == 25 and len(df) == 26
    assert list(body["ilce"]) == list(tuik["ilce"])          # TÜİK yazımı korunur
    assert int(body["tuik_2025"].sum()) == 5_910_320
    for col, src in (("oran_ghs_e2025", "ghs_e2025_100m"), ("oran_worldpop_2025", "worldpop_2025_100m"),
                     ("oran_kontur", "kontur_20231101_h3r8")):
        ratio = body[src].astype(float) / body["tuik_2025"]
        assert np.allclose(body[col].astype(float), ratio, atol=1e-4)
        out = ~body[col].astype(float).between(0.7, 1.3)
        flagged = body["aralik_disi"].str.contains(col.removeprefix("oran_"))
        assert (out == flagged).all(), col
    assert (common.PROCESSED / "ilce_kontrol.png").stat().st_size > 10_000
