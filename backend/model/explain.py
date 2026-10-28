"""Açıklama katmanının motor yardımcıları (Faz 6; şartname 7.3–7.9): "neden bu çözüm optimum?".

Bu modül yeni bir model kurmaz; motorun kendi parçalarını (evaluate, solve, Lagrange sezgiseli, sınır denetimi) çağırır.
Her sayı çözümden, parametre kaydından (data/reference/parameters.json) ya da çözücünün sınırlarından gelir.

İçerik
  * hospital_solution        sonuçtaki hastaneler (hücre, sınıf, yatak) → motorun Solution'ı (kesin değerlendirme)
  * closure_effect           7.4 "bu hastane kapatılırsa": hızlı tahmin = evaluate(açık küme − j): konumlar sabit,
                             yeniden atama + yeniden boyutlandırma (+ en yakın komşu hastanelerde sınıf seçimi). Kalan
                             hastanelerle OLURLU bir çözümdür, yani gerçek (yeniden optimize edilmiş) artışın ÜST
                             sınırıdır. İsteğe bağlı: tüm hastanelerde sınıf seçimli kesin değerlendirme ve j yasakken
                             tam yeniden çözüm (reoptimize_closed).
  * best_with_p              7.3 "neden N?": sabit_p modunda N − 1 / N + 1 hastaneli en iyi çözüm; başlangıç mevcut
                             çözümden tek tek kapatma/açma ile türetilir (neighbour_solution).
  * size_explanations        7.5 "neden bu boyut?": atanan talep, doluluk, sınıf eşiğindeki maliyet sıçraması, 1 ek
                             yatağın erişim değeri (dual) ile yatak başı maliyetin karşılaştırılması.
  * shadow_prices            7.6 gölge fiyatlar: atama LP'sinin dual değerleri, özet ve Türkçe yorum (dejenere LP'de
                             dual değerler tek değildir).
  * model_reasons            model düzeyindeki nedenler (sınıf maliyet tablosu, eşiklerde yığılma, R'nin zorladığı
                             hastaneler, bölme karşılaştırması, uzmanlaşma faydasının modellenmediği).
  * p_sweep / pareto / tornado  7.7–7.9 tarama çekirdekleri (ilerleme geri çağrısı, iptal, sıcak başlangıç).

"Fark gap içinde" kuralı (Faz 3 raporu §8, precomputed/p_sweep_*.json): bir karşılaştırma noktasının ALT sınırı
referans çözümün bulunan değerini aşmıyorsa (lb ≤ ub_ref) iki çözüm bu sınırlarla ayırt edilemez. Serbest (toplam modu)
problemin alt sınırı her sabit p ve her "j yasak" problemi için de geçerli alt sınırdır (onların gevşetmesidir).
Para TL, mesafe yol km. Türkçe sayı biçimi (1.234,5).
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Mapping

import numpy as np
import scipy.sparse as sp

from . import checks as checks_mod
from . import distance
from .evaluate import CANCELLED_TR, INF, EvalResult, _highs, _pass, assignment_duals, cost_breakdown, evaluate, run_highs
from .instance import Instance, Solution, build_instance
from .milp import BUDGET_EPS, SCALE
from .params import SPEC, ParamError, Params
# hafif ortak parçalar (sunucu süreci de kullanır); bu modülden de erişilebilsinler diye yeniden dışa aktarılır
from .explain_common import (CLOSE_EXACT_TIME, CLOSE_NEIGHBOURS, GROUP_LABEL_TR, LAMBDA_KEY,  # noqa: F401
                             NEIGHBOUR_TRY, PARETO_FACTORS, REL_TOL, TORNADO_DELTA, TORNADO_SPECS, apply_reference, fmt,
                             n_message, p_ranges, pareto_summary, pct, sweep_summary, tl, tornado_specs,
                             tornado_summary)


ProgressCb = Callable[[dict], None] | None


# ---------------------------------------------------------------- biçim


def _jsonable(o):
    from .solve import _jsonable as j
    return j(o)


# ---------------------------------------------------------------- çözüm dönüşümleri
def cell_positions(inst: Instance) -> dict[str, int]:
    return {str(c): j for j, c in enumerate(inst.s.cand_ids)}


def solution_from_result(inst: Instance, r: Mapping) -> Solution:
    """Sonuç sözlüğünden (hospitals + assignment) Solution; yay indisleri bu örneğin yapısından bulunur."""
    s = inst.s
    pos = cell_positions(inst)
    oj = np.array([pos[h["cell_id"]] for h in r["hospitals"]], dtype=np.int64)
    code = {c.code: k for k, c in enumerate(inst.classes)}
    cls = np.array([code[h["class"]] for h in r["hospitals"]], dtype=np.int64)
    beds = np.array([float(h["beds"]) for h in r["hospitals"]])
    did = {str(c): i for i, c in enumerate(s.demand_ids)}
    x = np.zeros(inst.nA)
    a = r["assignment"]
    for dc, hidx, share in zip(a["demand_cell"], a["hospital"], a["share"]):
        i = did[dc]
        js = s.arc_j[s.arc_ptr[i]:s.arc_ptr[i + 1]]
        x[s.arc_ptr[i] + int(np.flatnonzero(js == oj[hidx])[0])] = share
    o = np.argsort(oj)
    return Solution(oj[o], cls[o], beds[o], x, source="sonuc")


def hospital_solution(inst: Instance, hospitals: list[Mapping], *, time_limit: float = CLOSE_EXACT_TIME,
                      cancel_event=None) -> EvalResult:
    """Açıklanan çözümün hastaneleri [{cell_id, class?, beds?}] → kesin değerlendirme (EvalResult; çözüm motorun
    sırasında). Sınıf ve yatak hepsinde verilmişse yalnız atama LP'si (çözümün kendisi); sınıflar verilip yataklar
    verilmemişse boyutlandırma LP'si; aksi halde sınıf seçimli MIP (verilen sınıflar sabit)."""
    pos = cell_positions(inst)
    code = {c.code: k for k, c in enumerate(inst.classes)}
    oj = np.array([pos[str(h["cell_id"])] for h in hospitals], dtype=np.int64)
    cls = [None if h.get("class") is None else code[str(h["class"])] for h in hospitals]
    beds = [None if h.get("beds") is None else float(h["beds"]) for h in hospitals]
    if all(k is not None for k in cls):
        k = np.array(cls, dtype=np.int64)
        if all(b is not None for b in beds):
            b = np.clip(np.array(beds, dtype=float), inst.L[k], inst.U[k])
            ev = evaluate(inst, oj, cls=k, beds=b, cancel_event=cancel_event)
            if ev.ok:
                return ev
        return evaluate(inst, oj, cls=k, cancel_event=cancel_event, time_limit=time_limit)
    cf = np.array([-1 if k is None else k for k in cls], dtype=np.int64)
    return evaluate_partial(inst, oj, cf, time_limit=time_limit, cancel_event=cancel_event)


def solution_from_cells(inst: Instance, cells, *, gap: float = 1e-4, time_limit: float = 60.0,
                        cancel_event=None) -> Solution | None:
    """Açık hücre kimlikleri → sınıf seçimli kesin değerlendirme çözümü (aday olmayan hücre ya da olursuzsa None)."""
    pos = cell_positions(inst)
    try:
        oj = np.array(sorted(pos[str(c)] for c in cells), dtype=np.int64)
    except KeyError:
        return None
    ev = evaluate(inst, oj, gap=gap, time_limit=time_limit, cancel_event=cancel_event)
    return ev.solution if ev.ok else None


def transfer_solution(inst: Instance, cells, classes=None, *, cancel_event=None) -> Solution | None:
    """Başka parametrelerle bulunmuş çözümü (hücre + sınıf kodu) bu örneğe taşır: aday olmayan hücreler atlanır, sınıflar
    sabit boyutlandırma LP'si + sınıf onarımı (lagrange.fast_eval; olursuzsa sınıf seçimli MIP). Sıcak başlangıç için;
    olurlu değilse None (çözücü kendi sezgiseliyle başlar)."""
    from .lagrange import fast_eval
    pos = cell_positions(inst)
    code = {c.code: k for k, c in enumerate(inst.classes)}
    keep = [(pos[str(c)], code.get(str(k), 0) if k is not None else 0)
            for c, k in zip(cells, classes if classes is not None else [None] * len(cells)) if str(c) in pos]
    if not keep:
        return None
    keep.sort()
    oj = np.array([j for j, _ in keep], dtype=np.int64)
    ck = np.array([k for _, k in keep], dtype=np.int64)
    bad = ~inst.y_ub[oj, ck]
    if bad.any():                               # sınıf bu parametrelerle kapalı: izinli ilk sınıf
        ck[bad] = np.argmax(inst.y_ub[oj[bad]], axis=1)
    if cancel_event is not None and cancel_event.is_set():
        return None
    ev = fast_eval(inst, oj, ck, mip_time=5.0)
    if not ev.ok:
        return None
    sol = ev.solution
    return sol if checks_mod.check(inst, sol)["ok"] else None


