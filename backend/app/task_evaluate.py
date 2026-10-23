"""İş türü 'evaluate' (manuel mod, şartname 6.5 ve 7.6): kullanıcının sabitlediği açık hücreler (isteğe bağlı sınıf ve
yatak) için en iyi atama / boyutlandırma, maliyet kırılımı, KPI'lar, bağımsız denetim ve atama LP'sinin dual değerleri.

Çözüm yolu:
  * hiçbir hastanede sınıf/yatak verilmemişse ya da hepsinde sınıf verilmişse (yataklar hepsinde ya da hiçbirinde):
    motorun kendi değerlendirmesi (backend.model.evaluate.evaluate);
  * karışık durumda (bazı hastanelerin sınıfı/yatağı sabit, diğerleri serbest): aynı formülasyonun hastane başına
    sınıf ve yatak sınırlı genellemesi (evaluate_mixed). Testler, tekdüze durumlarda motorla aynı sonucu verdiğini
    denetler (tests/test_phase4_evaluate.py).
Yatak verilip sınıf verilmezse sınıf tebliğ eşiklerine göre yataktan belirlenir (şartname 4.1: maliyet sınıfı yatak
sayısına göre otomatik); genel sınıflar şehir sınıfından önce denenir.

Sunucu, sonuca aynı parametrelerle önbellekte bulunan en iyi 'solve' sonucuyla karşılaştırmayı ekler
("optimumdan %X daha pahalı"); karşılaştırma önbelleğe yazılmaz, her okumada güncel önbellekten hesaplanır.
"""
from __future__ import annotations

import math
import time

import numpy as np

from backend.model.params import CLASS_CODES

from .config import engine_version
from .schemas import EvaluateIn
from .task_solve import NO_LOCKS, solve_key
from .tasks import ApiValidationError, JobContext, JobType, Prepared, register, stable_key
from .validation import _grid_from, cache_params, check_cells, fmt_num, resolve_problem

EVAL_TIME_LIMIT_S = 30.0     # sınıf seçimi MIP'i için süre sınırı (motorun evaluate varsayılanı)
EVAL_GAP = 1e-6              # sınıf seçimi MIP'inin hedef gap'i (motorun evaluate varsayılanı)


