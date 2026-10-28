"""Faz 6 — saklanan açıklama çıktıları (precomputed/explain_*, pareto_*, tornado_*; pipeline/35_explanations.py):
temel çözümle ve kayıtla tutarlılık, N ± 1 noktalarının p taramasıyla aynı olması, kapatma tahmininin yeniden çözümün
üst sınırı olması, R'nin zorladığı hastanelerin gerçekten R dışında hücre bırakması, Pareto'da asıl toplamın f = 1'de
en küçük olması (gap'ler içinde), tornado çalışmalarının simetrik olması ve fark aralıklarının tutarlılığı.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase6_outputs.py
"""
from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from backend.model.params import Params

ROOT = Path(__file__).resolve().parents[1]
PRE = ROOT / "precomputed"


def _load(name: str) -> dict:
    p = PRE / f"{name}.json"
    if not p.exists():
        pytest.skip(f"{p.name} yok (pipeline/35_explanations.py)")
    return orjson.loads(p.read_bytes())


@pytest.mark.parametrize("km", [3, 5])
def test_explain_default_consistent(km):
    d = _load(f"explain_{km}km")
    base = orjson.loads((PRE / f"default_{km}km.json").read_bytes())
    assert d["params_hash"] == Params.load({"grid.cell_km": km}).hash() == base["params_hash"]
    assert [h["cell_id"] for h in d["base"]["hospitals"]] == [h["cell_id"] for h in base["hospitals"]]
    ub = d["base_summary"]["ub_tl"]
    assert ub == pytest.approx(base["bounds"]["ub_tl"], rel=1e-9)
    closes = [i for i in d["items"] if i["kind"] == "hastane_kapat"]
    assert {i["cell_id"] for i in closes} == {h["cell_id"] for h in base["hospitals"]}       # her hastane
    gap_abs = d["base"]["bounds"]["ub_tl"] - d["base"]["bounds"]["lb_tl"]
    for i in closes:
        assert i["message_tr"]
        if i["feasible"]:
            assert i["objective_tl"] == pytest.approx(i["base_objective_tl"] + i["delta_tl"])
            assert i["fark_gap_icinde"] is bool(i["delta_tl"] <= gap_abs + 1e-9 * ub)
            assert i["delta_tl"] >= -gap_abs - 1e-6 * ub        # kalan hastanelerle çözüm alt sınırın altına inemez
            if d["settings"]["exact"]:
                assert i["method"] == "kesin_sinif_secimi"
                assert i["quick_objective_tl"] >= i["objective_tl"] * (1 - 1e-9)   # hızlı tahmin ≥ kesin
            ro = i.get("reopt")
            if ro and ro.get("ok"):
                # konumlar yeniden optimize edilince artış hızlı tahminden büyük olamaz (tahmin bir üst sınır)
                assert ro["delta_tl"] <= i["delta_tl"] + 1e-6 * ub
                assert ro["lb_tl"] is None or ro["lb_tl"] >= d["base"]["bounds"]["lb_tl"] * (1 - 1e-12)
        else:
            assert i["reason"] in ("R", "k_listesi")
            u = i["uncovered"]
            assert u["cells"] >= 1
            if i["reason"] == "R":
                assert u["within_R_geometric"] < u["cells"] and u["nearest_other_km_max"] > 30.0
            else:
                assert u["within_R_geometric"] == u["cells"]
    cs = d["closure_summary"]
    assert cs["n"] == len(closes) == cs["n_feasible"] + cs["n_r_forced"] + cs["n_k_list_forced"]
    model = next(i for i in d["items"] if i["kind"] == "model")
    assert model["r_forced"]["n"] == cs["n_r_forced"]
    size = next(i for i in d["items"] if i["kind"] == "boyut")
    at = {int(k): v for k, v in size["at_bound_beds"].items()}
    kb = base["kpis"]
    assert sum(size["counts"].values()) == kb["n_hospitals"]
    hs = base["hospitals"]
    assert at.get(199, 0) == sum(1 for h in hs if h["class"] == "IV-C" and abs(h["beds"] - 199) < 1e-6)
    assert at.get(399, 0) == sum(1 for h in hs if h["class"] == "V-B" and abs(h["beds"] - 399) < 1e-6)


