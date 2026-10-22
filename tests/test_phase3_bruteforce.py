"""Faz 3 — oyuncak örnekte kaba kuvvet = MILP (şartname 4.6 ve 10: "Oyuncak örnekte kaba kuvvet = MILP").

Her rastgele oyuncak örnekte (5–6 hücre, 2–4 boyut sınıfı, kapasite ve R bağlayıcı) TÜM açık kümeler × sınıf seçimleri
sayılır; her biri için atama/boyutlandırma LP'si bu dosyadaki bağımsız kurulumla (scipy.optimize.linprog) çözülür, en
küçüğü MILP'in optimumuyla karşılaştırılır. Modlar (sabit p, en az/en çok sayı, kilitler, bütçe) aynı sayımda filtreyle
uygulanır.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase3_bruteforce.py
"""
from __future__ import annotations

import itertools

import numpy as np
import pytest
from scipy.optimize import linprog

from backend.model import checks, instance, milp
from backend.model.params import Params
from backend.model.solve import solve

# oyuncak sınıflar: IV-C [20, 59], V-B [60, 119], V-C [120, 200], V-D [201, 400] — hücre talepleri 25–125 yatak
TOY_SIZES = {"size.general_min_beds": 20, "size.class_threshold_1": 60, "size.class_threshold_2": 120,
             "size.general_max_beds": 200, "size.city_min_beds": 201, "size.city_max_beds": 400}


def toy(seed: int, n: int, n_classes: int, R: float = 15.0, extra: dict | None = None, locks=None):
    rng = np.random.default_rng(seed)
    ov = dict(TOY_SIZES)
    if n_classes == 2:
        ov.update({"size.general_max_beds": 119, "size.city_enabled": False})     # V-C boş, V-D kapalı
    elif n_classes == 3:
        ov["size.city_enabled"] = False
    ov.update(extra or {})
    p = Params.load(ov)
    xy = rng.uniform(0, 24_000, size=(n, 2))
    w = rng.uniform(8_000, 40_000, size=n).round()
    inst = instance.toy_instance(demand_xy=xy, w=w, cand_xy=xy, params=p, R_km=R, k=None, locks=locks)
    assert int(inst.class_on.sum()) == n_classes
    return inst


def lp_value(inst, S, kappa) -> float | None:
    """Açık küme S ve sınıflar kappa için atama/boyutlandırma LP'si (bağımsız kurulum). Olurlu değilse None."""
    s = inst.s
    S = list(S)
    pos = {j: t for t, j in enumerate(S)}
    arcs = [a for a in range(inst.nA) if int(s.arc_j[a]) in pos]
    nS, nX = len(S), len(arcs)
    F = np.array([inst.fixed[j, k] for j, k in zip(S, kappa)])
    V = np.array([inst.var[j, k] for j, k in zip(S, kappa)])
    acc = inst.acc[arcs]
    budget_mode = inst.mode == "butce"
    w_open = milp.BUDGET_EPS if budget_mode else 1.0
    # değişkenler: B (nS) | x (nX); amaç sabiti Σ F
    c = np.concatenate([w_open * V, acc]) / 1e6
    const = w_open * F.sum() / 1e6
    A_eq = np.zeros((inst.nI, nS + nX))
    for t, a in enumerate(arcs):
        A_eq[int(s.arc_i[a]), nS + t] = 1.0
    if (A_eq.sum(axis=1) == 0).any():
        return None
    A_ub = np.zeros((nS, nS + nX))
    for t, a in enumerate(arcs):
        A_ub[pos[int(s.arc_j[a])], nS + t] = inst.q[int(s.arc_i[a])]
    A_ub[np.arange(nS), np.arange(nS)] = -1.0
    b_ub = np.zeros(nS)
    if budget_mode:
        row = np.concatenate([V, np.zeros(nX)]) / 1e6
        A_ub = np.vstack([A_ub, row])
        b_ub = np.concatenate([b_ub, [(inst.budget - F.sum()) / 1e6]])
    bounds = [(inst.L[k], inst.U[k]) for k in kappa] + [(0.0, 1.0)] * nX
    r = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=np.ones(inst.nI), bounds=bounds, method="highs")
    if r.status != 0:
        return None
    return (r.fun + const) * 1e6


def brute_force(inst) -> tuple[float, tuple]:
    best = (np.inf, None)
    on = [k for k in range(inst.nK) if inst.class_on[k]]
    Q = inst.total_demand
    for n in range(1, inst.nJ + 1):
        if n < inst.count_min or (inst.count_max is not None and n > inst.count_max):
            continue
        for S in itertools.combinations(range(inst.nJ), n):
            if any(inst.lock_closed[j] for j in S) or any(inst.lock_open[j] and j not in S for j in range(inst.nJ)):
                continue
            for kappa in itertools.product(on, repeat=n):
                if sum(inst.U[k] for k in kappa) < Q - 1e-9:
                    continue
                v = lp_value(inst, S, kappa)
                if v is not None and v < best[0]:
                    best = (v, (S, kappa))
    return best