def prepare(req: EvaluateIn) -> Prepared:
    errors: list[dict] = []
    rp = None
    try:
        rp = resolve_problem(req)
        km = rp.grid_km
    except ApiValidationError as ex:          # hücre hataları da aynı yanıtta görünsün
        errors += ex.errors
        km = req.grid_km or _grid_from(req.overrides)
    errors += check_cells([h.cell_id for h in req.hospitals], km, ["body", "hospitals"], ["cell_id"])
    seen: dict[str, int] = {}
    for i, h in enumerate(req.hospitals):
        if h.cell_id in seen:
            errors.append({"loc": ["body", "hospitals", i, "cell_id"], "key": None, "value": h.cell_id,
                           "type": "tekrar", "message_tr": f"Aynı hücrede birden fazla hastane olamaz: '{h.cell_id}' "
                                                         f"(hastane {seen[h.cell_id] + 1} ile aynı)."})
        seen.setdefault(h.cell_id, i)
    if rp is None:
        raise ApiValidationError(errors, "Manuel yerleşim doğrulanamadı.")
    classes = rp.params.classes
    out = []
    for i, h in enumerate(req.hospitals):
        k = None
        loc = ["body", "hospitals", i]
        if h.class_ is not None:
            k = CLASS_CODES.index(h.class_)
            c = classes[k]
            if not c.enabled:
                errors.append({"loc": loc + ["class"], "key": None, "value": h.class_, "type": "sinif_kapali",
                               "message_tr": f"{c.code} sınıfı bu parametrelerle kullanılamıyor ({c.disabled_reason})."})
            elif h.beds is not None and not (c.L - 1e-9 <= h.beds <= c.U + 1e-9):
                errors.append({"loc": loc + ["beds"], "key": None, "value": h.beds, "type": "aralik_disi",
                               "allowed": {"min": c.L, "max": c.U, "min_inclusive": True, "max_inclusive": True},
                               "message_tr": f"{c.code} sınıfında yatak sayısı {fmt_num(c.L)}–{fmt_num(c.U)} "
                                             f"aralığında olmalı (verilen {fmt_num(h.beds)})."})
        elif h.beds is not None:
            match = [kk for kk, c in enumerate(classes) if c.enabled and c.L - 1e-9 <= h.beds <= c.U + 1e-9]
            if not match:
                rngs = "; ".join(f"{c.code} {fmt_num(c.L)}–{fmt_num(c.U)}" for c in classes if c.enabled)
                errors.append({"loc": loc + ["beds"], "key": None, "value": h.beds, "type": "aralik_disi",
                               "allowed": {"classes": [{"code": c.code, "min": c.L, "max": c.U}
                                                       for c in classes if c.enabled]},
                               "message_tr": f"Yatak sayısı ({fmt_num(h.beds)}) hiçbir etkin sınıfın aralığında "
                                             f"değil: {rngs}."})
            else:
                k = match[0]
        out.append({"cell_id": h.cell_id, "cls": k, "beds": None if h.beds is None else float(h.beds),
                    "class_given": h.class_ is not None, "beds_given": h.beds is not None})
    if errors:
        raise ApiValidationError(errors, "Manuel yerleşim doğrulanamadı.")
    ref_key = solve_key(rp.params, NO_LOCKS)
    payload = {"grid_km": km, "overrides": rp.overrides, "hospitals": out, "duals": bool(req.duals),
               "reference_key": ref_key}
    key = stable_key("evaluate", {"engine": engine_version(), "params": cache_params(rp.params),
                                  "hospitals": sorted([h["cell_id"], h["cls"], h["beds"]] for h in out)})
    info = {"grid_km": km, "mode": rp.params.objective_mode, "scenario_year": rp.params.scenario_year,
            "n_hospitals": len(out), "params_hash": rp.params.hash(), "overrides": rp.overrides}
    return Prepared(payload=payload, cache_key=key, variant={"duals": bool(req.duals)},
                    max_runtime_s=EVAL_TIME_LIMIT_S * 2 + 20.0, warnings=rp.warnings, info=info)


