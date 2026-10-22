"""Tek giriş noktası: solve(...) → JSON'a çevrilebilir sonuç sözlüğü (şartname 4.6).

Akış: parametreler (+ kullanıcı değişiklikleri) → örnek (yapı önbellekli) → ön olurluluk denetimi → sezgisel (açgözlü +
yerel arama + kesin değerlendirme) → MILP (sezgisel çözüm MIP başlangıcı; süre sınırı, gap, ilerleme, iptal) → en iyi
çözüm açık kümesinde sınıf/atama cilası (evaluate) → bağımsız denetim (checks) → KPI'lar ve maliyet kırılımı.

Durum kodları (status): optimal (hedef gap'e ulaşıldı) | sure_doldu (süre doldu, en iyi çözüm) | durduruldu
(kullanıcı iptal etti) | sezgisel (MIP çalışmadı/çözüm üretmedi; sezgisel çözüm) | olurlu_degil | cozum_yok | hata.
Para birimleri TL, mesafeler yol km.
"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Mapping

import numpy as np

from . import checks as checks_mod
from .evaluate import assignment_duals, cost_breakdown, evaluate, per_hospital
from .heuristics import heuristic
from .instance import InfeasibleError, Instance, Locks, Solution, build_instance
from .milp import BUDGET_EPS, MilpModel
from .params import ParamError, Params

KPI_RADII_KM = (10, 20, 30)          # "nüfusun %Y'si Z km içinde" (raporlama eşikleri; model parametresi değil)
_MODEL_CACHE: "OrderedDict[tuple, tuple[MilpModel, threading.Lock]]" = OrderedDict()
MODEL_CACHE_SIZE = 4


def _fmt(x: float, nd: int = 1) -> str:
    s = f"{x:,.{nd}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return [_jsonable(v) for v in o.tolist()]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _get_model(inst: Instance, linking) -> tuple[MilpModel, threading.Lock | None, str]:
    """Aynı yapı için önbellekteki modeli yerinde günceller; meşgulse ya da yoksa yeni kurar."""
    key = (inst.s.key, str(linking), inst.nK)
    if key in _MODEL_CACHE:
        m, lock = _MODEL_CACHE[key]
        if lock.acquire(blocking=False):
            try:
                m.h.resetOptions()                      # önceki çağrının seçenekleri taşınmasın
                m.h.setOptionValue("output_flag", False)
                m.update(inst)
                _MODEL_CACHE.move_to_end(key)
                return m, lock, "guncellendi"
            except Exception:
                lock.release()
                del _MODEL_CACHE[key]
    m = MilpModel(inst, linking=linking)
    lock = threading.Lock()
    lock.acquire()
    _MODEL_CACHE[key] = (m, lock)
    while len(_MODEL_CACHE) > MODEL_CACHE_SIZE:
        old_key = next(iter(_MODEL_CACHE))
        if old_key == key:
            break
        _MODEL_CACHE.pop(old_key)
    return m, lock, "kuruldu"


def clear_cache() -> None:
    _MODEL_CACHE.clear()


# ---------------------------------------------------------------- KPI'lar
def kpis(inst: Instance, sol: Solution) -> dict:
    s = inst.s
    x = sol.x
    a = np.flatnonzero(x > 0)
    w = s.w[s.arc_i[a]] * x[a]
    d = s.arc_d[a]
    pop = float(s.w.sum())
    by_class = {c.code: int((sol.cls == k).sum()) for k, c in enumerate(inst.classes)}
    beds = float(sol.beds.sum())
    out = {
        "n_hospitals": sol.n_open,
        "count_by_class": by_class,
        "total_beds": beds,
        "total_demand_beds": inst.total_demand,
        "population": pop,
        "pop_weighted_mean_km": float((w * d).sum() / pop) if pop > 0 else None,
        "share_pop_within_km": {str(r): min(1.0, float(w[d <= r + 1e-9].sum() / pop)) if pop > 0 else None
                                for r in KPI_RADII_KM},
        "max_km": float(d.max()) if len(d) else None,
        "mean_occupancy": float(inst.total_demand / beds) if beds > 0 else None,
        "beds_per_1000": float(beds / pop * 1000.0) if pop > 0 else None,
    }
    return out


def _summary_tr(k: dict, costs: dict, R: float, optimal: bool = True) -> str:
    c = k["count_by_class"]
    parts = [f"{c.get('IV-C', 0)} küçük", f"{c.get('V-B', 0)} orta", f"{c.get('V-C', 0)} büyük",
             f"{c.get('V-D', 0)} şehir hastanesi"]
    within = k["share_pop_within_km"]
    head = "Optimum" if optimal else "En iyi bulunan çözüm"
    return (f"{head}: {k['n_hospitals']} hastane ({', '.join(parts)}), toplam {_fmt(costs['toplam'] / 1e9, 1)} "
            f"milyar TL; nüfusun %{_fmt(100 * (within.get('10') or 0), 1)}'i 10 km, "
            f"%{_fmt(100 * (within.get('20') or 0), 1)}'i 20 km içinde.")


# ---------------------------------------------------------------- ana fonksiyon
def solve(grid_km: float | None = None, overrides: Mapping[str, Any] | None = None,
          locks: Locks | Mapping | None = None, mode: str | None = None, time_limit: float | None = None,
          gap: float | None = None, progress_cb: Callable[[dict], None] | None = None,
          cancel_event: threading.Event | None = None, *, linking="strong", heuristic_time: float | None = None,
          polish: bool = True, compute_duals: bool = False, use_heuristic: bool = True, use_mip: bool = True,
          fallback_bound: bool = True, start: Solution | None = None, mip_options: dict | None = None,
          instance: Instance | None = None, isolate: bool = True) -> dict:
    """Optimizasyonu çalıştırır; sonuç sözlüğü (JSON'a çevrilebilir). Hata/olursuzluk durumunda da sözlük döner.

    instance verilirse (testler, oyuncak örnekler) parametre/örnek kurulumu atlanır; overrides/mode/grid_km yok sayılır.
    isolate=True (varsayılan): MIP ayrı bir süreçte çözülür; iptal (cancel_event) ve süre sınırı her an etkilidir
    (aynı süreçte HiGHS kök LP sırasında kesilemiyor; bkz. MilpModel.solve). Ek yük ≈0,4 sn (Linux), Windows'ta ≈1 sn.
    """
    T0 = time.perf_counter()
    times: dict[str, float] = {}
    trace: list[dict] = []
    stop = cancel_event or threading.Event()

    def emit(ev: dict) -> None:
        ev = dict(ev)
        ev.setdefault("t_total", round(time.perf_counter() - T0, 4))
        trace.append(ev)
        if progress_cb is not None:
            try:
                progress_cb(ev)
            except Exception:
                pass

    def fail(status: str, msg: str, **extra) -> dict:
        times["total_s"] = time.perf_counter() - T0
        out = {"status": status, "ok": False, "message_tr": msg, "times": times, "trace": trace}
        out.update(extra)
        return _jsonable(out)

    # ---- parametreler ve örnek
    try:
        t = time.perf_counter()
        if instance is None:
            ov = dict(overrides or {})
            if mode is not None:
                ov["solver.objective_mode"] = mode
            if grid_km is not None:
                ov["grid.cell_km"] = int(grid_km)
            params = Params.load(ov)
            inst = build_instance(params["grid.cell_km"], params, locks)
        else:
            inst, params = instance, instance.params
        tl = float(time_limit if time_limit is not None else params["solver.time_limit_s"])
        tgap = float(gap if gap is not None else params["solver.mip_gap"])
        times["instance_s"] = time.perf_counter() - t
    except ParamError as e:
        return fail("hata", f"Parametre hatası: {e}")
    base = {"grid_km": int(params["grid.cell_km"]), "mode": params.objective_mode, "params_hash": params.hash(),
            "structure_hash": inst.s.key, "overrides": dict(params.overrides), "locks": inst.locks.as_dict(),
            "time_limit_s": tl, "target_gap": tgap,
            "instance": {"I": inst.nI, "J": inst.nJ, "K": inst.nK, "arcs": inst.nA,
                         "structure_from_cache": inst.from_cache, "structure_build_s": inst.s.build_s,
                         "coverage_repair": inst.s.repair, "total_demand_beds": inst.total_demand,
                         "classes": params.derived_summary()["classes"],
                         "lambda_tl_per_person_km": params.lam, "pv_factor": params.pv}}
    emit({"kind": "phase", "phase": "ornek", "t": times["instance_s"]})
    try:
        inst.precheck()
    except InfeasibleError as e:
        return fail("olurlu_degil", str(e), **base)

    def mobj(sol: Solution) -> float:
        """Modelin amaç değeri (TL; butce'de ε·açılış dahil)."""
        return sol.objective_tl(inst) + (BUDGET_EPS * sol.opening_tl(inst) if inst.mode == "butce" else 0.0)

    # ---- dışarıdan başlangıç (ör. önceki çözüm) ve sezgisel
    heur = None
    best: Solution | None = None
    best_src = None
    best_solver_obj = None
    if start is not None and checks_mod.check(inst, start)["ok"]:
        best, best_src = start, "baslangic"
    if use_heuristic and not stop.is_set():
        hb = heuristic_time if heuristic_time is not None else min(max(0.15 * tl, 1.0), 10.0)
        t = time.perf_counter()
        heur = heuristic(inst, time_budget=hb, stop=stop)
        times["heuristic_s"] = time.perf_counter() - t
        emit({"kind": "heuristic", "t": times["heuristic_s"], "primal": heur.objective_tl, "ok": heur.ok})
        if heur.ok and checks_mod.check(inst, heur.solution)["ok"] and (best is None or mobj(heur.solution)
                                                                         < mobj(best)):
            best, best_src = heur.solution, "sezgisel"
            best_solver_obj = heur.eval.solver_objective_tl if heur.eval else None

    # ---- MILP
    mres = None
    model_size = None
    if use_mip and not stop.is_set():
        t = time.perf_counter()
        model, lock, how = _get_model(inst, linking)
        times["model_build_s"] = time.perf_counter() - t
        model_size = model.size()
        model_size["model"] = how
        # cila ve yedek alt sınır için pay bırak (toplam süre sınıra yakın kalsın)
        reserve = min(0.12 * tl, 3.0) if (polish or fallback_bound) else 0.0
        remaining = tl - (time.perf_counter() - T0) - reserve
        try:
            if remaining > 0.2:
                emit({"kind": "phase", "phase": "mip", "t": time.perf_counter() - T0})
                t_mip0 = time.perf_counter() - T0

                def relay(ev: dict) -> None:
                    ev = dict(ev)
                    ev["t_total"] = round(t_mip0 + ev["t"], 4)
                    emit(ev)
                mres = model.solve(time_limit=remaining, gap=tgap, start=best, progress_cb=relay,
                                   cancel_event=stop, options=mip_options, isolate=isolate)
                if mres.spawn_s is not None:
                    times["mip_spawn_s"] = mres.spawn_s
                times["mip_s"] = mres.time_s
                if mres.solution is not None:
                    obj_m = model.objective_of(mres.solution)
                    obj_b = model.objective_of(best) if best is not None else math.inf
                    if obj_m < obj_b - 1e-9 * abs(obj_b):      # MIP başlangıcı geri döndüyse kaynak değişmez
                        best, best_src, best_solver_obj = mres.solution, "mip", mres.primal_tl
        finally:
            if lock is not None:
                lock.release()

    if best is None:
        if mres is not None and mres.status == "Infeasible":
            return fail("olurlu_degil", _infeasible_msg(inst), **base, model=model_size)
        if stop.is_set():
            return fail("durduruldu", "Çözüm kullanıcı tarafından durduruldu; henüz olurlu çözüm yoktu.", **base)
        msg = "Süre içinde olurlu çözüm bulunamadı."
        if heur is not None and not heur.ok and heur.message:
            msg += f" Sezgisel: {heur.message}"
        return fail("cozum_yok", msg, **base, model=model_size)

    # ---- cila: aynı açık kümede en iyi sınıf/atama
    # (sezgisel çözüm zaten evaluate'ten geçti; yalnız MIP ya da dışarıdan gelen çözüm cilalanır)
    if polish and best_src not in ("sezgisel",):
        t = time.perf_counter()
        ev = evaluate(inst, best.open_j, gap=1e-4, time_limit=max(1.0, min(0.1 * tl, 10.0)))
        times["polish_s"] = time.perf_counter() - t
        if ev.ok and ev.objective_tl < best.objective_tl(inst) * (1 - 1e-9):
            ev.solution.source = f"{best_src}+cila"
            best, best_src, best_solver_obj = ev.solution, f"{best_src}+cila", ev.solver_objective_tl

    # ---- alt sınır
    # MIP kök LP'yi bitiremediyse dual sınırı önemsizdir (önsüzgeç sınırı): zayıf LP gevşetmesi (kapasite bağlantılı,
    # x ≤ z satırları olmadan) hızlıdır ve geçerli bir alt sınırdır; ikisinin büyüğü raporlanır.
    lb = mres.dual_tl if mres is not None else None
    lb_src = "mip" if lb is not None else None
    meaningful = mres is not None and mres.first_dual_s is not None
    if not meaningful and fallback_bound and not stop.is_set():
        t = time.perf_counter()
        weak = MilpModel(inst, linking="weak")
        r = weak.solve(time_limit=max(5.0, 0.2 * tl), relax=True)
        times["fallback_bound_s"] = time.perf_counter() - t
        if r.status == "Optimal" and r.primal_tl is not None and (lb is None or r.primal_tl > lb):
            lb, lb_src = r.primal_tl, "zayif_lp"
    # model amacı (butce'de ε dahil) ile gap
    ub_model = mobj(best)
    if lb is not None:
        lb = min(lb, ub_model)
    gap_val = (ub_model - lb) / max(abs(ub_model), 1e-9) if lb is not None else None

    # ---- denetim
    t = time.perf_counter()
    chk = checks_mod.check(inst, best, solver_objective_tl=best_solver_obj)
    times["check_s"] = time.perf_counter() - t

    # ---- sonuç
    costs = cost_breakdown(inst, best)
    k = kpis(inst, best)
    hosp = per_hospital(inst, best)
    s = inst.s
    g = None if s.source == "toy" else s.grid
    for h in hosp:
        j = h.pop("pos")
        h["cell_id"] = str(s.cand_ids[j])
        h["cand_pos"] = j
        if g is not None:
            gi = int(s.cand_cells[j])
            h["grid_index"] = gi
            h["lon"] = float(g.lon[gi])
            h["lat"] = float(g.lat[gi])
            h["district"] = g.district_names[int(g.district[gi])] if int(g.district[gi]) >= 0 else None
        h["locked_open"] = bool(inst.lock_open[j])
    a = np.flatnonzero(best.x > 0)
    hpos = np.full(inst.nJ, -1, dtype=np.int64)
    hpos[best.open_j] = np.arange(best.n_open)
    assignment = {"demand_cell": [str(c) for c in s.demand_ids[s.arc_i[a]]],
                  "hospital": hpos[s.arc_j[a]].tolist(), "share": best.x[a].tolist(), "km": s.arc_d[a].tolist()}
    if stop.is_set():
        status, status_tr = "durduruldu", "Kullanıcı durdurdu; en iyi bulunan çözüm gösteriliyor."
    elif gap_val is not None and gap_val <= tgap + 1e-9:
        status, status_tr = "optimal", f"Optimal: alt sınırla fark hedef gap'in (%{_fmt(100 * tgap, 2)}) içinde."
    elif mres is None:
        status, status_tr = "sezgisel", "Yalnız sezgisel çözüm (MILP çalıştırılmadı)."
    else:
        status, status_tr = "sure_doldu", "Süre doldu; en iyi bulunan çözüm ve alt sınır gösteriliyor."
    times["total_s"] = time.perf_counter() - T0
    result = dict(base)
    result.update({
        "status": status, "ok": True, "status_tr": status_tr,
        "message_tr": _summary_tr(k, costs, s.R_km, status == "optimal"),
        "solver_status": mres.status if mres is not None else None,
        "solution_source": best_src,
        "bounds": {"ub_tl": ub_model, "lb_tl": lb, "gap": gap_val, "lb_source": lb_src,
                   "objective_tl": float(best.objective_tl(inst)),
                   "mip_primal_tl": mres.primal_tl if mres is not None else None,
                   "mip_dual_tl": mres.dual_tl if mres is not None else None,
                   "mip_gap": mres.gap if mres is not None else None,
                   "mip_nodes": mres.nodes if mres is not None else None},
        "times": times,
        "first_incumbent_s": (mres.first_incumbent_s if mres is not None else None),
        "first_dual_s": (mres.first_dual_s if mres is not None else None),
        "start_accepted": mres.start_accepted if mres is not None else None,
        "model": model_size,
        "heuristic": None if heur is None else {"ok": heur.ok, "objective_tl": heur.objective_tl,
                                                "approx_objective_tl": heur.approx_objective_tl,
                                                "time_s": heur.time_s, "construct_s": heur.construct_s,
                                                "local_s": heur.local_s, "eval_s": heur.eval_s,
                                                "moves": heur.moves, "message": heur.message},
        "hospitals": hosp,
        "assignment": assignment,
        "costs": costs,
        "kpis": k,
        "check": chk,
        "trace": trace,
        "warnings": list(inst.warnings),
    })
    if compute_duals:
        t = time.perf_counter()
        d = assignment_duals(inst, best)
        times["duals_s"] = time.perf_counter() - t
        result["duals"] = {"demand_cell": [str(c) for c in s.demand_ids],
                           "marginal_cost_tl": d.get("marginal_cost_tl"),
                           "marginal_cost_per_person_tl": d.get("marginal_cost_per_person_tl"),
                           "bed_value_tl": d.get("bed_value_tl"), "note_tr": d.get("degenerate_note")}
    return _jsonable(result)


def _infeasible_msg(inst: Instance) -> str:
    hints = []
    if inst.count_max is not None:
        hints.append(f"en çok hastane sayısını ({inst.count_max}) artırın")
    if inst.count_min:
        hints.append(f"en az hastane sayısını ({inst.count_min}) azaltın")
    if inst.budget is not None:
        hints.append("bütçeyi artırın")
    if inst.lock_open.any() or inst.lock_closed.any():
        hints.append("kilitleri gözden geçirin")
    if not inst.class_on[-1]:
        hints.append("şehir hastanesi sınıfını açın")
    hints.append("azami erişim mesafesini (R) artırın")
    return "Bu ayarlarla olurlu çözüm yok (kapasite, sınıf, R, kilit, sayı ya da bütçe kısıtları birlikte sağlanamıyor). " \
           "Öneri: " + "; ".join(hints) + "."