def milp_value(inst, linking="strong"):
    m = milp.MilpModel(inst, linking=linking)
    r = m.solve(gap=0.0, abs_gap=1e-9, time_limit=60)
    assert r.status == "Optimal", r.status
    return r


CASES = [  # (tohum, hücre, sınıf sayısı, R km, ek parametre)
    (1, 6, 2, 15.0, {}),
    (2, 6, 3, 15.0, {}),
    (3, 5, 4, 15.0, {}),
    (4, 6, 3, 12.0, {"access.alpha": 1.5}),
    (5, 5, 4, 30.0, {"land.include": True, "opex.include": True, "opex.fixed_per_hospital_year": 5e7}),
]


@pytest.mark.parametrize("seed,n,K,R,extra", CASES)
def test_bruteforce_equals_milp(seed, n, K, R, extra):
    inst = toy(seed, n, K, R, extra)
    bf, arg = brute_force(inst)
    assert np.isfinite(bf)
    for linking in ("strong", "weak"):
        r = milp_value(inst, linking)
        assert r.primal_tl == pytest.approx(bf, rel=1e-6), (linking, r.primal_tl, bf, arg)
        sol = r.solution
        assert sol.objective_tl(inst) == pytest.approx(bf, rel=1e-6)
        rep = checks.check(inst, sol, solver_objective_tl=r.primal_tl)
        assert rep["ok"], rep["violations"]
    # kapasite gerçekten bağlayıcı: sınıf eşikleri yüzünden talep en az iki hastaneye bölünür ya da sınıf atlar
    assert len(arg[0]) >= 2


@pytest.mark.parametrize("extra,locks,label", [
    # bu örnekte serbest optimum 5 hastane: {0, 1, 3, 4, 5}; hücre 1 ve 4 yalnız kendilerine R içinde
    ({"solver.objective_mode": "sabit_p", "solver.p_fixed": 4}, None, "sabit_p"),
    ({"size.min_hospitals": 6}, None, "en_az"),
    ({"size.max_hospitals": 4}, None, "en_cok"),
    ({}, {"open": ["c2"], "closed": ["c3"]}, "kilit"),
])
def test_bruteforce_modes(extra, locks, label):
    inst = toy(11, 6, 3, 15.0, extra, locks=locks)
    bf, arg = brute_force(inst)
    assert np.isfinite(bf), label
    r = milp_value(inst)
    assert r.primal_tl == pytest.approx(bf, rel=1e-6), (label, r.primal_tl, bf, arg)
    n = r.solution.n_open
    if label == "sabit_p":
        assert n == 4
    if label == "en_az":
        assert n == 6
    if label == "en_cok":
        assert n <= 4
    if label == "kilit":
        assert 2 in r.solution.open_j and 3 not in r.solution.open_j
    assert checks.check(inst, r.solution, solver_objective_tl=r.primal_tl)["ok"]


def test_bruteforce_budget_mode():
    base = toy(21, 5, 3, 15.0)
    bf_total, (S, kappa) = brute_force(base)
    # bütçe: toplam-optimumun açılış maliyetinin %110'u → erişim daha da azalabilir
    sol = milp_value(base).solution
    budget = 1.10 * sol.opening_tl(base)
    inst = toy(21, 5, 3, 15.0, {"solver.objective_mode": "butce", "solver.budget_tl": budget})
    bf, arg = brute_force(inst)
    r = milp_value(inst)
    assert r.primal_tl == pytest.approx(bf, rel=1e-6)
    assert r.solution.opening_tl(inst) <= budget * (1 + 1e-9)
    assert r.solution.access_tl(inst) <= sol.access_tl(base) * (1 + 1e-9)
    assert checks.check(inst, r.solution, solver_objective_tl=r.primal_tl)["ok"]


@pytest.mark.parametrize("isolate", [True, False])
def test_solve_pipeline_on_toy_matches_bruteforce(isolate):
    inst = toy(2, 6, 3, 15.0)
    bf, _ = brute_force(inst)
    res = solve(instance=inst, time_limit=30, gap=0.0, isolate=isolate)
    assert res["ok"] and res["check"]["ok"], res.get("check")
    assert res["bounds"]["objective_tl"] == pytest.approx(bf, rel=1e-6)
    assert res["status"] == "optimal"
