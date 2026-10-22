"""'En fazla kaç? / En az kaç?' kartları (şartname 4.5 sınır kartları).

  * Izgaraya sığan en fazla hastane: |J| (açılabilir aday hücre sayısı; yasaklılar hariç).
  * Kapasite alt sınırı: ⌈Σ q_i / U_max⌉ — şehir hastanesi sınıfıyla (U_max = şehir en çok yatak) ve onsuz
    (U_max = genel hastane en çok yatak).
  * Kapsama alt sınırı (küme kapsama, LSCP; Toregas vd. 1971): min Σ_j y_j  s.t.  Σ_{j∈N_i} y_j ≥ 1 ∀ i
    (N_i modelin yay listesi: R içinde, k-en-yakınla kırpılmış; kilitler uygulanır). HiGHS ile.
  * Gerçek p_min: tüm kısıtlar altında (kapasite, sınıflar, R, kilitler, bütçe) min Σ_j z_j. HiGHS ile, süre sınırlı;
    kanıtlanamazsa alt/üst sınır raporlanır.
"""
from __future__ import annotations

import math
import time

import numpy as np
import scipy.sparse as sp
import highspy

from .instance import Instance
from .milp import MilpModel

INF = highspy.kHighsInf


def capacity_bound(inst: Instance) -> dict:
    Q = inst.total_demand
    p = inst.params
    gen_max = float(p["size.general_max_beds"])
    city_max = float(p["size.city_max_beds"])
    with_city = math.ceil(Q / max(gen_max, city_max) - 1e-9)
    without = math.ceil(Q / gen_max - 1e-9)
    return {"total_demand_beds": Q, "with_city": with_city, "without_city": without,
            "applies": with_city if inst.class_on[-1] else without, "U_max_general": gen_max, "U_max_city": city_max}


def lscp(inst: Instance, *, time_limit: float = 20.0) -> dict:
    """Küme kapsama alt sınırı (her talep hücresinin R içinde en az bir açık adayı)."""
    t0 = time.perf_counter()
    s = inst.s
    nJ, nI = inst.nJ, inst.nI
    A = sp.csc_matrix((np.ones(s.nA), (s.arc_i, s.arc_j)), shape=(nI, nJ))
    A.data[:] = 1.0
    A.sum_duplicates()
    A.data[:] = 1.0
    A.sort_indices()
    lb = inst.lock_open.astype(float)
    ub = (~inst.lock_closed).astype(float)
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", 0.0)
    h.passModel(nJ, nI, int(A.nnz), 1, 1, 0.0, np.ones(nJ), lb, ub, np.ones(nI), np.full(nI, INF),
                A.indptr.astype(np.int32), A.indices.astype(np.int32), A.data.astype(float),
                np.ones(nJ, dtype=np.int32))
    h.run()
    info = h.getInfo()
    status = h.modelStatusToString(h.getModelStatus())
    ok = info.primal_solution_status == 2
    sel = np.flatnonzero(np.asarray(h.getSolution().col_value) > 0.5) if ok else np.zeros(0, dtype=int)
    return {"status": status, "value": int(round(info.objective_function_value)) if ok else None,
            "lower_bound": _ceil_lb(info.mip_dual_bound), "proven": status == "Optimal",
            "open_pos": sel.tolist(), "time_s": time.perf_counter() - t0}


def _ceil_lb(x) -> int | None:
    if x is None or not math.isfinite(x) or abs(x) >= 1e50:
        return None
    return int(math.ceil(x - 1e-6))


def pmin_start(inst: Instance, cover_open: list[int], *, max_adds: int = 200) -> tuple[np.ndarray, object]:
    """p_min için olurlu başlangıç: LSCP kümesinden başla; en yakın atamada sınıf üst sınırını aşan hastanelerin
    yükünü en çok azaltan adayı ekle; kesin değerlendirme (evaluate) olurlu olunca dur."""
    from .evaluate import evaluate
    from .heuristics import ApproxModel

    M = ApproxModel(inst)
    open_mask = np.zeros(inst.nJ, dtype=bool)
    open_mask[np.asarray(cover_open, dtype=np.int64)] = True
    open_mask |= inst.lock_open
    s = inst.s
    ev = None
    for _ in range(max_adds):
        st = M.state(open_mask)
        over = np.maximum(st.b - np.where(np.isfinite(M.Umax), M.Umax, 0.0), 0.0)
        if st.unassigned == 0 and not (over[open_mask] > 1e-9).any():
            ev = evaluate(inst, np.flatnonzero(open_mask), gap=1e-2, time_limit=10.0)
            if ev.ok:
                return open_mask, ev
        # rahatlama: yeni aday c açılırsa aşırı yüklü hastanelerden (ya da atanmamış talepten) ona geçecek talep
        cur_arc = st.arc[s.arc_i]
        moves = M.openable[s.arc_j] & ~open_mask[s.arc_j] & ((cur_arc < 0) | (np.arange(s.nA) < cur_arc))
        src = st.c[s.arc_i]
        hot = (src < 0) | (over[np.maximum(src, 0)] > 1e-9)
        if ev is not None and not ev.ok and not hot[moves].any():
            hot = np.ones_like(hot)        # aşım yok ama kesin değerlendirme olurlu değil: en çok talep çekeni ekle
        a = np.flatnonzero(moves & hot)
        if not len(a):
            break
        relief = np.bincount(s.arc_j[a], inst.q[s.arc_i[a]], minlength=inst.nJ)
        open_mask[int(np.argmax(relief))] = True
    return open_mask, ev