@pytest.mark.parametrize("km", [3, 5])
def test_explain_n_pm1_equals_p_sweep(km):
    """N ± 1 karşılaştırması uzun çözülmüş p taramasından: aynı üst/alt sınır, aynı fark ve 'fark gap içinde'."""
    d = _load(f"explain_{km}km")
    sweep = orjson.loads((PRE / f"p_sweep_{km}km.json").read_bytes())
    pts = {q["p"]: q for q in sweep["points"]}
    n = d["base_summary"]["n_hospitals"]
    for kind, p in (("n_eksi_1", n - 1), ("n_arti_1", n + 1)):
        it = next(i for i in d["items"] if i["kind"] == kind)
        q = pts[p]
        assert it["p"] == p and it["source"] == "p_taramasi"
        assert it["ub_tl"] == q["ub_tl"] and it["lb_tl"] == pytest.approx(q["lb_tl"])
        assert it["fark_tl"] == pytest.approx(q["fark_tl"], abs=1.0)
        assert it["fark_gap_icinde"] == q["fark_gap_icinde"]
        assert it["delta_opening_tl"] + it["delta_access_tl"] == pytest.approx(it["fark_tl"], abs=1e3)
    # 3 km: dip düz (fark gap içinde), 5 km: kanıtlı tek en iyi
    flags = [next(i for i in d["items"] if i["kind"] == k)["fark_gap_icinde"] for k in ("n_eksi_1", "n_arti_1")]
    assert flags == ([True, True] if km == 3 else [False, False])


@pytest.mark.parametrize("km", [3, 5])
def test_pareto_stored(km):
    d = _load(f"pareto_{km}km")
    assert d["params_hash"] == Params.load({"grid.cell_km": km}).hash()
    pts = [q for q in d["points"] if q.get("ok")]
    assert len(pts) == len(d["points"]) == len(d["settings"]["factors"])
    cur = next(q for q in pts if q["factor"] == 1.0)
    base = orjson.loads((PRE / f"default_{km}km.json").read_bytes())
    assert cur["source"] == "mevcut_cozum" and cur["n_hospitals"] == base["kpis"]["n_hospitals"]
    assert cur["opening_tl"] == pytest.approx(base["costs"]["acilis"], rel=1e-9)
    lam = Params.load({"grid.cell_km": km}).lam
    for q in pts:
        assert q["lambda_tl_per_person_km"] == pytest.approx(lam * q["factor"], rel=1e-9)
        assert q["total_tl"] == pytest.approx(q["opening_tl"] + q["access_tl"])
        # her nokta asıl problem için olurlu: asıl toplamı asıl optimumun alt sınırının altına inemez
        assert q["total_tl"] >= base["bounds"]["lb_tl"] * (1 - 1e-12)
    # açılış maliyeti λ ile kabaca artar, ortalama mesafe azalır (uçlar arasında)
    lo, hi = min(pts, key=lambda q: q["factor"]), max(pts, key=lambda q: q["factor"])
    assert hi["opening_tl"] > lo["opening_tl"] and hi["pop_weighted_mean_km"] < lo["pop_weighted_mean_km"]
    assert d["summary_tr"]


@pytest.mark.parametrize("km", [3, 5])
def test_tornado_stored_symmetric(km):
    d = _load(f"tornado_{km}km")
    assert d["params_hash"] == Params.load({"grid.cell_km": km}).hash()
    assert len(d["rows"]) == 16 and d["delta"] == 0.2
    ub0 = d["base"]["objective_tl"]
    for r in d["rows"]:
        lo, hi = r["low"], r["high"]
        assert lo["side"] == "dusuk" and hi["side"] == "yuksek"
        assert lo["factor"] == pytest.approx(0.8) and hi["factor"] == pytest.approx(1.2)
        for k in r["keys"]:
            for run, f in ((lo, 0.8), (hi, 1.2)):
                if not (run.get("clipped") or {}).get(k):
                    assert run["values"][k] == pytest.approx(f * r["base"][k])
        for run in (lo, hi):
            assert run["ok"], run
            assert run["delta_tl"] == pytest.approx(run["objective_tl"] - ub0)
            assert run["delta_lo_tl"] <= run["delta_tl"] + 1.0 <= run["delta_hi_tl"] + 2.0
    sw = [r["swing_pct"] for r in d["rows"]]
    assert sw == sorted(sw, reverse=True)
    rows = {r["id"]: r for r in d["rows"]}
    # tekdüze parametreler: maliyet artarsa toplam azalmaz (gerçek fark aralığının üst ucu > 0)
    for cid in ("cost.fixed_per_hospital", "cost.unit", "demand.beds_per_1000"):
        assert rows[cid]["high"]["delta_hi_tl"] > 0 and rows[cid]["low"]["delta_lo_tl"] < 0
