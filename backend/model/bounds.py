"""'En fazla kaç? / En az kaç?' kartları (şartname 4.5 sınır kartları).

  * Izgaraya sığan en fazla hastane: |J| (açılabilir aday hücre sayısı; yasaklılar hariç).
  * Kapasite alt sınırı: ⌈Σ q_i / U_max⌉ — şehir hastanesi sınıfıyla (U_max = şehir en çok yatak) ve onsuz
    (U_max = genel hastane en çok yatak).
  * Kapsama alt sınırı (küme kapsama, LSCP; Toregas vd. 1971): min Σ_j y_j  s.t.  Σ_{j∈N_i} y_j ≥ 1 ∀ i. Kartın
    anlamı "her talep hücresine R içinde (yol mesafesi) bir hastane": N_i = R içindeki TÜM adaylar (k = 0 yapısı).
    İkinci satır modelin kendi listeleriyle (N_i = R içindeki en yakın k aday; varsayılan k = 50): modelin olurlu
    çözümleri için geçerli alt sınır, k kırpması yüzünden geometrik minimumdan büyük olabilir (doğrulama C2/C3).
    Kilitler uygulanır. Satır/sütun baskınlığıyla küçültülür, HiGHS ile kesin çözülür.
  * Gerçek p_min: modelin tüm kısıtları altında (k listeleri, kapasite, sınıflar, R, kilitler, bütçe) min Σ_j z_j.
    HiGHS ile, süre sınırlı; kanıtlanamazsa alt/üst sınır raporlanır.
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


def _dominated(B: np.ndarray, drop: str) -> np.ndarray:
    """0/1 matrisin satırları arasında küme kapsama baskınlığı; tutulan satırların maskesi.
    drop='ust': N_i ⊆ N_i' ise i' atılır (kapsama satırı: i'yi kapsayan her küme i'yi de kapsar).
    drop='alt': C_j ⊆ C_j' ise j atılır (birim maliyetli aday: j yerine j' seçilebilir). Eşitlerden biri kalır."""
    size = B.sum(1)
    sub = (B @ B.T) >= size[:, None] - 0.5             # sub[a, b]: satır a ⊆ satır b
    np.fill_diagonal(sub, False)
    r = np.arange(len(size))
    if drop == "ust":                                   # b atılır: kendinden küçük (eşitse önce gelen) bir alt kümesi var
        return ~(sub & ((size[:, None] < size[None, :]) | ((size[:, None] == size[None, :])
                                                          & (r[:, None] < r[None, :])))).any(0)
    return ~(sub & ((size[:, None] < size[None, :]) | ((size[:, None] == size[None, :])
                                                       & (r[None, :] < r[:, None])))).any(1)


def lscp(inst: Instance, *, time_limit: float = 20.0, reduce: bool = True) -> dict:
    """Küme kapsama (LSCP; Toregas vd. 1971): min Σ_j y_j  s.t.  Σ_{j∈N_i} y_j ≥ 1 ∀ i, N_i = örneğin yay listesi
    (k = 0 yapısında R içindeki TÜM adaylar; modelin yapısında k en yakın). Kilitler uygulanır: yasak adaylar yok,
    zorunlu adaylar sayılır ve kapsadıkları satırlar düşer. reduce: satır ve sütun baskınlığıyla küçültme (birim
    maliyetli kapsamada optimum değeri değiştirmez: kapsadığı satırlar başka bir adayınkilerin alt kümesi olan aday
    atılabilir) — 2 km k = 50 ispatını HiGHS için belirgin kolaylaştırır."""
    t0 = time.perf_counter()
    s = inst.s
    nJ, nI = inst.nJ, inst.nI
    A = sp.csr_matrix((np.ones(s.nA, dtype=np.float32), (s.arc_i, s.arc_j)), shape=(nI, nJ))
    A.sum_duplicates()
    A.data[:] = 1.0
    allowed = ~inst.lock_closed
    forced = inst.lock_open & allowed
    covered = np.asarray(A[:, np.flatnonzero(forced)].sum(1)).ravel() > 0 if forced.any() else np.zeros(nI, bool)
    rows = np.flatnonzero(~covered)
    cols = np.flatnonzero(allowed & ~forced)
    B = A[rows][:, cols].toarray()
    info = {"rows": int(nI), "cols": int(allowed.sum()), "forced": int(forced.sum())}
    if len(rows) and (B.sum(1) == 0).any():
        return {"status": "Infeasible", "value": None, "lower_bound": None, "proven": True, "open_pos": [],
                "time_s": time.perf_counter() - t0, "reduction": info}
    if reduce and len(rows):
        for _ in range(20):
            n0 = B.shape
            keep_r = _dominated(B, "ust")
            B, rows = B[keep_r], rows[keep_r]
            keep_c = _dominated(np.ascontiguousarray(B.T), "alt")
            B, cols = B[:, keep_c], cols[keep_c]
            if B.shape == n0:
                break
    info.update(rows_reduced=int(B.shape[0]), cols_reduced=int(B.shape[1]), reduce_s=time.perf_counter() - t0)
    nf = int(forced.sum())
    if not len(rows):
        sel = np.flatnonzero(forced)
        return {"status": "Optimal", "value": nf, "lower_bound": nf, "proven": True, "open_pos": sel.tolist(),
                "time_s": time.perf_counter() - t0, "reduction": info}
    M = sp.csc_matrix(B)
    nr, nc = M.shape
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(max(time_limit - (time.perf_counter() - t0), 1.0)))
    h.setOptionValue("mip_rel_gap", 0.0)
    h.passModel(nc, nr, int(M.nnz), 1, 1, 0.0, np.ones(nc), np.zeros(nc), np.ones(nc), np.ones(nr),
                np.full(nr, INF), M.indptr.astype(np.int32), M.indices.astype(np.int32), M.data.astype(float),
                np.ones(nc, dtype=np.int32))
    h.run()
    hinfo = h.getInfo()
    status = h.modelStatusToString(h.getModelStatus())
    ok = hinfo.primal_solution_status == 2
    sel = cols[np.flatnonzero(np.asarray(h.getSolution().col_value) > 0.5)] if ok else np.zeros(0, dtype=int)
    sel = np.union1d(sel, np.flatnonzero(forced))
    lbv = _ceil_lb(hinfo.mip_dual_bound)
    return {"status": status, "value": (int(round(hinfo.objective_function_value)) + nf) if ok else None,
            "lower_bound": (lbv + nf) if lbv is not None else None, "proven": status == "Optimal",
            "nodes": int(hinfo.mip_node_count), "open_pos": sel.astype(int).tolist(),
            "time_s": time.perf_counter() - t0, "reduction": info}


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


def all_candidates_instance(inst: Instance) -> Instance | None:
    """Aynı parametre ve kilitlerle, R içindeki tüm adayların yay listesiyle (k = 0) kurulan örnek; model zaten k = 0
    ise kendisi. Oyuncak örneklerde None (yapı ızgaradan kurulamaz)."""
    if inst.s.k_nearest == 0:
        return inst
    if inst.s.source == "toy":
        return None
    from .instance import build_instance
    return build_instance(int(inst.s.cell_km), inst.params.with_overrides({"grid.k_nearest": 0}), inst.locks)


def bound_cards(inst: Instance, *, lscp_time: float = 20.0, pmin_time: float = 30.0, with_pmin: bool = True,
                lscp_all_time: float | None = None, inst_all: Instance | None = None) -> dict:
    """Sayfadaki 'En fazla kaç? / En az kaç?' kartlarının değerleri.

    coverage_lscp: R içindeki tüm adaylarla (k = 0; kartın anlamı). coverage_lscp_k_list: modelin k listeleriyle
    (p_min'in alt sınırı bu ikincisidir; k = 0 modelinde ikisi aynıdır)."""
    cap = capacity_bound(inst)
    k = int(inst.s.k_nearest)
    ls_k = lscp(inst, time_limit=lscp_time)
    ls_k.update(k_nearest=k, arcs=int(inst.nA))
    if inst_all is None:
        inst_all = all_candidates_instance(inst)
    if inst_all is inst:
        ls_all = dict(ls_k)
    elif inst_all is not None:
        ls_all = lscp(inst_all, time_limit=lscp_all_time if lscp_all_time is not None else lscp_time)
        ls_all.update(k_nearest=0, arcs=int(inst_all.nA))
    else:
        ls_all = None
    out = {
        "max_hospitals_grid": int((~inst.lock_closed).sum()),
        "candidates_total": inst.nJ,
        "capacity": cap,
        "coverage_lscp": ls_all,
        "coverage_lscp_k_list": ls_k,
    }
    if with_pmin:
        pm = p_min(inst, time_limit=pmin_time, cover_open=ls_k["open_pos"])
        lbs = [v for v in (pm["lower_bound"], ls_k["lower_bound"], cap["applies"], int(inst.lock_open.sum()),
                           inst.count_min) if v is not None]
        pm["lower_bound"] = max(lbs) if lbs else None
        if pm["value"] is not None and pm["lower_bound"] is not None and pm["lower_bound"] >= pm["value"]:
            pm["proven"] = True
        pm["k_nearest"] = k
        out["p_min"] = pm
    R = f"{inst.s.R_km:g}"
    out["labels_tr"] = {
        "max_hospitals_grid": "Izgaraya sığan en fazla hastane (aday hücre sayısı |J|)",
        "capacity": "Kapasite alt sınırı ⌈toplam yatak talebi / en büyük hastane⌉",
        "coverage_lscp": f"Kapsama alt sınırı: her talep hücresine R = {R} km yol mesafesi içinde bir hastane "
                         f"(küme kapsama; R içindeki tüm adaylar)",
        "coverage_lscp_k_list": f"Kapsama alt sınırı, model listeleriyle (k = {k}): her talep hücresine en yakın {k} "
                                f"adayından biri R = {R} km içinde açık" if k > 0 else
                                f"Kapsama alt sınırı, model listeleriyle (k = 0: R içindeki tüm adaylar)",
        "p_min": f"Gerçek en az hastane sayısı: modelin tüm kısıtlarıyla (kapasite, sınıflar, R; model listeleri "
                 f"k = {k})",
    }
    return out
