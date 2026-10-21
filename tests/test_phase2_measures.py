"""Faz 2 "Toplam mesafe" kartı kontrolleri: data/processed/ankara_olculer.json (pipeline/23_ankara_measures.py).

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase2_measures.py
Önkoşul: .venv/bin/python pipeline/23_ankara_measures.py
"""
from __future__ import annotations

import csv
import json
import re

import numpy as np
import pytest
import yaml

from pipeline import common

OUT_JSON = common.PROCESSED / "ankara_olculer.json"
OUT_PNG = common.PROCESSED / "ankara_olculer.png"
IL = common.PROCESSED / "ankara_il.geojson"
ILCE = common.PROCESSED / "ankara_ilceler.geojson"
KIZILAY = common.REFERENCE / "kizilay.yaml"
AREA_CSV = common.REFERENCE / "ankara_alan_kaynaklari.csv"
TUIK_CSV = common.REFERENCE / "tuik_adnks_ankara.csv"
PARAMS = common.REFERENCE / "parameters.json"

REQUIRED_KEYS = [
    "meta.generated_by", "meta.inputs",
    "boundary.vertex_count",
    "area.geodesic_km2.value", "area.geodesic_km2.unit", "area.geodesic_km2.method", "area.geodesic_km2.text",
    "area.epsg6933_km2.value", "area.rel_diff_epsg6933_vs_geodesic_pct",
    "perimeter.geodesic_km.value", "perimeter.web.vertex_count", "perimeter.web.perimeter_km",
    "perimeter.web.perimeter_diff_km", "perimeter.simplification_series", "perimeter.note",
    "extent.utm_bbox.width_km.value", "extent.utm_bbox.height_km.value",
    "extent.west", "extent.east", "extent.north", "extent.south",
    "extent.west_east_geodesic_km.value", "extent.north_south_geodesic_km.value",
    "diameter.distance_km.value", "diameter.p1", "diameter.p2", "diameter.hull_vertex_count",
    "diameter.bruteforce_same_pair", "diameter.proof",
    "kizilay.point", "kizilay.farthest.distance_km.value", "kizilay.nearest.distance_km.value",
    "centroid.point", "centroid.distance_to_kizilay_km.value",
    "water.permanent_water_km2.value", "water.land_area_km2.value", "water.checks",
    "area_comparison.rows", "area_comparison.own_rows", "area_comparison.note",
    "districts.rows", "districts.sum_pop_2025",
    "hospital_land.cells", "hospital_land.cases", "hospital_land.conclusion",
    "card.title", "card.items",
]


def get(d: dict, path: str):
    for k in path.split("."):
        assert isinstance(d, dict) and k in d, f"eksik anahtar: {path} ({k})"
        d = d[k]
    return d