# ---------------------------------------------------------------- kısmi sınıf seçimli değerlendirme
def evaluate_partial(inst: Instance, open_j, cls_fixed, *, time_limit: float = CLOSE_EXACT_TIME, gap: float = 1e-6,
                     cancel_event=None) -> EvalResult:
    """evaluate()'in hastane başına genellemesi: cls_fixed[t] ≥ 0 → sınıf sabit, −1 → sınıfı model seçer. Yataklar
    sınıf aralığında serbest. Formülasyon evaluate() ile birebir aynıdır (y_p | B_p | x; atama, sınıf seçimi, alt/üst,
    kapasite, bütçe); hepsi −1 ise evaluate(cls=None), hiçbiri −1 değilse evaluate(cls=…) ile aynı problemdir."""
    t0 = time.perf_counter()
    s = inst.s
    S = np.asarray(open_j, dtype=np.int64)
    cf = np.asarray(cls_fixed, dtype=np.int64)
    nS = len(S)
    if nS == 0:
        return EvalResult(False, "olurlu_degil", "Hiç hastane açık değil.")
    if len(np.unique(S)) != nS:
        return EvalResult(False, "gecersiz", "Aynı hücrede birden fazla hastane açılamaz.")
    pos = np.full(inst.nJ, -1, dtype=np.int64)
    pos[S] = np.arange(nS)
    sub = np.flatnonzero(pos[s.arc_j] >= 0)
    cover = np.zeros(inst.nI, dtype=bool)
    cover[s.arc_i[sub]] = True
    if not cover.all():
        bad = np.flatnonzero(~cover)
        return EvalResult(False, "olurlu_degil", f"{len(bad)} talep hücresinin ({fmt(float(s.w[bad].sum()), 0)} "
                                                 f"kişi) R içinde açık hastanesi yok.")
    allowed = inst.y_ub[S].copy()
    fixed = cf >= 0
    if fixed.any():
        ft = np.flatnonzero(fixed)
        keep = allowed[ft, cf[ft]]
        allowed[ft] = False
        allowed[ft, cf[ft]] = keep
    if not allowed.any(axis=1).all():
        return EvalResult(False, "olurlu_degil", "Açık hücrelerden birinde kullanılabilir boyut sınıfı yok.")
    pj, pk = np.nonzero(allowed)
    nP, nX = len(pj), len(sub)
    jg = S[pj]
    F = inst.fixed[jg, pk]
    V = inst.var[jg, pk]
    w_open = BUDGET_EPS if inst.mode == "butce" else 1.0
    c = np.concatenate([w_open * F, w_open * V, inst.acc[sub]]) / SCALE
    lb = np.zeros(2 * nP + nX)
    ub = np.concatenate([np.ones(nP), inst.U[pk], np.ones(nX)])
    single = allowed.sum(axis=1)[pj] == 1
    lb[:nP][single] = 1.0
    rows, cols, vals = [], [], []

    def add(r, cc, v):
        rows.append(np.asarray(r, np.int64))
        cols.append(np.asarray(cc, np.int64))
        vals.append(np.broadcast_to(np.asarray(v, float), np.shape(r)).astype(float))

    r_as, r_cl, r_lo = 0, inst.nI, inst.nI + nS
    r_up, r_cap, r_bud = inst.nI + nS + nP, inst.nI + nS + 2 * nP, inst.nI + 2 * nS + 2 * nP
    xi = np.arange(nX)
    add(r_as + s.arc_i[sub], 2 * nP + xi, 1.0)
    add(r_cl + pj, np.arange(nP), 1.0)
    add(r_lo + np.arange(nP), nP + np.arange(nP), 1.0)
    add(r_lo + np.arange(nP), np.arange(nP), -inst.L[pk])
    add(r_up + np.arange(nP), nP + np.arange(nP), 1.0)
    add(r_up + np.arange(nP), np.arange(nP), -inst.U[pk])
    add(r_cap + pos[s.arc_j[sub]], 2 * nP + xi, inst.q[s.arc_i[sub]])
    add(r_cap + pj, nP + np.arange(nP), -1.0)
    add(np.full(nP, r_bud), np.arange(nP), F / SCALE)
    add(np.full(nP, r_bud), nP + np.arange(nP), V / SCALE)
    nrow = r_bud + 1
    A = sp.csc_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(nrow, 2 * nP + nX))
    A.sort_indices()
    rlo = np.full(nrow, -INF)
    rhi = np.full(nrow, INF)
    rlo[r_as:r_as + inst.nI] = rhi[r_as:r_as + inst.nI] = 1.0
    rlo[r_cl:r_cl + nS] = rhi[r_cl:r_cl + nS] = 1.0
    rlo[r_lo:r_lo + nP] = 0.0
    rhi[r_up:r_up + nP] = 0.0
    rhi[r_cap:r_cap + nS] = 0.0
    if inst.budget is not None:
        rhi[r_bud] = inst.budget / SCALE
    integ = np.zeros(2 * nP + nX, dtype=np.int32)
    integ[:nP][~single] = 1
    h = _highs()
    _pass(h, c, lb, ub, A, rlo, rhi, integ)
    h.setOptionValue("time_limit", float(time_limit))
    if integ.any():
        h.setOptionValue("mip_rel_gap", float(gap))
    if run_highs(h, cancel_event):
        return EvalResult(False, "durduruldu", CANCELLED_TR, time_s=time.perf_counter() - t0)
    status = h.modelStatusToString(h.getModelStatus())
    info = h.getInfo()
    if info.primal_solution_status != 2:
        return EvalResult(False, "olurlu_degil", "Bu açık hastane kümesiyle olurlu atama yok (kapasite, sınıf ya da "
                                                 "bütçe sınırı).", time_s=time.perf_counter() - t0)
    v = np.asarray(h.getSolution().col_value)
    sel = np.flatnonzero(v[:nP] > 0.5)
    ck = np.full(nS, -1, dtype=np.int64)
    cb = np.zeros(nS)
    ck[pj[sel]] = pk[sel]
    cb[pj[sel]] = v[nP + sel]
    if (ck < 0).any():
        return EvalResult(False, "hata", "Sınıf seçimi okunamadı.", time_s=time.perf_counter() - t0)
    cb = np.clip(cb, inst.L[ck], inst.U[ck])
    x = np.zeros(inst.nA)
    x[sub] = np.clip(v[2 * nP:], 0.0, 1.0)
    x[x < 1e-9] = 0.0
    o = np.argsort(S)
    sol = Solution(open_j=S[o], cls=ck[o], beds=cb[o], x=x, source="evaluate_partial")
    return EvalResult(True, status, solution=sol, objective_tl=sol.objective_tl(inst),
                      solver_objective_tl=float(info.objective_function_value) * SCALE,
                      breakdown=cost_breakdown(inst, sol), time_s=time.perf_counter() - t0)


# ---------------------------------------------------------------- yardımcı ölçüler
def _district(inst: Instance, cand_pos: int) -> str | None:
    s = inst.s
    if s.source == "toy":
        return None
    g = s.grid
    gi = int(s.cand_cells[cand_pos])
    d = int(g.district[gi])
    return g.district_names[d] if d >= 0 else None


def _demand_district(inst: Instance, i: int) -> str | None:
    s = inst.s
    if s.source == "toy":
        return None
    g = s.grid
    d = int(g.district[int(s.demand_cells[i])])
    return g.district_names[d] if d >= 0 else None


def _label(inst: Instance, cand_pos: int) -> str:
    d = _district(inst, cand_pos)
    cid = str(inst.s.cand_ids[cand_pos])
    return f"{cid} ({d})" if d else cid


def sole_cover(inst: Instance, sol: Solution) -> dict:
    """Her açık hastane için, modelin yay listelerinde (R içinde, en yakın k aday) TEK açık hastanesi o olan talep
    hücreleri: sayı ve nüfus. Böyle bir hastane kapatılırsa model olurlu kalmaz. Ayrıca bu hücrelerden kaçının R içinde
    geometrik olarak da başka açık hastanesi olmadığı (cells_R: hastaneyi R kısıtı zorunlu kılıyor); geri kalanların
    başka hastanesi R içinde var ama en yakın k aday listesinin dışında (k kırpmasının etkisi)."""
    s = inst.s
    hpos = np.full(inst.nJ, -1, dtype=np.int64)
    hpos[sol.open_j] = np.arange(sol.n_open)
    oa = hpos[s.arc_j] >= 0
    n_open = np.bincount(s.arc_i[oa], minlength=inst.nI)
    only = np.flatnonzero(oa & (n_open[s.arc_i] == 1))
    ht = hpos[s.arc_j[only]]
    cells = np.bincount(ht, minlength=sol.n_open)
    pop = np.bincount(ht, s.w[s.arc_i[only]], minlength=sol.n_open)
    cells_R = cells.copy()
    pop_R = pop.copy()
    if len(only) and s.demand_xy is not None and s.cand_xy is not None and sol.n_open >= 2:
        from scipy.spatial import cKDTree
        hxy = s.cand_xy[sol.open_j]
        e, idx = cKDTree(hxy).query(s.demand_xy[s.arc_i[only]], k=2)
        other = np.where(idx[:, 0] == ht, 1, 0)
        e_other = e[np.arange(len(only)), other] / 1000.0
        d_other = distance.road_km_pairs(e_other, s.cell_km, s.circ)
        beyond = d_other > s.R_km + 1e-9
        cells_R = np.bincount(ht[beyond], minlength=sol.n_open)
        pop_R = np.bincount(ht[beyond], s.w[s.arc_i[only]][beyond], minlength=sol.n_open)
    return {"cells": cells.astype(int), "population": pop.astype(float), "cells_R": cells_R.astype(int),
            "population_R": pop_R.astype(float)}


def _served(inst: Instance, sol: Solution) -> tuple[np.ndarray, np.ndarray]:
    """Hastane başına hizmet edilen nüfus ve yatak talebi (çözüm sırası)."""
    s = inst.s
    hpos = np.full(inst.nJ, -1, dtype=np.int64)
    hpos[sol.open_j] = np.arange(sol.n_open)
    a = np.flatnonzero((sol.x > 0) & (hpos[s.arc_j] >= 0))
    hp = hpos[s.arc_j[a]]
    pop = np.bincount(hp, s.w[s.arc_i[a]] * sol.x[a], minlength=sol.n_open)
    dem = np.bincount(hp, inst.q[s.arc_i[a]] * sol.x[a], minlength=sol.n_open)
    return pop, dem


def _mean_km(inst: Instance, sol: Solution) -> float | None:
    s = inst.s
    a = np.flatnonzero(sol.x > 0)
    w = s.w[s.arc_i[a]] * sol.x[a]
    tot = float(s.w.sum())
    return float((w * s.arc_d[a]).sum() / tot) if tot > 0 else None


def _summary(inst: Instance, sol: Solution) -> dict:
    by = {c.code: int((sol.cls == k).sum()) for k, c in enumerate(inst.classes)}
    return {"n_hospitals": sol.n_open, "count_by_class": by, "opening_tl": sol.opening_tl(inst),
            "access_tl": sol.access_tl(inst), "objective_tl": sol.objective_tl(inst),
            "pop_weighted_mean_km": _mean_km(inst, sol), "total_beds": float(sol.beds.sum())}


# ---------------------------------------------------------------- 7.4 "bu hastane kapatılırsa"
def _uncovered_detail(inst: Instance, sol: Solution, t: int, keep_open: np.ndarray, bad: np.ndarray) -> dict:
    """R içinde açık hastanesi kalmayan talep hücreleri: sayı, nüfus, ilçeler, kalan en yakın hastaneye yol km."""
    s = inst.s
    out = {"cells": int(len(bad)), "population": float(s.w[bad].sum()),
           "districts": sorted({d for d in (_demand_district(inst, int(i)) for i in bad) if d})}
    if s.demand_xy is not None and s.cand_xy is not None and len(keep_open):
        _, e = distance.nearest_euclid(s.demand_xy[bad], s.cand_xy[keep_open])
        d = distance.road_km_pairs(e, s.cell_km, s.circ)
        out["nearest_other_km_max"] = float(d.max())
        out["nearest_other_km_mean"] = float(np.average(d, weights=np.maximum(s.w[bad], 1e-12)))
        # yay listesi dışında kalsa bile R içinde bir hastane var mı (k kırpmasının etkisi)
        out["within_R_geometric"] = int((d <= s.R_km + 1e-9).sum())
    return out