# ---------------------------------------------------------------------- değerlendirme (işçide)
def evaluate_mixed(inst, open_j, cls_opt: list, beds_opt: list, *, time_limit: float = EVAL_TIME_LIMIT_S,
                   gap: float = EVAL_GAP, cancel_event=None):
    """backend.model.evaluate.evaluate'in hastane başına genellemesi: cls_opt[t] None → sınıfı model seçer (izinli
    sınıflar arasından), değilse sabit; beds_opt[t] None → yatak sınıf aralığında serbest, değilse sabit.
    Sütunlar ve satırlar motorunkiyle birebir aynıdır (y_p | B_p | x; atama, sınıf seçimi, alt/üst, kapasite, bütçe)."""
    import scipy.sparse as sp

    from backend.model.evaluate import CANCELLED_TR, INF, EvalResult, _highs, _pass, cost_breakdown, run_highs
    from backend.model.instance import Solution
    from backend.model.milp import BUDGET_EPS, SCALE

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
    sub = np.flatnonzero(pos[s.arc_j] >= 0)
    cover = np.zeros(inst.nI, dtype=bool)
    cover[s.arc_i[sub]] = True
    if not cover.all():
        bad = np.flatnonzero(~cover)
        return EvalResult(False, "olurlu_degil",
                          f"{len(bad)} talep hücresinin ({fmt_num(int(round(s.w[bad].sum())))} kişi) R içinde açık "
                          f"hastanesi yok.")
    K = inst.nK
    allowed = np.zeros((nS, K), dtype=bool)
    for t in range(nS):
        if cls_opt[t] is None:
            allowed[t] = inst.y_ub[S[t]]
        elif inst.y_ub[S[t], int(cls_opt[t])]:
            allowed[t, int(cls_opt[t])] = True
    if not allowed.any(axis=1).all():
        return EvalResult(False, "olurlu_degil", "Açık hücrelerden birinde kullanılabilir boyut sınıfı yok "
                                                 "(sınıf kapalı ya da hücre yasak kilitli).")
    pj, pk = np.nonzero(allowed)
    nP = len(pj)
    nX = len(sub)
    jg = S[pj]
    F = inst.fixed[jg, pk]
    V = inst.var[jg, pk]
    w_open = BUDGET_EPS if inst.mode == "butce" else 1.0
    c = np.concatenate([w_open * F, w_open * V, inst.acc[sub]]) / SCALE
    lb = np.zeros(2 * nP + nX)
    ub = np.concatenate([np.ones(nP), inst.U[pk], np.ones(nX)])
    n_opt = allowed.sum(axis=1)
    single = n_opt[pj] == 1
    lb[:nP][single] = 1.0                                   # tek seçenekli hastane: y = 1
    bfix = np.array([np.nan if beds_opt[t] is None else float(beds_opt[t]) for t in range(nS)])[pj]
    fixed_b = ~np.isnan(bfix)
    lb[nP:2 * nP][fixed_b] = bfix[fixed_b]
    ub[nP:2 * nP][fixed_b] = bfix[fixed_b]
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
    is_mip = bool(integ.any())
    h = _highs()
    _pass(h, c, lb, ub, A, rlo, rhi, integ)
    h.setOptionValue("time_limit", float(time_limit))
    if is_mip:
        h.setOptionValue("mip_rel_gap", float(gap))
    if run_highs(h, cancel_event):
        return EvalResult(False, "durduruldu", CANCELLED_TR, time_s=time.perf_counter() - t0)
    status = h.modelStatusToString(h.getModelStatus())
    info = h.getInfo()
    if info.primal_solution_status != 2:
        return EvalResult(False, "olurlu_degil", "Bu açık hastane kümesiyle olurlu atama yok (kapasite, sınıf, sabit "
                                                 "yatak ya da bütçe sınırı).", time_s=time.perf_counter() - t0)
    v = np.asarray(h.getSolution().col_value)
    y = v[:nP]
    sel = np.flatnonzero(y > 0.5)
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
    sol = Solution(open_j=S[order], cls=chosen_k[order], beds=chosen_b[order], x=x, source="evaluate_mixed")
    return EvalResult(True, status, solution=sol, objective_tl=sol.objective_tl(inst),
                      solver_objective_tl=float(info.objective_function_value) * SCALE,
                      breakdown=cost_breakdown(inst, sol), time_s=time.perf_counter() - t0)


def _milyar(x: float) -> str:
    return f"{fmt_num(round(x / 1e9, 2))} milyar TL"


