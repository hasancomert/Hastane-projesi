"""Faz 4 incelemesi düzeltmeleri — motor tarafı:
  * HiGHS alt süreci: model süreç başladıktan sonra arka planda gönderilir; başlarken ölen alt süreç çözümü düşürmez,
    başlarken takılan alt süreç sert süre sınırını bozmaz (bulgu 3); yedek alt süreç (set_prespawn);
  * iptal edilebilir değerlendirme ve sınır kartı adımları (bulgu 2);
  * harita katmanları ve hastane boyut ayrıntısı (bulgu 6; backend/model/layers.py) — bağımsız hesapla karşılaştırma;
  * önceden çözülmüş çözümlerle motor tohumlama ve ızgaraya göre varsayılan süre sınırı (bulgu 1 ve 4).

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase4_engine.py
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import threading
import time

import numpy as np
import pytest

from backend.model import bounds as B
from backend.model import distance, milp
from backend.model import solve as S
from backend.model.evaluate import evaluate
from backend.model.heuristics import heuristic
from backend.model.instance import build_instance

from .test_phase4_common import ROOT, pid_alive, wait_for

FORCE_CORE = {"time_limit": 10, "gap": 1e-6}        # hedef gap ulaşılamaz → çekirdek MIP alt süreci kesin başlar


def _patched_spawn(monkeypatch, action: str) -> list[int]:
    orig = milp._spawn_proc
    pids: list[int] = []

    def patched(name="highs"):
        pr = orig(name)
        pids.append(pr.proc.pid)
        if action == "kill":
            pr.proc.kill()
        elif action == "stop":
            import signal
            os.kill(pr.proc.pid, signal.SIGSTOP)
        return pr
    monkeypatch.setattr(milp, "_spawn_proc", patched)
    return pids


def _cleanup(pids: list[int]) -> None:
    import signal
    for p in pids:
        if pid_alive(p) and sys.platform != "win32":
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass


def test_child_dead_at_start_does_not_fail_solve(monkeypatch):
    """Alt süreç modeli okumadan ölür (eskiden BrokenPipeError → 'Beklenmeyen hata', en iyi çözüm kayboluyordu)."""
    milp.set_prespawn(False)
    pids = _patched_spawn(monkeypatch, "kill")
    t = time.perf_counter()
    r = S.solve(5, {"demand.safety_margin": 0.03}, **FORCE_CORE)
    wall = time.perf_counter() - t
    _cleanup(pids)
    assert pids, "çekirdek MIP alt süreci hiç başlatılmadı"
    assert r["ok"] and r["check"]["ok"] and r["status"] in ("sure_doldu", "gap_ulasilamadi"), r.get("message_tr")
    assert wall < FORCE_CORE["time_limit"] + 1.0, wall
    assert all(not pid_alive(p) for p in pids)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGSTOP yalnız POSIX'te")
def test_child_stalled_at_start_keeps_hard_time_limit(monkeypatch):
    """Alt süreç başlarken donar (eskiden Process.start() modeli yazarken çözüm iş parçacığını bloke ediyordu: 10 sn'lik
    çözüm 76 sn sürdü). Şimdi gönderim arka planda; süre sınırı + 0,5 sn tutar, alt süreç biçilir."""
    milp.set_prespawn(False)
    pids = _patched_spawn(monkeypatch, "stop")
    try:
        t = time.perf_counter()
        r = S.solve(5, {"demand.safety_margin": 0.04}, **FORCE_CORE)
        wall = time.perf_counter() - t
    finally:
        _cleanup(pids)
    assert pids and r["ok"] and r["check"]["ok"]
    assert wall < FORCE_CORE["time_limit"] + 1.0, wall


def test_isolated_run_child_killed_before_model_is_read():
    inst = build_instance(5)
    h = heuristic(inst, time_budget=0.5)
    m = milp.MilpModel(inst)
    milp.set_prespawn(False)
    t = time.perf_counter()
    run = m.start_isolated(time_limit=30, gap=1e-6, start=h.solution)
    assert time.perf_counter() - t < 1.0                     # başlatma alt süreci beklemez
    run.proc.kill()
    with pytest.raises(RuntimeError, match="beklenmedik"):
        for _ in range(100):
            run.poll(timeout=0.05)
    t = time.perf_counter()
    run.close()
    assert time.perf_counter() - t < 2.0 and not run.proc.is_alive()


def test_prespawned_child_is_used_and_released():
    milp.set_prespawn(True)
    try:
        assert wait_for(lambda: milp.prespawn_info()["ready"], 30.0), milp.prespawn_info()
        spare_pid = milp.prespawn_info()["pid"]
        r = S.solve(5, {"demand.safety_margin": 0.05}, **FORCE_CORE)
        att = r["core_mip"]["attempts"]
        assert att[0]["from_spare"] is True and r["ok"]
        assert not pid_alive(spare_pid) or spare_pid != milp.prespawn_info()["pid"]
        assert wait_for(lambda: milp.prespawn_info()["ready"], 30.0)          # yerine yenisi açıldı
    finally:
        milp.set_prespawn(False)
    assert wait_for(lambda: not mp.active_children(), 10.0), mp.active_children()


def test_evaluate_and_bounds_are_cancellable():
    import orjson
    pre = orjson.loads((ROOT / "precomputed" / "default_3km.json").read_bytes())
    inst = build_instance(3)
    Sx = np.array([h["cand_pos"] for h in pre["hospitals"]])
    ev = threading.Event()
    threading.Timer(0.3, ev.set).start()
    t = time.perf_counter()
    r = evaluate(inst, Sx, cancel_event=ev)
    assert not r.ok and r.status == "durduruldu" and time.perf_counter() - t < 2.0
    ev2 = threading.Event()
    threading.Timer(1.0, ev2.set).start()
    t = time.perf_counter()
    pm = B.p_min(inst, time_limit=120, cancel_event=ev2)
    assert time.perf_counter() - t < 15.0 and not pm["proven"]


def test_map_layers_against_independent_computation():
    r = S.solve(5, {"demand.safety_margin": 0.02}, time_limit=5)
    assert r["ok"]
    inst = build_instance(5, S.Params.load({"grid.cell_km": 5, "demand.safety_margin": 0.02}))
    L = r["layers"]
    g = inst.s.grid
    acc = L["access"]
    assert acc["order"] == "grid" and len(acc["nearest_km"]) == g.n == len(acc["nearest_hospital"])
    # bağımsız: yoğun mesafe matrisi (tüm hücreler × açık hastaneler)
    hxy = np.array([inst.s.cand_xy[h["cand_pos"]] for h in r["hospitals"]])
    D = distance.pairwise_road_km(g.xy, hxy, inst.s.cell_km, inst.s.circ)
    assert np.allclose(acc["nearest_km"], D.min(axis=1), atol=1e-3)
    assert np.allclose(D[np.arange(g.n), acc["nearest_hospital"]], D.min(axis=1), atol=1e-6)
    # en yakın ≤ atanan; hizmet bölgesi = en büyük paylı hastane; akış nüfusu toplamı = nüfus
    dem = L["demand"]
    gi = np.array(dem["grid_index"])
    a = r["assignment"]
    did = {c: i for i, c in enumerate(dem["cell_id"])}
    best = {}
    for dc, hh, sh, km in zip(a["demand_cell"], a["hospital"], a["share"], a["km"]):
        i = did[dc]
        assert acc["nearest_km"][gi[i]] <= km + 1e-3
        if sh > best.get(i, (-1, 0))[1]:
            best[i] = (hh, sh)
    assert all(dem["service_hospital"][i] == hh for i, (hh, _) in best.items())
    assert abs(sum(a["population"]) - r["kpis"]["population"]) < 1e-6 * r["kpis"]["population"]
    assert len(L["candidates"]["cell_id"]) == inst.nJ and sum(L["candidates"]["open"]) == r["kpis"]["n_hospitals"]
    # R halkası: kuş uçuşu yarıçapın yol karşılığı R
    rg = L["rings"]
    assert abs(float(inst.s.circ.road_km(rg["R_crowfly_km"])) - rg["R_km"]) < 1e-2 and rg["R_crowfly_km"] < rg["R_km"]
    # boyut ayrıntısı
    for h in r["hospitals"]:
        sz = h["size"]
        assert sz["class_min_beds"] - 1e-6 <= h["beds"] <= sz["class_max_beds"] + 1e-6
        if sz["at_bound"] == "ust":
            assert abs(h["beds"] - sz["class_max_beds"]) < 1e-3
        assert abs(sz["opening_tl"] - h["costs"]["acilis"]) < 1e-6 * h["costs"]["acilis"]
    assert r["kpis"]["cost_per_capita_tl"] == pytest.approx(r["costs"]["toplam"] / r["kpis"]["population"])


def test_seed_engine_from_precomputed_and_grid_time_limit():
    from backend.app import task_solve
    from backend.app.schemas import SolveIn
    S.clear_cache(all_caches=False)
    task_solve._SEEDED.clear()
    inst = build_instance(3, S.Params.load({"grid.cell_km": 3, "cost.fixed_per_hospital": 300_000_000}))
    n = task_solve.seed_engine(inst)
    assert n >= 1 and S.remembered_count(inst) == n
    assert task_solve.seed_engine(inst) == 0                     # yapı başına bir kez
    r = S.solve(instance=inst, time_limit=4)
    assert r["ok"] and r["seeds"] and r["seeds"][0]["adopted"]
    # süre sınırı verilmezse ızgaranın önerilen süresi; overrides'ta açıkça verilirse o; istekteki her zaman önce
    assert task_solve.prepare(SolveIn(grid_km=2)).payload["time_limit"] == 60.0
    assert task_solve.prepare(SolveIn(grid_km=3)).payload["time_limit"] == 15.0
    assert task_solve.prepare(SolveIn(grid_km=2, overrides={"solver.time_limit_s": 30})).payload["time_limit"] == 30
    assert task_solve.prepare(SolveIn(grid_km=2, time_limit=7)).payload["time_limit"] == 7
    S.clear_cache(all_caches=False)
    task_solve._SEEDED.clear()


def test_seed_engine_adds_same_mode_solution_when_missing():
    """Toplam modundaki çözümlerden sonra gelen ilk bütçe isteği: bütçe senaryosunun önceden çözülmüş çözümü başa
    eklenir (toplam çözümleri bütçeye sığmaz)."""
    from backend.app import task_solve
    S.clear_cache(all_caches=False)
    task_solve._SEEDED.clear()
    p_t = S.Params.load({"grid.cell_km": 5})
    inst_t = build_instance(5, p_t)
    assert task_solve.seed_engine(inst_t) >= 1 and "butce" not in S.remembered_modes(inst_t)[:1]
    sc = next(e for e in task_solve._precomputed_solutions() if e["name"] == "scenario_dusuk_butce_5km")
    inst_b = build_instance(5, S.Params.load(sc["overrides"]))
    assert inst_b.s.key == inst_t.s.key and inst_b.mode == "butce"
    assert task_solve.seed_engine(inst_b) == 1 and S.remembered_modes(inst_b)[0] == "butce"
    assert task_solve.seed_engine(inst_b) == 0
    r = S.solve(instance=inst_b, time_limit=6)
    assert r["ok"] and r["seeds"][0]["adopted"] and r["check"]["ok"]
    S.clear_cache(all_caches=False)
    task_solve._SEEDED.clear()