def _absorbers(inst: Instance, before: Solution, after: Solution, t: int) -> dict:
    """Kapatılan hastanenin (before'daki t) hastaları yeni çözümde nereye gidiyor (doğrudan akış, oranlı paylaştırma) ve
    hangi hastanelerin sınıfı / yatağı değişti."""
    s = inst.s
    j = int(before.open_j[t])
    rows = np.flatnonzero((s.arc_j == j) & (before.x > 0))
    i_rows = s.arc_i[rows]
    share_j = before.x[rows]
    w_moved = s.w[i_rows] * share_j
    q_moved = inst.q[i_rows] * share_j
    hpos = np.full(inst.nJ, -1, dtype=np.int64)
    hpos[after.open_j] = np.arange(after.n_open)
    absorbed_pop = np.zeros(after.n_open)
    absorbed_beds = np.zeros(after.n_open)
    new_km_sum = 0.0
    old_km_sum = float((w_moved * s.arc_d[rows]).sum())
    for i, wm, qm in zip(i_rows, w_moved, q_moved):
        a0, a1 = s.arc_ptr[i], s.arc_ptr[i + 1]
        xs = after.x[a0:a1]
        m = (xs > 0) & (hpos[s.arc_j[a0:a1]] >= 0)
        tot = float(xs[m].sum())
        if tot <= 0:
            continue
        hp = hpos[s.arc_j[a0:a1][m]]
        f = xs[m] / tot
        np.add.at(absorbed_pop, hp, wm * f)
        np.add.at(absorbed_beds, hp, qm * f)
        new_km_sum += float(wm * (f * s.arc_d[a0:a1][m]).sum())
    moved = float(w_moved.sum())
    order = np.argsort(-absorbed_pop)
    bpos = {int(jj): k for k, jj in enumerate(before.open_j)}
    lst = []
    for h in order:
        if absorbed_pop[h] <= max(1e-6 * moved, 1e-9):
            break
        jj = int(after.open_j[h])
        kb = bpos.get(jj)
        lst.append({"cell_id": str(s.cand_ids[jj]), "district": _district(inst, jj),
                    "absorbed_population": float(absorbed_pop[h]), "absorbed_beds": float(absorbed_beds[h]),
                    "share": float(absorbed_pop[h] / moved) if moved > 0 else None,
                    "class_before": inst.classes[int(before.cls[kb])].code if kb is not None else None,
                    "class_after": inst.classes[int(after.cls[h])].code,
                    "beds_before": float(before.beds[kb]) if kb is not None else None,
                    "beds_after": float(after.beds[h])})
    changed = []
    for h, jj in enumerate(after.open_j):
        kb = bpos.get(int(jj))
        if kb is None:
            continue
        if int(before.cls[kb]) != int(after.cls[h]) or abs(float(before.beds[kb]) - float(after.beds[h])) > 0.05:
            changed.append({"cell_id": str(s.cand_ids[jj]), "class_before": inst.classes[int(before.cls[kb])].code,
                            "class_after": inst.classes[int(after.cls[h])].code, "beds_before": float(before.beds[kb]),
                            "beds_after": float(after.beds[h])})
    return {"displaced_population": moved, "displaced_beds": float(q_moved.sum()),
            "mean_km_before": old_km_sum / moved if moved > 0 else None,
            "mean_km_after": new_km_sum / moved if moved > 0 else None,
            "absorbers": lst, "resized": changed, "n_resized": len(changed),
            "n_class_changed": sum(1 for c in changed if c["class_before"] != c["class_after"])}


def closure_message(it: Mapping, *, n_remaining: int, mode: str, R_km: float, k_nearest: int) -> str:
    """Kapatma öğesinin Türkçe cümlesi (yalnız öğedeki sayılardan; saklanan öğeler yeniden hesaplanmadan yeniden
    yazılabilir)."""
    label = f"{it['cell_id']} ({it['district']})" if it.get("district") else str(it["cell_id"])
    if it.get("feasible") is False and it.get("reason") in ("R", "k_listesi"):
        unc = it.get("uncovered") or {}
        dist = f" ({', '.join(unc['districts'])})" if unc.get("districts") else ""
        head = (f"{label} kapatılamaz: {unc.get('cells')} talep hücresinin{dist} {fmt(unc.get('population') or 0, 0)} "
                f"kişisinin ")
        if it["reason"] == "k_listesi":
            k_txt = f"en yakın {k_nearest} aday listesinde" if k_nearest else "aday listesinde"
            return (head + f"modelin {k_txt} başka açık hastanesi kalmaz. Bu hücrelerin R = {fmt(R_km, 0)} km içinde "
                    f"başka hastanesi var ama listelerinin dışında (k = {k_nearest} kırpmasının etkisi; R içindeki tüm "
                    f"adaylarla, grid.k_nearest = 0, bu engel kalkar).")
        mx = unc.get("nearest_other_km_max")
        nd = 1
        while mx is not None and nd < 4 and round(mx, nd) <= R_km < mx:
            nd += 1                                     # R'nin hemen üstü: '30,0' değil '30,004'
        far = f"; bu hücrelerden kalan en yakın hastaneye yol {fmt(mx, nd)} km'ye çıkar" if mx is not None else ""
        msg = (head + f"R = {fmt(R_km, 0)} km içinde başka açık hastanesi kalmaz{far}. Bu hastaneyi maliyet değil "
               f"azami erişim mesafesi kısıtı (R) zorunlu kılıyor.")
        n_geo = int(unc.get("within_R_geometric") or 0)
        if n_geo:
            msg += f" ({n_geo} hücrede engel yalnız k = {k_nearest} aday listesi.)"
        return msg
    if not it.get("feasible"):
        return str(it.get("message_tr") or "")
    delta = float(it["delta_tl"])
    what = "erişim maliyeti" if mode == "butce" else "toplam maliyet"
    msg = (f"{label} kapatılırsa (konumlar sabit; kalan {n_remaining} hastanede yeniden atama ve boyutlandırma): "
           f"{what} {('en çok ' + tl(delta) + ' artar') if delta >= 0 else (tl(-delta) + ' azalır')} "
           f"(açılış {tl(it['delta_opening_tl'], True)}, erişim {tl(it['delta_access_tl'], True)}).")
    ab = it.get("absorbers") or []
    if ab and (it.get("displaced_population") or 0) > 0:
        first = ab[0]
        a, b = it.get("mean_km_before") or 0.0, it.get("mean_km_after") or 0.0
        verb = "çıkar" if b > a + 0.05 else ("iner" if b < a - 0.05 else "değişmez")
        km_txt = (f"{fmt(a, 1)} km'den {fmt(b, 1)} km'ye {verb}" if verb != "değişmez" else f"{fmt(a, 1)} km'de kalır")
        msg += (f" Hastaları ({fmt(it['displaced_population'], 0)} kişi) en çok {first['cell_id']}"
                + (f" ({first['district']})" if first.get("district") else "") +
                f" hastanesine gider (%{fmt(100 * (first.get('share') or 0), 0)}); bu hastaların ortalama yolu "
                f"{km_txt}.")
    if it.get("n_class_changed"):
        msg += f" {it['n_class_changed']} hastanenin sınıfı değişir."
    if it.get("fark_gap_icinde"):
        msg += (f" Bu artış temel çözümün kanıtlanmamış payından ({tl(it.get('base_gap_tl'))}) küçük: fark gap içinde, "
                f"bu hastanenin gerekliliği bu sınırlarla kanıtlanamıyor.")
    elif delta >= 0:
        msg += " Bu bir üst sınırdır: konumlar yeniden optimize edilirse artış daha küçük olabilir."
    return msg