@pytest.fixture(scope="module")
def m() -> dict:
    assert OUT_JSON.exists(), "önce pipeline/23_ankara_measures.py çalıştırılmalı"
    return json.loads(OUT_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def il():
    from shapely.geometry import shape
    return shape(json.loads(IL.read_text(encoding="utf-8"))["features"][0]["geometry"])


@pytest.fixture(scope="module")
def params() -> dict:
    return json.loads(PARAMS.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- yapı
def test_json_keys_present(m):
    for path in REQUIRED_KEYS:
        get(m, path)
    for key in ("value", "unit", "method", "text"):
        assert key in m["diameter"]["distance_km"]
    assert len(m["card"]["items"]) >= 8
    for it in m["card"]["items"]:
        assert {"key", "label", "value_text", "tooltip"} <= set(it)


def test_png_written():
    assert OUT_PNG.exists() and OUT_PNG.stat().st_size > 50_000
    assert OUT_PNG.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_turkish_number_text(m):
    pat = re.compile(r"^\d{1,3}(\.\d{3})*(,\d+)? (km²|km)$")
    for path in ("area.geodesic_km2.text", "perimeter.geodesic_km.text", "diameter.distance_km.text",
                 "kizilay.farthest.distance_km.text", "water.land_area_km2.text"):
        assert pat.match(get(m, path)), get(m, path)


# ---------------------------------------------------------------- alan ve çevre
def test_area_geodesic_vs_epsg6933_within_001pct(m, il):
    geo = m["area"]["geodesic_km2"]["value"]
    ea = m["area"]["epsg6933_km2"]["value"]
    assert abs(ea / geo - 1) < 1e-4            # %0,01
    # JSON güncel mi: alanları sınırdan yeniden hesapla
    from pyproj import Geod, Transformer
    import shapely
    a, p = Geod(ellps="WGS84").geometry_area_perimeter(il)
    assert abs(abs(a) / 1e6 - geo) < 1e-3
    assert abs(p / 1e3 - m["perimeter"]["geodesic_km"]["value"]) < 1e-3
    t = Transformer.from_crs(common.CRS_WGS84, common.CRS_EQUAL_AREA, always_xy=True)
    il_ea = shapely.transform(il, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))
    assert abs(il_ea.area / 1e6 - ea) < 1e-3


def test_perimeter_depends_on_resolution(m):
    full = m["boundary"]["vertex_count"]
    web = m["perimeter"]["web"]
    assert web["vertex_count"] < full
    assert web["perimeter_km"] < m["perimeter"]["geodesic_km"]["value"]
    s = m["perimeter"]["simplification_series"]
    per = [r["perimeter_km"] for r in s]
    assert all(b <= a for a, b in zip(per, per[1:])), "tolerans büyüdükçe çevre kısalmalı"
    # alan çevreden çok daha az değişir
    assert all(abs(r["area_diff_pct"]) < abs(r["perimeter_diff_pct"]) for r in s)


# ---------------------------------------------------------------- genişlik ve çap
def test_diameter_at_least_any_bbox_side(m):
    d = m["diameter"]["distance_km"]["value"]
    bb = m["extent"]["utm_bbox"]
    assert d >= bb["width_km"]["value"] and d >= bb["height_km"]["value"]
    assert d >= m["extent"]["west_east_geodesic_km"]["value"]
    assert d >= m["extent"]["north_south_geodesic_km"]["value"]
    assert d >= m["kizilay"]["farthest"]["distance_km"]["value"]


def test_diameter_hull_bruteforce(m):
    from pyproj import Geod
    dia = m["diameter"]
    assert dia["bruteforce_same_pair"] is True
    n = dia["vertex_count"]
    assert dia["bruteforce_pairs"] == n * (n - 1) // 2
    assert 3 <= dia["hull_vertex_count"] < n
    _, _, dist = Geod(ellps="WGS84").inv(dia["p1"]["lon"], dia["p1"]["lat"], dia["p2"]["lon"], dia["p2"]["lat"])
    assert abs(dist / 1e3 - dia["distance_km"]["value"]) < 1e-2


def test_extreme_points_are_extreme(m, il):
    v = np.asarray(il.exterior.coords)
    e = m["extent"]
    assert e["west"]["lon"] == pytest.approx(v[:, 0].min(), abs=1e-6)
    assert e["east"]["lon"] == pytest.approx(v[:, 0].max(), abs=1e-6)
    assert e["north"]["lat"] == pytest.approx(v[:, 1].max(), abs=1e-6)
    assert e["south"]["lat"] == pytest.approx(v[:, 1].min(), abs=1e-6)


# ---------------------------------------------------------------- Kızılay
def test_kizilay_inside_cankaya(m):
    from shapely.geometry import Point, shape
    kz = yaml.safe_load(KIZILAY.read_text(encoding="utf-8"))
    pt = Point(float(kz["lon"]), float(kz["lat"]))
    feats = json.loads(ILCE.read_text(encoding="utf-8"))["features"]
    inside = [f["properties"]["ilce_key"] for f in feats if shape(f["geometry"]).contains(pt)]
    assert inside == ["cankaya"]
    assert m["kizilay"]["point"]["district"]["ilce_key"] == "cankaya"
    assert m["kizilay"]["point"]["inside_province"] is True


def test_kizilay_distances_consistent(m, il):
    from shapely.geometry import Point
    k = m["kizilay"]
    near, far = k["nearest"]["distance_km"]["value"], k["farthest"]["distance_km"]["value"]
    assert 0 < near <= k["nearest_vertex"]["distance_km"] + 1e-9 and near < far
    assert k["aeqd_check_max_abs_diff_m"] < 0.01
    assert il.contains(Point(m["centroid"]["point"]["lon"], m["centroid"]["point"]["lat"]))
    assert m["centroid"]["distance_to_kizilay_km"]["value"] < far


def test_centroid_province_laea(m, il):
    # ağırlık merkezi il merkezli LAEA'da (EPSG:6933 değil); JSON ve kart bu hesapla aynı
    import shapely
    from pyproj import Geod, Transformer
    laea = "+proj=laea +lat_0=39.7 +lon_0=32.8 +datum=WGS84 +units=m +no_defs"
    c = m["centroid"]
    assert c["projection"] == laea and m["meta"]["crs"]["centroid"] == laea
    fwd = Transformer.from_crs(common.CRS_WGS84, laea, always_xy=True)
    inv = Transformer.from_crs(laea, common.CRS_WGS84, always_xy=True)
    cc = shapely.transform(il, lambda xy: np.column_stack(fwd.transform(xy[:, 0], xy[:, 1]))).centroid
    lon, lat = inv.transform(cc.x, cc.y)
    assert c["point"]["lon"] == pytest.approx(lon, abs=1e-6) and c["point"]["lat"] == pytest.approx(lat, abs=1e-6)
    kz = m["kizilay"]["point"]
    _, _, d = Geod(ellps="WGS84").inv(kz["lon"], kz["lat"], lon, lat)
    assert c["distance_to_kizilay_km"]["value"] == pytest.approx(d / 1e3, abs=1e-3)
    item = next(it for it in m["card"]["items"] if it["key"] == "centroid")
    assert item["value_text"] == c["distance_to_kizilay_km"]["text"] and "LAEA" in item["tooltip"]
    # karşılaştırma: EPSG:6933 ağırlık merkezi farklı bir nokta
    assert c["comparison_epsg6933"]["shift_from_laea_km"] > 0


# ---------------------------------------------------------------- su ve kaynak tablosu
def test_water_and_land_area(m):
    w = m["water"]
    area = m["area"]["geodesic_km2"]["value"]
    water = w["permanent_water_km2"]["value"]
    assert 0 < water < 0.1 * area
    assert w["land_area_km2"]["value"] == pytest.approx(area - water, abs=2e-3)
    c = w["checks"]
    assert c["nodata_cells_in_province"] == 0
    assert abs(c["raster_vs_polygon_area_pct"]) < 0.01
    assert abs(c["pixel_centre_water_km2"] / water - 1) < 0.005
    assert c["nominal_1ha_water_km2"] > water           # 1 ha varsayımı alanı büyütür
    assert c["cell_area_m2_max"] < 10_000
    assert c["cell_area_geodesic_vs_epsg6933_max_rel_diff"] < 1e-6
    assert w["districts_water_sum_km2"] == pytest.approx(water, rel=1e-3)


def test_area_table_matches_csv(m):
    with open(AREA_CSV, encoding="utf-8") as f:
        src = list(csv.DictReader(f))
    rows = m["area_comparison"]["rows"]
    ref = m["area_comparison"]["reference_km2"]
    assert ref == m["area"]["geodesic_km2"]["value"]
    assert len(rows) == len(src)
    land = m["water"]["land_area_km2"]["value"]
    for r, s in zip(rows, src):
        assert r["value_km2"] == pytest.approx(float(s["deger_km2"]))
        assert r["lakes_excluded"] == ("hariç" in s["kapsam"] and "göl" in s["kapsam"].lower())
        assert r["diff_km2"] == pytest.approx(r["value_km2"] - ref, abs=2e-3)
        assert r["diff_pct"] == pytest.approx(100 * (r["value_km2"] / ref - 1), abs=2e-3)
        if r["lakes_excluded"]:
            assert r["diff_vs_land_km2"] == pytest.approx(r["value_km2"] - land, abs=2e-3)
    assert any(r["lakes_excluded"] for r in rows)


# ---------------------------------------------------------------- ilçeler
def test_districts_area_and_density(m):
    with open(TUIK_CSV, encoding="utf-8") as f:
        tuik = {r["ilce_key"]: int(r["nufus_2025"]) for r in csv.DictReader(f)}
    d = m["districts"]
    rows = d["rows"]
    assert len(rows) == 25 and {r["ilce_key"] for r in rows} == set(tuik)
    assert d["sum_pop_2025"] == sum(tuik.values()) == common.TUIK_PROVINCE_POP_2025
    assert abs(d["sum_area_geodesic_km2"] / m["area"]["geodesic_km2"]["value"] - 1) < 1e-4
    for r in rows:
        assert r["pop_2025"] == tuik[r["ilce_key"]]
        assert r["density_per_km2"] == pytest.approx(r["pop_2025"] / r["area_geodesic_km2"], rel=1e-5, abs=0.006)
        assert abs(r["area_geodesic_km2"] / r["area_epsg6933_km2_phase1"] - 1) < 1e-4
        assert abs(r["area_raster_km2"] / r["area_geodesic_km2"] - 1) < 1e-3
        assert r["density_land_per_km2"] >= r["density_per_km2"]
    dens = [r["density_per_km2"] for r in rows]
    assert dens == sorted(dens, reverse=True)


# ---------------------------------------------------------------- hastane arsası / hücre
def test_hospital_land_not_binding(m, params):
    h = m["hospital_land"]
    land_per_bed = params["land.m2_per_bed"]["value"]
    g = next(c for c in h["cases"] if c["key"] == "general_300")
    assert g["land_m2"] == pytest.approx(300 * land_per_bed)
    if land_per_bed == 130:
        assert g["land_m2"] == 39_000
    a0, a = params["cost.core_area_m2"]["value"], params["cost.m2_per_bed.general"]["value"]
    assert g["floor_area_m2"] == pytest.approx(a0 + 300 * a, abs=0.1)
    assert sorted(c["area_km2"] for c in h["cells"]) == [4.0, 9.0, 25.0]
    beds = {c["beds"] for c in h["cases"]}
    assert {300, params["size.city_min_beds"]["value"], params["size.city_max_beds"]["value"]} <= beds
    assert {c["land_m2_per_bed"] for c in h["cases"]} >= {130.0, 220.0}
    for c in h["cases"]:
        for s in c["shares"]:
            assert s["share_pct"] == pytest.approx(100 * c["land_m2"] / 1e6 / s["cell_area_km2"], rel=1e-3)
            assert s["share_pct"] < 50 and s["fits_per_cell"] >= 1
    assert h["conclusion"].startswith("Arazi bağlayıcı değil")