def run(ctx: JobContext, payload: dict) -> dict:
    from backend.model import checks as checks_mod
    from backend.model import layers as layers_mod
    from backend.model.evaluate import assignment_duals, cost_breakdown, evaluate, per_hospital
    from backend.model.instance import build_instance
    from backend.model.params import Params
    from backend.model.solve import _jsonable, kpis

    T0 = time.perf_counter()
    times: dict[str, float] = {}
    km = int(payload["grid_km"])
    hs = payload["hospitals"]
    params = Params.load(payload["overrides"])
    ctx.emit({"phase": "ornek", "phase_tr": "Örnek kuruluyor"}, force=True)
    inst = build_instance(km, params, None)
    pos = {str(c): j for j, c in enumerate(inst.s.cand_ids)}
    extra = sorted({h["cell_id"] for h in hs if h["cell_id"] not in pos})
    if extra:                       # aday olmayan hücreler 'aç' kilidiyle aday kümesine eklenir (yapı değişir)
        inst = build_instance(km, params, {"open": extra})
        pos = {str(c): j for j, c in enumerate(inst.s.cand_ids)}
    times["instance_s"] = time.perf_counter() - T0
    S = [pos[h["cell_id"]] for h in hs]
    cls = [h["cls"] for h in hs]
    beds = [h["beds"] for h in hs]
    ctx.emit({"phase": "degerlendirme", "phase_tr": "Atama ve boyutlandırma"}, force=True)
    t = time.perf_counter()
    ce = ctx.cancel_event
    if all(k is None for k in cls) and all(b is None for b in beds):
        method = "motor_evaluate"
        ev = evaluate(inst, S, time_limit=EVAL_TIME_LIMIT_S, gap=EVAL_GAP, cancel_event=ce)
    elif all(k is not None for k in cls) and (all(b is None for b in beds) or all(b is not None for b in beds)):
        method = "motor_evaluate_sabit_sinif"
        ev = evaluate(inst, S, cls=np.array(cls, dtype=np.int64),
                      beds=None if beds[0] is None else np.array(beds, dtype=float), time_limit=EVAL_TIME_LIMIT_S,
                      gap=EVAL_GAP, cancel_event=ce)
    else:
        method = "karisik"
        ev = evaluate_mixed(inst, S, cls, beds, cancel_event=ce)
    times["evaluate_s"] = time.perf_counter() - t
    base = {"grid_km": km, "mode": params.objective_mode, "params_hash": params.hash(),
            "structure_hash": inst.s.key, "overrides": dict(params.overrides), "method": method,
            "reference_key": payload["reference_key"], "added_candidates": extra,
            "instance": {"I": inst.nI, "J": inst.nJ, "K": inst.nK, "arcs": inst.nA,
                         "total_demand_beds": inst.total_demand, "lambda_tl_per_person_km": params.lam},
            "input": hs, "warnings": list(inst.warnings)}
    if not ev.ok:
        times["total_s"] = time.perf_counter() - T0
        return _jsonable({**base, "ok": False, "status": ev.status, "message_tr": ev.message, "times": times})
    sol = ev.solution
    t = time.perf_counter()
    chk = checks_mod.check(inst, sol, solver_objective_tl=ev.solver_objective_tl)
    times["check_s"] = time.perf_counter() - t
    costs = cost_breakdown(inst, sol)
    k = kpis(inst, sol)
    hosp = per_hospital(inst, sol)
    s = inst.s
    g = s.grid
    given = {h["cell_id"]: h for h in hs}
    for h in hosp:
        j = h.pop("pos")
        cid = str(s.cand_ids[j])
        gi = int(s.cand_cells[j])
        h.update(cell_id=cid, cand_pos=j, grid_index=gi, lon=float(g.lon[gi]), lat=float(g.lat[gi]),
                 district=g.district_names[int(g.district[gi])] if int(g.district[gi]) >= 0 else None,
                 class_given=given[cid]["class_given"], beds_given=given[cid]["beds_given"])
    for h, sz in zip(hosp, layers_mod.size_info(inst, sol)):
        h["size"] = sz
    hpos = np.full(inst.nJ, -1, dtype=np.int64)
    hpos[sol.open_j] = np.arange(sol.n_open)
    a = np.flatnonzero((sol.x > 0) & (hpos[s.arc_j] >= 0))
    assignment = {"demand_cell": [str(c) for c in s.demand_ids[s.arc_i[a]]], "hospital": hpos[s.arc_j[a]].tolist(),
                  "share": sol.x[a].tolist(), "km": s.arc_d[a].tolist(),
                  "population": layers_mod.flow_population(inst, sol, a)}
    k["cost_per_capita_tl"] = costs["toplam"] / k["population"] if k["population"] > 0 else None
    c = k["count_by_class"]
    within = k["share_pop_within_km"]
    msg = (f"Elle seçilen yerleşim: {k['n_hospitals']} hastane ({c.get('IV-C', 0)} küçük, {c.get('V-B', 0)} orta, "
           f"{c.get('V-C', 0)} büyük, {c.get('V-D', 0)} şehir hastanesi), toplam {_milyar(costs['toplam'])}; "
           f"hastaneye 10 km içinde oturan nüfusun payı %{fmt_num(round(100 * (within.get('10') or 0), 1))}.")
    result = {**base, "ok": True, "status": "degerlendirildi", "status_tr": "Değerlendirildi",
              "message_tr": msg, "solver_status": ev.status, "objective_tl": ev.objective_tl,
              "solver_objective_tl": ev.solver_objective_tl, "hospitals": hosp, "assignment": assignment,
              "costs": costs, "kpis": k, "check": chk, "layers": layers_mod.map_layers(inst, sol)}
    if payload.get("duals") and not ctx.cancelled():
        t = time.perf_counter()
        d = assignment_duals(inst, sol)
        times["duals_s"] = time.perf_counter() - t
        result["duals"] = {"demand_cell": [str(x) for x in s.demand_ids],
                           "marginal_cost_tl": d.get("marginal_cost_tl"),
                           "marginal_cost_per_person_tl": d.get("marginal_cost_per_person_tl"),
                           "bed_value_tl": d.get("bed_value_tl"), "note_tr": d.get("degenerate_note")}
    times["total_s"] = time.perf_counter() - T0
    result["times"] = times
    return _jsonable(result)


