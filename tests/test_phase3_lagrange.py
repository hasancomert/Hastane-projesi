"""Faz 3 — Lagrange gevşetmesi, çekirdek MIP, sınıf başına bağlantı satırları ve canlı çözüm hedefi.

  * alt problem (aday başına kesirli sırt çantası) bağımsız LP kurulumuyla (scipy linprog) aynı G_jk'yı verir;
  * L(μ) ≤ z* (kaba kuvvet optimumu) — rastgele ve yakınsamış μ, tüm modlar ve kilitler;
  * sınıf başına bağlantı satırları geçerli (MILP = kaba kuvvet) ve LP sınırını yükseltir;
  * çekirdek örnek: incumbent çekirdekte olurlu, çekirdek çözümü tam modelde olurlu;
  * 5 km: Lagrange sınırı ≤ bilinen en iyi çözüm, %1 sertifika; 3 km canlı çözüm 15 sn hedefinde;
  * k = 0 (R içindeki tüm adaylar) yapısı neighbor_lists(k=None) ile aynı; iptal çekirdek MIP sırasında da çalışır.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase3_lagrange.py
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest
from scipy.optimize import linprog

from backend.model import checks, distance, instance, lagrange, milp
from backend.model.params import Params
from backend.model.solve import solve
from tests.test_phase3_bruteforce import brute_force, toy

BEST_KNOWN_5KM_TL = 402.8232e9      # Faz 3 temel ölçümü (tam MILP, gap %0,105) — k = 50 modelinin bir çözümü


def _reference_G(inst, mu_scaled: np.ndarray) -> np.ndarray:
    """Her (j, k) için alt problem LP'si (bağımsız kurulum): min F + V·B + Σ (c_a − μ_i)·x_a,
    Σ q_i x_a ≤ B, L_k ≤ B ≤ U_k, 0 ≤ x ≤ 1 (milyon TL)."""
    s = inst.s
    G = np.full((inst.nJ, inst.nK), np.inf)
    for j in range(inst.nJ):
        arcs = np.flatnonzero(s.arc_j == j)
        r = inst.acc[arcs] / 1e6 - mu_scaled[s.arc_i[arcs]]
        q = inst.q[s.arc_i[arcs]]
        for k in range(inst.nK):
            if not inst.y_ub[j, k]:
                continue
            c = np.concatenate([[inst.var[j, k] / 1e6], r])
            A = np.concatenate([[-1.0], q])[None, :]
            bounds = [(inst.L[k], inst.U[k])] + [(0.0, 1.0)] * len(arcs)
            res = linprog(c, A_ub=A, b_ub=[0.0], bounds=bounds, method="highs")
            assert res.status == 0
            G[j, k] = inst.fixed[j, k] / 1e6 + res.fun
    return G


@pytest.mark.parametrize("seed,n,K", [(1, 6, 2), (3, 5, 4), (7, 6, 3)])
def test_subproblem_equals_reference_lp(seed, n, K):
    inst = toy(seed, n, K, 15.0)
    sub = lagrange.Subproblem(inst)
    rng = np.random.default_rng(seed)
    for _ in range(4):
        mu = rng.uniform(0, 3, inst.nI) * inst.q * 20.0 + rng.normal(0, 50, inst.nI)
        S = sub.solve(mu)
        ref = _reference_G(inst, mu)
        fin = np.isfinite(ref)
        assert np.array_equal(fin, np.isfinite(S.G))
        assert np.allclose(S.G[fin], ref[fin], rtol=1e-7, atol=1e-6)
        assert S.L == pytest.approx(mu.sum() + S.Gmin[S.open_].sum(), rel=1e-12)


TOY_MODES = [
    ({}, None),
    ({"solver.objective_mode": "sabit_p", "solver.p_fixed": 4}, None),
    ({"size.min_hospitals": 6}, None),
    ({"size.max_hospitals": 4}, None),
    ({}, {"open": ["c2"], "closed": ["c3"]}),
    ({"access.alpha": 1.5, "land.include": True}, None),
]


@pytest.mark.parametrize("extra,locks", TOY_MODES)
def test_lagrange_bound_never_exceeds_bruteforce_optimum(extra, locks):
    rng = np.random.default_rng(0)
    n_ok = 0
    for seed in (11, 12, 13):
        inst = toy(seed, 6, 3, 15.0, extra, locks=locks)
        z, _ = brute_force(inst)
        if not np.isfinite(z):                                       # bu tohumda mod olurlu değil
            continue
        n_ok += 1
        sub = lagrange.Subproblem(inst)
        for _ in range(25):                                          # rastgele çarpanlar
            mu = rng.uniform(0, 3, inst.nI) * inst.q * 20.0 + rng.normal(0, 50, inst.nI)
            assert sub.solve(mu).L * 1e6 <= z * (1 + 1e-9) + 1.0
        res = lagrange.run(inst, time_limit=3.0, target_gap=0.0, use_cache=False)    # yakınsamış çarpanlar
        assert res.ok and res.bound_tl <= z * (1 + 1e-9) + 1.0
        assert res.bound_tl >= 0.9 * z                               # anlamlı bir sınır
        if res.solution is not None:
            assert checks.check(inst, res.solution)["ok"]
            assert res.solution.objective_tl(inst) >= z * (1 - 1e-9)
    assert n_ok >= 1


def test_budget_mode_not_supported():
    inst = toy(21, 5, 3, 15.0, {"solver.objective_mode": "butce", "solver.budget_tl": 1e12})
    res = lagrange.run(inst, time_limit=1.0)
    assert not res.ok and res.bound_tl is None


def _budget_toy(seed: int, factor: float):
    base = toy(seed, 6, 3, 15.0)
    free_open = milp.MilpModel(base).solve(gap=0.0, abs_gap=1e-9).solution.opening_tl(base)
    return toy(seed, 6, 3, 15.0, {"solver.objective_mode": "butce", "solver.budget_tl": factor * free_open})


@pytest.mark.parametrize("seed,factor", [(21, 1.10), (2, 1.0), (5, 1.25), (7, 1.05)])
def test_budget_lagrange_bound_valid_on_toys(monkeypatch, seed, factor):
    """Bütçe satırının Lagrange gevşetmesi: her ν ≥ 0 ve her μ için L_ν − ν·B ≤ en küçük erişim maliyeti (kaba kuvvet,
    ε = 0); ν araması ve solve() alt sınırı da geçerli; solve() kaba kuvvet optimumunu bulur (gap 1e-4)."""
    from backend.model import budget
    inst = _budget_toy(seed, factor)
    with monkeypatch.context() as mp:
        mp.setattr(milp, "BUDGET_EPS", 0.0)                       # kaba kuvvet: yalnız erişim
        z_acc, _ = brute_force(inst)
    assert np.isfinite(z_acc)
    bf_model, _ = brute_force(inst)                               # erişim + ε·açılış (modelin amacı)
    rng = np.random.default_rng(seed)
    sub = lagrange.Subproblem(inst)
    F0, V0 = sub.F.copy(), sub.V.copy()
    B = inst.budget / 1e6
    for nu in (0.05, 0.3, 0.8, 1.0, 1.7, 4.0, 20.0):
        sub.F, sub.V = F0 * nu, V0 * nu
        for _ in range(5):
            mu = rng.uniform(0, 2, inst.nI) * inst.acc.max() / 1e6
            assert (sub.solve(mu).L - nu * B) * 1e6 <= z_acc * (1 + 1e-9) + 1.0
        res = lagrange.run(budget.scaled_instance(inst, nu), time_limit=0.5, use_cache=False, target_gap=1e-9)
        assert res.bound_tl - nu * inst.budget <= z_acc * (1 + 1e-9) + 1.0
    br = budget.run(inst, time_limit=2.0)
    assert br.ok and br.lb_tl <= z_acc * (1 + 1e-9) + 1.0
    r = solve(instance=inst, time_limit=30, gap=1e-4)
    assert r["ok"] and r["check"]["ok"] and r["check"]["checks"]["butce"]
    b = r["bounds"]
    assert b["model_objective_tl"] == pytest.approx(bf_model, rel=1e-6)
    assert b["objective_tl"] == pytest.approx(z_acc, rel=1e-4)
    assert b["lb_tl"] <= z_acc * (1 + 1e-9) + 1.0 and b["lb_tl"] <= b["objective_tl"]
    assert not b["lb_rejected"] and r["status"] == "optimal"


def test_status_label_closed_lock_and_unreachable_gap():
    """Doğrulama #11: 'yasak' kilitli hücrelerin yayları çekirdeği tam modelden ayırmaz (gap 0 → optimal); hedef gap
    sayısal olarak erişilemezse (yalnız Lagrange, ikilik boşluğu) süre dolmadan biten çözüm 'süre doldu' değil
    'gap_ulasilamadi' olarak etiketlenir."""
    inst = toy(11, 6, 3, 15.0, {}, locks={"open": ["c2"], "closed": ["c3"]})
    bf, _ = brute_force(inst)
    r = solve(instance=inst, time_limit=30, gap=0.0)
    assert r["ok"] and r["status"] == "optimal", (r["status"], r["bounds"])
    assert r["bounds"]["objective_tl"] == pytest.approx(bf, rel=1e-6)
    assert r["core_mip"] is None or r["core_mip"]["is_full_model"]
    inst = toy(2, 6, 3, 15.0)
    r = solve(instance=inst, time_limit=30, gap=0.0, mip="none")
    assert r["ok"] and r["bounds"]["mip_kind"] == "none"
    if r["bounds"]["gap"] > 0:
        assert r["status"] == "gap_ulasilamadi" and r["times"]["total_s"] < 30


def test_per_class_rows_valid_and_tighter():
    for seed in (1, 2, 4):
        inst = toy(seed, 6, 3, 15.0)
        bf, _ = brute_force(inst)
        m_pc = milp.MilpModel(inst, linking="strong", per_class=True)
        m_no = milp.MilpModel(inst, linking="strong", per_class=False)
        assert m_pc.lay.nP > 0 and m_no.lay.nP == 0
        r = m_pc.solve(gap=0.0, abs_gap=1e-9, time_limit=60)
        assert r.primal_tl == pytest.approx(bf, rel=1e-6)
        lp_pc = m_pc.solve(relax=True).primal_tl
        lp_no = m_no.solve(relax=True).primal_tl
        assert lp_no <= lp_pc * (1 + 1e-9) <= bf * (1 + 1e-9)


@pytest.fixture(scope="module")
def inst5():
    return instance.build_instance(5)


def test_lagrange_5km_certificate(inst5):
    from backend.model.heuristics import heuristic
    lagrange.clear_cache()
    h = heuristic(inst5, time_budget=2.0)
    res = lagrange.run(inst5, start=h.solution, time_limit=15.0, target_gap=0.01)
    assert res.ok and res.gap <= 0.01
    assert res.bound_tl <= BEST_KNOWN_5KM_TL
    assert res.bound_tl >= 400e9                                    # güçlü LP sınırı 401,11 milyar TL civarı
    assert checks.check(inst5, res.solution)["ok"]
    assert res.mu.shape == (inst5.nI,) and res.G_min_tl.shape == (inst5.nJ,)
    # çekirdek örnek: incumbent çekirdekte olurlu; çekirdek çözümü tam modele taşınınca olurlu ve aynı değerde
    core, sel = lagrange.core_instance(inst5, res, res.solution, n_keep=150, m=8)
    assert core.nA == len(sel) < inst5.nA and (~core.lock_closed).sum() < inst5.nJ
    st = lagrange.solution_to_core(res.solution, sel)
    assert checks.check(core, st)["ok"]
    back = lagrange.solution_from_core(st, sel, inst5.nA)
    assert back.objective_tl(inst5) == pytest.approx(res.solution.objective_tl(inst5), rel=1e-12)
    # sıcak başlangıç (önbellek): aynı parametrelerle ikinci çalıştırma önbellekten başlar
    res2 = lagrange.run(inst5, start=res.solution, time_limit=5.0, target_gap=0.01)
    assert res2.init_source == "onbellek_ayni" and res2.bound_tl >= 0.999 * res.bound_tl


def test_solve_5km_lagrange_certified():
    r = solve(5, time_limit=15)
    assert r["ok"] and r["check"]["ok"] and r["status"] == "optimal"
    assert r["bounds"]["lb_source"] == "lagrange" and r["bounds"]["gap"] <= 0.01
    assert r["bounds"]["lb_tl"] <= BEST_KNOWN_5KM_TL
    assert "Alt sınır L" in r["certificate_tr"]
    # bounds.mip_kind gerçekte çalışanı yazar (doğrulama E4): çekirdek evresi ya hiç başlamadı ya da çalıştı
    assert r["bounds"]["mip_kind"] in ("none", "core") and r["bounds"]["mip_kind_planned"] == "core"
    assert (r["bounds"]["mip_kind"] == "core") == (r["core_mip"] is not None or r["improve"] is not None)


def test_solve_3km_live_target():
    """Şartname 4.6: 3 km'de canlı çözüm ≲ 15 sn; süre sınırı sert (≤ 15 + 0,5 sn)."""
    r = solve(3, time_limit=15)
    assert r["ok"] and r["check"]["ok"], r.get("check")
    assert r["status"] == "optimal" and r["bounds"]["gap"] <= 0.01
    assert r["times"]["total_s"] <= 15 + 0.5
    assert r["bounds"]["lb_tl"] <= r["bounds"]["ub_tl"]