def closure_effect(inst: Instance, sol: Solution, t: int, *, base_obj: float | None = None,
                   base_bounds: Mapping | None = None, exact: bool = False, neighbours: int = CLOSE_NEIGHBOURS,
                   exact_time: float = CLOSE_EXACT_TIME, cancel_event=None) -> dict:
    """sol'deki t. hastane kapatılırsa: kalan hastanelerle (konumlar sabit) yeniden atama + boyutlandırma.

    Hızlı tahmin: en yakın `neighbours` hastanede sınıf serbest, diğerlerinde sınıf sabit (yataklar her yerde serbest);
    exact=True: tüm hastanelerde sınıf seçimli kesin değerlendirme (evaluate). Her iki değer de kalan hastanelerle olurlu
    bir çözümün maliyetidir: konumlar yeniden optimize edilirse artış ancak daha küçük olabilir (ÜST sınır).
    base_bounds {ub_tl, lb_tl, mode}: temel çözümün sınırları ('fark gap içinde' işareti için)."""
    t0 = time.perf_counter()
    s = inst.s
    j = int(sol.open_j[t])
    cid = str(s.cand_ids[j])
    base_obj = float(sol.objective_tl(inst) if base_obj is None else base_obj)
    out: dict[str, Any] = {"kind": "hastane_kapat", "cell_id": cid, "district": _district(inst, j),
                           "class": inst.classes[int(sol.cls[t])].code, "beds": float(sol.beds[t]),
                           "base_objective_tl": base_obj, "method": None}
    keep = np.arange(sol.n_open) != t
    oj, ck = sol.open_j[keep], sol.cls[keep]
    ctx = {"n_remaining": int(len(oj)), "mode": inst.mode, "R_km": float(s.R_km), "k_nearest": int(s.k_nearest)}
    if inst.lock_open[j]:
        out.update(feasible=False, reason="kilit", objective_tl=None, delta_tl=None,
                   message_tr=f"{_label(inst, j)} kullanıcı tarafından 'aç' kilitli; kapatma etkisi hesaplanmadı.")
        return out
    if len(oj) < inst.count_min:
        out.update(feasible=False, reason="sayi", objective_tl=None, delta_tl=None,
                   message_tr=f"En az {inst.count_min} hastane isteniyor; bir hastane kapatılamaz.")
        return out
    hpos = np.full(inst.nJ, -1, dtype=np.int64)
    hpos[oj] = 1
    cover = np.zeros(inst.nI, dtype=bool)
    cover[s.arc_i[hpos[s.arc_j] >= 0]] = True
    if not cover.all():
        bad = np.flatnonzero(~cover)
        unc = _uncovered_detail(inst, sol, t, oj, bad)
        n_geo = int(unc.get("within_R_geometric", 0))
        reason = "k_listesi" if n_geo >= unc["cells"] else "R"
        out.update(feasible=False, reason=reason, objective_tl=None, delta_tl=None, uncovered=unc,
                   time_s=time.perf_counter() - t0)
        out["message_tr"] = closure_message(out, **ctx)
        return out
    # hızlı tahmin: en yakın komşularda sınıf serbest
    cf = ck.copy()
    if s.cand_xy is not None and neighbours > 0 and len(oj):
        d = np.hypot(*(s.cand_xy[oj] - s.cand_xy[j]).T)
        cf[np.argsort(d, kind="stable")[:neighbours]] = -1
    else:
        cf[:] = -1
    ev = evaluate_partial(inst, oj, cf, time_limit=exact_time, cancel_event=cancel_event)
    method = "hizli_komsu_sinif"
    if ev.status == "durduruldu":
        out.update(feasible=None, status="durduruldu", objective_tl=None, delta_tl=None, message_tr=CANCELLED_TR)
        return out
    quick_obj = ev.objective_tl if ev.ok else None
    if exact or not ev.ok:
        ev2 = evaluate(inst, oj, gap=1e-6, time_limit=exact_time, cancel_event=cancel_event)
        if ev2.status == "durduruldu" and not ev.ok:
            out.update(feasible=None, status="durduruldu", objective_tl=None, delta_tl=None, message_tr=CANCELLED_TR)
            return out
        if ev2.ok and (not ev.ok or ev2.objective_tl < ev.objective_tl):
            ev, method = ev2, "kesin_sinif_secimi"
        elif ev2.ok:
            method = "kesin_sinif_secimi"          # kesin değerlendirme hızlı tahminden iyi değil: ikisi eşit
    if not ev.ok:
        out.update(feasible=False, reason="kapasite", objective_tl=None, delta_tl=None, time_s=time.perf_counter() - t0,
                   message_tr=f"{_label(inst, j)} kapatılırsa kalan hastanelerle olurlu atama yok ({ev.message}).")
        return out
    new = ev.solution
    obj = float(ev.objective_tl)
    opening_b, access_b = sol.opening_tl(inst), sol.access_tl(inst)
    opening_a, access_a = new.opening_tl(inst), new.access_tl(inst)
    absorb = _absorbers(inst, sol, new, t)
    delta = obj - base_obj
    out.update(feasible=True, reason=None, method=method, objective_tl=obj, delta_tl=delta,
               quick_objective_tl=quick_obj, opening_tl=opening_a, access_tl=access_a,
               delta_opening_tl=opening_a - opening_b, delta_access_tl=access_a - access_b,
               delta_pct=delta / base_obj if base_obj else None, upper_bound=True, **absorb)
    b = dict(base_bounds or {})
    if b.get("ub_tl") is not None and b.get("lb_tl") is not None:
        gap_abs = float(b["ub_tl"]) - float(b["lb_tl"])
        out["base_gap_tl"] = gap_abs
        # artışın üst sınırı bile temel çözümün gap'inden küçükse: bu hastanenin katkısı çözücü sınırlarıyla ayırt edilemez
        out["fark_gap_icinde"] = bool(delta <= gap_abs + 1e-9 * abs(base_obj))
    else:
        out["fark_gap_icinde"] = None
    out["message_tr"] = closure_message(out, **ctx)
    out["time_s"] = time.perf_counter() - t0
    out["_solution"] = new
    return out


def reoptimize_closed(inst: Instance, sol: Solution, t: int, closure: Mapping, *, time_limit: float, gap: float,
                      base_bounds: Mapping | None = None, cancel_event=None, progress_cb: ProgressCb = None) -> dict:
    """j yasakken (kapalı kilit) tam yeniden çözüm; başlangıç hızlı tahminin çözümü (sonuç ondan kötü olamaz).
    Alt sınır: kendi alt sınırı ile (toplam modunda) serbest problemin alt sınırının büyüğü (yasaklı problem serbest
    problemin kısıtlamasıdır)."""
    from .solve import solve
    s = inst.s
    j = int(sol.open_j[t])
    cid = str(s.cand_ids[j])
    locks = inst.locks.as_dict()
    locks["closed"] = sorted(set(locks["closed"]) | {cid})
    inst2 = build_instance(s.cell_km, inst.params, locks, structure=s)
    start = closure.get("_solution")
    r = solve(instance=inst2, start=start, time_limit=time_limit, gap=gap, cancel_event=cancel_event,
              progress_cb=progress_cb)
    if not r.get("ok"):
        return {"status": r.get("status"), "ok": False, "message_tr": r.get("message_tr")}
    b = r["bounds"]
    base_obj = float(closure.get("base_objective_tl"))
    lb_own = b.get("lb_tl")
    bb = dict(base_bounds or {})
    lb_free = bb.get("lb_tl") if inst.mode == "toplam" else None
    lbs = [v for v in (lb_own, lb_free) if v is not None]
    lb = max(lbs) if lbs else None
    ub = float(b["ub_tl"])
    opened = sorted(set(h["cell_id"] for h in r["hospitals"]) - set(str(c) for c in s.cand_ids[sol.open_j]))
    closed = sorted(set(str(c) for c in s.cand_ids[sol.open_j]) - set(h["cell_id"] for h in r["hospitals"]))
    out = {"ok": True, "status": r["status"], "objective_tl": ub, "lb_tl": lb, "lb_own_tl": lb_own,
           "gap": (ub - lb) / ub if lb is not None and ub else None, "delta_tl": ub - base_obj,
           "delta_lo_tl": (lb - float(bb["ub_tl"])) if lb is not None and bb.get("ub_tl") is not None else None,
           "delta_hi_tl": (ub - float(bb["lb_tl"])) if bb.get("lb_tl") is not None else None,
           "n_hospitals": r["kpis"]["n_hospitals"], "count_by_class": r["kpis"]["count_by_class"],
           "opening_tl": r["costs"]["acilis"], "access_tl": r["costs"]["erisim"], "opened": opened,
           "closed": closed, "time_s": r["times"].get("total_s"),
           "improvement_vs_quick_tl": (float(closure["objective_tl"]) - ub) if closure.get("objective_tl") else None}
    out["fark_gap_icinde"] = bool(lb is not None and bb.get("ub_tl") is not None and lb <= float(bb["ub_tl"]) * (1 + REL_TOL))
    msg = (f"Konumlar da yeniden optimize edilince ({cid} yasak, {r['kpis']['n_hospitals']} hastane): "
           f"{tl(ub)}, temel çözüme göre {tl(ub - base_obj, True)}")
    if opened or closed:
        msg += f"; {len(closed) - 1 if cid in closed else len(closed)} hastane daha kapanır, {len(opened)} yeni hücre açılır"
    msg += "."
    if out["fark_gap_icinde"]:
        msg += " Fark çözücü gap'i içinde: bu konumun yasaklanmasının maliyeti sınırlarla ayırt edilemiyor."
    elif out["delta_lo_tl"] is not None and out["delta_lo_tl"] > 0:
        msg += f" Kanıtlı olarak en az {tl(out['delta_lo_tl'])} pahalı."
    out["message_tr"] = msg
    return out


# ---------------------------------------------------------------- 7.3 N − 1 / N + 1
def neighbour_solution(inst: Instance, sol: Solution, p: int, *, approx=None, cancel_event=None) -> Solution | None:
    """sol'den tek tek kapatma (n > p) ya da açma (n < p) ile p hastaneli çözüm: her adımda yaklaşık modelin
    (heuristics.ApproxModel) en iyi NEIGHBOUR_TRY hamlesi sabit sınıflı LP + sınıf onarımıyla (lagrange.fast_eval)
    kesin değerlendirilir, en ucuzu alınır; sonda sınıf seçimli cila. 'Aç' kilitli hastaneler kapatılmaz.
    (pipeline/32_precompute.py refine'daki komşu başlangıcının motor sürümü.)"""
    from .heuristics import ApproxModel
    from .lagrange import fast_eval
    M = approx or ApproxModel(inst)
    cur = sol
    while cur.n_open != p:
        if cancel_event is not None and cancel_event.is_set():
            return None
        om = np.zeros(inst.nJ, dtype=bool)
        om[cur.open_j] = True
        st = M.state(om)
        drop = cur.n_open > p
        if drop:
            da, do = M.eval_drop(st, M.openable & ~inst.lock_open)
        else:
            da, do = M.eval_add(st, M.openable)
        score = da + do
        cand = np.flatnonzero(np.isfinite(score))
        cand = cand[np.argsort(score[cand], kind="stable")][:NEIGHBOUR_TRY]
        cls_of = dict(zip(cur.open_j.tolist(), cur.cls.tolist()))
        best = None
        for jj in cand:
            om2 = om.copy()
            om2[jj] = not drop
            oj = np.flatnonzero(om2)
            k0 = np.array([cls_of.get(int(c), int(np.argmax(inst.y_ub[c]))) for c in oj], dtype=np.int64)
            ev = fast_eval(inst, oj, k0, approx=M, mip_time=5.0)
            if ev.ok and (best is None or ev.objective_tl < best.objective_tl):
                best = ev
        if best is None:
            return None
        cur = best.solution
    ev = evaluate(inst, cur.open_j, gap=1e-4, time_limit=30.0, cancel_event=cancel_event)
    return ev.solution if ev.ok and ev.objective_tl < cur.objective_tl(inst) else cur


def p_params(params: Params, p: int) -> Params:
    """Aynı parametreler, sabit_p modu ve p hastane (yapı değişmez: mod yapı anahtarı değildir)."""
    return params.with_overrides({"solver.objective_mode": "sabit_p", "solver.p_fixed": int(p)})