def p_min(inst: Instance, *, time_limit: float = 30.0, linking="weak", cover_open: list[int] | None = None) -> dict:
    """Tüm model kısıtları altında en az hastane sayısı (amaç Σ z_j): kapasite, sınıf aralıkları, R, kilitler; mod butce
    ise bütçe de. Kullanıcının hastane sayısı sınırları uygulanmaz. LSCP çözümünden kurulan olurlu çözüm MIP
    başlangıcı olarak verilir."""
    t0 = time.perf_counter()
    m = MilpModel(inst, linking=linking)
    lay = m.lay
    cost = np.zeros(lay.ncol)
    cost[lay.z0:lay.z0 + lay.nJ] = 1.0
    m.h.changeColsCost(lay.ncol, np.arange(lay.ncol, dtype=np.int32), cost)
    # kullanıcının sayı sınırları (en az/en çok, sabit p) bu kartta yok sayılır: fiziksel en az sayı aranır
    m.h.changeRowBounds(lay.r_count, 0.0, INF)
    # geçerli kapsama eşitsizlikleri Σ_{j∈N_i} z_j ≥ 1 (her talep bir açık hastaneye atanmalı): LP sınırını LSCP'ye
    # yaklaştırır, ispatı hızlandırır
    s = inst.s
    m.h.addRows(inst.nI, np.ones(inst.nI), np.full(inst.nI, INF), s.nA, s.arc_ptr[:-1].astype(np.int32),
                (lay.z0 + s.arc_j).astype(np.int32), np.ones(s.nA))
    start = None
    start_n = None
    if cover_open is None:
        cover_open = lscp(inst, time_limit=min(20.0, time_limit))["open_pos"]
    if cover_open:
        _, ev = pmin_start(inst, cover_open)
        if ev is not None and ev.ok:
            start, start_n = ev.solution, ev.solution.n_open
    remaining = max(1.0, time_limit - (time.perf_counter() - t0))
    res = m.solve(time_limit=remaining, gap=0.0, abs_gap=0.999, start=start)
    val = res.solution.n_open if res.solution is not None else start_n
    lbv = _ceil_lb(res.dual_tl / 1e6) if res.dual_tl is not None else None
    return {"status": res.status, "status_tr": res.status_tr, "value": val, "lower_bound": lbv,
            "proven": res.status == "Optimal", "start_value": start_n, "time_s": time.perf_counter() - t0}


def bound_cards(inst: Instance, *, lscp_time: float = 20.0, pmin_time: float = 30.0, with_pmin: bool = True) -> dict:
    """Sayfadaki 'En fazla kaç? / En az kaç?' kartlarının değerleri."""
    cap = capacity_bound(inst)
    ls = lscp(inst, time_limit=lscp_time)
    out = {
        "max_hospitals_grid": int((~inst.lock_closed).sum()),
        "candidates_total": inst.nJ,
        "capacity": cap,
        "coverage_lscp": ls,
    }
    if with_pmin:
        pm = p_min(inst, time_limit=pmin_time, cover_open=ls["open_pos"])
        lbs = [v for v in (pm["lower_bound"], ls["lower_bound"], cap["applies"], int(inst.lock_open.sum()),
                           inst.count_min) if v is not None]
        pm["lower_bound"] = max(lbs) if lbs else None
        if pm["value"] is not None and pm["lower_bound"] is not None and pm["lower_bound"] >= pm["value"]:
            pm["proven"] = True
        out["p_min"] = pm
    out["labels_tr"] = {
        "max_hospitals_grid": "Izgaraya sığan en fazla hastane (aday hücre sayısı |J|)",
        "capacity": "Kapasite alt sınırı ⌈toplam yatak talebi / en büyük hastane⌉",
        "coverage_lscp": f"Kapsama alt sınırı: her hücre R = {inst.s.R_km:g} km içinde bir hastaneye (küme kapsama)",
        "p_min": "Gerçek en az hastane sayısı (tüm kısıtlar)",
    }
    return out