@pytest.mark.parametrize("km,tl,ov", [(5, 4.0, {}), (3, 6.0, {}), (2, 12.0, {}),
                                      (3, 8.0, {"solver.objective_mode": "sabit_p", "solver.p_fixed": 70})])
def test_hard_time_limit(km, tl, ov):
    """Doğrulama #16: hedef gap erişilemezken (1e-5) toplam süre time_limit + 0,5 sn'yi aşmaz (alt süreç dahil)."""
    t = time.perf_counter()
    r = solve(km, ov, time_limit=tl, gap=1e-5)
    wall = time.perf_counter() - t
    assert r["ok"] and r["check"]["ok"]
    assert r["times"]["total_s"] <= tl + 0.5 and wall <= tl + 0.6, (r["times"], wall)
    assert r["status"] in ("sure_doldu", "gap_ulasilamadi", "optimal")


def test_k_zero_means_all_candidates_within_R():
    p0 = Params.load({"grid.k_nearest": 0})
    inst0 = instance.build_instance(5, p0)
    inst50 = instance.build_instance(5)
    assert inst0.s.k_nearest == 0 and inst50.s.k_nearest == 50
    s = inst50.s
    nl = distance.neighbor_lists(s.demand_xy, s.cand_xy, s.cell_km, s.R_km, None, s.circ)
    assert inst0.nA == len(nl.indices) == 28543 and inst50.nA == 27639
    assert np.array_equal(inst0.s.arc_j, nl.indices) and np.array_equal(inst0.s.arc_ptr, nl.indptr)
    r = solve(instance=inst0, time_limit=15)
    assert r["ok"] and r["check"]["ok"] and r["bounds"]["lb_scope"] == "tam"


def test_cancel_during_core_mip():
    ev = threading.Event()
    seen: dict = {}
    out: dict = {}

    def cb(e: dict) -> None:
        if e.get("kind") == "phase" and e.get("phase") == "cekirdek_mip" and "t" not in seen:
            seen["t"] = time.perf_counter()
            threading.Timer(1.0, ev.set).start()

    th = threading.Thread(target=lambda: out.setdefault("r", solve(3, time_limit=90, gap=0.001, progress_cb=cb,
                                                                    cancel_event=ev)))
    th.start()
    th.join(timeout=120)
    assert not th.is_alive() and "t" in seen
    r = out["r"]
    assert r["status"] == "durduruldu" and r["ok"] and r["check"]["ok"]
    assert r["times"].get("core_mip_s", 0) < 1.0 + 5.0                # süreç hemen sonlandırıldı