def point_from_result(r: Mapping, p: int) -> dict:
    b, c, k = r["bounds"], r["costs"], r["kpis"]
    return {"p": int(p), "ok": True, "status": r["status"], "ub_tl": b["ub_tl"], "lb_own_tl": b["lb_tl"],
            "lb_tl": b["lb_tl"], "lb_source": b.get("lb_source"), "gap": b["gap"], "opening_tl": c["acilis"],
            "access_tl": c["erisim"], "total_tl": c["toplam"], "count_by_class": k["count_by_class"],
            "total_beds": k["total_beds"], "pop_weighted_mean_km": k["pop_weighted_mean_km"],
            "time_s": (r.get("times") or {}).get("total_s"), "solution_source": r.get("solution_source"),
            "check_ok": (r.get("check") or {}).get("ok"), "hospitals": [h["cell_id"] for h in r["hospitals"]],
            "classes": [h["class"] for h in r["hospitals"]], "beds": [h["beds"] for h in r["hospitals"]]}


def best_with_p(inst: Instance, sol: Solution, p: int, *, ref: Mapping, time_limit: float, gap: float,
                start: Solution | None = None, cancel_event=None, progress_cb: ProgressCb = None) -> tuple[dict, Solution | None]:
    """sabit_p modunda p hastaneli en iyi çözüm (başlangıç: sol'den komşu çözüm). Döner: (nokta, çözüm)."""
    from .solve import solve
    t0 = time.perf_counter()
    inst_p = build_instance(inst.s.cell_km, p_params(inst.params, p), inst.locks, structure=inst.s)
    if start is None:
        start = neighbour_solution(inst_p, sol, p, cancel_event=cancel_event)
    start_obj = start.objective_tl(inst_p) if start is not None else None
    if start is not None and not checks_mod.check(inst_p, start)["ok"]:
        start = None
    r = solve(instance=inst_p, start=start, time_limit=max(0.5, time_limit - (time.perf_counter() - t0)), gap=gap,
              cancel_event=cancel_event, progress_cb=progress_cb)
    if not r.get("ok"):
        return {"p": int(p), "ok": False, "status": r.get("status"), "message_tr": r.get("message_tr")}, None
    pt = point_from_result(r, p)
    pt["start_objective_tl"] = start_obj
    pt["source"] = "canli"
    pt["time_s"] = time.perf_counter() - t0
    apply_reference(pt, ref)
    return pt, solution_from_result(inst_p, r)


def reference_of(inst: Instance, sol: Solution, bounds: Mapping | None = None) -> dict:
    """Temel çözümün referans özeti (fark ve 'fark gap içinde' için)."""
    b = dict(bounds or {})
    sm = _summary(inst, sol)
    ub = float(b["ub_tl"]) if b.get("ub_tl") is not None else sm["objective_tl"]
    ub = min(ub, sm["objective_tl"]) if b.get("ub_tl") is not None else ub
    return {"n": sol.n_open, "ub_tl": ub, "lb_tl": b.get("lb_tl"), "objective_tl": sm["objective_tl"],
            "opening_tl": sm["opening_tl"], "access_tl": sm["access_tl"],
            "pop_weighted_mean_km": sm["pop_weighted_mean_km"], "count_by_class": sm["count_by_class"],
            "free_lb_valid": inst.mode == "toplam" and b.get("lb_tl") is not None,
            "cells": [str(c) for c in inst.s.cand_ids[sol.open_j]], "mode": inst.mode}


# ---------------------------------------------------------------- 7.7 p taraması (U eğrisi)
def p_sweep(inst: Instance, sol: Solution, ps, *, ref: Mapping, time_limit: float, gap: float,
            precomputed: Mapping[int, dict] | None = None, cancel_event=None,
            on_point: Callable[[dict, int, int], None] | None = None, progress_cb: ProgressCb = None) -> dict:
    """p değerleri temel çözümün sayısından dışa doğru (|p − n| artan) çözülür; her noktanın başlangıcı en yakın çözülmüş
    p'nin çözümünden tek tek kapatma/açma ile türetilir. precomputed: {p: nokta} (ör. varsayılan parametrelerin uzun
    çözülmüş taraması) — bu noktalar yeniden çözülmez."""
    n0 = int(ref["n"])
    ps = sorted({int(p) for p in ps})
    order = sorted(ps, key=lambda p: (abs(p - n0), p))
    sols: dict[int, Solution] = {n0: sol}
    points: dict[int, dict] = {}
    status = "tamam"
    for done, p in enumerate(order):
        if cancel_event is not None and cancel_event.is_set():
            status = "durduruldu"
            break
        pre = (precomputed or {}).get(p)
        if p == n0 and ref.get("free_lb_valid"):
            pt = {"p": p, "ok": True, "status": "referans", "ub_tl": ref["ub_tl"], "lb_own_tl": ref.get("lb_tl"),
                  "lb_tl": ref.get("lb_tl"), "opening_tl": ref["opening_tl"], "access_tl": ref["access_tl"],
                  "total_tl": ref["objective_tl"], "count_by_class": ref.get("count_by_class"),
                  "pop_weighted_mean_km": ref.get("pop_weighted_mean_km"), "hospitals": ref.get("cells"),
                  "source": "temel_cozum"}
            if pre is not None and pre.get("ok") and pre["ub_tl"] < ref["ub_tl"] * (1 - REL_TOL):
                pt = dict(pre, source="onceden")
        elif pre is not None:
            pt = {k: v for k, v in pre.items()}
            pt["source"] = "onceden"
        else:
            # başlangıç: çözülmüş en yakın p
            near = min(sols, key=lambda q: (abs(q - p), q))
            pt, s_new = best_with_p(inst, sols[near], p, ref=ref, time_limit=time_limit, gap=gap,
                                    cancel_event=cancel_event, progress_cb=progress_cb)
            if s_new is not None:
                sols[p] = s_new
            if cancel_event is not None and cancel_event.is_set() and pt.get("status") == "durduruldu":
                pt["interrupted"] = True
        apply_reference(pt, ref)
        points[p] = pt
        if on_point is not None:
            on_point(pt, done + 1, len(order))
    pts = [points[p] for p in ps if p in points]
    ok = [q for q in pts if q.get("ok") and q.get("status") != "durduruldu"]
    best = min(ok, key=lambda q: (q["ub_tl"], q["p"] != n0)) if ok else None
    plateau = [q["p"] for q in ok if q.get("referans") or q.get("fark_gap_icinde")]
    out = {"status": status, "points": pts, "reference": {k: v for k, v in ref.items() if k != "cells"},
           "best_p": best["p"] if best else None, "best_ub_tl": best["ub_tl"] if best else None,
           "plateau_p": plateau}
    out["summary_tr"] = sweep_summary(out, ref)
    return out


# ---------------------------------------------------------------- 7.8 Pareto (λ taraması)
def pareto(km: int, overrides: Mapping, locks: Mapping | None, factors, *, time_limit: float, gap: float,
           base: Mapping | None = None, cancel_event=None, on_point: Callable[[dict, int, int], None] | None = None,
           progress_cb: ProgressCb = None, make_instance: Callable[[Params], Instance] | None = None) -> dict:
    """Erişim ağırlığı λ'yı f ile ölçekleyerek (v·f, bkz. LAMBDA_KEY) açılış maliyeti – nüfus ağırlıklı ortalama mesafe
    ödünleşimi. Her f için toplam modunda çözüm; noktada açılış maliyeti, ortalama mesafe, erişim maliyeti ve toplam
    maliyet ASIL λ ile (erişim / f) raporlanır: asıl toplam f = 1'de en küçük olmalı (gap'ler içinde).
    base {hospitals: [{cell_id, class, beds}], bounds}: mevcut çözüm (f = 1 noktası olarak kullanılır, çözülmez)."""
    from .solve import solve
    p0 = Params.load(dict(overrides))
    if p0.objective_mode == "butce":
        raise ParamError("Pareto taraması toplam (ya da sabit p) modunda tanımlıdır; bütçe modunda erişim zaten "
                         "bütçe altında en küçüklenir.")
    v0 = float(p0[LAMBDA_KEY])
    lam0 = p0.lam
    fs = sorted({float(f) for f in factors} | {1.0})
    order = [1.0] + [f for f in fs if f > 1.0] + sorted([f for f in fs if f < 1.0], reverse=True)
    pts: dict[float, dict] = {}
    prev: dict[float, tuple] = {}
    status = "tamam"
    for done, f in enumerate(order):
        if cancel_event is not None and cancel_event.is_set():
            status = "durduruldu"
            break
        t0 = time.perf_counter()
        pf = p0.with_overrides({LAMBDA_KEY: v0 * f}) if f != 1.0 else p0
        inst = make_instance(pf) if make_instance is not None else build_instance(km, pf, locks)
        pt: dict[str, Any] = {"factor": f, "lambda_tl_per_person_km": pf.lam, LAMBDA_KEY: v0 * f}
        if f == 1.0 and base is not None and base.get("hospitals"):
            ev = hospital_solution(inst, base["hospitals"], cancel_event=cancel_event)
            if ev.ok:
                sm = _summary(inst, ev.solution)
                b = dict(base.get("bounds") or {})
                pt.update(ok=True, status="mevcut_cozum", source="mevcut_cozum", n_hospitals=sm["n_hospitals"],
                          count_by_class=sm["count_by_class"], opening_tl=sm["opening_tl"],
                          access_scaled_tl=sm["access_tl"], pop_weighted_mean_km=sm["pop_weighted_mean_km"],
                          ub_tl=min(sm["objective_tl"], b.get("ub_tl") or math.inf), lb_tl=b.get("lb_tl"),
                          hospitals=[str(h["cell_id"]) for h in base["hospitals"]],
                          classes=[inst.classes[int(k)].code for k in ev.solution.cls])
                prev[f] = (pt["hospitals"], [h.get("class") for h in base["hospitals"]])
        if "ok" not in pt:
            near = min(prev, key=lambda q: abs(math.log(q) - math.log(f))) if prev else None
            start = transfer_solution(inst, *prev[near], cancel_event=cancel_event) if near is not None else None
            r = solve(instance=inst, start=start, time_limit=time_limit, gap=gap, cancel_event=cancel_event,
                      progress_cb=progress_cb)
            if not r.get("ok"):
                pt.update(ok=False, status=r.get("status"), message_tr=r.get("message_tr"))
            else:
                k = r["kpis"]
                pt.update(ok=True, status=r["status"], source="canli", n_hospitals=k["n_hospitals"],
                          count_by_class=k["count_by_class"], opening_tl=r["costs"]["acilis"],
                          access_scaled_tl=r["costs"]["erisim"], pop_weighted_mean_km=k["pop_weighted_mean_km"],
                          share_within_10km=k["share_pop_within_km"].get("10"), max_km=k["max_km"],
                          ub_tl=r["bounds"]["ub_tl"], lb_tl=r["bounds"]["lb_tl"],
                          hospitals=[h["cell_id"] for h in r["hospitals"]], classes=[h["class"] for h in r["hospitals"]])
                prev[f] = (pt["hospitals"], pt["classes"])
        if pt.get("ok"):
            pt["access_tl"] = pt["access_scaled_tl"] / f           # asıl λ ile erişim maliyeti
            pt["total_tl"] = pt["opening_tl"] + pt["access_tl"]    # asıl λ ile toplam
            pt["gap"] = ((pt["ub_tl"] - pt["lb_tl"]) / pt["ub_tl"]) if pt.get("lb_tl") is not None else None
            # ölçekli problemin alt sınırı: asıl toplamın değil, kendi amacının sınırı (gap bu yüzden noktaya özgü)
        pt["time_s"] = time.perf_counter() - t0
        pts[f] = pt
        if on_point is not None:
            on_point(pt, done + 1, len(order))
    lst = [pts[f] for f in fs if f in pts]
    ok = [q for q in lst if q.get("ok")]
    for q in ok:
        q["dominated"] = any((o["opening_tl"] <= q["opening_tl"] and o["pop_weighted_mean_km"] <= q["pop_weighted_mean_km"]
                              and (o["opening_tl"] < q["opening_tl"] * (1 - 1e-9) or
                                   o["pop_weighted_mean_km"] < q["pop_weighted_mean_km"] * (1 - 1e-9)))
                             for o in ok if o is not q)
    cur = next((q for q in ok if q["factor"] == 1.0), None)
    best_total = min(ok, key=lambda q: q["total_tl"]) if ok else None
    out = {"status": status, "lambda_key": LAMBDA_KEY, "lambda_tl_per_person_km": lam0, "points": lst,
           "current_factor": 1.0, "best_total_factor": best_total["factor"] if best_total else None}
    if cur is not None and best_total is not None:
        diff = cur["total_tl"] - best_total["total_tl"]
        out["current_total_gap_tl"] = diff
        tol = (cur["ub_tl"] - cur["lb_tl"]) if cur.get("lb_tl") is not None else None
        out["current_is_min_total"] = bool(diff <= (tol or 0) + 1e-9 * cur["total_tl"])
    out["summary_tr"] = pareto_summary(out)
    return out


