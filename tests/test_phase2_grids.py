"""Faz 2 ızgara kontrolleri: kalibre nüfus toplamları, parça tablosu, hücre özellikleri, web GeoJSON'u, mesafe modülü,
aday kuralı ve kapsama onarımı.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase2_grids.py
Önkoşul: pipeline/22_grids.py çalıştırılmış olmalı (data/processed/grid/).
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from backend.model import distance, grid_io
from pipeline import common

GRID_DIR = common.PROCESSED / "grid"
SIZES = (2, 3, 5)
SOURCES = ("wp", "ghs")
YEARS = (2025, 2030)
P2025 = 5_910_320
P2030 = 6_062_005


@pytest.fixture(scope="module")
def params() -> dict:
    return grid_io.load_params()


@pytest.fixture(scope="module")
def tuik() -> pd.DataFrame:
    return pd.read_csv(common.REFERENCE / "tuik_adnks_ankara.csv", dtype={"ilce_key": str})


@pytest.fixture(scope="module")
def meta() -> dict:
    return json.loads((GRID_DIR / "grid_meta.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def summary() -> dict:
    return json.loads((GRID_DIR / "grid_summary.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def grids() -> dict[int, grid_io.Grid]:
    return {s: grid_io.load_grid(s) for s in SIZES}


@pytest.fixture(scope="module")
def circ(params) -> distance.Circuity:
    return distance.Circuity.from_params(params)


def _pieces(s: int) -> pd.DataFrame:
    return pd.read_csv(GRID_DIR / f"grid_{s}km_pieces.csv", dtype={"cell_id": str, "ilce_key": str})


def _grids_module():
    spec = importlib.util.spec_from_file_location("grids22", common.ROOT / "pipeline" / "22_grids.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- nüfus toplamları
def test_params_province_totals(params):
    assert params["population.province_2025"]["value"] == P2025
    assert params["population.province_2030"]["value"] == P2030


@pytest.mark.parametrize("s", SIZES)
def test_province_totals_exact(grids, s):
    g = grids[s]
    for src in SOURCES:
        assert g.pop(src, 2025).dtype == np.int64 and g.pop(src, 2030).dtype == np.int64
        assert int(g.pop(src, 2025).sum()) == P2025
        assert int(g.pop(src, 2030).sum()) == P2030
        assert (g.pop(src, 2025) >= 0).all() and (g.pop(src, 2030) >= 0).all()


@pytest.mark.parametrize("s", SIZES)
def test_district_totals_exact(s, tuik, meta):
    df = _pieces(s)
    t25 = tuik.set_index("ilce_key")["nufus_2025"]
    t30 = {d["ilce_key"]: d for d in meta["districts"]}
    for src in SOURCES:
        by = df.groupby("ilce_key")[f"pop2025_{src}"].sum().reindex(t25.index, fill_value=0)
        assert (by == t25).all(), src
        by30 = df.groupby("ilce_key")[f"pop2030_{src}"].sum().reindex(t25.index, fill_value=0)
        assert all(by30[k] == t30[k][f"pop2030_{src}"] for k in t25.index), src
        assert int(by30.sum()) == P2030


@pytest.mark.parametrize("s", SIZES)
def test_pieces_sum_to_cells(grids, s):
    g = grids[s]
    df = _pieces(s)
    assert not df.duplicated(["cell_id", "ilce_key"]).any()
    assert set(df["cell_id"]) <= set(g.id.tolist())
    for src in SOURCES:
        for yr in YEARS:
            col = f"pop{yr}_{src}"
            by = df.groupby("cell_id")[col].sum().reindex(g.id, fill_value=0).to_numpy()
            assert np.array_equal(by, g.pop(src, yr)), col


def test_calibration_table(tuik):
    cal = pd.read_csv(GRID_DIR / "ilce_kalibrasyon.csv", dtype={"ilce_key": str})
    p1 = pd.read_csv(common.PROCESSED / "ilce_nufus_kaynaklar.csv", dtype={"ilce_key": str})
    p1 = p1[p1["satir_turu"] == "ilce"].set_index("ilce_key").loc[tuik["ilce_key"]]
    assert list(cal["ilce_key"]) == list(tuik["ilce_key"])
    # Faz 1 ilçe toplamları (0,1'e yuvarlı) yeniden üretilir
    for src, c25, c30 in (("wp", "worldpop_2025_100m", "worldpop_2030_100m"),
                          ("ghs", "ghs_e2025_100m", "ghs_e2030_100m")):
        assert np.allclose(cal[f"{src}_kaynak_2025"], p1[c25], atol=0.11)
        assert np.allclose(cal[f"{src}_kaynak_2030"], p1[c30], atol=0.11)
        f = cal[f"{src}_kalibrasyon_katsayisi"].to_numpy()
        assert np.allclose(f, tuik["nufus_2025"].to_numpy() / p1[c25].to_numpy(), rtol=1e-4)
        assert int(cal[f"{src}_senaryo_2030"].sum()) == P2030
    # GHS Çubuk 2030'da küçülüyor ve bayraklı
    row = cal.set_index("ilce_key").loc["cubuk"]
    assert row["ghs_senaryo_buyume_2030_2025"] < 1 and "kuculuyor" in row["ghs_bayrak"]


def test_summary_phase1_reproduced(summary):
    for src in SOURCES:
        assert summary["calibration"][src]["phase1_max_abs_diff"] < 0.06


# ---------------------------------------------------------------- hücreler
@pytest.mark.parametrize("s", SIZES)
def test_cell_ids_and_geometry(grids, s):
    g = grids[s]
    assert g.cell_km == s and g.n > 0
    assert len(np.unique(g.id)) == g.n
    assert all(i == f"{s}km_{a}_{b}" for i, a, b in zip(g.id, g.ix, g.iy))
    assert np.allclose(g.x, (g.ix + 0.5) * s * 1000) and np.allclose(g.y, (g.iy + 0.5) * s * 1000)
    assert ((g.inside_frac > 0) & (g.inside_frac <= 1 + 1e-12)).all()
    assert np.allclose(g.area_km2, g.inside_frac * s * s)
    assert (g.district >= 0).all() and (g.district < 25).all()
    # komşular: gerçekten 8-komşu
    nb = g.nbr8
    ok = nb >= 0
    rows = np.repeat(np.arange(g.n), 8).reshape(-1, 8)
    assert (np.abs(g.ix[nb[ok]] - g.ix[rows[ok]]) <= 1).all() and (np.abs(g.iy[nb[ok]] - g.iy[rows[ok]]) <= 1).all()
    assert (nb[ok] != rows[ok]).all()


def test_area_close_to_province(grids):
    # UTM'de il alanı ≈ 25.630 km² (eşit alanlı 25.648); hücre içi alanların toplamı aynı poligondan
    areas = [float(grids[s].area_km2.sum()) for s in SIZES]
    assert max(areas) - min(areas) < 1.0
    assert 25_500 < areas[0] < 25_800


@pytest.mark.parametrize("s", SIZES)
def test_landcover(grids, params, s):
    g = grids[s]
    has = g.lc_pixels > 0
    assert (g.inside_frac[~has] < 0.5).all()   # yeterince il içi her hücrede piksel var
    for a in (g.water_pct, g.wetland_pct, g.built_pct):
        assert ((a[has] >= 0) & (a[has] <= 100)).all()
    assert np.allclose(g.water_frac[has], (g.water_pct[has] + g.wetland_pct[has]) / 100)
    assert (g.water_frac[has] <= 1 + 1e-12).all()


def test_grid_meta(meta, tuik):
    assert meta["cell_sizes_km"] == list(SIZES)
    assert meta["crs"] == common.CRS_UTM36N
    assert [d["ilce_key"] for d in meta["districts"]] == list(tuik["ilce_key"])
    assert meta["generated_by"] == "pipeline/22_grids.py"
    assert (common.ROOT / meta["generated_by"]).is_file()
    assert "nüfussuzsa" not in json.dumps(meta, ensure_ascii=False)
    # önizleme PNG'si artık üretilmiyor (yerine docs/screenshots/faz2_nufus_3km.png, pipeline/24_static_maps.py)
    assert "preview_png" not in meta["files"] and not (GRID_DIR / "grid_3km_onizleme.png").exists()


@pytest.mark.parametrize("s", SIZES)
def test_majority_district(grids, s):
    # çoğunluk ilçesi: tam sayı nüfusu en büyük ilçe (eşitlik kesimi yuvarlanmamış nüfus / alanla 22_grids.py'de)
    g = grids[s]
    df = _pieces(s)
    keys = g.district_keys
    piv = (df.pivot_table(index="cell_id", columns="ilce_key", values="pop2025_wp", aggfunc="sum", fill_value=0)
           .reindex(index=g.id, columns=keys, fill_value=0).to_numpy())
    has = piv.sum(axis=1) > 0
    chosen = piv[np.arange(g.n), g.district.astype(int)]
    assert np.array_equal(chosen[has], piv[has].max(axis=1))
    assert np.array_equal(g.district[~has], g.district_area[~has])


def test_majority_district_tie_break():
    m = _grids_module()
    pop_int = np.array([[5, 5, 0], [5, 5, 0], [0, 3, 3], [2, 0, 0]])
    pop_raw = np.array([[4.9, 5.2, 0.0], [5.0, 5.0, 0.0], [0.0, 3.0, 3.0], [1.6, 0.0, 0.3]])
    area = np.array([[1.0, 1.0, 0.0], [1.0, 2.0, 0.0], [0.0, 2.0, 2.0], [1.0, 5.0, 3.0]])
    idx, ties = m.majority_district(pop_int, pop_raw, area)
    # 1) yuvarlanmamış nüfus kazanır; 2) ham da eşit → alan; 3) hepsi eşit → küçük indis; 4) eşitlik yok
    assert idx.tolist() == [1, 1, 1, 0]
    assert ties == {"integer_ties": 3, "still_tied_after_raw": 2, "still_tied_after_area": 1}


# ---------------------------------------------------------------- GeoJSON
@pytest.mark.parametrize("s", SIZES)
def test_geojson(grids, s):
    path = GRID_DIR / f"grid_{s}km_cells.geojson"
    assert path.stat().st_size < 3_000_000
    gj = json.loads(path.read_text(encoding="utf-8"))
    g = grids[s]
    feats = gj["features"]
    assert gj["type"] == "FeatureCollection" and len(feats) == g.n
    keys = {"id", "pop", "pop_ghs", "pop2030", "dens", "water", "built", "ilce", "aday"}
    assert all(set(f["properties"]) == keys for f in feats)
    ring = feats[0]["geometry"]["coordinates"][0]
    assert len(ring) == 5 and ring[0] == ring[-1]
    assert all(30.0 < x < 35.0 and 38.0 < y < 41.5 for f in feats for x, y in f["geometry"]["coordinates"][0])
    assert [f["properties"]["id"] for f in feats] == g.id.tolist()
    assert sum(f["properties"]["pop"] for f in feats) == P2025
    assert sum(f["properties"]["pop_ghs"] for f in feats) == P2025
    assert sum(f["properties"]["pop2030"] for f in feats) == P2030
    assert np.array_equal(np.array([f["properties"]["aday"] for f in feats]), g.candidate)
    names = set(g.district_names)
    assert all(f["properties"]["ilce"] in names for f in feats)


# ---------------------------------------------------------------- mesafe modülü
def test_circuity_profile_points(params, circ):
    prof = params["access.circuity_profile"]["value"]
    xs = np.array([p[0] for p in prof])
    ks = np.array([p[1] for p in prof])
    assert circ.mode == "mesafeye_bagli" and circ.scale == 1.0
    assert np.allclose(circ.k(xs), ks)
    assert np.allclose(circ.k([0.0, 1.0, xs[0]]), ks[0])          # kısa mesafede uç değer
    assert np.allclose(circ.k([xs[-1], 500.0]), ks[-1])           # uzun mesafede uç değer
    mid = (xs[0] + xs[1]) / 2
    assert circ.k(mid) == pytest.approx((ks[0] + ks[1]) / 2)
    # pipeline/21_circuity_profile.py circuity_at ile aynı
    spec = importlib.util.spec_from_file_location("circ21", common.ROOT / "pipeline" / "21_circuity_profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    e = np.linspace(0, 150, 301)
    assert np.allclose(circ.k(e), mod.circuity_at(e, prof))
    assert circ.min_factor == pytest.approx(ks.min())


def test_circuity_modes_and_road(params, circ):
    e = np.linspace(0, 120, 241)
    road = circ.road_km(e)
    assert (road >= e).all() and (road >= e * circ.min_factor - 1e-12).all()
    assert (np.diff(road) > 0).all()                                # bu profilde yol mesafesi kuş uçuşuyla artar
    sab = distance.Circuity.from_params(params, mode="sabit")
    assert np.allclose(sab.k(e), params["access.circuity"]["value"])
    assert np.allclose(sab.road_km(e), e * params["access.circuity"]["value"])
    sc = distance.Circuity.from_params(params, scale=1.1)
    assert np.allclose(sc.road_km(e), 1.1 * road)
    with pytest.raises(ValueError):
        distance.Circuity(mode="bilinmeyen", profile=((1.0, 1.2),))


def test_intra_cell_constant_monte_carlo():
    assert distance.INTRA_CELL_FACTOR == pytest.approx((np.sqrt(2) + np.log(1 + np.sqrt(2))) / 6)
    assert distance.INTRA_CELL_FACTOR == pytest.approx(0.3826, abs=1e-4)
    rng = np.random.default_rng(20261021)
    for s in SIZES:
        p = rng.uniform(-s / 2, s / 2, size=(400_000, 2))
        mc = np.hypot(p[:, 0], p[:, 1]).mean()
        assert mc == pytest.approx(distance.intra_cell_euclid_km(s), rel=0.01)


def _brute(dxy, cxy, cell_km, R, k, circ):
    # k'ıncı mesafeye eşit (1e-9 km içinde) adayların hepsi tutulur
    e = np.hypot(dxy[:, None, 0] - cxy[None, :, 0], dxy[:, None, 1] - cxy[None, :, 1]) / 1000
    d = distance.road_km_pairs(e, cell_km, circ)
    out = []
    for i in range(len(dxy)):
        j = np.flatnonzero(d[i] <= R)
        j = j[np.lexsort((j, d[i, j]))]
        if k and len(j) > k:
            j = j[d[i, j] <= d[i, j[k - 1]] + 1e-9]
        out.append((j, d[i, j]))
    return out


@pytest.mark.parametrize("k", [1, 10, 50, None])
def test_neighbor_lists_match_brute_force(grids, params, circ, k):
    g = grids[5]
    R = params["access.max_distance_km"]["value"]
    dem = np.flatnonzero(g.pop("wp", 2025) > 0)
    cand = np.flatnonzero(g.candidate)
    nl = distance.neighbor_lists(g.xy[dem], g.xy[cand], g.cell_km, R, k, circ)
    ref = _brute(g.xy[dem], g.xy[cand], g.cell_km, R, k, circ)
    assert len(nl.indptr) == len(dem) + 1 and nl.indptr[0] == 0 and nl.indptr[-1] == len(nl.indices)
    for i, (j, d) in enumerate(ref):
        jj, dd = nl.row(i)
        assert np.array_equal(jj, j) and np.allclose(dd, d)


@pytest.mark.parametrize("s", SIZES)
def test_neighbor_lists_properties(grids, params, circ, s):
    g = grids[s]
    R = params["access.max_distance_km"]["value"]
    k = params["grid.k_nearest"]["value"]
    dem = np.flatnonzero(g.pop("wp", 2025) > 0)
    cand = np.flatnonzero(g.candidate)
    nl = distance.neighbor_lists(g.xy[dem], g.xy[cand], g.cell_km, R, k, circ)
    allr = distance.neighbor_lists(g.xy[dem], g.xy[cand], g.cell_km, R, None, circ)
    assert (nl.counts >= np.minimum(allr.counts, k)).all() and (nl.counts <= allr.counts).all()
    for i in np.flatnonzero(nl.counts > k):                       # k'yı aşan satırlar yalnız eşitlik yüzünden
        jj, dd = nl.row(i)
        assert np.array_equal(jj, allr.row(i)[0][:len(jj)])
        assert dd[-1] - dd[k - 1] <= distance.TIE_TOL_KM
        if len(jj) < allr.counts[i]:
            assert allr.row(i)[1][len(jj)] > dd[k - 1] + distance.TIE_TOL_KM
    assert (nl.dist_km <= R + 1e-9).all() and (nl.dist_km > 0).all()
    rows = np.repeat(np.arange(len(dem)), nl.counts)
    same_row = rows[1:] == rows[:-1]
    assert (np.diff(nl.dist_km)[same_row] >= 0).all()              # satır içinde artan
    e = np.hypot(*(g.xy[dem][rows] - g.xy[cand][nl.indices]).T) / 1000
    assert (nl.dist_km >= e - 1e-9).all()                          # yol ≥ kuş uçuşu
    self_pair = dem[rows] == cand[nl.indices]
    assert np.allclose(nl.dist_km[self_pair], circ.road_km(distance.intra_cell_euclid_km(s)))


def test_neighbor_lists_keep_exact_ties(circ):
    # talep noktası merkezde; 4 aday 1 km'de (K, G, D, B — eşit), 1 aday 2 km'de; k = 2 → eşit 4 adayın hepsi kalır
    dem = np.array([[500_000.0, 4_400_000.0]])
    off = np.array([[0, 1000], [0, -1000], [1000, 0], [-1000, 0], [2000, 0]], dtype=float)
    nl = distance.neighbor_lists(dem, dem + off, 3.0, 30.0, 2, circ)
    jj, dd = nl.row(0)
    assert jj.tolist() == [0, 1, 2, 3]                            # eşitlikte aday sırası; hiçbiri atılmaz
    assert np.ptp(dd) <= distance.TIE_TOL_KM and nl.counts.tolist() == [4]
    # k = 4: tam k eşit aday, 2 km'deki dışarıda; k = 5: hepsi
    assert distance.neighbor_lists(dem, dem + off, 3.0, 30.0, 4, circ).counts.tolist() == [4]
    assert distance.neighbor_lists(dem, dem + off, 3.0, 30.0, 5, circ).counts.tolist() == [5]
    # eşitlik yoksa satır tam k
    nl1 = distance.neighbor_lists(dem, dem + off[[0, 4]], 3.0, 30.0, 1, circ)
    assert nl1.row(0)[0].tolist() == [0]


def test_euclid_for_road_km(params, circ, summary):
    R = params["access.max_distance_km"]["value"]
    e = circ.euclid_for_road_km(R)
    assert circ.road_km(e) == pytest.approx(R, abs=1e-6) and e < R
    assert summary["circuity"]["R_euclid_km"] == pytest.approx(e, abs=1e-6)
    sab = distance.Circuity.from_params(params, mode="sabit")
    assert sab.euclid_for_road_km(R) == pytest.approx(R / params["access.circuity"]["value"], abs=1e-6)
    # erisim.yaml'daki R metni bu hesaptan (1 ondalık)
    text = json.dumps(yaml.safe_load((common.PARAMS_DIR / "erisim.yaml").read_text(encoding="utf-8")),
                      ensure_ascii=False)
    assert f"≈{common.fmt_tr(e, 1)} km" in text and "Faz 2'de" not in text


def test_neighbor_file_matches_recomputation(grids, params, circ):
    z = np.load(GRID_DIR / "neighbors_3km_default.npz")
    g = grids[3]
    assert float(z["cell_km"]) == 3 and float(z["R_km"]) == params["access.max_distance_km"]["value"]
    assert int(z["k"]) == params["grid.k_nearest"]["value"]
    cand = grid_io.default_candidates(g, params)
    assert np.array_equal(z["cand_idx"], np.flatnonzero(cand))
    assert np.array_equal(z["demand_idx"], np.flatnonzero(g.pop("wp", 2025) > 0))
    nl = distance.neighbor_lists(g.xy[z["demand_idx"]], g.xy[z["cand_idx"]], 3, float(z["R_km"]), int(z["k"]), circ)
    assert np.array_equal(nl.indptr, z["indptr"]) and np.array_equal(nl.indices, z["indices"])
    assert np.allclose(nl.dist_km, z["dist_km"])
    assert (np.diff(z["indptr"]) >= 1).all()                       # her talep hücresinin en az bir adayı var
    nb = json.loads((GRID_DIR / "grid_summary.json").read_text(encoding="utf-8"))["sizes"]["3km"]["neighbors_default"]
    assert nb["nnz_k_nearest"] == len(z["indices"]) and nb["k_list_row_len_max"] == int(np.diff(z["indptr"]).max())


# ---------------------------------------------------------------- aday kuralı ve kapsama
def test_rule_params_in_registry(params, summary):
    for key, v in (("grid.candidate_min_inside", 0.5), ("grid.candidate_max_water", 0.5)):
        assert params[key]["value"] == v and params[key]["status"] == "model_secimi"
    assert "grid.candidate_near_pop" not in params
    p = params["grid.candidate_near_density"]
    assert p["status"] == "model_secimi" and p["rationale"] and p["unit"] == "kişi/km²"
    assert p["value"] == 1500 and "nufus_izgara_ghsl_data_package_2023" in p["source_ids"]
    rule = grid_io.RuleParams.from_params(params)
    # yoğunluk × hücre alanı: 2 km 6.000, 3 km 13.500, 5 km 37.500 kişi / hücre
    assert [rule.near_threshold(s) for s in SIZES] == [6000.0, 13500.0, 37500.0]
    assert summary["near_threshold_per_cell"] == {f"{s}km": rule.near_threshold(s) for s in SIZES}
    for s in SIZES:
        assert summary["sizes"][f"{s}km"]["J_table"]["near_threshold_per_cell"] == rule.near_threshold(s)


@pytest.mark.parametrize("s", SIZES)
def test_candidate_rule_matches_file(grids, params, s):
    g = grids[s]
    rule = grid_io.RuleParams.from_params(params)
    cand = grid_io.candidate_mask(g, rule, "wp", 2025)
    assert np.array_equal(cand, g.candidate)
    adm = grid_io.admissible_mask(g, rule.min_inside, rule.max_water)
    assert np.array_equal(adm, g.admissible)
    assert not (cand & ~adm).any()
    assert np.array_equal(g.neighbor_max(g.pop("wp", 2025)), g.nbr_max_pop2025_wp)
    # yakınlık kuralı elle: komşu en büyük nüfus ≥ yoğunluk × s²
    pop = g.pop("wp", 2025)
    manual = adm & ((pop >= rule.min_pop) | (g.nbr_max_pop2025_wp >= rule.near_density * s * s))
    assert np.array_equal(manual, cand)
    # yakınlık kuralı yalnız ekler
    no_near = grid_io.candidate_mask(g, grid_io.RuleParams.from_params(params, near_rule=False), "wp", 2025)
    assert not (no_near & ~cand).any()
    # P_min = 0 → bütün uygun hücreler aday
    assert np.array_equal(grid_io.candidate_mask(g, grid_io.RuleParams.from_params(params, min_pop=0), "wp", 2025), adm)


def test_summary_tables_consistent(grids, params, summary):
    for s in SIZES:
        g = grids[s]
        rows = summary["sizes"][f"{s}km"]["J_table"]["rows"]
        assert [r["P_min"] for r in rows] == [0, 100, 250, 500, 1000, 2500]
        for r in rows:
            base = grid_io.RuleParams.from_params(params, min_pop=r["P_min"])
            assert r["J_with_near"] == int(grid_io.candidate_mask(g, base, "wp", 2025).sum())
            no = grid_io.RuleParams.from_params(params, min_pop=r["P_min"], near_rule=False)
            assert r["J_without_near"] == int(grid_io.candidate_mask(g, no, "wp", 2025).sum())
        assert summary["sizes"][f"{s}km"]["n_cells"] == g.n


@pytest.mark.parametrize("s", SIZES)
@pytest.mark.parametrize("src,yr", [("wp", 2025), ("ghs", 2025), ("wp", 2030), ("ghs", 2030)])
def test_coverage_after_repair_is_complete(grids, params, circ, s, src, yr):
    g = grids[s]
    rule = grid_io.RuleParams.from_params(params)
    R = params["access.max_distance_km"]["value"]
    cand = grid_io.candidate_mask(g, rule, src, yr)
    rep = grid_io.coverage_repair(g, cand, rule, R, circ, src, yr)
    assert len(rep.uncovered_after) == 0 and len(rep.no_admissible_within_R) == 0
    assert not (cand & ~rep.mask).any() and rep.mask.sum() == cand.sum() + len(rep.added)
    assert rep.mask[rep.added].all() and g.admissible[rep.added].all()
    cov = grid_io.coverage(g, rep.mask, R, circ, src, yr)
    assert cov.covered.all() and cov.share_covered_pop == 1.0


def test_repair_adds_self_for_isolated_cell(grids, params, circ):
    # yapay durum: tek aday en kalabalık hücre; R dışındaki uygun talep hücreleri kendilerini aday yapar
    g = grids[5]
    rule = grid_io.RuleParams.from_params(params)
    pop = g.pop("wp", 2025)
    cand = np.zeros(g.n, dtype=bool)
    cand[np.argmax(pop)] = True
    rep = grid_io.coverage_repair(g, cand, rule, 30.0, circ, "wp", 2025)
    assert len(rep.uncovered_before) > 0 and len(rep.added) > 0
    assert len(rep.uncovered_after) == 0
    top = rep.uncovered_before[np.argmax(pop[rep.uncovered_before])]
    if g.admissible[top]:
        assert rep.added[0] == top


# ---------------------------------------------------------------- yardımcılar ve bağımlılıklar
def test_largest_remainder():
    m = _grids_module()
    vals = np.array([1.4, 2.6, 0.0, 3.5, 0.5, 2.0])
    grp = np.array([0, 0, 0, 1, 1, 1])
    out = m.largest_remainder(vals, grp, np.array([4, 6]))
    assert out.tolist() == [1, 3, 0, 4, 0, 2]   # grup 1: kalanlar 0,5 = 0,5 → küçük indis kazanır
    with pytest.raises(ValueError):
        m.largest_remainder(np.array([1.0, 0.0]), np.array([0, 0]), np.array([2]))


def test_backend_modules_do_not_import_pipeline_libraries():
    code = ("import sys; import backend.model.grid_io, backend.model.distance; "
            "bad = [m for m in ('geopandas', 'shapely', 'rasterio', 'pyproj', 'pandas') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=common.ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    allowed = {"numpy", "scipy", "json", "math", "dataclasses", "pathlib", "typing", "__future__"}
    for f in ("distance.py", "grid_io.py"):
        src = (common.ROOT / "backend" / "model" / f).read_text(encoding="utf-8")
        mods = set(re.findall(r"^(?:from|import)\s+([a-zA-Z_][\w]*)", src, flags=re.M))
        assert mods - {"."} <= allowed, (f, mods - allowed)


def _j_table_text(rows: list[dict]) -> str:
    """izgara.yaml notlarındaki |J| tablosu yazımı: 'P_min → kurallı / kuralsız' (eşitse tek sayı)."""
    parts = []
    for r in rows:
        a, b = r["J_with_near"], r["J_without_near"]
        parts.append(f"{common.fmt_tr(r['P_min'])} → {common.fmt_tr(a)}" + ("" if a == b else f" / {common.fmt_tr(b)}"))
    return "; ".join(parts)


def test_izgara_yaml_numbers_match_summary(summary):
    # izgara.yaml notlarındaki sayılar grid_summary.json'dan (pipeline/22_grids.py) gelir
    items = yaml.safe_load((common.PARAMS_DIR / "izgara.yaml").read_text(encoding="utf-8"))
    text = json.dumps(items, ensure_ascii=False)
    assert "Faz 2'de" not in text and "GEÇİCİ" not in text and "candidate_near_pop" not in text
    s3 = summary["sizes"]["3km"]
    for v in (s3["n_cells"], s3["n_demand_cells"]["wp_2025"], s3["coverage_default"]["J_repaired"]):
        assert common.fmt_tr(v) in text, v
    by_key = {it["key"]: it for it in items}
    notes = " ".join(by_key["grid.candidate_min_pop"]["notes"].split())
    for s in SIZES:
        tab = _j_table_text(summary["sizes"][f"{s}km"]["J_table"]["rows"])
        assert f"{s} km: {tab}" in notes, (s, tab)
    J = " / ".join(common.fmt_tr(summary["sizes"][f"{s}km"]["coverage_default"]["J_repaired"]) for s in SIZES)
    assert f"|J| {J}" in " ".join(by_key["grid.cell_km"]["notes"].split())
    k3 = summary["sizes"]["3km"]["neighbors_default"]
    knotes = " ".join(by_key["grid.k_nearest"]["notes"].split())
    for v in (k3["nnz_all_within_R"], k3["nnz_k_nearest"], k3["k_list_tie_extra_pairs"]):
        assert common.fmt_tr(v) in knotes, v


def test_arsa_yaml_density_from_summary(grids, summary):
    # arsa.yaml'daki 3 km yoğunluk dağılımı grid_summary.json'dan (density_stats); Faz 2 sözü kalmadı
    g = grids[3]
    pop = g.pop("wp", 2025)
    d = summary["sizes"]["3km"]["density_per_km2"]
    dem = pop > 0
    dens = pop[dem] / g.area_km2[dem]
    assert d["demand_cells"]["n_cells"] == int(dem.sum())
    assert d["demand_cells"]["median"] == pytest.approx(np.median(dens))
    assert d["demand_cells"]["p90"] == pytest.approx(np.percentile(dens, 90))
    assert d["demand_cells"]["max"] == pytest.approx(dens.max())
    assert d["candidate_cells"]["n_cells"] == summary["sizes"]["3km"]["coverage_default"]["J_repaired"]
    text = " ".join(json.dumps(yaml.safe_load((common.PARAMS_DIR / "arsa.yaml").read_text(encoding="utf-8")),
                               ensure_ascii=False).split())
    assert "Faz 2" not in text
    for grp in ("demand_cells", "candidate_cells"):
        for q in ("median", "p90", "max"):
            v = d[grp][q]
            assert common.fmt_tr(v, 1 if v < 100 else 0) in text, (grp, q)
