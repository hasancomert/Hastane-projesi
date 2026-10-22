"""Tek giriş noktası: solve(...) → JSON'a çevrilebilir sonuç sözlüğü (şartname 4.6).

Akış: parametreler (+ kullanıcı değişiklikleri) → örnek (yapı önbellekli) → ön olurluluk denetimi → sezgisel (açgözlü +
yerel arama + kesin değerlendirme) → Lagrange gevşetmesi A evresi (hızlı geçerli alt sınır + hafif Lagrange sezgiseli;
hedef gap'in yarısına ulaşılırsa biter) → çekirdek MIP (Lagrange'ın seçtiği adaylar ve yakın yaylarla küçük MILP, AYRI
SÜREÇTE) ve aynı anda ana süreçte Lagrange B evresi (alt sınır yükselir; çekirdeğin çözümleri üst sınır olarak beslenir)
→ en iyi çözüm açık kümesinde sınıf/atama cilası (evaluate) → gerekirse Lagrange 2. evre → [uzun süre sınırında ya da
bütçe modunda tam MILP] → bağımsız denetim (checks) → KPI'lar ve maliyet kırılımı.

Faz 3 ölçümü (4 çekirdekli geliştirme konteyneri, varsayılan parametreler): güçlü bağlantılı tam modelin kök LP'si 5 km'de
≈7–13 sn, 3 km'de ≈126–160 sn sürüyor, 2 km'de 60 sn'de bitmiyor; Lagrange sınırı 3 km'de aynı değere ≈3–5 sn'de ulaşıyor.
Bu yüzden canlı çözümde alt sınırın (optimallik sertifikasının) ana kaynağı Lagrange gevşetmesidir (lagrange.py).
Bütçe modunda (butce) Lagrange sınırı yok: eski yol (sezgisel → tam MILP → gerekirse zayıf LP sınırı).

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
from . import lagrange as lagrange_mod
from .evaluate import assignment_duals, cost_breakdown, evaluate, per_hospital
from .heuristics import heuristic
from .instance import InfeasibleError, Instance, Locks, Solution, build_instance
from .milp import BUDGET_EPS, SCALE, MilpModel
from .params import ParamError, Params

KPI_RADII_KM = (10, 20, 30)          # "nüfusun %Y'si Z km içinde" (raporlama eşikleri; model parametresi değil)
# Çözüm stratejisi sabitleri (model parametresi değil; Faz 3 ayar deneyleri ve ölçümleri, docs/FAZ3_RAPOR.md):
FULL_MIP_MIN_TIME = 300.0            # sn; süre sınırı bu kadarsa (önceden çözülen senaryolar) tam MILP de çalışır. Tam modelin
                                     # kök LP'si 5 km ≈7–13 sn, 3 km ≈126–160 sn, 2 km > 60 sn (canlı çözümde kullanılamaz)
CORE_N = 150                         # çekirdek MIP: G_j en küçük aday sayısı (+ incumbent'in açık hücreleri)
CORE_M = 8                           # çekirdek MIP: talep hücresi başına tutulan en yakın çekirdek adayı
CORE_GAP = 1e-3                      # çekirdek MIP'in kendi gap'i
CORE_MIN_TIME = 2.0                  # çekirdek MIP için gereken en az süre (sn)
PHASE1_SHARE = 0.25                  # canlı çözümde Lagrange 1. (A) evrenin süre payı; kalan süre çekirdek MIP + B evresi
PHASE1_STALL_TOL = 1e-4              # 1. evre: L son pencerede bundan az (göreli) arttıysa dur
CORE_FALLBACK_SHARE = 0.3            # çekirdek MIP pencerenin bu payında iyileştiremezse B evresinde Lagrange sezgiseli açılır
                                     # (ölçüm: 3 km'de 5 çalıştırmanın 1'inde, 2 km ılık çözümde 5'in 2'sinde çekirdek hiç
                                     # iyileştiremedi ve gap %1,3–2,4'te kaldı)
# çekirdek MIP planlıyken A evresinde Lagrange sezgiseli hafif (yalnız sabit sınıflı LP + onarım): ölçüm (3 km, 15 sn,
# 3'er çalıştırma): tam sezgiselle A evresi yalnız ≈155 yineleme yapıyor, son gap %0,33–0,90; hafif sezgiselle ≈210
# yineleme, gap %0,22–0,36 (üst sınırı zaten çekirdek MIP veriyor)
# (sayı sınırı ya da sabit p varken tam sezgisel kalır: kapsama onarımı sayıyı bozduğundan hafif sezgisel p'yi
# sağlayan çözüm üretemiyor ve çekirdek MIP kötü bir başlangıçla kalıyor — 3 km p taramasında p = 70'te gap %1,27)
PHASE1_LH_CORE: dict = {"class_mip_share": 0.0, "local_search": False}
LAGRANGE_MAX_BEFORE_FULL_MIP = 30.0  # tam MILP planlanmışsa Lagrange 1. evre en çok bu kadar (sn; sınır ≈10–20 sn'de durulur)
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
            f"milyar TL; hastaneye 10 km içinde oturan nüfusun payı %{_fmt(100 * (within.get('10') or 0), 1)}, "
            f"20 km içinde %{_fmt(100 * (within.get('20') or 0), 1)}.")


# ---------------------------------------------------------------- ana fonksiyon
def solve(grid_km: float | None = None, overrides: Mapping[str, Any] | None = None,
          locks: Locks | Mapping | None = None, mode: str | None = None, time_limit: float | None = None,
          gap: float | None = None, progress_cb: Callable[[dict], None] | None = None,
          cancel_event: threading.Event | None = None, *, linking="strong", heuristic_time: float | None = None,
          polish: bool = True, compute_duals: bool = False, use_heuristic: bool = True, use_mip: bool = True,
          fallback_bound: bool = True, start: Solution | None = None, mip_options: dict | None = None,
          instance: Instance | None = None, isolate: bool = True, use_lagrange: bool = True, mip: str = "auto",
          lagrange_mu0: np.ndarray | None = None, polish_time: float | None = None) -> dict:
    """Optimizasyonu çalıştırır; sonuç sözlüğü (JSON'a çevrilebilir). Hata/olursuzluk durumunda da sözlük döner.

    instance verilirse (testler, oyuncak örnekler) parametre/örnek kurulumu atlanır; overrides/mode/grid_km yok sayılır.
    isolate=True (varsayılan): MIP ayrı bir süreçte çözülür; iptal (cancel_event) ve süre sınırı her an etkilidir
    (aynı süreçte HiGHS kök LP sırasında kesilemiyor; bkz. MilpModel.solve). Ek yük ≈0,4 sn (Linux), Windows'ta ≈1 sn.
    use_lagrange: Lagrange alt sınırı ve sezgiseli (butce modunda kendiliğinden kapalı).
    mip: 'auto' → canlı çözümde 'core' (Lagrange A → çekirdek MIP ∥ Lagrange B → cila → gerekirse Lagrange 2. evre),
    süre sınırı ≥ FULL_MIP_MIN_TIME ya da bütçe modunda 'full' (Lagrange → çekirdek MIP → tam MILP); 'none' MILP'siz
    (yalnız sezgisel + Lagrange). use_mip=False ≡ 'none'.
    lagrange_mu0: Lagrange başlangıç çarpanları (TL / talep hücresi; ör. önceki sonucun çarpanları).
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
        # kesin değerlendirmede gap 1e-2: sezgisel yalnız ilk üst sınırdır (2 km'de 5 → 2,7 sn); en iyi çözüm sonda cilalanır
        heur = heuristic(inst, time_budget=hb, stop=stop, eval_gap=1e-2, eval_time=min(5.0, max(1.0, 0.05 * tl)))
        times["heuristic_s"] = time.perf_counter() - t
        emit({"kind": "heuristic", "t": times["heuristic_s"], "primal": heur.objective_tl, "ok": heur.ok})
        if heur.ok and checks_mod.check(inst, heur.solution)["ok"] and (best is None or mobj(heur.solution)
                                                                         < mobj(best)):
            best, best_src = heur.solution, "sezgisel"
            best_solver_obj = heur.eval.solver_objective_tl if heur.eval else None

    def remaining() -> float:
        return tl - (time.perf_counter() - T0)

    def adopt(sol: Solution | None, src: str, solver_obj: float | None = None) -> bool:
        """Denetimden geçen ve daha iyi olan çözümü en iyi çözüm yapar."""
        nonlocal best, best_src, best_solver_obj
        if sol is None or not checks_mod.check(inst, sol)["ok"]:
            return False
        if best is not None and not mobj(sol) < mobj(best) * (1 - 1e-12):
            return False
        best, best_src, best_solver_obj = sol, src, solver_obj
        return True

    def gap_now(lbv: float | None) -> float | None:
        if lbv is None or best is None:
            return None
        u = mobj(best)
        return (u - lbv) / max(abs(u), 1e-9)

    reserve = min(0.12 * tl, 3.0) if polish else 0.0
    mip_kind = "none" if not use_mip else mip
    if mip_kind not in ("auto", "full", "core", "none"):
        return fail("hata", f"Geçersiz MIP stratejisi: {mip!r}", **base)
    lag_on = use_lagrange and inst.mode != "butce"
    if mip_kind == "auto":
        mip_kind = "full" if (not lag_on or tl >= FULL_MIP_MIN_TIME) else "core"
    if mip_kind == "core" and not lag_on:
        mip_kind = "full"
    use_core = lag_on and (mip_kind == "core" or (mip_kind == "full" and tl >= FULL_MIP_MIN_TIME))
    tgap_core = 0.5 * tgap               # çekirdek MIP'in hedefi: hedef gap'in yarısı (daha iyi çözüm, aynı süre bütçesi)
    constrained = inst.count_min > 0 or inst.count_max is not None    # sayı sınırı ya da sabit p

    # ---- Lagrange gevşetmesi (A evresi)
    lag = None
    lag_lb = None
    lag_info: dict = {}

    def lag_relay(t_off: float, phase: int):
        def relay(ev: dict) -> None:
            e = dict(ev)
            e["t_total"] = round(t_off + ev["t"], 4)
            e["phase"] = phase
            e["primal"], e["dual"] = ev.get("ub"), ev.get("lb")
            emit(e)
        return relay

    if lag_on and not stop.is_set():
        cap = remaining() - reserve
        stall = 0.0
        if mip_kind == "core":
            cap, stall = min(cap, PHASE1_SHARE * tl), PHASE1_STALL_TOL
        elif mip_kind == "full":
            cap = min(cap, max(0.4 * cap, 3.0), LAGRANGE_MAX_BEFORE_FULL_MIP)
            stall = PHASE1_STALL_TOL if use_core else 0.0
        t_l0 = time.perf_counter() - T0
        emit({"kind": "phase", "phase": "lagrange", "t": t_l0})
        lag = lagrange_mod.run(inst, start=best, time_limit=max(cap, 0.5),
                               target_gap=tgap_core if use_core else tgap, stop=stop,
                               progress_cb=lag_relay(t_l0, 1), mu0_tl=lagrange_mu0, stall_tol=stall,
                               **(PHASE1_LH_CORE if use_core and not constrained else {}))
        times["lagrange_s"] = time.perf_counter() - T0 - t_l0
        if lag.ok:
            lag_lb = lag.bound_tl
            if lag.solution is not None and lag.solution is not best:
                adopt(lag.solution, lag.source or "lagrange")
            lag_info = {"bound_tl": lag.bound_tl, "ub_tl": lag.ub_tl, "gap": lag.gap, "iters": lag.iters,
                        "time_s": lag.time_s, "init_s": lag.init_s, "init_source": lag.init_source,
                        "lh_calls": lag.lh_calls, "lh_time_s": lag.lh_time_s, "sub_ms": lag.sub_ms,
                        "source": lag.source}

    def certified(g: float) -> bool:
        lbs = [v for v in (lag_lb, core_lb) if v is not None]
        v = gap_now(max(lbs)) if lbs else None
        return v is not None and v <= g + 1e-9

    # ---- cila: aynı açık kümede en iyi sınıf/atama (sınıf seçimli küçük MIP; evaluate)
    # (her açık küme bir kez; çekirdek MIP'in çözümü burada tam yay kümesinde yeniden atanır)
    polished: set = set()

    def do_polish() -> None:
        if not polish or best is None or stop.is_set():
            return
        key = hash(best.open_j.tobytes())
        if key in polished:
            return
        polished.add(key)
        pt = polish_time if polish_time is not None else max(1.0, min(0.1 * tl, 10.0, remaining()))
        t = time.perf_counter()
        ev = evaluate(inst, best.open_j, gap=1e-3 if tl < FULL_MIP_MIN_TIME else 1e-4, time_limit=pt)
        times["polish_s"] = times.get("polish_s", 0.0) + time.perf_counter() - t
        if ev.ok and ev.objective_tl < best.objective_tl(inst) * (1 - 1e-9):
            src0 = best_src
            ev.solution.source = f"{src0}+cila"
            if adopt(ev.solution, f"{src0}+cila", ev.solver_objective_tl):
                emit({"kind": "polish", "primal": mobj(best)})

    # ---- çekirdek MIP (Lagrange yönlendirmeli küçük MILP; üst sınırı iyileştirir, alt sınırı küresel değildir).
    # Ayrı süreçte çalışırken ana süreçte Lagrange yinelemeleri (B evresi) sürer: alt sınır yükselir, çekirdeğin
    # bulduğu çözümler Lagrange'ın adım boyuna ve durma ölçütüne üst sınır olarak beslenir (iki çekirdek kullanılır).
    core_info: dict | None = None
    core_lb = None
    core_infeasible = False
    if use_core and lag is not None and lag.ok and not stop.is_set() and not certified(tgap_core):
        window = remaining() - reserve
        if mip_kind == "full":
            window = min(window, max(0.2 * tl, 30.0))
        if window >= CORE_MIN_TIME:
            t = time.perf_counter()
            core, sel = lagrange_mod.core_instance(inst, lag, best, n_keep=CORE_N, m=CORE_M)
            cm = MilpModel(core, linking=linking)
            build_s = time.perf_counter() - t
            emit({"kind": "phase", "phase": "cekirdek_mip", "t": time.perf_counter() - T0})
            t_c0 = time.perf_counter() - T0

            def relay_core(ev: dict) -> None:
                ev = dict(ev)
                ev["t_total"] = round(t_c0 + ev["t"], 4)
                ev["core_dual"], ev["dual"] = ev.get("dual"), None       # çekirdeğin alt sınırı küresel değil
                ev["core"] = True
                emit(ev)
            handle = None
            try:
                handle = cm.start_isolated(time_limit=max(window - build_s, 0.5), gap=CORE_GAP,
                                           start=lagrange_mod.solution_to_core(best, sel) if best is not None else None,
                                           progress_cb=relay_core,
                                           options={"objective_target": lag_lb / (1.0 - tgap_core) / SCALE})

                def feed() -> float | None:
                    handle.poll()
                    return handle.st["primal"]

                start_obj = mobj(best) if best is not None else math.inf
                t_core0 = time.perf_counter()

                def lh_needed() -> bool:
                    # Lagrange sezgiseli: sayı sınırı/sabit p varken her zaman; yoksa çekirdek MIP pencerenin
                    # CORE_FALLBACK_SHARE'i içinde başlangıç çözümünü iyileştiremediyse (çekirdek bazen hiç çözüm bulamıyor)
                    if constrained:
                        return True
                    u = handle.st["primal"]
                    improved_ = u is not None and u < start_obj * (1 - 1e-9)
                    return not improved_ and time.perf_counter() - t_core0 > CORE_FALLBACK_SHARE * window

                def extra_stop(L_tl: float, U_tl: float) -> bool:
                    # çekirdek bittiyse hedef gap'te dur (yarı hedef yalnız çekirdek sürerken aranır)
                    return handle.done and (U_tl - L_tl) / U_tl <= tgap
                # B evresi: sıcak çarpanlar; sezgisel kapalı (üst sınır çekirdekten) — sayı sınırı/sabit p varken ya da
                # çekirdek iyileştiremiyorsa açık (lh_needed)
                t_l0 = time.perf_counter() - T0
                lagB = lagrange_mod.run(inst, start=best, time_limit=max(window - build_s, 0.5),
                                        target_gap=tgap_core, stop=stop, progress_cb=lag_relay(t_l0, 2),
                                        mu0_tl=lag.mu, theta0=0.1, heuristics_on=lh_needed, ub_feed=feed,
                                        extra_stop=extra_stop)
                times["lagrange_b_s"] = time.perf_counter() - T0 - t_l0
                if lagB.ok and lagB.solution is not None and lagB.solution is not best:
                    adopt(lagB.solution, lagB.source or "lagrange")          # yedek sezgiselin çözümü
                if lagB.ok and lagB.bound_tl > lag_lb:
                    lag_lb = lagB.bound_tl
                    lag_info["bound_tl"] = lag_lb
                lag_info["phase_b"] = {"bound_tl": lagB.bound_tl, "iters": lagB.iters, "time_s": lagB.time_s}
                # çekirdek hâlâ çalışıyorsa: sertifika varsa ya da süre bittiyse durdur, yoksa süre sonuna dek bekle
                while not handle.done:
                    handle.poll(timeout=0.05)
                    u = handle.st["primal"]
                    ub_now = min(mobj(best) if best is not None else math.inf, u if u is not None else math.inf)
                    if (stop.is_set() or time.perf_counter() > handle.deadline - 3.0 or remaining() < reserve
                            or (math.isfinite(ub_now) and (ub_now - lag_lb) / ub_now <= tgap_core)):
                        handle.terminate("Interrupted by user" if stop.is_set() else "Time limit reached")
                cres = handle.result()
            finally:
                if handle is not None and not handle.done:
                    handle.terminate()
                    handle.result()
            times["core_mip_s"] = time.perf_counter() - t
            improved = False
            if cres.solution is not None:
                full_sol = lagrange_mod.solution_from_core(cres.solution, sel, inst.nA)
                improved = adopt(full_sol, "cekirdek_mip", None)
            # çekirdek tam modelin kendisiyse (küçük örnekler: tüm adaylar ve yaylar) alt sınırı da küreseldir
            core_is_full = len(sel) == inst.nA and np.array_equal(core.lock_closed, inst.lock_closed)
            if core_is_full and cres.dual_tl is not None:
                core_lb = cres.dual_tl
            if core_is_full and cres.status == "Infeasible":
                core_infeasible = True
            core_info = {"candidates": int((~core.lock_closed).sum()), "arcs": int(len(sel)), "status": cres.status,
                         "primal_tl": cres.primal_tl, "core_dual_tl": cres.dual_tl, "time_s": cres.time_s,
                         "first_incumbent_s": cres.first_incumbent_s, "build_s": build_s, "improved": improved,
                         "is_full_model": bool(core_is_full), "model": cm.size()}
            do_polish()

    # ---- tam MILP (bütçe modu, Lagrange kapalı, uzun süre sınırı ya da hiç olurlu çözüm bulunamadıysa: tam model
    # olursuzluğu da kanıtlayabilir)
    mres = None
    model_size = None
    if mip_kind == "core" and best is None and not core_infeasible:
        mip_kind = "full"
    if mip_kind == "full" and not stop.is_set() and not certified(tgap) and not core_infeasible:
        t = time.perf_counter()
        model, lock, how = _get_model(inst, linking)
        times["model_build_s"] = time.perf_counter() - t
        model_size = model.size()
        model_size["model"] = how
        budget_mip = remaining() - reserve
        opts = dict(mip_options or {})
        if lag_lb is not None and "objective_target" not in opts:
            opts["objective_target"] = lag_lb / (1.0 - tgap) / SCALE   # Lagrange sınırına göre hedef gap'te dur
        try:
            if budget_mip > 0.2:
                emit({"kind": "phase", "phase": "mip", "t": time.perf_counter() - T0})
                t_mip0 = time.perf_counter() - T0

                def relay(ev: dict) -> None:
                    ev = dict(ev)
                    ev["t_total"] = round(t_mip0 + ev["t"], 4)
                    emit(ev)
                mres = model.solve(time_limit=budget_mip, gap=tgap, start=best, progress_cb=relay,
                                   cancel_event=stop, options=opts, isolate=isolate)
                if mres.spawn_s is not None:
                    times["mip_spawn_s"] = mres.spawn_s
                times["mip_s"] = mres.time_s
                if mres.solution is not None:
                    obj_m = model.objective_of(mres.solution)
                    obj_b = model.objective_of(best) if best is not None else math.inf
                    if obj_m < obj_b - 1e-9 * abs(obj_b):      # MIP başlangıcı geri döndüyse kaynak değişmez
                        adopt(mres.solution, "mip", mres.primal_tl)
        finally:
            if lock is not None:
                lock.release()

    # ---- Lagrange 2. evre (çekirdek MIP'ten sonra hâlâ hedef gap'te değilse; sıcak çarpanlar, güncel üst sınır)
    if (lag is not None and lag.ok and mip_kind == "core" and not stop.is_set() and not certified(tgap)
            and remaining() - reserve > 0.5):
        t_l0 = time.perf_counter() - T0
        emit({"kind": "phase", "phase": "lagrange_2", "t": t_l0})
        lag2 = lagrange_mod.run(inst, start=best, time_limit=remaining() - reserve, target_gap=tgap, stop=stop,
                                progress_cb=lag_relay(t_l0, 2), mu0_tl=lag.mu, theta0=0.1)
        times["lagrange2_s"] = time.perf_counter() - T0 - t_l0
        if lag2.ok:
            if lag2.solution is not None and lag2.solution is not best:
                adopt(lag2.solution, lag2.source or "lagrange")
            if lag2.bound_tl > lag_lb:
                lag_lb = lag2.bound_tl
            lag_info["phase2"] = {"bound_tl": lag2.bound_tl, "iters": lag2.iters, "time_s": lag2.time_s,
                                  "lh_calls": lag2.lh_calls, "source": lag2.source}
            lag_info["bound_tl"] = lag_lb

    if best is None:
        if core_infeasible or (mres is not None and mres.status == "Infeasible"):
            return fail("olurlu_degil", _infeasible_msg(inst), **base, model=model_size)
        if stop.is_set():
            return fail("durduruldu", "Çözüm kullanıcı tarafından durduruldu; henüz olurlu çözüm yoktu.", **base)
        msg = "Süre içinde olurlu çözüm bulunamadı."
        if heur is not None and not heur.ok and heur.message:
            msg += f" Sezgisel: {heur.message}"
        return fail("cozum_yok", msg, **base, model=model_size)

    do_polish()

    # ---- alt sınır: Lagrange, tam MILP'in dual sınırı (çekirdek MIP'inki küresel değil), gerekirse zayıf LP
    lb, lb_src = None, None
    if lag_lb is not None:
        lb, lb_src = lag_lb, "lagrange"
    if core_lb is not None and (lb is None or core_lb > lb):
        lb, lb_src = core_lb, "mip"
    meaningful = mres is not None and mres.first_dual_s is not None
    if mres is not None and mres.dual_tl is not None and (lb is None or mres.dual_tl > lb):
        lb, lb_src = mres.dual_tl, "mip"
    if lag_lb is None and not meaningful and fallback_bound and not stop.is_set():
        # MIP kök LP'yi bitiremediyse dual sınırı önemsizdir (önsüzgeç sınırı): zayıf LP gevşetmesi (kapasite bağlantılı,
        # x ≤ z satırları olmadan) hızlıdır ve geçerli bir alt sınırdır; ikisinin büyüğü raporlanır.
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
    hpos = np.full(inst.nJ, -1, dtype=np.int64)
    hpos[best.open_j] = np.arange(best.n_open)
    a = np.flatnonzero((best.x > 0) & (hpos[s.arc_j] >= 0))
    assignment = {"demand_cell": [str(c) for c in s.demand_ids[s.arc_i[a]]],
                  "hospital": hpos[s.arc_j[a]].tolist(), "share": best.x[a].tolist(), "km": s.arc_d[a].tolist()}
    if stop.is_set():
        status, status_tr = "durduruldu", "Kullanıcı durdurdu; en iyi bulunan çözüm gösteriliyor."
    elif gap_val is not None and gap_val <= tgap + 1e-9:
        status, status_tr = "optimal", f"Optimal: alt sınırla fark hedef gap'in (%{_fmt(100 * tgap, 2)}) içinde."
    elif mres is None and lag is None:
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
        "certificate_tr": _certificate_tr(ub_model, lb, gap_val, lb_src, inst),
        "bounds": {"ub_tl": ub_model, "lb_tl": lb, "gap": gap_val, "lb_source": lb_src,
                   "lb_scope": "k_listesi" if s.k_nearest > 0 else "tam",
                   "objective_tl": float(best.objective_tl(inst)),
                   "lagrange_tl": lag_lb,
                   "mip_kind": mip_kind,
                   "mip_primal_tl": mres.primal_tl if mres is not None else None,
                   "mip_dual_tl": mres.dual_tl if mres is not None else None,
                   "mip_gap": mres.gap if mres is not None else None,
                   "mip_nodes": mres.nodes if mres is not None else None},
        "lagrange": lag_info or None,
        "core_mip": core_info,
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


_LB_SOURCE_TR = {"lagrange": "Lagrange gevşetmesi", "mip": "dal-sınır (HiGHS)", "zayif_lp": "zayıf LP gevşetmesi"}


def _certificate_tr(ub: float, lb: float | None, gap: float | None, src: str | None, inst: Instance) -> str:
    """Şartname 7.2 optimallik sertifikası cümlesi."""
    if lb is None or gap is None:
        return f"Bulunan çözüm {_fmt(ub / 1e9, 2)} milyar TL; alt sınır hesaplanamadı (optimallik kanıtlanmadı)."
    scope = (f" (k = {inst.s.k_nearest} en yakın aday listesiyle kurulan model için)" if inst.s.k_nearest > 0 else "")
    return (f"Alt sınır L = {_fmt(lb / 1e9, 2)} milyar TL ({_LB_SOURCE_TR.get(src, src)}), bulunan çözüm "
            f"U = {_fmt(ub / 1e9, 2)} milyar TL → fark %{_fmt(100 * gap, 2)}. Bu modelde{scope} daha iyi bir çözüm "
            f"varsa bile bu çözümden en fazla %{_fmt(100 * gap, 2)} daha ucuzdur.")


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
