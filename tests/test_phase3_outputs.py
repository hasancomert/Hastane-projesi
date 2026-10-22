"""Faz 3 — saklanan çıktıların denetimi (yeniden çözmeden): varsayılan çözümler, p taraması (kabul ölçütü), sınır kartları,
kıyaslama dosyası.

Kabul ölçütü (şartname 10): "Varsayılan parametrelerde p taramasının en iyisi = serbest p'li çözüm" — saklanan
precomputed/p_sweep_{3,5}km.json üzerinde, çözümlerin sertifikalı gap'leri içinde:
  * hiçbir p'nin çözümü serbest çözümün alt sınırının altında değil (alt sınır geçerli),
  * taramanın en iyi değeri serbest optimumla tolerans (gap'lerin en büyüğü) içinde aynı,
  * serbest optimumun hastane sayısı, alt sınırları serbest çözümün değerini aşmayan p'ler (düz dip) arasında.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase3_outputs.py
"""
from __future__ import annotations

import json

import pytest

from pipeline import common

PRE = common.ROOT / "precomputed"
SPEC_TARGET_S = {3: 15.0, 2: 60.0}


def _load(path):
    if not path.exists():
        pytest.fail(f"{path.relative_to(common.ROOT)} yok — pipeline/32_precompute.py çalıştırılmalı")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("km", [5, 3, 2])
def test_default_solutions_complete_and_checked(km):
    r = _load(PRE / f"default_{km}km.json")
    assert r["ok"] and r["grid_km"] == km and r["mode"] == "toplam"
    assert r["overrides"] == {"grid.cell_km": km}                    # varsayılan parametreler (yalnız ızgara seçimi)
    assert r["check"]["ok"], r["check"]["violations"]
    b, c, k = r["bounds"], r["costs"], r["kpis"]
    assert b["lb_tl"] <= b["ub_tl"]
    assert r["status"] in ("optimal", "sure_doldu")
    assert (r["status"] == "optimal") == (b["gap"] <= r["target_gap"] + 1e-9)
    assert b["gap"] <= 0.01
    assert c["toplam"] == pytest.approx(c["acilis"] + c["erisim"]) == pytest.approx(b["objective_tl"])
    assert sum(k["count_by_class"].values()) == k["n_hospitals"] == len(r["hospitals"])
    assert k["total_beds"] >= k["total_demand_beds"] * (1 - 1e-9)
    assert k["count_by_class"]["IV-C"] > k["n_hospitals"] / 2        # yatak başı maliyet sınıfla artıyor
    assert len(r["duals"]["marginal_cost_tl"]) == r["instance"]["I"]
    assert "Alt sınır L" in r["certificate_tr"]


@pytest.mark.parametrize("km", [3, 5])
def test_p_sweep_best_equals_free_optimum(km):
    sw = _load(PRE / f"p_sweep_{km}km.json")
    free = sw["free"]
    d = _load(PRE / f"default_{km}km.json")
    assert free["ub_tl"] == pytest.approx(d["bounds"]["ub_tl"]) and free["n_hospitals"] == d["kpis"]["n_hospitals"]
    pts = [p for p in sw["points"] if p["ok"]]
    assert len(pts) >= 20
    ps = [p["p"] for p in pts]
    assert min(ps) < free["n_hospitals"] < max(ps) and free["n_hospitals"] in ps
    for p in pts:
        assert p["check_ok"] and len(p["hospitals"]) == p["p"]
        assert p["lb_tl"] <= p["ub_tl"] * (1 + 1e-12)
        assert p["total_tl"] == pytest.approx(p["opening_tl"] + p["access_tl"])
        assert p["ub_tl"] >= free["lb_tl"] * (1 - 1e-12)                 # alt sınır geçerli
    best = min(pts, key=lambda p: p["ub_tl"])
    at_free = next(p for p in pts if p["p"] == free["n_hospitals"])
    tol = max(free["gap"], best["gap"], at_free["gap"])
    assert tol <= 0.01
    assert abs(best["ub_tl"] - free["ub_tl"]) <= tol * free["ub_tl"]
    plateau = [p["p"] for p in pts if p["lb_tl"] <= free["ub_tl"] * (1 + 1e-12)]
    assert free["n_hospitals"] in plateau and best["p"] in plateau
    # U şekli: uç noktalar dipten belirgin pahalı; az hastanede erişim, çok hastanede açılış maliyeti yüksek
    # (az hastanede açılış da yüksek: büyük sınıfların yatak başı maliyeti yüksek — verinin özelliği)
    lo, hi = pts[0], pts[-1]
    assert lo["ub_tl"] > best["ub_tl"] * 1.005 and hi["ub_tl"] > best["ub_tl"] * 1.005
    assert lo["access_tl"] > best["access_tl"] and hi["opening_tl"] > best["opening_tl"]
    assert sw["acceptance"]["passed"]


def test_bound_cards_all_grids():
    bc = _load(common.PROCESSED / "sinir_kartlari.json")["grids"]
    exp_J = {"5": 454, "3": 634, "2": 891}
    for km, card in bc.items():
        assert card["max_hospitals_grid"] == exp_J[km]
        assert card["capacity"]["with_city"] == 5 and card["capacity"]["without_city"] == 19
        ls, pm = card["coverage_lscp"], card["p_min"]
        assert ls["value"] is not None and ls["lower_bound"] <= ls["value"]
        assert pm["value"] is not None and pm["lower_bound"] <= pm["value"]
        assert pm["lower_bound"] >= max(ls["lower_bound"], card["capacity"]["with_city"])
        d = PRE / f"default_{km}km.json"
        if d.exists():
            assert pm["value"] <= json.loads(d.read_text(encoding="utf-8"))["kpis"]["n_hospitals"] <= exp_J[km]


def test_benchmark_file_complete():
    """Kıyaslama dosyası eksiksiz ve tutarlı (hedeflere uyum raporda yorumlanır; burada yalnız veri denetlenir)."""
    b = _load(common.PROCESSED / "solver_benchmark.json")
    for km in ("5", "3", "2"):
        g = b["grids"][km]
        agg = g["aggregate"]
        assert agg["cold"]["all_checks_ok"] and agg["warm"]["all_checks_ok"]
        assert agg["cold"]["runs"] == len(g["runs"]) >= 3
        for run in g["runs"]:
            c = run["cold"]
            assert c["lb_tl"] <= c["ub_tl"] and c["lb_source"] == "lagrange"
            assert (c["status"] == "optimal") == (c["gap"] <= 0.01 + 1e-9)
            t1 = c["time_to_gap_s"]["0.01"]
            assert t1 is None or t1 <= c["times"]["total_s"] + 1e-6
        assert agg["target_s"] == SPEC_TARGET_S.get(int(km))
    assert "trace" in b["grids"]["3"]["runs"][0]["cold"]
