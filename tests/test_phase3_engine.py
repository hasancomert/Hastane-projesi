"""Faz 3 — gerçek ızgaralarda çözüm motoru: denetleyici, modlar, kilitler, yerinde güncelleme, olurlu-değil mesajları,
iptal, sınır kartları, dual değerler ve backend bağımlılıkları.

Süre: birkaç dakika (5 km çözümleri süre sınırlı). Çalıştırma (repo kökünden):
  .venv/bin/python -m pytest -q tests/test_phase3_engine.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from backend.model import bounds, checks, evaluate, heuristics, instance, milp
from backend.model.instance import Solution
from backend.model.params import Params
from backend.model.solve import solve
from pipeline import common

TL5 = 40.0      # 5 km çözüm süre sınırı (sn)


@pytest.fixture(scope="module")
def inst5():
    return instance.build_instance(5)


@pytest.fixture(scope="module")
def res5():
    return solve(5, time_limit=TL5, gap=0.01)


def _sol_from_result(inst, r) -> Solution:
    """Sonuç sözlüğünden Solution (hastane listesi + atama listesi)."""
    pos = np.array([h["cand_pos"] for h in r["hospitals"]], dtype=np.int64)
    code = {c.code: k for k, c in enumerate(inst.classes)}
    cls = np.array([code[h["class"]] for h in r["hospitals"]], dtype=np.int64)
    beds = np.array([h["beds"] for h in r["hospitals"]])
    s = inst.s
    did = {str(c): i for i, c in enumerate(s.demand_ids)}
    x = np.zeros(inst.nA)
    a_ptr = s.arc_ptr
    for dc, hidx, share in zip(r["assignment"]["demand_cell"], r["assignment"]["hospital"], r["assignment"]["share"]):
        i = did[dc]
        js = s.arc_j[a_ptr[i]:a_ptr[i + 1]]
        a = a_ptr[i] + int(np.flatnonzero(js == pos[hidx])[0])
        x[a] = share
    order = np.argsort(pos)
    return Solution(pos[order], cls[order], beds[order], x)


# ---------------------------------------------------------------- varsayılan çözüm
def test_default_5km_solution(res5, inst5):
    r = res5
    assert r["ok"] and r["check"]["ok"], r["check"]
    assert r["status"] in ("optimal", "sure_doldu")
    json.dumps(r)                                                  # JSON'a çevrilebilir
    k, c = r["kpis"], r["costs"]
    assert sum(k["count_by_class"].values()) == k["n_hospitals"] == len(r["hospitals"])
    assert k["total_beds"] >= k["total_demand_beds"] * (1 - 1e-9)
    assert k["share_pop_within_km"]["30"] == pytest.approx(1.0)
    assert k["max_km"] <= 30 + 1e-9
    assert c["insaat"] == pytest.approx(c["yapi"] + c["kdv"] + c["altyapi"] + c["donanim"])
    assert c["toplam"] == pytest.approx(c["acilis"] + c["erisim"])
    assert c["toplam"] == pytest.approx(r["bounds"]["objective_tl"])
    assert r["bounds"]["lb_tl"] <= r["bounds"]["ub_tl"] * (1 + 1e-9)
    for h in r["hospitals"]:
        assert 38 < h["lat"] < 41 and 30 < h["lon"] < 35
        assert h["beds"] >= h["assigned_beds"] * (1 - 1e-6)
        assert sum(h["costs"][x] for x in ("yapi", "kdv", "altyapi", "donanim", "sabit", "arsa", "isletme")) \
            == pytest.approx(h["costs"]["acilis"])
    # sonuç sözlüğünden yeniden kurulan çözüm de denetimden geçer ve aynı amaç değerini verir
    sol = _sol_from_result(inst5, r)
    rep = checks.check(inst5, sol, solver_objective_tl=r["bounds"]["objective_tl"])
    assert rep["ok"], rep["violations"]
    # şartnamenin beklentisi: yatak başı maliyet sınıfla arttığı için çoğu hastane küçük (<200 yatak)
    assert k["count_by_class"]["IV-C"] > k["n_hospitals"] / 2


def test_checker_detects_violations(res5, inst5):
    sol = _sol_from_result(inst5, res5)
    # 1) bir hastaneyi kapat: atamalar kapalı hücreye kalır
    bad = Solution(sol.open_j[1:], sol.cls[1:], sol.beds[1:], sol.x)
    rep = checks.check(inst5, bad)
    assert not rep["ok"] and not rep["checks"]["acik_hastaneye"]
    # 2) yatakları azalt: kapasite ve sınıf sınırı
    bad = Solution(sol.open_j, sol.cls, sol.beds * 0.5, sol.x)
    rep = checks.check(inst5, bad)
    assert not rep["checks"]["kapasite"] and not rep["checks"]["sinif_sinirlari"]
    # 3) bir hücrenin talebini yarıya indir
    x = sol.x.copy()
    a = int(np.flatnonzero(x > 0.99)[0])
    x[a] = 0.5
    rep = checks.check(inst5, Solution(sol.open_j, sol.cls, sol.beds, x))
    assert not rep["checks"]["tam_atama"]
    # 4) yanlış amaç değeri
    rep = checks.check(inst5, sol, solver_objective_tl=res5["bounds"]["objective_tl"] * 1.01)
    assert not rep["checks"]["amac_tutarli"]
    # 5) aynı hücrede iki hastane
    dup = Solution(np.r_[sol.open_j, sol.open_j[:1]], np.r_[sol.cls, sol.cls[:1]], np.r_[sol.beds, sol.beds[:1]], sol.x)
    assert not checks.check(inst5, dup)["checks"]["hucre_basina_1"]


def test_checker_on_3km_live_solution():
    r = solve(3, time_limit=20)
    assert r["ok"] and r["check"]["ok"], r["check"]
    assert r["bounds"]["lb_tl"] is not None and r["bounds"]["lb_tl"] <= r["bounds"]["ub_tl"]
    assert r["instance"]["J"] == 634 and r["instance"]["arcs"] == 81288
    assert r["times"]["total_s"] <= 20 + 0.5                       # sert süre sınırı (doğrulama #16)


# ---------------------------------------------------------------- modlar
def test_mode_fixed_p():
    r = solve(5, {"solver.p_fixed": 50}, mode="sabit_p", time_limit=25)
    assert r["ok"] and r["check"]["ok"] and r["kpis"]["n_hospitals"] == 50


@pytest.mark.parametrize("ov,cmp", [({"size.min_hospitals": 80}, lambda n: n >= 80),
                                    ({"size.max_hospitals": 50}, lambda n: n <= 50)])
def test_mode_count_bounds(ov, cmp):
    r = solve(5, ov, time_limit=25)
    assert r["ok"] and r["check"]["ok"] and cmp(r["kpis"]["n_hospitals"])


def test_locks_respected(res5, inst5):
    open_now = {h["cell_id"] for h in res5["hospitals"]}
    closed_target = sorted(open_now)[0]
    not_open = [str(c) for c in inst5.s.cand_ids if str(c) not in open_now]
    open_target = not_open[len(not_open) // 2]
    r = solve(5, locks={"open": [open_target], "closed": [closed_target]}, time_limit=25)
    assert r["ok"] and r["check"]["ok"] and r["check"]["checks"]["kilitler"]
    ids = {h["cell_id"] for h in r["hospitals"]}
    assert open_target in ids and closed_target not in ids
    assert any(h["locked_open"] for h in r["hospitals"])
    assert r["bounds"]["objective_tl"] >= res5["bounds"]["lb_tl"]


def test_budget_mode(res5):
    budget = 1.03 * res5["costs"]["acilis"]
    r = solve(5, {"solver.budget_tl": budget}, mode="butce", time_limit=30)
    assert r["ok"] and r["check"]["ok"] and r["check"]["checks"]["butce"]
    assert r["costs"]["acilis"] <= budget * (1 + 1e-7)
    # varsayılan çözüm bu bütçeye sığar ve önceki çözüm olarak başlangıçtır → erişim maliyeti ondan kötü olamaz
    assert r["costs"]["erisim"] <= res5["costs"]["erisim"] * (1 + 1e-6)
    assert r["bounds"]["objective_tl"] == pytest.approx(r["costs"]["erisim"])
    assert r["bounds"]["lb_tl"] <= r["bounds"]["objective_tl"]


@pytest.mark.parametrize("km", [5, 3])
def test_budget_mode_default_budget(km):
    """Doğrulama #9/#12: varsayılan bütçeyle (350 milyar TL) bütçe modu olurlu çözüm verir (eskiden 5 km'de cozum_yok);
    serbest çözüm bütçeye sığıyorsa (3 km) erişim maliyeti ondan kötü değildir; alt sınır (bütçe satırının Lagrange
    gevşetmesi; ε eşitlik bozucusu sınırlardan çıkarılmış) ≤ raporlanan erişim maliyeti; süre sınırı sert."""
    from backend.model import solve as solve_mod
    solve_mod.clear_cache()
    free = solve(km, time_limit=15)
    assert free["ok"] and free["check"]["ok"]
    B = Params.load()["solver.budget_tl"]
    r = solve(km, mode="butce", time_limit=15)
    assert r["ok"] and r["check"]["ok"] and r["check"]["checks"]["butce"], r.get("message_tr")
    assert r["status"] in ("optimal", "sure_doldu", "gap_ulasilamadi")
    assert r["costs"]["acilis"] <= B * (1 + 1e-7)
    b = r["bounds"]
    assert b["objective_tl"] == pytest.approx(r["costs"]["erisim"]) and b["ub_tl"] == pytest.approx(b["objective_tl"])
    assert b["lb_source"] == "lagrange_butce" and b["lb_tl"] is not None
    assert 0 < b["lb_tl"] <= b["objective_tl"] and not b["lb_rejected"] and b["lb_adjusted"] is None
    assert b["gap"] == pytest.approx((b["ub_tl"] - b["lb_tl"]) / b["ub_tl"])
    assert r["budget"]["nu"] > 0 and r["budget"]["points"]
    if free["costs"]["acilis"] <= B:
        assert r["costs"]["erisim"] <= free["costs"]["erisim"] * (1 + 1e-6)
    assert r["times"]["total_s"] <= 15 + 0.5


# ---------------------------------------------------------------- yerinde güncelleme
@pytest.mark.parametrize("ov", [
    {"access.alpha": 1.3, "cost.fixed_per_hospital": 3e8},                       # yalnız maliyet
    {"cost.unit.IV-C": 45000, "size.city_enabled": False, "size.max_hospitals": 90,
     "size.class_threshold_1": 180},                                               # sınır + birkaç katsayı
    {"demand.beds_per_1000": 3.0, "land.include": True},                           # q (çok katsayı) → yeniden geçir
    {"solver.objective_mode": "butce", "solver.budget_tl": 4e11},                   # bütçe satırı
])
def test_inplace_update_equals_fresh_build(inst5, ov):
    new = instance.build_instance(5, ov)
    assert new.s is inst5.s
    m = milp.MilpModel(inst5, linking="weak")
    info = m.update(new)
    fresh = milp.MilpModel(new, linking="weak")
    lp_u, lp_f = m.h.getLp(), fresh.h.getLp()
    for attr in ("col_cost_", "col_lower_", "col_upper_", "row_lower_", "row_upper_"):
        assert np.allclose(np.asarray(getattr(lp_u, attr)), np.asarray(getattr(lp_f, attr)), rtol=1e-12), attr
    for M in (m, fresh):
        M.h.ensureColwise()
    au, af = m.h.getLp().a_matrix_, fresh.h.getLp().a_matrix_
    assert np.array_equal(np.asarray(au.start_), np.asarray(af.start_))
    assert np.array_equal(np.asarray(au.index_), np.asarray(af.index_))
    assert np.allclose(np.asarray(au.value_), np.asarray(af.value_), rtol=1e-12)
    ru = m.solve(relax=True, time_limit=60)
    rf = fresh.solve(relax=True, time_limit=60)
    assert ru.primal_tl == pytest.approx(rf.primal_tl, rel=1e-9)
    assert info["costs"] > 0 or info["coeffs"] > 0 or info["col_bounds"] > 0 or info["row_bounds"] > 0


def test_inplace_update_mip_on_toy():
    from tests.test_phase3_bruteforce import toy
    a = toy(2, 6, 3, 15.0)
    b = toy(2, 6, 3, 15.0, {"access.alpha": 1.4, "cost.fixed_per_hospital": 4e8, "demand.beds_per_1000": 2.8})
    b.s = a.s                                                       # aynı yapı
    m = milp.MilpModel(a)
    m.update(b)
    r1 = m.solve(gap=0.0, abs_gap=1e-9)
    r2 = milp.MilpModel(b).solve(gap=0.0, abs_gap=1e-9)
    assert r1.primal_tl == pytest.approx(r2.primal_tl, rel=1e-9)


# ---------------------------------------------------------------- olurlu değil / hata mesajları
def test_infeasible_isolated_area_locked_out(inst5):
    s = inst5.s
    counts = np.diff(s.arc_ptr)
    i = int(np.argmin(counts))                                      # en az adaylı talep hücresi
    cands = [str(s.cand_ids[j]) for j in s.arc_j[s.arc_ptr[i]:s.arc_ptr[i + 1]]]
    r = solve(5, locks={"closed": cands}, time_limit=10)
    assert r["status"] == "olurlu_degil" and not r["ok"]
    assert "açılabilir aday" in r["message_tr"] and "yasak" in r["message_tr"]


@pytest.mark.parametrize("ov,mode,needle", [
    ({"size.city_enabled": False, "size.max_hospitals": 10}, None, "şehir hastanesi"),
    ({"solver.budget_tl": 1e9}, "butce", "Bütçe"),
    ({"size.min_hospitals": 5000}, None, "en az 5000"),
])
def test_infeasible_settings_messages(ov, mode, needle):
    r = solve(5, ov, mode=mode, time_limit=10)
    assert r["status"] == "olurlu_degil" and needle in r["message_tr"], r["message_tr"]


def test_invalid_parameter_and_lock_messages():
    r = solve(5, {"access.max_distance_km": -1}, time_limit=5)
    assert r["status"] == "hata" and "Parametre hatası" in r["message_tr"]
    r = solve(5, locks={"open": ["5km_0_0"]}, time_limit=5)
    assert r["status"] == "hata" and "Bilinmeyen hücre" in r["message_tr"]


def test_infeasible_detected_by_milp_on_toy():
    # iki uzak hücre; biri yalnız kendisine erişebiliyor ve talebi en büyük genel hastaneyi aşıyor (şehir sınıfı kapalı)
    p = Params.load({"size.city_enabled": False, "size.general_max_beds": 300})
    inst = instance.toy_instance(demand_xy=[[0, 0], [100_000, 0]], w=[150_000, 10_000],
                                 cand_xy=[[0, 0], [100_000, 0]], params=p, R_km=30)
    inst.precheck()                                                  # ön denetim bunu yakalamaz
    r = solve(instance=inst, time_limit=10)
    assert r["status"] == "olurlu_degil" and "olurlu çözüm yok" in r["message_tr"]


# ---------------------------------------------------------------- iptal ve ilerleme
def test_cancel_and_progress():
    from backend.model import solve as solve_mod
    solve_mod.clear_cache()                      # önceki testlerin çarpan / çözüm önbelleği çözümü erken bitirebilir
    ev = threading.Event()
    events: list[dict] = []
    out: dict = {}
    seen = threading.Event()

    def cb(e: dict) -> None:
        events.append(e)
        if e.get("kind") == "lagrange" and not seen.is_set():     # sezgisel bitti, Lagrange A evresi başladı
            seen.set()

    def run():
        out["r"] = solve(3, time_limit=120, progress_cb=cb, cancel_event=ev)

    th = threading.Thread(target=run)
    th.start()
    assert seen.wait(timeout=60)
    time.sleep(1.0)
    t_cancel = time.perf_counter()
    ev.set()
    th.join(timeout=60)
    assert not th.is_alive()
    assert time.perf_counter() - t_cancel < 5.0                     # iptal her evrede birkaç sn içinde etkili
    r = out["r"]
    assert r["status"] == "durduruldu" and r["ok"] and r["check"]["ok"]
    kinds = {e.get("kind") for e in events}
    assert {"phase", "heuristic"} <= kinds


# ---------------------------------------------------------------- sınır kartları, dual değerler
def test_bound_cards_5km(inst5, res5):
    bc = bounds.bound_cards(inst5, lscp_time=30, pmin_time=60)
    assert bc["max_hospitals_grid"] == 454
    assert bc["capacity"]["with_city"] == 5 and bc["capacity"]["without_city"] == 19
    ls, lk = bc["coverage_lscp"], bc["coverage_lscp_k_list"]
    # kartın anlamı "her talep hücresine R içinde bir hastane": R içindeki tüm adaylarla (k = 0); k listesiyle olan
    # ikinci satır daha kısıtlı model → değeri en az o kadar (doğrulama C3: 5 km'de 25 / 26)
    assert ls["k_nearest"] == 0 and lk["k_nearest"] == 50 and ls["arcs"] > lk["arcs"]
    assert ls["proven"] and lk["proven"] and 1 <= ls["value"] <= lk["value"] <= 454
    assert ls["reduction"]["rows_reduced"] < inst5.nI                   # baskınlık küçültmesi çalıştı
    pm = bc["p_min"]
    assert pm["value"] is not None and pm["value"] >= max(lk["value"], 5)
    assert pm["lower_bound"] >= lk["lower_bound"]
    assert pm["lower_bound"] <= pm["value"] <= res5["kpis"]["n_hospitals"]
    assert "k = 50" in bc["labels_tr"]["coverage_lscp_k_list"] and "yol mesafesi" in bc["labels_tr"]["coverage_lscp"]


def test_lscp_dominance_reduction_exact():
    """Satır/sütun baskınlığıyla küçültülmüş küme kapsama = küçültmesiz çözüm = kaba kuvvet (rastgele kapsama
    matrisleri; zorunlu / yasak aday kilitleriyle)."""
    from types import SimpleNamespace
    rng = np.random.default_rng(7)
    for t in range(12):
        nI, nJ = 40, 14
        M = rng.random((nI, nJ)) < 0.18
        M[np.arange(nI), rng.integers(0, nJ, nI)] = True              # her satır kapsanabilir
        lock_open = np.zeros(nJ, dtype=bool)
        lock_closed = np.zeros(nJ, dtype=bool)
        if t % 3 == 1:
            lock_open[rng.integers(0, nJ)] = True
        if t % 3 == 2:
            j = rng.integers(0, nJ)
            lock_closed[j] = True
            lone = M.sum(1) == M[:, j]                                  # yalnız j'nin kapsadığı satır kalmasın
            M[np.flatnonzero(lone), (j + 1) % nJ] = True
        ai, aj = np.nonzero(M)
        inst = SimpleNamespace(s=SimpleNamespace(arc_i=ai, arc_j=aj, nA=len(ai)), nI=nI, nJ=nJ,
                               lock_open=lock_open, lock_closed=lock_closed)
        a = bounds.lscp(inst, time_limit=20, reduce=True)
        b = bounds.lscp(inst, time_limit=20, reduce=False)
        assert a["proven"] and b["proven"] and a["value"] == b["value"]
        # kaba kuvvet (bit maskeleri)
        col = [int(sum(1 << i for i in np.flatnonzero(M[:, j]))) for j in range(nJ)]
        full = (1 << nI) - 1
        best = None
        for mask in range(1 << nJ):
            js = [j for j in range(nJ) if mask >> j & 1]
            if any(lock_closed[j] for j in js) or not all(mask >> j & 1 for j in np.flatnonzero(lock_open)):
                continue
            cov = 0
            for j in js:
                cov |= col[j]
            if cov == full and (best is None or len(js) < best):
                best = len(js)
        assert a["value"] == best
        sel = a["open_pos"]
        assert len(sel) == best and M[:, sel].any(1).all()
        assert not lock_closed[sel].any() and set(np.flatnonzero(lock_open)) <= set(sel)
    assert a["reduction"]["rows_reduced"] < nI


def test_duals_5km(res5, inst5):
    sol = _sol_from_result(inst5, res5)
    d = evaluate.assignment_duals(inst5, sol)
    mc = d["marginal_cost_per_person_tl"]
    assert mc.shape == (inst5.nI,) and np.isfinite(mc).all() and (mc > 0).all()
    bv = d["bed_value_tl"]
    assert bv.shape == (sol.n_open,) and (bv >= -1e-3).all()
    # kişi başı marjinal maliyet ≥ en yakın açık hastaneye erişim maliyeti λ·d^α (asgari yatakla kurulmuş, boş
    # kapasitesi olan hastaneye ek talep yalnız erişim maliyeti doğurur: kapasite duali 0)
    s = inst5.s
    open_mask = np.zeros(inst5.nJ, dtype=bool)
    open_mask[sol.open_j] = True
    dmin = np.full(inst5.nI, np.inf)
    a = np.flatnonzero(open_mask[s.arc_j])
    np.minimum.at(dmin, s.arc_i[a], s.arc_d[a])
    assert (mc >= inst5.params.lam * dmin ** inst5.params.alpha * (1 - 1e-6)).all()
    # dolu (yatak > alt sınır) hastanelere atanan hücrelerde yatak maliyeti de marjinal maliyete girer
    assert np.median(mc) > inst5.params.beds_per_person * inst5.var.min() * 0.9


def test_heuristic_feasible_and_close(inst5, res5):
    h = heuristics.heuristic(inst5, time_budget=3)
    assert h.ok and checks.check(inst5, h.solution)["ok"]
    assert h.objective_tl <= res5["bounds"]["lb_tl"] * 1.05


# ---------------------------------------------------------------- bağımlılıklar
ALLOWED_STDLIB = {"__future__", "collections", "dataclasses", "hashlib", "itertools", "json", "math", "multiprocessing",
                  "os", "pathlib", "queue", "threading", "time", "typing"}
ALLOWED_THIRD = {"numpy", "scipy", "highspy"}


def test_backend_imports_only_allowed_packages():
    code = ("import sys; import backend.model.solve, backend.model.bounds; "
            "bad = [m for m in ('pandas', 'geopandas', 'shapely', 'pyproj', 'rasterio', 'yaml', 'statsmodels') "
            "if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=common.ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    req = (common.ROOT / "requirements.txt").read_text(encoding="utf-8")
    for f in sorted((common.ROOT / "backend" / "model").glob("*.py")):
        src = f.read_text(encoding="utf-8")
        mods = set(re.findall(r"^\s*(?:from|import)\s+([a-zA-Z_][\w]*)", src, flags=re.M))
        extra = mods - {"."} - ALLOWED_STDLIB - ALLOWED_THIRD
        assert not extra, (f.name, extra)
        for m in mods & ALLOWED_THIRD:
            assert re.search(rf"^{m}==", req, flags=re.M), m
