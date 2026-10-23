"""Faz 4 — manuel değerlendirmenin karışık (hastane başına sınıf/yatak sabit ya da serbest) çözümü motorun kendi
değerlendirmesiyle tutarlı mı: tekdüze durumlarda backend.model.evaluate.evaluate ile aynı amaç, karışık durumda
denetimden geçen çözüm ve doğru sıralama (serbest ≤ karışık ≤ tümü sabit). Bütçe modu ve olursuzluk iletileri de.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase4_evaluate.py
"""
from __future__ import annotations

import numpy as np
import orjson
import pytest

from backend.app.task_evaluate import evaluate_mixed
from backend.model import checks
from backend.model.evaluate import evaluate
from backend.model.instance import build_instance
from backend.model.params import Params

from .test_phase4_common import ROOT


@pytest.fixture(scope="module")
def setup():
    pre = orjson.loads((ROOT / "precomputed" / "default_5km.json").read_bytes())
    params = Params.load({"grid.cell_km": 5})
    inst = build_instance(5, params, None)
    pos = {str(c): j for j, c in enumerate(inst.s.cand_ids)}
    S = [pos[h["cell_id"]] for h in pre["hospitals"]]
    free = evaluate(inst, S)
    assert free.ok
    return inst, S, free, pre


def test_all_free_equals_engine(setup):
    inst, S, free, pre = setup
    mixed = evaluate_mixed(inst, S, [None] * len(S), [None] * len(S))
    assert mixed.ok and checks.check(inst, mixed.solution)["ok"]
    assert mixed.objective_tl == pytest.approx(free.objective_tl, rel=1e-8)
    assert free.objective_tl == pytest.approx(pre["bounds"]["ub_tl"], rel=1e-8)


def test_fixed_classes_and_beds_equal_engine(setup):
    inst, S, free, _ = setup
    sol = free.solution
    k_of = dict(zip(sol.open_j.tolist(), sol.cls.tolist()))
    b_of = dict(zip(sol.open_j.tolist(), sol.beds.tolist()))
    cls = [k_of[j] for j in S]
    beds = [b_of[j] for j in S]
    e_cls = evaluate(inst, S, cls=np.array(cls))
    m_cls = evaluate_mixed(inst, S, cls, [None] * len(S))
    assert m_cls.objective_tl == pytest.approx(e_cls.objective_tl, rel=1e-8)
    e_all = evaluate(inst, S, cls=np.array(cls), beds=np.array(beds))
    m_all = evaluate_mixed(inst, S, cls, beds)
    assert m_all.ok and m_all.objective_tl == pytest.approx(e_all.objective_tl, rel=1e-8)
    assert e_all.objective_tl == pytest.approx(free.objective_tl, rel=1e-7)   # optimum değerlerinde sabitlemek


def test_mixed_ordering_and_check(setup):
    inst, S, free, _ = setup
    n = len(S)
    sol = free.solution
    j_big = int(sol.open_j[np.argmax(sol.beds)])
    t_big = S.index(j_big)
    cls = [None] * n
    beds = [None] * n
    cls[0] = 2                                   # V-C (yatak serbest)
    beds[t_big] = float(inst.L[0])               # en büyük hastaneyi IV-C alt sınırına (50 yatak) sabitle
    cls[t_big] = 0
    mixed = evaluate_mixed(inst, S, cls, beds)
    assert mixed.ok, mixed.message
    chk = checks.check(inst, mixed.solution, solver_objective_tl=mixed.solver_objective_tl)
    assert chk["ok"], chk["violations"]
    got = dict(zip(mixed.solution.open_j.tolist(), zip(mixed.solution.cls.tolist(), mixed.solution.beds.tolist())))
    assert got[S[0]][0] == 2 and got[j_big] == (0, pytest.approx(50.0))
    assert mixed.objective_tl >= free.objective_tl * (1 - 1e-9)          # kısıt eklemek maliyeti düşürmez


def test_infeasible_messages(setup):
    inst, S, _, _ = setup
    r = evaluate_mixed(inst, S[:3], [None] * 3, [None] * 3)
    assert not r.ok and r.status == "olurlu_degil" and "R içinde açık hastanesi yok" in r.message
    r = evaluate_mixed(inst, [S[0], S[0]], [None, None], [None, None])
    assert not r.ok and r.status == "gecersiz"
    # tüm hastaneler 50 yatakta sabit: kapasite yetmez
    r = evaluate_mixed(inst, S, [0] * len(S), [50.0] * len(S))
    assert not r.ok and "olurlu atama yok" in r.message


def test_budget_mode_matches_engine(setup):
    _, _, _, pre = setup
    params = Params.load({"grid.cell_km": 5, "solver.objective_mode": "butce", "solver.budget_tl": 360e9})
    inst = build_instance(5, params, None)
    pos = {str(c): j for j, c in enumerate(inst.s.cand_ids)}
    S = [pos[h["cell_id"]] for h in pre["hospitals"]]
    e = evaluate(inst, S)
    m = evaluate_mixed(inst, S, [None] * len(S), [None] * len(S))
    assert e.ok and m.ok
    assert m.objective_tl == pytest.approx(e.objective_tl, rel=1e-7)     # bütçe modunda amaç = erişim maliyeti
    assert m.solution.opening_tl(inst) <= 360e9 * (1 + 1e-9)