# ---------------------------------------------------------------- 7.9 tornado (±%20)
def _domain_clip(key: str, v: float) -> tuple[float, str | None]:
    spec = SPEC.get(key)
    if spec is None or spec[0] not in ("f", "i"):
        return v, None
    lo, hi = spec[1], spec[2]
    lo_inc = spec[3] if len(spec) > 3 else True
    hi_inc = spec[4] if len(spec) > 4 else True
    note = None
    if v < lo or (v == lo and not lo_inc):
        v, note = (lo if lo_inc else lo + 1e-9 * max(1.0, abs(lo))), "alt_sinir"
    if v > hi or (v == hi and not hi_inc):
        v, note = (hi if hi_inc else hi - 1e-9 * max(1.0, abs(hi))), "ust_sinir"
    if spec[0] == "i":
        v = int(round(v))
    return v, note


def tornado(km: int, overrides: Mapping, locks: Mapping | None, *, delta: float = TORNADO_DELTA, keys=None,
            time_limit: float, gap: float, base: Mapping, cancel_event=None,
            on_run: Callable[[dict, int, int], None] | None = None, progress_cb: ProgressCb = None,
            make_instance: Callable[[Params], Instance] | None = None) -> dict:
    """Her parametre (grup) için taban × (1 − δ) ve × (1 + δ): aynı süre sınırı, aynı gap, aynı sıcak başlangıç (temel
    çözüm bu parametrelerle yeniden değerlendirilir) — iki yön simetrik. Fark = bulunan(çalışma) − bulunan(temel);
    gerçek fark [L(çalışma) − U(temel), U(çalışma) − L(temel)] aralığında.
    base: {objective_tl (=U), lb_tl, n_hospitals, hospitals [cell], classes [kod], opening_tl, access_tl}.
    make_instance(params): örnek kurucu (testlerde oyuncak örnek; yoksa build_instance(km, params, locks))."""
    from .solve import solve
    p0 = Params.load(dict(overrides))
    specs = tornado_specs(p0, keys)
    ub0, lb0 = float(base["objective_tl"]), base.get("lb_tl")
    n0 = int(base["n_hospitals"])
    runs: list[dict] = []
    jobs = [(sp_, side) for sp_ in specs for side in (-1, +1)]
    status = "tamam"
    for done, (spec, side) in enumerate(jobs):
        if cancel_event is not None and cancel_event.is_set():
            status = "durduruldu"
            break
        t0 = time.perf_counter()
        f = 1.0 + side * delta
        vals, notes = {}, {}
        for k in spec["keys"]:
            v, note = _domain_clip(k, float(spec["base"][k]) * f)
            vals[k] = v
            if note:
                notes[k] = note
        run: dict[str, Any] = {"id": spec["id"], "side": "dusuk" if side < 0 else "yuksek", "factor": f,
                               "values": vals, "clipped": notes or None}
        if all(float(spec["base"][k]) == 0.0 for k in spec["keys"]):
            run.update(ok=False, status="atlandi", message_tr="Taban değer 0: ±%20 değişiklik tanımsız.")
        else:
            try:
                pr = p0.with_overrides(vals)
                inst = make_instance(pr) if make_instance is not None else build_instance(km, pr, locks)
            except (ParamError, ValueError) as ex:
                pr, inst = None, None
                run.update(ok=False, status="hata", message_tr=str(ex))
            if inst is not None:
                start = transfer_solution(inst, base.get("hospitals") or [], base.get("classes"),
                                          cancel_event=cancel_event)
                r = solve(instance=inst, start=start, time_limit=time_limit, gap=gap, cancel_event=cancel_event,
                          progress_cb=progress_cb)
                if not r.get("ok"):
                    run.update(ok=False, status=r.get("status"), message_tr=r.get("message_tr"))
                else:
                    b = r["bounds"]
                    ub, lb = float(b["ub_tl"]), b.get("lb_tl")
                    run.update(ok=True, status=r["status"], objective_tl=ub, lb_tl=lb, gap=b.get("gap"),
                               n_hospitals=r["kpis"]["n_hospitals"], count_by_class=r["kpis"]["count_by_class"],
                               opening_tl=r["costs"]["acilis"], access_tl=r["costs"]["erisim"],
                               pop_weighted_mean_km=r["kpis"]["pop_weighted_mean_km"],
                               delta_tl=ub - ub0, delta_pct=(ub - ub0) / ub0,
                               delta_lo_tl=(lb - ub0) if lb is not None else None,
                               delta_hi_tl=(ub - float(lb0)) if lb0 is not None else None,
                               delta_n=r["kpis"]["n_hospitals"] - n0, warm_start=start is not None,
                               hospitals=[h["cell_id"] for h in r["hospitals"]])
                    run["delta_lo_pct"] = run["delta_lo_tl"] / ub0 if run["delta_lo_tl"] is not None else None
                    run["delta_hi_pct"] = run["delta_hi_tl"] / ub0 if run["delta_hi_tl"] is not None else None
                    # işaret kesin mi: aralık sıfırı içermiyorsa
                    lo_, hi_ = run["delta_lo_tl"], run["delta_hi_tl"]
                    run["sign_certain"] = bool(lo_ is not None and hi_ is not None and (lo_ > 0 or hi_ < 0))
        run["time_s"] = time.perf_counter() - t0
        runs.append(run)
        if on_run is not None:
            on_run(run, done + 1, len(jobs))
    rows = []
    for spec in specs:
        lo = next((r for r in runs if r["id"] == spec["id"] and r["side"] == "dusuk"), None)
        hi = next((r for r in runs if r["id"] == spec["id"] and r["side"] == "yuksek"), None)
        sw = [abs(r["delta_pct"]) for r in (lo, hi) if r and r.get("ok")]
        rows.append({"id": spec["id"], "keys": spec["keys"], "label_tr": spec["label_tr"], "unit": spec["unit"],
                     "status": spec["status"], "base": spec["base"], "low": lo, "high": hi,
                     "swing_pct": max(sw) if sw else None,
                     "range_pct": ((hi["delta_pct"] if hi and hi.get("ok") else 0.0) -
                                   (lo["delta_pct"] if lo and lo.get("ok") else 0.0)) if (lo or hi) else None})
    rows.sort(key=lambda r: -(r["swing_pct"] or 0.0))
    out = {"status": status, "delta": delta, "base": {k: v for k, v in base.items() if k not in ("hospitals", "classes")},
           "rows": rows}
    out["summary_tr"] = tornado_summary(out)
    return out


