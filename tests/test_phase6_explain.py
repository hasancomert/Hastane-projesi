"""Faz 6 — açıklama katmanının motor yardımcıları (backend/model/explain.py) oyuncak örneklerde, kesin çözümlerle:

  * kısmi sınıf seçimli değerlendirme motorun evaluate()'iyle aynı problemi çözer;
  * "bu hastane kapatılırsa" hızlı tahmini, konumlar yeniden optimize edildiğinde (j yasakken kesin MILP) gerçek artışın
    ÜST sınırıdır; yeniden çözüm (reoptimize_closed) hızlı tahminden kötü olamaz;
  * N ± 1 noktaları sabit_p modunun kesin optimumuna eşit; p taramasının en iyisi temel (kanıtlı) optimum;
  * Pareto: asıl λ ile toplam f = 1'de en küçük, açılış maliyeti f ile azalmaz, ortalama mesafe artmaz;
  * tornado: her satırda iki simetrik çalışma (× 0,8 ve × 1,2), tekdüze parametrelerde farkın işareti doğru;
  * boyut nedenleri, gölge fiyatlar ve model nedenleri çözümle ve kayıtla tutarlı; Türkçe sayı biçimi.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase6_explain.py
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from backend.model import explain as X
from backend.model import explain_common as XC
from backend.model import instance, milp
from backend.model.evaluate import evaluate
from backend.model.params import Params

from .test_phase3_bruteforce import TOY_SIZES

REL = 1e-6


def toy_data(seed: int, n: int):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 24_000, size=(n, 2))
    w = rng.uniform(8_000, 40_000, size=n).round()
    return xy, w


def make_toy(seed: int = 7, n: int = 8, extra: dict | None = None, locks=None, R: float | None = 15.0):
    ov = dict(TOY_SIZES)
    ov["size.city_enabled"] = False
    ov["access.max_distance_km"] = 15.0
    ov.update(extra or {})
    p = Params.load(ov)
    xy, w = toy_data(seed, n)
    return instance.toy_instance(demand_xy=xy, w=w, cand_xy=xy, params=p, R_km=R, k=None, locks=locks), (xy, w)


def factory(xy, w):
    """Parametrelerden aynı geometriyle oyuncak örnek (R parametreden: yapı da değişebilir)."""
    return lambda pr: instance.toy_instance(demand_xy=xy, w=w, cand_xy=xy, params=pr, R_km=None, k=None)


def exact(inst):
    r = milp.MilpModel(inst).solve(gap=0.0, abs_gap=1e-9, time_limit=60)
    return r if r.status == "Optimal" else None


def with_params(inst, **ov):
    p = inst.params.with_overrides(ov)
    return instance.build_instance(inst.s.cell_km, p, inst.locks, structure=inst.s)


@pytest.fixture(scope="module")
def base():
    inst, (xy, w) = make_toy()
    r = exact(inst)
    assert r is not None
    sol = r.solution
    bounds = {"ub_tl": r.primal_tl, "lb_tl": r.primal_tl}          # kanıtlı optimum (gap 0)
    return inst, sol, bounds, (xy, w)


# ---------------------------------------------------------------------- değerlendirme
def test_evaluate_partial_matches_evaluate(base):
    inst, sol, _, _ = base
    free = evaluate(inst, sol.open_j, gap=1e-9)
    part = X.evaluate_partial(inst, sol.open_j, np.full(sol.n_open, -1), gap=1e-9)
    assert free.ok and part.ok
    assert part.objective_tl == pytest.approx(free.objective_tl, rel=REL)
    fixed = evaluate(inst, sol.open_j, cls=sol.cls)
    part2 = X.evaluate_partial(inst, sol.open_j, sol.cls)
    assert part2.objective_tl == pytest.approx(fixed.objective_tl, rel=REL)
    # sonuçtaki hastanelerden (hücre, sınıf, yatak) kurulan çözüm aynı değeri verir
    hs = [{"cell_id": str(inst.s.cand_ids[j]), "class": inst.classes[int(k)].code, "beds": float(b)}
          for j, k, b in zip(sol.open_j, sol.cls, sol.beds)]
    ev = X.hospital_solution(inst, hs)
    assert ev.ok and ev.objective_tl == pytest.approx(sol.objective_tl(inst), rel=REL)


# ---------------------------------------------------------------------- 7.4 kapatma
def test_closure_estimate_upper_bounds_reoptimised_increase(base):
    """Hızlı tahmin ve kesin değerlendirme kalan hastanelerle olurlu çözümlerdir: Δ(tahmin) ≥ Δ(kesin) ≥ Δ(j yasakken
    kesin MILP optimumu) − tolerans. Olurlu olmayan kapatmalarda kalan hastanelerle atama gerçekten yoktur."""
    inst, sol, bounds, _ = base
    opt = sol.objective_tl(inst)
    n_feas = n_r = 0
    for t in range(sol.n_open):
        q = X.closure_effect(inst, sol, t, base_bounds=bounds)
        e = X.closure_effect(inst, sol, t, base_bounds=bounds, exact=True)
        cid = str(inst.s.cand_ids[sol.open_j[t]])
        if not q["feasible"]:
            assert q["reason"] in ("R", "k_listesi", "kapasite") and not e["feasible"]
            ev = evaluate(inst, np.delete(sol.open_j, t))
            assert not ev.ok
            n_r += 1
            continue
        n_feas += 1
        assert q["delta_tl"] >= e["delta_tl"] - REL * opt
        forb = exact(instance.build_instance(3, inst.params, {"closed": [cid]}, structure=inst.s))
        assert forb is not None
        true_delta = forb.primal_tl - opt
        assert e["delta_tl"] >= true_delta - REL * opt, (cid, e["delta_tl"], true_delta)
        assert true_delta >= -REL * opt                     # temel çözüm kanıtlı optimum: yasak onu iyileştiremez
        # kapatılan hastanenin hastaları başka hastanelere dağıtılır (paylar toplamı 1)
        assert sum(a["share"] for a in q["absorbers"]) == pytest.approx(1.0, abs=1e-6)
        assert q["displaced_population"] == pytest.approx(sum(a["absorbed_population"] for a in q["absorbers"]),
                                                           rel=1e-6)
        assert q["fark_gap_icinde"] is (q["delta_tl"] <= 1e-9 * opt)   # temel gap 0
        assert "_solution" in q and q["message_tr"]
        # tam yeniden çözüm hızlı tahminden kötü olamaz ve kesin optimumdan iyi olamaz
        ro = X.reoptimize_closed(inst, sol, t, q, time_limit=10, gap=1e-9, base_bounds=bounds)
        assert ro["ok"] and ro["delta_tl"] <= q["delta_tl"] + REL * opt
        assert ro["delta_tl"] >= true_delta - REL * opt
    assert n_feas >= 1


def test_closure_reports_r_forced_hospital():
    """Tek başına uzak bir hücre: kendi hastanesi kapatılırsa R içinde hastanesi kalmaz → reason 'R'."""
    xy = np.array([[0, 0], [2000, 1000], [1000, 3000], [3000, 2500], [60_000, 60_000]], dtype=float)
    w = np.array([30_000, 25_000, 20_000, 28_000, 9_000], dtype=float)
    ov = dict(TOY_SIZES, **{"size.city_enabled": False})
    inst = instance.toy_instance(demand_xy=xy, w=w, cand_xy=xy, params=Params.load(ov), R_km=15.0, k=None)
    sol = exact(inst).solution
    far = int(np.flatnonzero(sol.open_j == 4)[0])
    c = X.closure_effect(inst, sol, far)
    assert c["feasible"] is False and c["reason"] == "R"
    assert c["uncovered"]["cells"] == 1 and c["uncovered"]["population"] == pytest.approx(9_000)
    assert c["uncovered"]["nearest_other_km_max"] > 15.0
    sc = X.sole_cover(inst, sol)
    assert sc["cells_R"][far] == 1 and sc["cells"][far] == 1
    md = X.model_reasons(inst, sol)
    assert md["r_forced"]["n"] >= 1 and "c4" in md["r_forced"]["cells"]


# ---------------------------------------------------------------------- 7.3 N ± 1 ve 7.7 p taraması
def test_n_pm1_equals_exact_fixed_p_optimum(base):
    inst, sol, bounds, _ = base
    ref = X.reference_of(inst, sol, bounds)
    for p in (sol.n_open - 1, sol.n_open + 1):
        pt, s2 = X.best_with_p(inst, sol, p, ref=ref, time_limit=10, gap=1e-9)
        ex = exact(with_params(inst, **{"solver.objective_mode": "sabit_p", "solver.p_fixed": p}))
        if ex is None:
            assert not pt.get("ok")
            continue
        assert pt["ok"] and pt["ub_tl"] == pytest.approx(ex.primal_tl, rel=1e-6)
        assert pt["fark_tl"] == pytest.approx(ex.primal_tl - ref["ub_tl"], abs=REL * ref["ub_tl"])
        assert pt["fark_tl"] >= -REL * ref["ub_tl"]                 # serbest optimum kanıtlı
        # temel alt sınır (serbest problem) her p için geçerli: lb ≥ temel lb; işaret kuralı
        assert pt["lb_tl"] >= bounds["lb_tl"] * (1 - 1e-12)
        assert pt["fark_gap_icinde"] is bool(pt["lb_tl"] <= ref["ub_tl"] * (1 + 1e-12))
        assert s2 is not None and s2.n_open == p
        msg = XC.n_message(pt, ref)
        assert str(p) in msg and "milyar TL" in msg


def test_p_sweep_best_is_proven_optimum(base):
    inst, sol, bounds, _ = base
    ref = X.reference_of(inst, sol, bounds)
    n = sol.n_open
    ps = [p for p in range(n - 2, n + 3) if p >= 1]
    seen = []
    res = X.p_sweep(inst, sol, ps, ref=ref, time_limit=10, gap=1e-9, on_point=lambda pt, d, t: seen.append((d, t)))
    assert res["status"] == "tamam" and [d for d, _ in seen] == list(range(1, len(ps) + 1))
    assert res["best_p"] == n
    pts = {q["p"]: q for q in res["points"]}
    assert pts[n]["referans"] and pts[n]["fark_tl"] == 0
    for p, q in pts.items():
        if p != n and q.get("ok"):
            assert q["ub_tl"] >= ref["ub_tl"] * (1 - REL)
            assert q["fark_gap_icinde"] is bool(q["lb_tl"] <= ref["ub_tl"] * (1 + 1e-12))
    assert n in res["plateau_p"] and res["summary_tr"]


def test_apply_reference_flags_and_intervals():
    ref = {"n": 89, "ub_tl": 100.0, "lb_tl": 99.0, "free_lb_valid": True, "opening_tl": 90.0, "access_tl": 10.0}
    pt = {"p": 88, "ok": True, "ub_tl": 100.5, "lb_tl": 98.0, "opening_tl": 89.0, "access_tl": 11.5}
    XC.apply_reference(pt, ref)
    assert pt["lb_tl"] == 99.0 and pt["lb_source"] == "serbest_alt_sinir"    # serbest alt sınır geçerli
    assert pt["fark_gap_icinde"] is True and pt["fark_tl"] == pytest.approx(0.5)
    assert pt["fark_lo_tl"] == pytest.approx(-1.0) and pt["fark_hi_tl"] == pytest.approx(1.5)
    assert pt["delta_opening_tl"] == pytest.approx(-1.0) and pt["delta_access_tl"] == pytest.approx(1.5)
    pt2 = {"p": 90, "ok": True, "ub_tl": 103.0, "lb_tl": 101.0, "opening_tl": 92.0, "access_tl": 11.0}
    XC.apply_reference(pt2, ref)
    assert pt2["fark_gap_icinde"] is False and pt2["lb_tl"] == 101.0
    assert "kanıtlı olarak en az" in XC.n_message(pt2, ref)
    # temel çözüm sabit_p modundaysa (free_lb_valid False) temel alt sınır başka p'ye uygulanmaz
    pt3 = {"p": 88, "ok": True, "ub_tl": 100.5, "lb_tl": 98.0, "opening_tl": 89.0, "access_tl": 11.5}
    XC.apply_reference(pt3, dict(ref, free_lb_valid=False))
    assert pt3["lb_tl"] == 98.0


# ---------------------------------------------------------------------- 7.8 Pareto
def test_pareto_weighted_sum_properties(base):
    inst, sol, bounds, (xy, w) = base
    hs = [{"cell_id": str(inst.s.cand_ids[j]), "class": inst.classes[int(k)].code, "beds": float(b)}
          for j, k, b in zip(sol.open_j, sol.cls, sol.beds)]
    fs = [0.25, 0.5, 2.0, 4.0]
    seen = []
    res = X.pareto(3, dict(inst.params.overrides), None, fs, time_limit=10, gap=1e-9,
                   base={"hospitals": hs, "bounds": bounds}, make_instance=factory(xy, w),
                   on_point=lambda pt, d, t: seen.append(pt["factor"]))
    assert res["status"] == "tamam" and seen[0] == 1.0 and sorted(seen) == sorted(fs + [1.0])
    pts = sorted((q for q in res["points"] if q["ok"]), key=lambda q: q["factor"])
    assert len(pts) == 5
    cur = next(q for q in pts if q["factor"] == 1.0)
    assert cur["source"] == "mevcut_cozum" and cur["total_tl"] == pytest.approx(sol.objective_tl(inst), rel=REL)
    for q in pts:
        # her nokta asıl problem için de olurlu: asıl toplam f = 1'de en küçük
        assert q["total_tl"] >= cur["total_tl"] * (1 - REL)
        assert q["lambda_tl_per_person_km"] == pytest.approx(inst.params.lam * q["factor"], rel=1e-9)
    for a, b in zip(pts, pts[1:]):                      # ağırlıklı toplamın tekdüzeliği (kesin çözümlerde)
        assert b["opening_tl"] >= a["opening_tl"] * (1 - REL)
        assert b["pop_weighted_mean_km"] <= a["pop_weighted_mean_km"] * (1 + REL)
    assert res["current_is_min_total"] is True and res["summary_tr"]


# ---------------------------------------------------------------------- 7.9 tornado
def test_tornado_symmetric_runs_and_signs(base):
    inst, sol, bounds, (xy, w) = base
    b = {"objective_tl": bounds["ub_tl"], "lb_tl": bounds["lb_tl"], "n_hospitals": sol.n_open,
         "hospitals": [str(c) for c in inst.s.cand_ids[sol.open_j]],
         "classes": [inst.classes[int(k)].code for k in sol.cls],
         "opening_tl": sol.opening_tl(inst), "access_tl": sol.access_tl(inst)}
    keys = ["cost.fixed_per_hospital", "access.max_distance_km", "cost.unit"]
    runs = []
    res = X.tornado(3, dict(inst.params.overrides), None, keys=keys, time_limit=10, gap=1e-9, base=b,
                    make_instance=factory(xy, w), on_run=lambda r, d, t: runs.append(r))
    assert res["status"] == "tamam" and len(runs) == 2 * len(keys) and len(res["rows"]) == len(keys)
    tol = REL * bounds["ub_tl"]
    for row in res["rows"]:
        lo, hi = row["low"], row["high"]
        assert lo["side"] == "dusuk" and hi["side"] == "yuksek"
        assert lo["factor"] == pytest.approx(0.8) and hi["factor"] == pytest.approx(1.2)
        for k in row["keys"]:
            assert lo["values"][k] == pytest.approx(0.8 * row["base"][k]) and hi["values"][k] == pytest.approx(1.2 * row["base"][k])
        assert lo["ok"] and hi["ok"]
        for r in (lo, hi):
            assert r["delta_lo_tl"] - tol <= r["delta_tl"] <= r["delta_hi_tl"] + tol
    rows = {r["id"]: r for r in res["rows"]}
    # maliyet artışı optimumu düşüremez, R'yi büyütmek (gevşetme) artıramaz
    for cid in ("cost.fixed_per_hospital", "cost.unit"):
        assert rows[cid]["high"]["delta_tl"] >= -tol and rows[cid]["low"]["delta_tl"] <= tol
    assert rows["access.max_distance_km"]["high"]["delta_tl"] <= tol
    assert rows["access.max_distance_km"]["low"]["delta_tl"] >= -tol
    sw = [r["swing_pct"] for r in res["rows"]]
    assert sw == sorted(sw, reverse=True) and res["summary_tr"]


def test_tornado_domain_clip():
    v, note = X._domain_clip("access.discount_rate", 1.08)
    assert v == 1.0 and note == "ust_sinir"
    v, note = X._domain_clip("demand.beds_per_1000", 0.0)          # alt sınır dahil değil
    assert v > 0 and note == "alt_sinir"
    v, note = X._domain_clip("cost.fixed_per_hospital", 3e8)
    assert v == 3e8 and note is None
    specs = XC.tornado_specs(Params.load())
    assert len(specs) == len(XC.TORNADO_SPECS) == 16
    assert all(s["label_tr"] for s in specs)
    unit = next(s for s in specs if s["id"] == "cost.unit")
    assert set(unit["keys"]) == {"cost.unit.IV-C", "cost.unit.V-B", "cost.unit.V-C", "cost.unit.V-D"}


# ---------------------------------------------------------------------- 7.5 boyut, 7.6 gölge fiyat, model nedenleri
def test_size_explanations_consistent(base):
    inst, sol, _, _ = base
    sz = X.size_explanations(inst, sol)
    rows = sz["hospitals"]
    assert len(rows) == sol.n_open and sum(sz["counts"].values()) == sol.n_open
    for r, k, b in zip(rows, sol.cls, sol.beds):
        U, L = inst.U[int(k)], inst.L[int(k)]
        if r["reason"] in ("ust_esik", "ust_sinir"):
            assert b == pytest.approx(U, rel=1e-6)
        elif r["reason"] == "alt_sinir":
            assert b == pytest.approx(L, rel=1e-6)
        else:
            assert L < b < U
        if r["reason"] == "ust_esik":
            nx = r["next_class"]
            assert nx["jump_tl"] == pytest.approx(nx["opening_tl"] - r["opening_tl"])
            assert nx["beds"] == inst.L[int(k) + 1]
        assert r["occupancy"] is None or r["occupancy"] <= 1 + 1e-6
        assert r["reason_tr"]


def test_shadow_prices_shapes_and_caveat(base):
    inst, sol, _, _ = base
    sh = X.shadow_prices(inst, sol)
    assert len(sh["marginal_cost_tl"]) == inst.nI and len(sh["bed_value_tl"]) == sol.n_open
    assert "dejenere" in sh["caveat_tr"] and "tek değildir" in sh["caveat_tr"]
    assert sh["per_person"]["p5"] <= sh["per_person"]["median"] <= sh["per_person"]["p95"]
    assert (np.asarray(sh["bed_value_tl"]) >= -1e-6).all()


def test_model_reasons_numbers_from_registry(base):
    inst, sol, _, _ = base
    md = X.model_reasons(inst, sol)
    cls = {c.code: c for c in inst.params.classes}
    for c in md["classes"]:
        assert c["fixed_tl"] == pytest.approx(cls[c["code"]].fixed) and c["per_bed_tl"] == pytest.approx(cls[c["code"]].var)
    small = cls["IV-C"]
    for s in md["splits"]:
        k = cls[s["single_class"]]
        assert s["single_tl"] == pytest.approx(k.fixed + k.var * s["beds"])
        assert s["small_total_tl"] == pytest.approx(s["n_small"] * small.fixed + small.var * s["beds"])
        assert s["n_small"] == math.ceil(s["beds"] / small.U - 1e-9)
    ids = {r["id"] for r in md["reasons"]}
    assert {"yatak_basi_artiyor", "sabit_kisim", "esik_yigilma", "uzmanlasma"} <= ids
    assert sum(c["n_chosen"] for c in md["classes"]) == sol.n_open


def test_turkish_formatting():
    assert XC.fmt(1234567.891, 1) == "1.234.567,9"
    assert XC.fmt(-0.04, 1) == "0,0" and XC.fmt(-2.5, 1) == "−2,5"
    assert XC.tl(381_314_289_391.98) == "381,31 milyar TL"
    assert XC.tl(3_900_000, signed=True) == "+3,9 milyon TL" and XC.tl(-483_000_000) == "−483,0 milyon TL"
    assert XC.pct(0.0006) == "%0,06" and XC.pct(-0.0095, 2) == "−%0,95" and XC.pct(0.033, 2, True) == "+%3,30"
    assert XC.p_ranges([82, 83, 84, 86, 88, 89, 90]) == "82–84, 86, 88–90"
