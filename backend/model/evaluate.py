"""Açık hastane kümesi verildiğinde en iyi atama ve boyutlandırma; maliyet kırılımı; atama LP'sinin dual değerleri.

evaluate(inst, open_j, cls=None, beds=None):
  cls verilmezse her açık hücre için sınıf seçimi küçük bir MIP'tir (y_jk ikili, açık hücre başına tam bir sınıf);
  cls verilirse LP; beds de verilirse yataklar sabittir (yalnız atama LP'si).
  Amaç modun amacıdır: toplam/sabit_p → açılış + erişim; butce → erişim + ε·açılış ve açılış ≤ bütçe.

Dual değerler (şartname 7.6; açık hastaneler ve sınıflar sabit):
  marginal_cost_tl[i]   boyutlandırma LP'si (yatak sınıf aralığında serbest) atama satırının duali u_i: i hücresinin
                        talebinin tamamına hizmetin marjinal maliyeti (TL / hücre); kişi başı = u_i / w_i.
  bed_value_tl[j]       yataklar çözümdeki değerlerde sabitken atama LP'sinin kapasite satırı duali (işareti
                        çevrilmiş): j'ye 1 ek (bedava) yatak verilseydi erişim maliyetinin azalacağı tutar (TL / yatak).
Dejenere satırlarda dual değerler tek değildir (HiGHS'in verdiği bir köşe çözümü raporlanır).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import highspy

from .instance import Instance, Solution
from .milp import BUDGET_EPS, SCALE

INF = highspy.kHighsInf


@dataclass
class EvalResult:
    ok: bool
    status: str
    message: str = ""
    solution: Solution | None = None
    objective_tl: float | None = None     # modun amacı (butce'de yalnız erişim)
    solver_objective_tl: float | None = None  # HiGHS'in bildirdiği amaç (butce'de ε·açılış dahil)
    breakdown: dict = field(default_factory=dict)
    duals: dict | None = None
    time_s: float = 0.0


def _highs() -> highspy.Highs:
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    return h


def _pass(h: highspy.Highs, c, lb, ub, A: sp.csc_matrix, rlo, rhi, integ=None) -> None:
    n, m = A.shape[1], A.shape[0]
    integ = np.zeros(n, dtype=np.int32) if integ is None else integ.astype(np.int32)
    h.passModel(n, m, int(A.nnz), 1, 1, 0.0, np.asarray(c, float), np.asarray(lb, float), np.asarray(ub, float),
                np.asarray(rlo, float), np.asarray(rhi, float), A.indptr.astype(np.int32),
                A.indices.astype(np.int32), A.data.astype(float), integ)


def evaluate(inst: Instance, open_j, cls=None, beds=None, *, duals: bool = False, time_limit: float = 30.0,
             gap: float = 1e-6) -> EvalResult:
    """Açık küme için en iyi atama/boyutlandırma. Olurlu değilse ok=False ve Türkçe mesaj."""
    t0 = time.perf_counter()
    s = inst.s
    S = np.asarray(open_j, dtype=np.int64)
    if len(np.unique(S)) != len(S):
        return EvalResult(False, "gecersiz", "Aynı hücrede birden fazla hastane açılamaz.")
    nS = len(S)
    if nS == 0:
        return EvalResult(False, "olurlu_degil", "Hiç hastane açık değil.")
    pos = np.full(inst.nJ, -1, dtype=np.int64)
    pos[S] = np.arange(nS)
    sub = np.flatnonzero(pos[s.arc_j] >= 0)              # açık hücrelere giden yaylar
    cover = np.zeros(inst.nI, dtype=bool)
    cover[s.arc_i[sub]] = True
    if not cover.all():
        bad = np.flatnonzero(~cover)
        return EvalResult(False, "olurlu_degil",
                          f"{len(bad)} talep hücresinin ({int(round(s.w[bad].sum())):,} kişi) R içinde açık hastanesi "
                          f"yok.".replace(",", "."))
    K = inst.nK
    if cls is None:
        allowed = inst.y_ub[S]                              # (nS, K)
        if not allowed.any(axis=1).all():
            return EvalResult(False, "olurlu_degil", "Açık hücrelerden birinde kullanılabilir boyut sınıfı yok.")
    else:
        cls = np.asarray(cls, dtype=np.int64)
        allowed = np.zeros((nS, K), dtype=bool)
        allowed[np.arange(nS), cls] = True
    pj, pk = np.nonzero(allowed)                              # sınıf seçenekleri (açık konum, sınıf)
    nP = len(pj)
    nX = len(sub)
    # sütunlar: y_p (nP) | B_p (nP) | x (nX)
    jg = S[pj]
    F = inst.fixed[jg, pk]
    V = inst.var[jg, pk]
    w_open = BUDGET_EPS if inst.mode == "butce" else 1.0
    c = np.concatenate([w_open * F, w_open * V, inst.acc[sub]]) / SCALE
    lb = np.zeros(2 * nP + nX)
    ub = np.concatenate([np.ones(nP), inst.U[pk], np.ones(nX)])
    if cls is not None:
        lb[:nP] = 1.0                                         # sınıf sabit → y = 1
        if beds is not None:
            b = np.asarray(beds, dtype=float)
            lb[nP:2 * nP] = b
            ub[nP:2 * nP] = b
    # satırlar: atama (nI) | sınıf seçimi (nS) | alt (nP) | üst (nP) | kapasite (nS) | bütçe (1)
    rows, cols, vals = [], [], []

    def add(r, cc, v):
        rows.append(np.asarray(r, np.int64))
        cols.append(np.asarray(cc, np.int64))
        vals.append(np.broadcast_to(np.asarray(v, float), np.shape(r)).astype(float))

    r_as, r_cl, r_lo, r_up, r_cap, r_bud = 0, inst.nI, inst.nI + nS, inst.nI + nS + nP, inst.nI + nS + 2 * nP, \
        inst.nI + 2 * nS + 2 * nP
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
    if cls is None:
        integ[:nP] = 1
    h = _highs()
    _pass(h, c, lb, ub, A, rlo, rhi, integ)
    h.setOptionValue("time_limit", float(time_limit))
    if cls is None:
        h.setOptionValue("mip_rel_gap", float(gap))
    h.run()
    status = h.modelStatusToString(h.getModelStatus())
    info = h.getInfo()
    if info.primal_solution_status != 2:
        msg = "Bu açık hastane kümesiyle olurlu atama yok (kapasite, sınıf ya da bütçe sınırı)."
        return EvalResult(False, "olurlu_degil", msg, time_s=time.perf_counter() - t0)
    v = np.asarray(h.getSolution().col_value)
    y = v[:nP]
    sel = np.flatnonzero(y > 0.5) if cls is None else np.arange(nP)
    # her açık konum için seçilen sınıf
    chosen_k = np.full(nS, -1, dtype=np.int64)
    chosen_b = np.zeros(nS)
    chosen_k[pj[sel]] = pk[sel]
    chosen_b[pj[sel]] = v[nP + sel]
    if (chosen_k < 0).any():
        return EvalResult(False, "hata", "Sınıf seçimi okunamadı.", time_s=time.perf_counter() - t0)
    chosen_b = np.clip(chosen_b, inst.L[chosen_k], inst.U[chosen_k])
    x = np.zeros(inst.nA)
    x[sub] = np.clip(v[2 * nP:], 0.0, 1.0)
    x[x < 1e-9] = 0.0
    order = np.argsort(S)
    sol = Solution(open_j=S[order], cls=chosen_k[order], beds=chosen_b[order], x=x, source="evaluate")
    res = EvalResult(True, status, solution=sol, objective_tl=sol.objective_tl(inst),
                     solver_objective_tl=float(info.objective_function_value) * SCALE,
                     breakdown=cost_breakdown(inst, sol))
    if duals:
        res.duals = assignment_duals(inst, sol)
    res.time_s = time.perf_counter() - t0
    return res


def cost_breakdown(inst: Instance, sol: Solution) -> dict:
    """Maliyet kırılımı (TL): yapı (KDV hariç), KDV, altyapı, donanım, sabit, arsa, işletme, erişim."""
    parts = {"yapi": 0.0, "kdv": 0.0, "altyapi": 0.0, "donanim": 0.0, "sabit": 0.0, "arsa": 0.0, "isletme": 0.0}
    for j, k, b in zip(sol.open_j, sol.cls, sol.beds):
        c = inst.classes[int(k)]
        for key, val in c.fixed_parts.items():
            parts[key] += val
        for key, val in c.var_parts.items():
            parts[key] += val * b
        parts["arsa"] += inst.land_per_bed[int(j)] * b
    parts["erisim"] = sol.access_tl(inst)
    parts["insaat"] = parts["yapi"] + parts["kdv"] + parts["altyapi"] + parts["donanim"]
    parts["acilis"] = parts["insaat"] + parts["sabit"] + parts["arsa"] + parts["isletme"]
    parts["toplam"] = parts["acilis"] + parts["erisim"]
    return parts


def per_hospital(inst: Instance, sol: Solution) -> list[dict]:
    """Hastane başına: atanan talep, nüfus, ortalama/en uzak yol km, maliyet kırılımı."""
    s = inst.s
    used = sol.x > 0
    a = np.flatnonzero(used)
    pos = np.full(inst.nJ, -1, dtype=np.int64)
    pos[sol.open_j] = np.arange(sol.n_open)
    hp = pos[s.arc_j[a]]
    xa = sol.x[a]
    pop = np.bincount(hp, s.w[s.arc_i[a]] * xa, minlength=sol.n_open)
    dem = np.bincount(hp, inst.q[s.arc_i[a]] * xa, minlength=sol.n_open)
    dsum = np.bincount(hp, s.w[s.arc_i[a]] * xa * s.arc_d[a], minlength=sol.n_open)
    dmax = np.zeros(sol.n_open)
    np.maximum.at(dmax, hp, s.arc_d[a])
    acc = np.bincount(hp, inst.acc[a] * xa, minlength=sol.n_open)
    out = []
    for t, (j, k, b) in enumerate(zip(sol.open_j, sol.cls, sol.beds)):
        c = inst.classes[int(k)]
        parts = {key: float(val) for key, val in c.fixed_parts.items()}
        for key, val in c.var_parts.items():
            parts[key] = parts.get(key, 0.0) + float(val * b)
        parts["arsa"] = float(inst.land_per_bed[int(j)] * b)
        parts["acilis"] = float(sum(parts.values()))
        parts["erisim"] = float(acc[t])
        out.append({"pos": int(j), "class": c.code, "class_name": c.name, "beds": float(b),
                    "assigned_beds": float(dem[t]), "population": float(pop[t]),
                    "mean_km": float(dsum[t] / pop[t]) if pop[t] > 0 else None,
                    "max_km": float(dmax[t]) if pop[t] > 0 else None,
                    "occupancy": float(dem[t] / b) if b > 0 else None, "costs": parts})
    return out


def assignment_duals(inst: Instance, sol: Solution) -> dict:
    """Açık hastaneler ve sınıflar sabitken iki LP'nin dual değerleri (bkz. modül notu)."""
    s = inst.s
    S = sol.open_j
    nS = len(S)
    pos = np.full(inst.nJ, -1, dtype=np.int64)
    pos[S] = np.arange(nS)
    sub = np.flatnonzero(pos[s.arc_j] >= 0)
    nX = len(sub)
    k = sol.cls
    V = inst.var[S, k]
    out: dict = {}
    for variant in ("sizing", "fixed_beds"):
        # sütunlar: B (nS) | x (nX); satırlar: atama (nI) | kapasite (nS)
        if variant == "sizing":
            c = np.concatenate([V, inst.acc[sub]]) / SCALE
            lb = np.concatenate([inst.L[k], np.zeros(nX)])
            ub = np.concatenate([inst.U[k], np.ones(nX)])
        else:
            c = np.concatenate([np.zeros(nS), inst.acc[sub]]) / SCALE
            lb = np.concatenate([sol.beds, np.zeros(nX)])
            ub = np.concatenate([sol.beds, np.ones(nX)])
        rows = np.concatenate([s.arc_i[sub], inst.nI + pos[s.arc_j[sub]], inst.nI + np.arange(nS)])
        cols = np.concatenate([nS + np.arange(nX), nS + np.arange(nX), np.arange(nS)])
        vals = np.concatenate([np.ones(nX), inst.q[s.arc_i[sub]], -np.ones(nS)])
        A = sp.csc_matrix((vals, (rows, cols)), shape=(inst.nI + nS, nS + nX))
        A.sort_indices()
        rlo = np.concatenate([np.ones(inst.nI), np.full(nS, -INF)])
        rhi = np.concatenate([np.ones(inst.nI), np.zeros(nS)])
        h = _highs()
        _pass(h, c, lb, ub, A, rlo, rhi)
        h.setOptionValue("solver", "simplex")
        h.run()
        if h.getInfo().primal_solution_status != 2 or h.getInfo().dual_solution_status != 2:
            out[variant] = None
            continue
        rd = np.asarray(h.getSolution().row_dual) * SCALE
        out[variant] = {"assign": rd[:inst.nI], "capacity": rd[inst.nI:], "objective_tl":
                        float(h.getInfo().objective_function_value) * SCALE}
    sz, fb = out.get("sizing"), out.get("fixed_beds")
    res = {"degenerate_note": "Dejenere satırlarda dual değerler tek değildir."}
    if sz is not None:
        res["marginal_cost_tl"] = sz["assign"]
        with np.errstate(divide="ignore", invalid="ignore"):
            res["marginal_cost_per_person_tl"] = np.where(s.w > 0, sz["assign"] / s.w, np.nan)
        res["sizing_bed_price_tl"] = -sz["capacity"]
    if fb is not None:
        res["bed_value_tl"] = -fb["capacity"]
        res["marginal_access_cost_tl"] = fb["assign"]
    return res