# ---------------------------------------------------------------- 7.5 "neden bu boyut?"
def size_explanations(inst: Instance, sol: Solution, duals: Mapping | None = None) -> dict:
    """Hastane başına: sınıf aralığı, yatak, atanan talep, doluluk, yatak başı maliyet, 1 ek yatağın erişim değeri
    (yataklar sabitken atama LP'sinin kapasite duali), sınıf eşiğindeki maliyet sıçraması ve neden kodu:
      ust_esik   yatak sınıfın üst sınırında ve bir üst sınıf var (ör. 199 IV-C): bir yatak daha sınıf değiştirir;
      ust_sinir  yatak izinli en büyük boyutta (üst sınıf yok ya da bu hücrede kapalı);
      alt_sinir  yatak sınıfın en az yatağında ve talepten fazla (boş yatak): hastane kapsama/erişim için açık;
      ic_denge   sınıf içinde: 1 ek yatağın erişim değeri ≈ yatak başı maliyet (LP'nin optimallik koşulu)."""
    d = duals if duals is not None else assignment_duals(inst, sol)
    bed_value = d.get("bed_value_tl")
    sizing_price = d.get("sizing_bed_price_tl")
    pop, dem = _served(inst, sol)
    sc = sole_cover(inst, sol)
    K = inst.nK
    rows = []
    counts: dict[str, int] = {}
    at_values: dict[str, int] = {}
    for t, (j, k, b) in enumerate(zip(sol.open_j, sol.cls, sol.beds)):
        j, k, b = int(j), int(k), float(b)
        c = inst.classes[k]
        L, U = float(inst.L[k]), float(inst.U[k])
        tol = 1e-6 * max(1.0, U)
        fixed = float(inst.fixed[j, k])
        per_bed = float(inst.var[j, k])
        cur = fixed + per_bed * b
        occ = float(dem[t] / b) if b > 0 else None
        bv = float(bed_value[t]) if bed_value is not None else None
        spv = float(sizing_price[t]) if sizing_price is not None else None
        nxt = None
        for kk in range(k + 1, K):
            if inst.class_on[kk] and inst.y_ub[j, kk] and inst.L[kk] > b - tol:
                bb = float(inst.L[kk])
                nxt = {"class": inst.classes[kk].code, "beds": bb,
                       "opening_tl": float(inst.fixed[j, kk] + inst.var[j, kk] * bb),
                       "per_bed_tl": float(inst.var[j, kk])}
                nxt["jump_tl"] = nxt["opening_tl"] - cur
                nxt["extra_beds"] = bb - b
                break
        prv = None
        if k - 1 >= 0 and inst.class_on[k - 1] and inst.y_ub[j, k - 1]:
            bb = float(inst.U[k - 1])
            prv = {"class": inst.classes[k - 1].code, "beds": bb,
                   "opening_tl": float(inst.fixed[j, k - 1] + inst.var[j, k - 1] * bb),
                   "per_bed_tl": float(inst.var[j, k - 1])}
            # aynı yatak sayısının alt sınıfta maliyeti (alt sınıfa sığıyorsa) — sınıfın neden üstte olduğunu gösterir
        same_in_next = None
        if k + 1 < K and inst.class_on[k + 1] and inst.y_ub[j, k + 1]:
            same_in_next = float(inst.fixed[j, k + 1] + inst.var[j, k + 1] * max(b, float(inst.L[k + 1])))
        if b >= U - tol:
            reason = "ust_esik" if nxt is not None else "ust_sinir"
        elif b <= L + tol:
            reason = "alt_sinir"
        else:
            reason = "ic_denge"
        counts[reason] = counts.get(reason, 0) + 1
        if reason in ("ust_esik", "ust_sinir", "alt_sinir"):
            key = str(int(round(U if reason != "alt_sinir" else L)))
            at_values[key] = at_values.get(key, 0) + 1
        label = _label(inst, j)
        if reason == "ust_esik":
            txt = (f"{label}: {c.code} sınıfının üst sınırında ({fmt(b, 0)} yatak). {fmt(nxt['beds'], 0)}. yatak "
                   f"hastaneyi {nxt['class']} sınıfına taşır: açılış maliyeti {tl(nxt['jump_tl'], True)} sıçrar "
                   f"(yatak başı {tl(per_bed)} → {tl(nxt['per_bed_tl'])}).")
            if bv is not None:
                txt += (f" Oysa 1 ek yatağın erişim değeri (dual) yalnız {tl(bv)}: talebin fazlası komşu hastaneye "
                        f"kaydırılıyor.")
        elif reason == "ust_sinir":
            txt = (f"{label}: izinli en büyük boyutta ({fmt(b, 0)} yatak, {c.code}); daha fazla talep komşu "
                   f"hastanelere gidiyor.")
        elif reason == "alt_sinir":
            spare = b - float(dem[t])
            if spare > 1e-6 * max(1.0, b):
                txt = (f"{label}: {c.code} sınıfının en az yatağında ({fmt(L, 0)}); atanan talep "
                       f"{fmt(float(dem[t]), 1)} yatak (doluluk {pct(occ, 0)}), {fmt(spare, 1)} yatak boş — boş yatağın "
                       f"erişim değeri 0.")
            else:
                txt = (f"{label}: {c.code} sınıfının en az yatağında ({fmt(L, 0)}); bu yataklar çevreden kaydırılan "
                       f"taleple dolduruluyor"
                       + (f" (1 ek yatağın erişim değeri {tl(bv)} < yatak başı {tl(per_bed)}: daha küçük olabilseydi "
                          f"küçülürdü)" if bv is not None and bv < per_bed else "") + ".")
            if sc["cells_R"][t] > 0:
                txt += (f" {int(sc['cells_R'][t])} talep hücresinin ({fmt(float(sc['population_R'][t]), 0)} kişi) R "
                        f"içindeki tek hastanesi: R kısıtı bu hastaneyi zorunlu kılıyor.")
            elif sc["cells"][t] > 0:
                txt += (f" {int(sc['cells'][t])} talep hücresinin aday listesindeki tek açık hastanesi (k kırpması).")
            else:
                txt += " Hastane erişim kazancı için açık (kapatma etkisine bakın)."
        else:
            txt = (f"{label}: {c.code} sınıfı içinde ({fmt(b, 1)} yatak, doluluk {pct(occ, 0)}). Yatak sayısı atanan "
                   f"talebe eşit; 1 ek yatak {tl(per_bed)} tutar")
            if bv is not None and abs(bv - per_bed) <= 0.01 * per_bed:
                txt += (f" ve erişimi de {tl(bv)} azaltır: yatak başı maliyet = 1 ek yatağın erişim değeri (LP'nin "
                        f"denge koşulu).")
            elif bv is not None:
                txt += f", erişimi {tl(bv)} azaltır."
            else:
                txt += "."
        rows.append({"cell_id": str(inst.s.cand_ids[j]), "district": _district(inst, j), "class": c.code,
                     "class_name": c.name, "beds": b, "class_min_beds": L, "class_max_beds": U,
                     "assigned_beds": float(dem[t]), "population": float(pop[t]), "occupancy": occ,
                     "fixed_tl": fixed, "per_bed_tl": per_bed, "opening_tl": cur, "bed_value_tl": bv,
                     "sizing_bed_price_tl": spv, "next_class": nxt, "prev_class": prv,
                     "same_beds_next_class_tl": same_in_next,
                     "sole_cover_cells": int(sc["cells"][t]), "sole_cover_population": float(sc["population"][t]),
                     "r_forced_cells": int(sc["cells_R"][t]), "r_forced_population": float(sc["population_R"][t]),
                     "reason": reason, "reason_tr": txt})
    reason_tr = {"ust_esik": "sınıf eşiğinde (bir yatak daha sınıf değiştirir)", "ust_sinir": "izinli en büyük boyutta",
                 "alt_sinir": "sınıfın en az yatağında", "ic_denge": "sınıf içinde (yatak = talep)"}
    summ = ", ".join(f"{n} hastane {reason_tr[r]}" for r, n in sorted(counts.items(), key=lambda x: -x[1]))
    return {"kind": "boyut", "hospitals": rows, "counts": counts, "at_bound_beds": at_values,
            "summary_tr": f"{sol.n_open} hastaneden {summ}.",
            "note_tr": ("1 ek yatağın erişim değeri: yataklar ve sınıflar sabitken atama LP'sinin kapasite satırının dual "
                        "değeri (bedava bir yatak verilseydi erişim maliyetinin azalacağı tutar). Dejenere LP'de tek "
                        "değildir; marjinal (küçük değişiklik) yorumuyla okunmalı.")}


# ---------------------------------------------------------------- 7.6 gölge fiyatlar
def shadow_prices(inst: Instance, sol: Solution, duals: Mapping | None = None, top: int = 10) -> dict:
    """Atama LP'sinin dual değerleri (açık hastaneler ve sınıflar sabit; evaluate.assignment_duals) + özet + yorum."""
    d = duals if duals is not None else assignment_duals(inst, sol)
    s = inst.s
    mc = d.get("marginal_cost_tl")
    mcp = d.get("marginal_cost_per_person_tl")
    bv = d.get("bed_value_tl")
    out: dict[str, Any] = {"kind": "golge_fiyat", "demand_cell": [str(c) for c in s.demand_ids],
                           "marginal_cost_tl": None if mc is None else np.asarray(mc, float),
                           "marginal_cost_per_person_tl": None if mcp is None else np.asarray(mcp, float),
                           "bed_value_tl": None if bv is None else np.asarray(bv, float),
                           "hospital_cells": [str(c) for c in s.cand_ids[sol.open_j]]}
    pv = inst.params.pv
    T = inst.params["access.horizon_years"]
    if mcp is not None:
        v = np.asarray(mcp, float)
        okm = np.isfinite(v) & (s.w > 0)
        w = s.w[okm]
        qs = np.percentile(v[okm], [5, 25, 50, 75, 95]) if okm.any() else [None] * 5
        # en yakın açık hastaneye (atanan değil) uzaklıkla ilişki: 10 km içi ve dışı nüfus ağırlıklı ortalama
        a = np.flatnonzero(sol.x > 0)
        dmean = np.bincount(s.arc_i[a], sol.x[a] * s.arc_d[a], minlength=inst.nI)
        near = okm & (dmean <= 10.0)
        far = okm & (dmean > 10.0)
        order = np.argsort(-np.where(okm, v, -np.inf))[:top]
        out["per_person"] = {
            "p5": qs[0], "p25": qs[1], "median": qs[2], "p75": qs[3], "p95": qs[4],
            "pop_weighted_mean": float(np.average(v[okm], weights=w)) if okm.any() else None,
            "mean_within_10km": float(np.average(v[near], weights=s.w[near])) if near.any() else None,
            "mean_beyond_10km": float(np.average(v[far], weights=s.w[far])) if far.any() else None,
            "top_cells": [{"cell_id": str(s.demand_ids[i]), "district": _demand_district(inst, int(i)),
                           "value_tl": float(v[i]), "population": float(s.w[i]), "assigned_km": float(dmean[i])}
                          for i in order if okm[i]]}
        out["per_person"]["annual_equiv"] = (out["per_person"]["median"] / pv) if pv and qs[2] is not None else None
    if bv is not None:
        b = np.asarray(bv, float)
        pop, dem = _served(inst, sol)
        full = dem >= sol.beds * (1 - 1e-6)
        out["bed_value"] = {"min": float(b.min()), "median": float(np.median(b)), "max": float(b.max()),
                            "n_zero": int((b <= 1e-6).sum()), "n_full": int(full.sum()),
                            "median_full": float(np.median(b[full])) if full.any() else None,
                            "per_bed_cost_by_class": {inst.classes[k].code: float(np.median(inst.var[sol.open_j[sol.cls == k], k]))
                                                      for k in np.unique(sol.cls)},
                            "top": [{"cell_id": str(s.cand_ids[sol.open_j[t]]), "district": _district(inst, int(sol.open_j[t])),
                                     "class": inst.classes[int(sol.cls[t])].code, "beds": float(sol.beds[t]),
                                     "value_tl": float(b[t])} for t in np.argsort(-b)[:top]]}
    pp = out.get("per_person") or {}
    bvs = out.get("bed_value") or {}
    msg = []
    if pp.get("median") is not None:
        msg.append(f"Bir talep hücresine 1 kişi daha eklenseydi (hastaneler ve sınıfları sabit) toplam maliyet ortanca "
                   f"{fmt(pp['median'], 0)} TL artardı ({fmt(T, 0)} yıllık bugünkü değer; yatak + erişim). Nüfus "
                   f"ağırlıklı ortalama {fmt(pp['pop_weighted_mean'], 0)} TL; %5–%95 aralığı {fmt(pp['p5'], 0)}–"
                   f"{fmt(pp['p95'], 0)} TL.")
        if pp.get("mean_within_10km") is not None and pp.get("mean_beyond_10km") is not None:
            far_more = pp["mean_beyond_10km"] > pp["mean_within_10km"]
            msg.append(f"Hastanesine 10 km'den uzak hücrelerde ortalama {fmt(pp['mean_beyond_10km'], 0)} TL, 10 km "
                       f"içindekilerde {fmt(pp['mean_within_10km'], 0)} TL"
                       + (": uzak hücrelerin hizmeti daha pahalı." if far_more else
                          ": marjinal maliyeti yalnız mesafe değil, hücrenin bağlı olduğu hastanenin dolu olup olmadığı "
                          "ve sınıf eşiği de belirliyor."))
    if bvs:
        msg.append(f"1 ek (bedava) yatağın erişim değeri: {bvs['n_full']} dolu hastanede ortanca "
                   f"{tl(bvs['median_full'])}; {bvs['n_zero']} hastanede 0 (boş yatak var).")
    out["interpretation_tr"] = " ".join(msg)
    out["caveat_tr"] = ("Gölge fiyatlar açık hastaneler ve sınıfları SABİTKEN atama/boyutlandırma LP'sinin dual "
                        "değerleridir; tamsayı programın kendisinin gölge fiyatı değildir ve yalnız küçük (marjinal) "
                        "değişiklikler için geçerlidir. LP dejenere olabildiğinden dual değerler tek değildir: HiGHS'in "
                        "bulduğu bir köşe çözümü raporlanır (eşit maliyetli başka dual çözümler de vardır).")
    return out