# ---------------------------------------------------------------------- sunucuda: optimumla karşılaştırma
def finalize(res: dict, manager) -> dict:
    """Aynı parametrelerle (kilitsiz) önbellekteki en iyi 'solve' sonucuyla karşılaştırma (şartname 6.5)."""
    if not res.get("ok"):
        return res
    res = dict(res)
    cache = getattr(manager, "cache", None)
    entries = cache.entries(res.get("reference_key", "")) if cache is not None else []
    entries = [e for e in entries if e.variant.get("ub") is not None]
    if not entries:
        res["comparison"] = {"available": False,
                             "message_tr": "Aynı parametrelerle çözülmüş bir optimum henüz yok; karşılaştırma için "
                                           "önce optimizasyonu çalıştırın."}
        return res
    best = min(entries, key=lambda e: (e.variant["ub"], e.variant.get("ach") or 1.0))
    ub = float(best.variant["ub"])
    # aynı problemin her kaydının alt sınırı geçerlidir: en büyüğü kullanılır
    lbs = [float(e.variant["lb"]) for e in entries if e.variant.get("lb") is not None]
    lb = max(lbs) if lbs else None
    ach = (ub - lb) / abs(ub) if lb is not None and ub else best.variant.get("ach")
    obj = float(res["objective_tl"])
    diff = obj - ub
    pct = diff / abs(ub) if ub else None
    pct_lb = (obj - lb) / abs(lb) if lb else None
    what = "erişim maliyeti " if res.get("mode") == "butce" else ""
    if pct is not None and abs(pct) < 5e-7:
        msg = f"Bu yerleşimin {what}maliyeti optimumla aynı"
    elif pct is not None and pct >= 0:
        msg = (f"Bu yerleşimin {what}maliyeti optimumdan %{fmt_num(round(100 * pct, 2))} "
               f"({_milyar(diff)}) daha pahalı")
    else:
        msg = (f"Bu yerleşimin {what}maliyeti bulunan en iyi çözümden %{fmt_num(round(-100 * (pct or 0), 2))} "
               f"({_milyar(-diff)}) daha düşük — en iyi çözüm kanıtlı optimum değildi")
    if ach is not None:
        msg += (f" (karşılaştırılan çözüm: {best.variant.get('n')} hastane, {_milyar(ub)}; alt sınırla farkı "
                f"%{fmt_num(round(100 * ach, 3))}")
        if pct_lb is not None and pct_lb >= 0:
            msg += f"; kanıtlanmış alt sınıra göre en çok %{fmt_num(round(100 * pct_lb, 2))} pahalı"
        msg += ")"
    msg += "."
    res["comparison"] = {
        "available": True, "diff_tl": diff, "pct_vs_best": pct, "pct_vs_lower_bound": pct_lb,
        "reference": {"ub_tl": ub, "lb_tl": lb, "gap": ach, "status": best.variant.get("status"),
                      "n_hospitals": best.variant.get("n"), "time_limit_bucket_s": best.variant.get("tl"),
                      "cache_entry": best.name},
        "message_tr": msg,
    }
    if pct is not None and not math.isfinite(pct):
        res["comparison"]["pct_vs_best"] = None
    return res


register(JobType(
    name="evaluate", request_model=EvaluateIn, prepare=prepare, run=run,
    description_tr="Manuel mod: elle seçilen hastanelerle atama + boyutlandırma, maliyet, KPI ve dual değerler.",
    cooperative_cancel=True,            # HiGHS kesme geri çağrısıyla (backend.model.evaluate.run_highs)
    satisfies=lambda c, r: bool(c.get("duals")) or not r.get("duals"),
    finalize=finalize,
))