# ---------------------------------------------------------------- model düzeyindeki nedenler
def model_reasons(inst: Instance, sol: Solution) -> dict:
    """Çözümün model düzeyindeki nedenleri (sayılar kayıttan ve çözümden): sınıf maliyet tablosu, bölme karşılaştırması,
    eşiklerde yığılma, R'nin zorladığı hastaneler, düşük doluluk, şehir hastanesi ve uzmanlaşma notu."""
    p = inst.params
    classes = []
    for k, c in enumerate(inst.classes):
        classes.append({"code": c.code, "name": c.name, "L": c.L, "U": c.U, "enabled": bool(c.enabled),
                        "unit_cost_tl_m2": c.unit_cost, "m2_per_bed": c.m2_per_bed, "fixed_tl": c.fixed,
                        "per_bed_tl": c.var, "n_chosen": int((sol.cls == k).sum())})
    on = [c for c in classes if c["enabled"]]
    small = next((c for c in on if c["code"] == "IV-C"), on[0])
    # bölme karşılaştırması: tek hastane (sınıfın üst sınırında) ↔ aynı yatağın en küçük sınıfta m hastaneye bölünmesi
    splits = []
    for c in on:
        if c["code"] == small["code"]:
            continue
        # karşılaştırılan yatak: küçük sınıfın üst sınırının bu sınıfa sığan en büyük katı (ör. 2 × 199 = 398 V-B)
        B = small["U"] * math.floor(c["U"] / small["U"])
        if B < c["L"]:
            B = c["U"]
        m = int(math.ceil(B / small["U"] - 1e-9))
        one = c["fixed_tl"] + c["per_bed_tl"] * B
        many = m * small["fixed_tl"] + small["per_bed_tl"] * B
        if B / m < small["L"]:
            continue
        splits.append({"beds": B, "single_class": c["code"], "single_tl": one, "n_small": m, "small_class": small["code"],
                       "small_total_tl": many, "saving_tl": one - many})
    k = {c.code: kk for kk, c in enumerate(inst.classes)}
    tol = 1e-6
    at_u = {inst.classes[kk].code: int(((sol.cls == kk) & (sol.beds >= inst.U[kk] - tol * inst.U[kk])).sum())
            for kk in range(inst.nK)}
    at_l = int(sum(((sol.cls == kk) & (sol.beds <= inst.L[kk] + tol * max(1, inst.L[kk]))).sum()
                   for kk in range(inst.nK)))
    sc = sole_cover(inst, sol)
    forced = np.flatnonzero(sc["cells_R"] > 0)
    forced_k = np.flatnonzero((sc["cells"] > 0) & (sc["cells_R"] == 0))
    pop, dem = _served(inst, sol)
    occ = dem / np.maximum(sol.beds, 1e-12)
    fixed_rng = (min(c["fixed_tl"] for c in on), max(c["fixed_tl"] for c in on))
    bed_rng = [(c["code"], c["per_bed_tl"]) for c in on]
    reasons = []
    reasons.append({"id": "yatak_basi_artiyor",
                    "text_tr": "Yatak başı inşaat maliyeti sınıf büyüdükçe artıyor (tebliğ birim fiyatı ve yatak başı alan): "
                               + " → ".join(f"{c} {tl(v)}" for c, v in bed_rng) + ". Büyük hastane ölçek ekonomisi "
                               "getirmiyor; tersine, her yatak daha pahalı."})
    reasons.append({"id": "sabit_kisim",
                    "text_tr": f"Hastane başına sabit kısım (çekirdek alan A0 = {fmt(p['cost.core_area_m2'], 0)} m² + "
                               f"F_sabit = {tl(p['cost.fixed_per_hospital'])}) sınıfa göre {tl(fixed_rng[0])} – "
                               f"{tl(fixed_rng[1])}. Bir hastane daha açmanın ek maliyeti budur; karşılığında erişim "
                               f"maliyeti azalır. Optimum bu iki etkinin dengesinde (U eğrisinin dibi)."})
    if splits:
        txt = "; ".join(f"{fmt(x['beds'], 0)} yatak tek {x['single_class']} hastanesinde {tl(x['single_tl'])}, "
                        f"{x['n_small']} {x['small_class']} hastanesine bölünürse {tl(x['small_total_tl'])} "
                        f"({tl(abs(x['saving_tl']))} {'ucuz' if x['saving_tl'] > 0 else 'pahalı'})" for x in splits)
        reasons.append({"id": "bolme",
                        "text_tr": f"Aynı yatak sayısını küçük hastanelere bölmek, erişim kazancı hesaba katılmadan bile: "
                                   f"{txt}. Erişim de kısalır; bu yüzden çok sayıda küçük hastane seçiliyor."})
    reasons.append({"id": "esik_yigilma",
                    "text_tr": f"{at_u.get('IV-C', 0)} hastane {fmt(inst.U[k['IV-C']], 0)} yatakta (IV-C üst sınırı), "
                               f"{at_u.get('V-B', 0)} hastane {fmt(inst.U[k['V-B']], 0)} yatakta (V-B üst sınırı), "
                               f"{at_l} hastane sınıfın en az yatağında: yatak başı maliyet sınıf eşiğinde sıçradığı "
                               f"için model bir üst sınıfa geçmek yerine fazla talebi komşu hastaneye kaydırıyor."})
    if len(forced):
        names = [d for d in (_district(inst, int(sol.open_j[t])) for t in forced) if d]
        uniq = sorted(set(names), key=names.index)
        reasons.append({"id": "R_zorunlu",
                        "text_tr": f"{len(forced)} hastane azami erişim mesafesi R = {fmt(inst.s.R_km, 0)} km yüzünden "
                                   f"zorunlu: kapatılırsa bazı talep hücrelerinin R içinde hiç hastanesi kalmıyor"
                                   + (f" (ör. {', '.join(uniq[:6])})" if uniq else "") +
                                   f". Bunların en düşük doluluğu {pct(float(occ[forced].min()), 0)}: eşitlik–verimlilik "
                                   f"ödünleşimi (kırsal hastaneler maliyet için değil kapsama için açık)."
                                   + (f" {len(forced_k)} hastane daha yalnız modelin en yakın {inst.s.k_nearest} aday "
                                      f"listesi yüzünden kapatılamıyor (k kırpması)." if len(forced_k) else "")})
    city = next((c for c in classes if c["code"] == "V-D"), None)
    if city is not None:
        reasons.append({"id": "sehir_hastanesi",
                        "text_tr": (f"Şehir hastanesi (V-D) {'seçilmedi' if city['n_chosen'] == 0 else str(city['n_chosen']) + ' kez seçildi'}: "
                                    f"yatak başı {tl(city['per_bed_tl'])}, küçük hastanenin ({tl(small['per_bed_tl'])}) "
                                    f"{fmt(city['per_bed_tl'] / small['per_bed_tl'], 2)} katı."
                                    + ("" if city["enabled"] else " (Bu parametrelerle sınıf kapalı.)"))})
    reasons.append({"id": "uzmanlasma",
                    "text_tr": "Uzmanlaşmanın (üçüncü basamak hizmetin) faydası modellenmedi: model büyük hastanenin "
                               "ağır vaka, uzmanlık, eğitim ve yoğun bakım faydasını bilmez; yalnız inşaat/açılış ve "
                               "erişim maliyetini karşılaştırır. Bu yüzden büyük ve şehir hastaneleri seçilmiyor — bu bir "
                               "sınırlılıktır, gerçek planlama önerisi değil."})
    summary = " ".join(r["text_tr"] for r in reasons if r["id"] in ("sabit_kisim", "bolme", "R_zorunlu"))
    return {"kind": "model", "summary_tr": summary, "classes": classes, "splits": splits, "at_upper_bound": at_u, "at_lower_bound": at_l,
            "r_forced": {"n": int(len(forced)), "cells": [str(inst.s.cand_ids[sol.open_j[t]]) for t in forced],
                         "districts": [_district(inst, int(sol.open_j[t])) for t in forced],
                         "population_sole": [float(sc["population_R"][t]) for t in forced],
                         "occupancy": [float(occ[t]) for t in forced]},
            "k_list_forced": {"n": int(len(forced_k)),
                              "cells": [str(inst.s.cand_ids[sol.open_j[t]]) for t in forced_k]},
            "occupancy": {"min": float(occ.min()) if len(occ) else None, "n_below_50pct": int((occ < 0.5).sum())},
            "reasons": reasons}
