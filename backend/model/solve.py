"""Tek giriş noktası: solve(...) → JSON'a çevrilebilir sonuç sözlüğü (şartname 4.6).

Akış: parametreler (+ kullanıcı değişiklikleri) → örnek (yapı önbellekli) → ön olurluluk denetimi → başlangıçlar (verilen
start; aynı yapıdaki önceki çözümlerin açık kümeleri bu parametrelerle yeniden değerlendirilir) → sezgisel (açgözlü +
yerel arama + kesin değerlendirme; bütçe modunda önce kapsama, sonra kalan bütçe) → Lagrange A evresi (hızlı geçerli alt
sınır; sınır durulunca biter; bütçe modunda bütçe satırının ν ile gevşetilmesi, budget.py) → İYİLEŞTİRME evresi:
  * çekirdek problem (Lagrange'ın seçtiği adaylar + yakın yaylar) ve LP gevşetmesinin yuvarlanması (lns.lp_round);
  * çekirdek MIP AYRI SÜREÇTE; ilerlemesi ölçülür, durursa (kök LP'den sonra CORE_STALL_S boyunca iyileşme yok) ana
    süreçteki daha iyi çözümden farklı aday/yay kümesiyle yeniden başlatılır (2. deneme);
  * Lagrange B evresi arka plan iş parçacığında (sınır durulana dek; çekirdeğin ve pencerelerin çözümleri üst sınır
    olarak beslenir; bütçe modunda ν aramasının A evresinin bıraktığı yerden sürdürülmesi, budget.py), aynı anda ana
    iş parçacığında pencere araması (lns.WindowSearch: yarıçap pencerelerinde kesin küçük MILP'ler); progress_cb iki
    iş parçacığından çağrılabilir (olaylar kilitle sıralanır);
→ cila (en iyi açık kümede sınıf/atama MIP'i) → [uzun süre sınırında tam MILP] → gerekirse Lagrange 2. evre →
bağımsız denetim (checks) → KPI'lar ve maliyet kırılımı.

Süre: toplam duvar süresi time_limit'i en çok ≈0,5 sn aşar (sert sınır): her evre T0 + time_limit − (cila + sonuç payı)
anına göre sınırlanır, alt süreç kibarca durdurulur (son çözümü alınır) ve gerekirse öldürülür.

Faz 3 ölçümü (4 çekirdekli geliştirme konteyneri, varsayılan parametreler): güçlü bağlantılı tam modelin kök LP'si 5 km'de
≈7–13 sn, 3 km'de ≈126–160 sn sürüyor, 2 km'de 60 sn'de bitmiyor; Lagrange sınırı 3 km'de aynı değere ≈3–5 sn'de ulaşıyor.
Bu yüzden canlı çözümde alt sınırın (optimallik sertifikasının) ana kaynağı Lagrange gevşetmesidir (lagrange.py, budget.py).

Durum kodları (status): optimal (hedef gap'e ulaşıldı) | sure_doldu (süre doldu, en iyi çözüm) | gap_ulasilamadi (süre
dolmadan durdu: alt/üst sınır yöntemleri yakınsadı, hedef gap bu yöntemlerle sayısal olarak erişilemiyor) | durduruldu
(kullanıcı iptal etti) | sezgisel (MIP/Lagrange çalışmadı; sezgisel çözüm) | olurlu_degil | cozum_yok | hata.
Bütçe modunda raporlanan amaç, üst ve alt sınır yalnız ERİŞİM maliyetidir (ε·açılış eşitlik bozucusu çıkarılır: MIP
kaynaklı sınırlardan ε·bütçe düşülür, böylece alt sınır ≤ objective_tl her zaman geçerlidir).
Para birimleri TL, mesafeler yol km.
"""
from __future__ import annotations

import math
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Mapping

import numpy as np

from . import budget as budget_mod
from . import checks as checks_mod
from . import lagrange as lagrange_mod
from . import layers as layers_mod
from . import lns
from .evaluate import assignment_duals, cost_breakdown, evaluate, per_hospital
from .heuristics import heuristic
from .instance import InfeasibleError, Instance, Locks, Solution, build_instance
from .milp import BUDGET_EPS, SCALE, STOP_GRACE_S, MilpModel
from .params import ParamError, Params

KPI_RADII_KM = (10, 20, 30)          # "nüfusun %Y'si Z km içinde" (raporlama eşikleri; model parametresi değil)
# Çözüm stratejisi sabitleri (model parametresi değil; Faz 3 ayar deneyleri ve ölçümleri, docs/FAZ3_RAPOR.md):
FULL_MIP_MIN_TIME = 300.0            # sn; süre sınırı bu kadarsa (önceden çözülen senaryolar) tam MILP de çalışır. Tam modelin
                                     # kök LP'si 5 km ≈7–13 sn, 3 km ≈126–160 sn, 2 km > 60 sn (canlı çözümde kullanılamaz)
CORE_N = 150                         # çekirdek MIP: G_j en küçük aday sayısı (+ incumbent'in açık hücreleri)
CORE_M = 8                           # çekirdek MIP: talep hücresi başına tutulan en yakın çekirdek adayı
CORE_RETRY = ((CORE_N + 50, 6), (CORE_N + 100, 6))   # 2./3. deneme: daha geniş aday kümesi, daha az yay (kök LP hızlı)
CORE_GAP = 1e-3                      # çekirdek MIP'in kendi gap'i — en çok; hedef gap'in yarısından büyük olamaz
CORE_MIN_TIME = 2.0                  # çekirdek MIP için gereken en az süre (sn)
CORE_STALL_S = 4.0                   # çekirdek MIP kök LP'sinden sonra en az bu kadar (ve penceresinin %25'i) iyileştiremezse
                                     # ve ana süreçte daha iyi çözüm varsa yeniden başlatılır (Faz 3 doğrulaması D1/D2:
                                     # başarısız 2 km / yüklü 3 km çalıştırmalarında çekirdek başlangıcını hiç iyileştiremedi;
                                     # ölçüm: kök düğümde kesme turlarında kalıyor, 37 sn'de 0 düğüm). Bu sürümün kıyaslaması
                                     # (data/processed/solver_benchmark.json, 10'ar soğuk çalıştırma): LP yuvarlamasından
                                     # sonra çekirdek MIP 3 km'de 10, 2 km'de 30 denemenin hiçbirinde incumbent'ı
                                     # iyileştiremedi; iyileşmeyi pencere araması ve cila verdi. Bütçe modunda (3 km) ise
                                     # son çözüm 3 soğuk çalıştırmanın 2'sinde çekirdek MIP'ten, 1'inde LP yuvarlamasından
                                     # geldi (aynı dosyanın 'butce' anahtarı, solution_source / core_attempts).
PHASE1_SHARE = 0.2                   # Lagrange A evresinin en çok süre payı (sınır durulursa daha erken biter; sınır
                                     # B evresinde arka planda yükselmeye devam eder)
PHASE1_STALL_TOL = 1e-4              # A / B evresi: L son max(2 sn, geçen sürenin %30'u) içinde bundan az (göreli) arttıysa dur
PHASE1_LH_CORE: dict = {"class_mip_share": 0.0, "local_search": False}
# çekirdek MIP planlıyken A evresinde Lagrange sezgiseli hafif (yalnız sabit sınıflı LP + onarım): ölçüm (3 km, 15 sn,
# 3'er çalıştırma): tam sezgiselle A evresi yalnız ≈155 yineleme yapıyor, son gap %0,33–0,90; hafif sezgiselle ≈210
# yineleme, gap %0,22–0,36. Sayı sınırı ya da sabit p varken tam sezgisel kalır (kapsama onarımı sayıyı bozar).
LAGRANGE_MAX_BEFORE_FULL_MIP = 30.0  # tam MILP planlanmışsa Lagrange 1. evre en çok bu kadar (sn)
BUDGET_LAG_SHARE = 0.35              # bütçe modu: ν aramasının A evresi en çok bu pay; arama iyileştirme evresinde
                                     # arka planda sürer (B evresi, budget.py)
FIN_RESERVE_S = 0.25                 # denetim + KPI + sonuç sözlüğü için ayrılan süre (sn)
EARLY_STOP_MARGIN = (0.5, 0.05)      # bu kadar (sn, süre payı) önce biten çözüm "süre doldu" değil, "erken durdu" sayılır
LB_TOL = 1e-9                        # alt sınır > üst sınır farkı bu kadar (göreli) ise sayısal tolerans (kayıtla eşitlenir)
SOL_CACHE_KEYS = 8                   # önceki çözüm önbelleği: yapı sayısı
SOL_CACHE_PER_KEY = 4                # yapı başına saklanan açık küme
SEED_MAX = 2                         # yeniden değerlendirilen en çok önceki çözüm
_MODEL_CACHE: "OrderedDict[tuple, tuple[MilpModel, threading.Lock]]" = OrderedDict()
MODEL_CACHE_SIZE = 4
_SOL_CACHE: "OrderedDict[tuple, list[dict]]" = OrderedDict()


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


def clear_cache(all_caches: bool = True) -> None:
    """Model ve önceki çözüm önbelleklerini boşaltır; all_caches: Lagrange çarpanı ve bütçe ν önbellekleri de."""
    _MODEL_CACHE.clear()
    _SOL_CACHE.clear()
    if all_caches:
        lagrange_mod.clear_cache()
        budget_mod.clear_cache()


# ---------------------------------------------------------------- önceki çözümler (şartname 4.6: önceki çözüm başlangıç)
def _sol_key(inst: Instance) -> tuple:
    return (inst.s.key, inst.nK, inst.nJ)


def remember_solution(inst: Instance, sol: Solution) -> None:
    """Açık küme + sınıflar yapı anahtarıyla saklanır (aynı yapıdaki sonraki çözümlerde başlangıç adayı)."""
    key = _sol_key(inst)
    lst = _SOL_CACHE.setdefault(key, [])
    sig = (sol.open_j.tobytes(), sol.cls.tobytes())
    lst[:] = [e for e in lst if (e["open_j"].tobytes(), e["cls"].tobytes()) != sig]
    lst.insert(0, {"open_j": sol.open_j.copy(), "cls": sol.cls.copy(), "mode": inst.mode,
                   "params_hash": inst.params.hash()})
    del lst[SOL_CACHE_PER_KEY:]
    _SOL_CACHE.move_to_end(key)
    while len(_SOL_CACHE) > SOL_CACHE_KEYS:
        _SOL_CACHE.popitem(last=False)


def remembered_count(inst: Instance) -> int:
    """Bu yapı için saklanan önceki çözüm sayısı."""
    return len(_SOL_CACHE.get(_sol_key(inst), ()))


def remembered_modes(inst: Instance) -> list[str]:
    """Bu yapı için saklanan önceki çözümlerin amaç modları (listedeki sırayla)."""
    return [str(e["mode"]) for e in _SOL_CACHE.get(_sol_key(inst), ())]


def cached_open_sets(inst: Instance) -> list[dict]:
    """Bu örnekte kullanılabilecek önceki açık kümeler (kilitler ve sayı sınırlarıyla uyumlu olanlar)."""
    out = []
    for e in _SOL_CACHE.get(_sol_key(inst), []):
        oj = e["open_j"]
        if len(oj) == 0 or oj.max() >= inst.nJ or inst.lock_closed[oj].any():
            continue
        if not np.isin(np.flatnonzero(inst.lock_open), oj).all():
            continue
        if not (len(oj) >= inst.count_min and (inst.count_max is None or len(oj) <= inst.count_max)):
            continue
        if not inst.y_ub[oj, e["cls"]].all():
            e = dict(e, cls=None)
        out.append(e)
    return out


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
          lagrange_mu0: np.ndarray | None = None, polish_time: float | None = None, use_cache: bool = True,
          use_windows: bool = True) -> dict:
    """Optimizasyonu çalıştırır; sonuç sözlüğü (JSON'a çevrilebilir). Hata/olursuzluk durumunda da sözlük döner.

    instance verilirse (testler, oyuncak örnekler) parametre/örnek kurulumu atlanır; overrides/mode/grid_km yok sayılır.
    isolate=True (varsayılan): tam MILP ayrı bir süreçte çözülür (çekirdek MIP her zaman ayrı süreçte); iptal
    (cancel_event) ve süre sınırı her an etkilidir. Ek yük ≈0,4 sn (Linux), Windows'ta ≈1 sn.
    use_lagrange: Lagrange alt sınırı ve sezgiseli (bütçe modunda bütçe satırının ν gevşetmesi).
    mip: 'auto' → canlı çözümde 'core' (Lagrange A → LP yuvarlaması + çekirdek MIP ∥ Lagrange B + pencere araması →
    cila), süre sınırı ≥ FULL_MIP_MIN_TIME ise 'full' (Lagrange → çekirdek evresi → tam MILP); 'none' MILP'siz.
    use_mip=False ≡ 'none'. lagrange_mu0: Lagrange başlangıç çarpanları (TL / talep hücresi).
    use_cache: aynı yapıdaki önceki çözümlerin açık kümeleri başlangıç adayıdır (şartname 4.6); çözüm sonunda saklanır.
    use_windows: iyileştirme evresinde pencere araması.
    """
    T0 = time.perf_counter()
    times: dict[str, float] = {}
    trace: list[dict] = []
    stop = cancel_event or threading.Event()

    emit_lock = threading.Lock()

    def emit(ev: dict) -> None:
        """İz olayı (iş parçacığı güvenli: Lagrange B evresi arka planda da olay üretir)."""
        ev = dict(ev)
        ev.setdefault("t_total", round(time.perf_counter() - T0, 4))
        with emit_lock:
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

    # ---- süre bütçesi (sert sınır: T_END + ≈0,5 sn)
    T_END = T0 + tl
    fin_reserve = FIN_RESERVE_S + (min(max(0.05 * tl, 0.3), 3.0) if compute_duals else 0.0)
    polish_reserve = min(0.08 * tl, 2.5) if polish else 0.0
    reserve = polish_reserve + fin_reserve

    def remaining() -> float:
        return T_END - time.perf_counter()

    def work_left() -> float:
        """Ana evreler için kalan süre (cila ve sonuç payı düşülmüş)."""
        return remaining() - reserve

    is_budget = inst.mode == "butce" and inst.budget is not None
    eps_B = BUDGET_EPS * float(inst.budget) if is_budget else 0.0

    def mobj(sol: Solution) -> float:
        """Modelin amaç değeri (TL; butce'de ε·açılış dahil) — çözüm karşılaştırmaları için."""
        return sol.objective_tl(inst) + (BUDGET_EPS * sol.opening_tl(inst) if is_budget else 0.0)

    def rep(sol: Solution) -> float:
        """Raporlanan amaç (TL): toplam/sabit_p → açılış + erişim; butce → yalnız erişim."""
        return sol.objective_tl(inst)

    best: Solution | None = None
    best_src = None
    best_solver_obj = None

    def adopt(sol: Solution | None, src: str, solver_obj: float | None = None) -> bool:
        """Denetimden geçen ve daha iyi olan çözümü en iyi çözüm yapar."""
        nonlocal best, best_src, best_solver_obj
        if sol is None or not checks_mod.check(inst, sol)["ok"]:
            return False
        if best is not None and not mobj(sol) < mobj(best) * (1 - 1e-12):
            return False
        best, best_src, best_solver_obj = sol, src, solver_obj
        emit({"kind": "cozum", "source": src, "primal": rep(sol)})
        return True

    # alt sınırlar (raporlanan birimde: butce'de yalnız erişim) — kaynak → değer
    lbs: dict[str, float] = {}

    def add_lb(src: str, v: float | None) -> None:
        if v is not None and math.isfinite(v) and (src not in lbs or v > lbs[src]):
            lbs[src] = float(v)

    def best_lb() -> float | None:
        return max(lbs.values()) if lbs else None

    def gap_of(u: float | None, lbv: float | None) -> float | None:
        if u is None or lbv is None:
            return None
        return (u - lbv) / max(abs(u), 1e-9)

    def certified(g: float, extra_ub: float | None = None) -> bool:
        us = [v for v in ((rep(best) if best is not None else None), extra_ub) if v is not None]
        v = gap_of(min(us), best_lb()) if us else None
        return v is not None and v <= g + 1e-12

    # ---- dışarıdan başlangıç ve önceki çözümler (aynı yapı; bu parametrelerle yeniden değerlendirilir)
    if start is not None:
        adopt(start, "baslangic")
    seeds_info = []
    if use_cache and not stop.is_set():
        t = time.perf_counter()
        for e in cached_open_sets(inst)[:SEED_MAX]:
            if work_left() < 0.5:
                break
            ev = evaluate(inst, e["open_j"], cls=e["cls"]) if e["cls"] is not None else None
            if ev is None or not ev.ok:
                ev = evaluate(inst, e["open_j"], gap=1e-2, time_limit=max(0.2, min(2.0, 0.1 * work_left())))
            ok = bool(ev.ok) and adopt(ev.solution, "onceki_cozum", ev.solver_objective_tl)
            seeds_info.append({"mode": e["mode"], "n": int(len(e["open_j"])), "ok": bool(ev.ok), "adopted": ok,
                               "objective_tl": ev.objective_tl if ev.ok else None})
        times["seed_s"] = time.perf_counter() - t

    # ---- sezgisel
    heur = None
    if use_heuristic and not stop.is_set():
        hb = heuristic_time if heuristic_time is not None else min(max(0.15 * tl, 1.0), 10.0)
        hb = max(0.3, min(hb, 0.4 * max(work_left(), 0.0)))
        t = time.perf_counter()
        # kesin değerlendirmede gap 1e-2: sezgisel yalnız ilk üst sınırdır (2 km'de 5 → 2,7 sn); en iyi çözüm sonda cilalanır
        heur = heuristic(inst, time_budget=hb, stop=stop, eval_gap=1e-2,
                         eval_time=max(0.3, min(5.0, max(1.0, 0.05 * tl), 0.3 * max(work_left(), 0.0))),
                         start_open=None)
        times["heuristic_s"] = time.perf_counter() - t
        emit({"kind": "heuristic", "t": times["heuristic_s"], "primal": heur.objective_tl, "ok": heur.ok})
        if heur.ok and heur.solution is not None:
            adopt(heur.solution, "sezgisel", heur.eval.solver_objective_tl if heur.eval else None)

    mip_kind = "none" if not use_mip else mip
    if mip_kind not in ("auto", "full", "core", "none"):
        return fail("hata", f"Geçersiz MIP stratejisi: {mip!r}", **base)
    lag_on = use_lagrange
    if mip_kind == "auto":
        mip_kind = "full" if (not lag_on or tl >= FULL_MIP_MIN_TIME) else "core"
    if mip_kind == "core" and not lag_on:
        mip_kind = "full"
    use_core = lag_on and (mip_kind == "core" or (mip_kind == "full" and tl >= FULL_MIP_MIN_TIME))
    tgap_core = 0.5 * tgap               # iyileştirme evresinin hedefi: hedef gap'in yarısı
    core_gap = min(CORE_GAP, tgap_core)  # çekirdek MIP'in kendi gap'i (doğrulama #11: hedef gap < 1e-3 iken de tutarlı)
    constrained = inst.count_min > 0 or inst.count_max is not None    # sayı sınırı ya da sabit p
    ran: list[str] = []                  # gerçekten çalışan MIP türleri (bounds.mip_kind)

    # ---- Lagrange gevşetmesi (A evresi)
    lag = None                           # serbest/sabit_p: lagrange.LagrangeResult; butce: budget.BudgetLagResult
    lag_info: dict = {}
    budget_info: dict | None = None

    def lag_relay(t_off: float, phase: int):
        def relay(ev: dict) -> None:
            e = dict(ev)
            e["t_total"] = round(t_off + ev["t"], 4)
            e["phase"] = phase
            e["primal"], e["dual"] = ev.get("ub"), ev.get("lb")
            emit(e)
        return relay

    if lag_on and not stop.is_set() and work_left() > 0.2:
        cap = work_left()
        stall = 0.0
        if mip_kind == "core":
            cap, stall = min(cap, (BUDGET_LAG_SHARE if is_budget else PHASE1_SHARE) * tl), PHASE1_STALL_TOL
        elif mip_kind == "full":
            cap = min(cap, max(0.4 * cap, 3.0), LAGRANGE_MAX_BEFORE_FULL_MIP)
            stall = PHASE1_STALL_TOL if use_core else 0.0
        t_l0 = time.perf_counter() - T0
        emit({"kind": "phase", "phase": "lagrange", "t": t_l0})
        if is_budget:
            def relay_b(ev: dict) -> None:
                emit({**ev, "t_total": round(t_l0 + ev["t"], 4), "dual": ev.get("lb"),
                      "primal": rep(best) if best is not None else None})
            lag = budget_mod.run(inst, time_limit=max(cap, 0.5), stop=stop,
                                 solutions=[best] if best is not None else [], target_gap=tgap_core,
                                 ub_access_tl=lambda: rep(best) if best is not None else None, progress_cb=relay_b)
            times["lagrange_s"] = time.perf_counter() - T0 - t_l0
            if lag.ok:
                add_lb("lagrange_butce", lag.lb_tl)
                # ν-problemlerinin çözümleri (serbest problemde olurlu): bütçeye sığanlar bütçe modunda değerlendirilir
                for sol in sorted(lag.solutions, key=lambda x: x.access_tl(inst)):
                    if work_left() < 0.5 or stop.is_set():
                        break
                    if sol.opening_tl(inst) > 1.03 * inst.budget:
                        continue
                    ev = evaluate(inst, sol.open_j, cls=sol.cls)       # sınıflar sabit LP (bütçe satırlı; hızlı)
                    if not ev.ok:
                        ev = evaluate(inst, sol.open_j, gap=1e-2, time_limit=max(0.2, min(1.0, 0.1 * work_left())))
                    if ev.ok:
                        adopt(ev.solution, "lagrange_butce", ev.solver_objective_tl)
            budget_info = {"lb_tl": lag.lb_tl, "nu": lag.nu, "time_s": lag.time_s, "message": lag.message,
                           "nu_core": lag.nu_core, "points": [p.as_dict() for p in lag.points]}
        else:
            lag = lagrange_mod.run(inst, start=best, time_limit=max(cap, 0.5),
                                   target_gap=tgap_core if use_core else tgap, stop=stop,
                                   progress_cb=lag_relay(t_l0, 1), mu0_tl=lagrange_mu0, stall_tol=stall,
                                   **(PHASE1_LH_CORE if use_core and not constrained else {}))
            times["lagrange_s"] = time.perf_counter() - T0 - t_l0
            if lag.ok:
                add_lb("lagrange", lag.bound_tl)
                if lag.solution is not None and lag.solution is not best:
                    adopt(lag.solution, lag.source or "lagrange")
                lag_info = {"bound_tl": lag.bound_tl, "ub_tl": lag.ub_tl, "gap": lag.gap, "iters": lag.iters,
                            "time_s": lag.time_s, "init_s": lag.init_s, "init_source": lag.init_source,
                            "lh_calls": lag.lh_calls, "lh_time_s": lag.lh_time_s, "sub_ms": lag.sub_ms,
                            "source": lag.source}
    lag_ok = lag is not None and lag.ok
    G_src = lag if lag_ok else None      # çekirdek aday seçimi (G_min_tl)

    # ---- İYİLEŞTİRME evresi: LP yuvarlaması + çekirdek MIP (ayrı süreç; durursa yeniden) ∥ Lagrange B + pencereler
    core_info: dict | None = None
    core_lb = None
    core_infeasible = False
    improve_info: dict = {}
    attempts: list[dict] = []
    if use_core and lag_ok and not stop.is_set() and not certified(tgap_core):
        stage_end = time.perf_counter() + work_left()
        if mip_kind == "full":
            stage_end = min(stage_end, time.perf_counter() + max(0.2 * tl, 30.0))

        def stage_left() -> float:
            return stage_end - time.perf_counter()

        if stage_left() >= CORE_MIN_TIME:
            ran.append("core")
            emit({"kind": "phase", "phase": "cekirdek_mip", "t": time.perf_counter() - T0})
            t_stage = time.perf_counter()
            # (1) çekirdek + LP gevşetmesinin yuvarlanması
            core, sel = lagrange_mod.core_instance(inst, G_src, best, n_keep=CORE_N, m=CORE_M)
            rounded, rinfo = lns.lp_round(inst, core, sel, linking=linking,
                                          time_limit=max(0.2, min(10.0, 0.3 * stage_left())),
                                          G_tl=G_src.G_min_tl, stop=stop,
                                          eval_time=max(0.2, min(2.0, 0.1 * stage_left())))
            improve_info["lp_round"] = rinfo
            for sol in rounded:
                adopt(sol, "lp_yuvarlama")
            # yuvarlanan kümenin sınıfları sabit sınıflı LP + onarımdan gelir: süre bolsa hemen sınıf seçimli cila
            # (2 km ölçümü: ≈0,2–0,3 sn, ≈0,05% iyileşme); son cila yine yapılır
            if (best is not None and best_src == "lp_yuvarlama" and not stop.is_set() and not certified(tgap_core)
                    and stage_left() > 8.0):
                ev = evaluate(inst, best.open_j, gap=1e-3, time_limit=min(3.0, 0.1 * stage_left()))
                if ev.ok:
                    adopt(ev.solution, "lp_yuvarlama+cila", ev.solver_objective_tl)
            times["lp_round_s"] = time.perf_counter() - t_stage

            handle = None
            cur = {"sel": None, "core": None, "start_obj": math.inf, "t0": 0.0, "last_imp": 0.0, "best_seen": math.inf,
                   "n": 0}

            core_errors: list[str] = []

            def core_warn(what: str, ex: BaseException) -> None:
                """Alt süreç hatası çözümü düşürmez (Faz 4 incelemesi, bulgu 3): uyarı yazılır, en iyi çözümle sürülür."""
                msg = (f"Uyarı: çekirdek MIP alt süreci {what} ({type(ex).__name__}); çözüm onsuz sürüyor, en iyi "
                       f"bulunan çözüm korunuyor.")
                core_errors.append(f"{what}: {type(ex).__name__}: {ex}")
                inst.warnings.append(msg)
                emit({"kind": "uyari", "message_tr": msg})

            def start_core(n_keep: int, m: int, mu_src) -> None:
                nonlocal handle
                try:
                    _start_core(n_keep, m, mu_src)
                except Exception as ex:  # noqa: BLE001
                    if handle is not None:
                        try:
                            handle.close()
                        except Exception:
                            pass
                    handle = None
                    core_warn("başlatılamadı", ex)

            def _start_core(n_keep: int, m: int, mu_src) -> None:
                """Güncel en iyi çözümden çekirdek kurar ve alt süreçte MIP'i başlatır."""
                nonlocal handle
                t = time.perf_counter()
                core_, sel_ = lagrange_mod.core_instance(inst, mu_src, best, n_keep=n_keep, m=m)
                cm = MilpModel(core_, linking=linking)
                opts = {}
                if not is_budget and best_lb() is not None:
                    opts["objective_target"] = best_lb() / (1.0 - tgap_core) / SCALE
                win = max(stage_left() - STOP_GRACE_S, 0.3)

                def relay_core(ev: dict, t_c0=time.perf_counter() - T0, k=cur["n"] + 1) -> None:
                    ev = dict(ev)
                    ev["t_total"] = round(t_c0 + ev["t"], 4)
                    ev["core_dual"], ev["dual"] = ev.get("dual"), None       # çekirdeğin alt sınırı küresel değil
                    ev["core"] = True
                    ev["attempt"] = k
                    if is_budget and ev.get("primal") is not None:
                        ev["primal"] = None              # model amacı (ε dahil); rapor birimi değil
                    emit(ev)
                st_core = lagrange_mod.solution_to_core(best, sel_) if best is not None else None
                handle = cm.start_isolated(time_limit=win, gap=core_gap, start=st_core, progress_cb=relay_core,
                                           options=opts)
                now = time.perf_counter()
                cur.update(sel=sel_, core=core_, model=cm,
                           start_obj=cm.objective_of(st_core) if st_core is not None else math.inf, t0=now,
                           last_imp=now, best_seen=math.inf, n=cur["n"] + 1, build_s=now - t, n_keep=n_keep, m=m)

            def finish_core(reason: str) -> None:
                """Alt süreci kibarca durdurur, sonucunu alır, en iyi çözüme uygular; süreci her durumda kapatır."""
                nonlocal handle, core_lb, core_infeasible, core_info
                if handle is None:
                    return
                h, handle = handle, None
                try:
                    if not h.done:
                        h.terminate("Interrupted by user" if stop.is_set() else "Time limit reached")
                    cres = h.result()
                except Exception as ex:  # noqa: BLE001
                    core_warn("sonucu alınamadı", ex)
                    attempts.append({"attempt": cur["n"], "status": "hata", "stop_reason": reason, "error": repr(ex),
                                     "is_full_model": False, "improved": False})
                    core_info = dict(attempts[-1])
                    return
                finally:
                    h.close()
                improved = False
                if cres.solution is not None:
                    full_sol = lagrange_mod.solution_from_core(cres.solution, cur["sel"], inst.nA)
                    improved = adopt(full_sol, "cekirdek_mip", None)
                core_ = cur["core"]
                # çekirdek tam modelin kendisiyse (küçük örnekler) alt sınırı da küreseldir. Yasak kilitli hücrelere
                # giden yaylar iki modelde de 0 olmak zorunda olduğundan karşılaştırmada sayılmaz (doğrulama #11).
                need = np.flatnonzero(~inst.lock_closed[inst.s.arc_j])
                is_full = bool(np.array_equal(core_.lock_closed, inst.lock_closed)
                               and np.isin(need, cur["sel"]).all())
                if is_full and cres.dual_tl is not None:
                    add_lb("mip", cres.dual_tl - eps_B)
                    core_lb = cres.dual_tl - eps_B
                if is_full and cres.status == "Infeasible":
                    core_infeasible = True
                a = {"attempt": cur["n"], "candidates": int((~core_.lock_closed).sum()), "arcs": int(len(cur["sel"])),
                     "n_keep": cur["n_keep"], "m": cur["m"], "status": cres.status, "primal_tl": cres.primal_tl,
                     "core_dual_tl": cres.dual_tl, "time_s": cres.time_s, "spawn_s": cres.spawn_s,
                     "first_incumbent_s": cres.first_incumbent_s, "first_dual_s": cres.first_dual_s,
                     "from_spare": bool(getattr(h, "from_spare", False)),
                     "build_s": cur["build_s"], "improved": improved, "improved_start": cur["best_seen"] < cur[
                         "start_obj"] * (1 - 1e-9), "stop_reason": reason, "is_full_model": is_full,
                     "model": cur["model"].size()}
                attempts.append(a)
                core_info = dict(a)

            def core_poll() -> float | None:
                """Alt süreç olaylarını işler; çekirdeğin güncel amaç değeri (model birimi) ya da None."""
                if handle is None:
                    return None
                try:
                    handle.poll()
                except RuntimeError:
                    pass
                u = handle.st["primal"]
                if u is not None and u < cur["best_seen"]:
                    if u < cur["start_obj"] * (1 - 1e-9) and u < cur["best_seen"] * (1 - 1e-9):
                        cur["last_imp"] = time.perf_counter()
                    cur["best_seen"] = u
                return u

            def core_stalled() -> bool:
                if handle is None or handle.done:
                    return False
                root_done = handle.st["first_dual"] is not None
                since = time.perf_counter() - cur["last_imp"]
                win_total = stage_end - cur["t0"]
                return root_done and since > max(CORE_STALL_S, 0.25 * win_total)

            th_b = None
            b_stop = threading.Event()
            b_res: dict = {}
            try:
                if stage_left() >= CORE_MIN_TIME and not certified(tgap_core):
                    start_core(CORE_N, CORE_M, G_src)
                # (2) Lagrange B ARKA PLAN iş parçacığında: sınır durulana dek yükselir; aynı anda ana iş parçacığı pencere
                # aramasını ve çekirdeği yürütür (numpy ve HiGHS GIL'i bırakır). Çekirdeğin ve pencerelerin çözümleri üst
                # sınır olarak beslenir. Bütçe modunda B evresi ν aramasının sürdürülmesidir (budget.run, A evresinin
                # bıraktığı ν, μ ve köşeli ayraçtan) ve en iyi ν'ye uzun süre verir. Ölçüm (solver_benchmark.json
                # 'butce', nu_points_a / nu_points_b): 3 km'de A evresi sonundaki 31,73–31,88 milyar TL'lik sınırı B
                # evresi 31,98–32,02'ye çıkardı; 5 km'de A evresi zaten yakınsamış (artış ≤ 0,013).
                class _BStop:
                    @staticmethod
                    def is_set() -> bool:
                        return stop.is_set() or b_stop.is_set()

                if is_budget and not stop.is_set() and stage_left() > 1.0:
                    t_lb0 = time.perf_counter() - T0

                    def relay_bb(ev: dict) -> None:
                        if ev.get("lb") is not None:
                            b_res["lb"] = max(b_res.get("lb", -math.inf), ev["lb"])
                        emit({**ev, "t_total": round(t_lb0 + ev["t"], 4), "dual": ev.get("lb"), "phase": 2,
                              "primal": rep(best) if best is not None else None})

                    def run_bb() -> None:
                        try:
                            b_res["r"] = budget_mod.run(
                                inst, time_limit=max(stage_left() - STOP_GRACE_S, 0.2), stop=_BStop(),
                                solutions=[best], target_gap=tgap_core,
                                ub_access_tl=lambda: rep(best) if best is not None else None, progress_cb=relay_bb)
                        except BaseException as ex:  # noqa: BLE001
                            b_res["err"] = ex
                        finally:
                            b_res["t"] = time.perf_counter() - T0 - t_lb0

                    th_b = threading.Thread(target=run_bb, name="butce-lagrange-b", daemon=True)
                    th_b.start()
                if not is_budget and not stop.is_set() and stage_left() > 1.0:
                    def feed() -> float | None:
                        us = [v for v in (cur.get("u"), rep(best) if best is not None else None) if v is not None]
                        return min(us) if us else None

                    def extra_stop(L_tl: float, U_tl: float) -> bool:
                        # çekirdek bittiyse hedef gap'te dur (yarı hedef yalnız iyileştirme sürerken aranır)
                        return (handle is None or handle.done) and (U_tl - L_tl) / U_tl <= tgap

                    t_lb0 = time.perf_counter() - T0
                    relay_b = lag_relay(t_lb0, 2)

                    def relay_b2(ev: dict) -> None:
                        if ev.get("lb") is not None:
                            b_res["lb"] = max(b_res.get("lb", -math.inf), ev["lb"])
                        relay_b(ev)

                    def run_b() -> None:
                        try:
                            b_res["r"] = lagrange_mod.run(
                                inst, start=best, time_limit=max(stage_left() - STOP_GRACE_S, 0.2),
                                target_gap=tgap_core, stop=_BStop(), progress_cb=relay_b2, mu0_tl=lag.mu, theta0=0.1,
                                heuristics_on=constrained, ub_feed=feed, extra_stop=extra_stop,
                                stall_tol=PHASE1_STALL_TOL, stall_min_s=1.0)
                        except BaseException as ex:  # noqa: BLE001
                            b_res["err"] = ex
                        finally:
                            b_res["t"] = time.perf_counter() - T0 - t_lb0

                    th_b = threading.Thread(target=run_b, name="lagrange-b", daemon=True)
                    th_b.start()
                # (3) pencere araması; çekirdek izlenir, durursa yeniden başlatılır
                ws = (lns.WindowSearch(inst, linking=linking)
                      if use_windows and inst.s.cand_xy is not None and inst.s.demand_xy is not None else None)
                t_w0 = time.perf_counter()
                while not stop.is_set():
                    if stage_left() <= STOP_GRACE_S + 0.05:
                        break
                    if "lb" in b_res:
                        add_lb("lagrange_butce" if is_budget else "lagrange", b_res["lb"])
                    u = core_poll()                      # çekirdeğin amacı (butce'de ε·açılış dahil ≥ erişim)
                    cur["u"] = u
                    if certified(tgap_core, u):
                        break
                    if handle is not None and handle.done:
                        finish_core("bitti")
                        if certified(tgap_core):
                            break
                    elif core_stalled() and best is not None and mobj(best) < (cur["best_seen"] if math.isfinite(
                            cur["best_seen"]) else cur["start_obj"]) * (1 - 1e-9):
                        finish_core("durdu")
                    if (handle is None and attempts and len(attempts) <= len(CORE_RETRY) and best is not None
                            and len(core_errors) < 2
                            and stage_left() > CORE_MIN_TIME + 1.0 and not certified(tgap_core)
                            and (attempts[-1]["stop_reason"] == "durdu"
                                 or (attempts[-1]["status"] != "Infeasible" and not attempts[-1]["is_full_model"]
                                     and stage_left() > 3 * CORE_MIN_TIME))):
                        n_keep, m = CORE_RETRY[len(attempts) - 1]
                        mu_src = (b_res["r"] if not is_budget and b_res.get("r") is not None and b_res["r"].ok
                                  else G_src)        # bütçe: çekirdek G'si A evresinden (ν ≈ 1; budget.py)
                        start_core(n_keep, m, mu_src)
                    if ws is not None and not ws.exhausted and best is not None:
                        cand, winfo = ws.step(best, time_limit=max(0.05, min(lns.WINDOW_TIME,
                                                                                stage_left() - STOP_GRACE_S - 0.05)),
                                              stop=stop)
                        if cand is not None:
                            g0 = mobj(best)
                            if adopt(cand, "pencere"):
                                ws.accepted(g0 - mobj(best))
                    elif handle is not None:
                        try:
                            handle.poll(timeout=0.05)
                        except RuntimeError:
                            pass
                    elif th_b is not None and th_b.is_alive():
                        th_b.join(timeout=0.05)
                    else:
                        break
                if ws is not None:
                    improve_info["windows"] = dict(ws.stats, exhausted=ws.exhausted,
                                                   time_s=time.perf_counter() - t_w0)
            finally:
                b_stop.set()
                if th_b is not None:
                    th_b.join()
                finish_core("evre_sonu")
            lagB = b_res.get("r")
            if "err" in b_res:
                raise b_res["err"]
            if lagB is not None and is_budget:
                times["lagrange_b_s"] = b_res.get("t")
                if lagB.ok:
                    add_lb("lagrange_butce", lagB.lb_tl)
                    if budget_info is not None:
                        budget_info["phase_b"] = {"lb_tl": lagB.lb_tl, "nu": lagB.nu, "time_s": lagB.time_s,
                                                  "points": [p.as_dict() for p in lagB.points]}
                        if budget_info["lb_tl"] is None or lagB.lb_tl > budget_info["lb_tl"]:
                            budget_info["lb_tl"], budget_info["nu"] = lagB.lb_tl, lagB.nu
            elif lagB is not None:
                times["lagrange_b_s"] = b_res.get("t")
                if lagB.ok:
                    if lagB.solution is not None and lagB.solution is not best:
                        adopt(lagB.solution, lagB.source or "lagrange")
                    add_lb("lagrange", lagB.bound_tl)
                    lag_info["bound_tl"] = max(lag_info.get("bound_tl") or -math.inf, lagB.bound_tl)
                    lag_info["phase_b"] = {"bound_tl": lagB.bound_tl, "iters": lagB.iters, "time_s": lagB.time_s}
                    if lagB.bound_tl >= lag.bound_tl:
                        lag = lagB
            times["core_mip_s"] = time.perf_counter() - t_stage
            if core_info is not None:
                core_info["attempts"] = attempts

    # ---- cila: aynı açık kümede en iyi sınıf/atama (sınıf seçimli küçük MIP; evaluate)
    polished: set = set()

    def do_polish() -> None:
        if not polish or best is None or stop.is_set():
            return
        key = hash(best.open_j.tobytes())
        if key in polished:
            return
        avail = remaining() - fin_reserve
        if avail < 0.15:
            return
        polished.add(key)
        pt = polish_time if polish_time is not None else max(1.0, min(0.1 * tl, 10.0))
        pt = min(pt, avail)
        t = time.perf_counter()
        ev = evaluate(inst, best.open_j, gap=1e-3 if tl < FULL_MIP_MIN_TIME else 1e-4, time_limit=pt)
        times["polish_s"] = times.get("polish_s", 0.0) + time.perf_counter() - t
        if ev.ok and mobj(ev.solution) < mobj(best) * (1 - 1e-9):
            src0 = best_src
            ev.solution.source = f"{src0}+cila"
            if adopt(ev.solution, f"{src0}+cila", ev.solver_objective_tl):
                emit({"kind": "polish", "primal": rep(best)})

    # ---- tam MILP (Lagrange kapalı, uzun süre sınırı ya da hiç olurlu çözüm bulunamadıysa: tam model olursuzluğu da
    # kanıtlayabilir)
    mres = None
    model_size = None
    if mip_kind == "core" and best is None and not core_infeasible:
        mip_kind = "full"
    if mip_kind == "full" and not stop.is_set() and not certified(tgap) and not core_infeasible:
        do_polish()                          # tam MILP'e iyi başlangıç
        t = time.perf_counter()
        model, lock, how = _get_model(inst, linking)
        times["model_build_s"] = time.perf_counter() - t
        model_size = model.size()
        model_size["model"] = how
        budget_mip = work_left()
        opts = dict(mip_options or {})
        if not is_budget and best_lb() is not None and "objective_target" not in opts:
            opts["objective_target"] = best_lb() / (1.0 - tgap) / SCALE   # Lagrange sınırına göre hedef gap'te dur
        try:
            if budget_mip > 0.2:
                ran.append("full")
                emit({"kind": "phase", "phase": "mip", "t": time.perf_counter() - T0})
                t_mip0 = time.perf_counter() - T0

                def relay(ev: dict) -> None:
                    ev = dict(ev)
                    ev["t_total"] = round(t_mip0 + ev["t"], 4)
                    if is_budget:
                        ev["primal"] = None
                        ev["dual"] = ev["dual"] - eps_B if ev.get("dual") is not None else None
                    emit(ev)
                try:
                    mres = model.solve(time_limit=budget_mip, gap=tgap, start=best, progress_cb=relay,
                                       cancel_event=stop, options=opts, isolate=isolate,
                                       deadline=T_END - reserve if isolate else None)
                except (OSError, EOFError, RuntimeError, ValueError) as ex:
                    # alt süreç başlatılamadı / öldü (Faz 4 incelemesi, bulgu 3): çözüm düşmez, en iyi çözümle sürer
                    msg = (f"Uyarı: tam MILP alt süreci çalıştırılamadı ({type(ex).__name__}); en iyi bulunan çözüm "
                           f"korunuyor.")
                    inst.warnings.append(msg)
                    emit({"kind": "uyari", "message_tr": msg})
                    mres = None
                if mres is not None:
                    if mres.spawn_s is not None:
                        times["mip_spawn_s"] = mres.spawn_s
                    times["mip_s"] = mres.time_s
                if mres is not None and mres.solution is not None:
                    obj_m = model.objective_of(mres.solution)
                    obj_b = model.objective_of(best) if best is not None else math.inf
                    if obj_m < obj_b - 1e-9 * abs(obj_b):      # MIP başlangıcı geri döndüyse kaynak değişmez
                        adopt(mres.solution, "mip", mres.primal_tl)
                if mres is not None and mres.dual_tl is not None and mres.first_dual_s is not None:
                    add_lb("mip", mres.dual_tl - eps_B)
        finally:
            if lock is not None:
                lock.release()

    # ---- Lagrange 2. evre (iyileştirme evresi erken bittiyse ve hâlâ hedef gap'te değilse: sınır tarafı)
    if (lag_ok and mip_kind == "core" and not stop.is_set() and not certified(tgap) and work_left() > 0.5
            and best is not None):
        t_l0 = time.perf_counter() - T0
        emit({"kind": "phase", "phase": "lagrange_2", "t": t_l0})
        if is_budget:
            lag2b = budget_mod.run(inst, time_limit=work_left(), stop=stop, solutions=[best], target_gap=tgap,
                                   ub_access_tl=lambda: rep(best))
            if lag2b.ok:
                add_lb("lagrange_butce", lag2b.lb_tl)
                if budget_info is not None:
                    budget_info["phase2"] = {"lb_tl": lag2b.lb_tl, "nu": lag2b.nu, "time_s": lag2b.time_s}
                    if budget_info["lb_tl"] is None or lag2b.lb_tl > budget_info["lb_tl"]:
                        budget_info["lb_tl"], budget_info["nu"] = lag2b.lb_tl, lag2b.nu
        else:
            lag2 = lagrange_mod.run(inst, start=best, time_limit=work_left(), target_gap=tgap, stop=stop,
                                    progress_cb=lag_relay(t_l0, 2), mu0_tl=lag.mu, theta0=0.1,
                                    stall_tol=PHASE1_STALL_TOL, stall_min_s=1.0)
            if lag2.ok:
                if lag2.solution is not None and lag2.solution is not best:
                    adopt(lag2.solution, lag2.source or "lagrange")
                add_lb("lagrange", lag2.bound_tl)
                lag_info["phase2"] = {"bound_tl": lag2.bound_tl, "iters": lag2.iters, "time_s": lag2.time_s,
                                      "lh_calls": lag2.lh_calls, "source": lag2.source}
                lag_info["bound_tl"] = max(lag_info.get("bound_tl") or -math.inf, lag2.bound_tl)
        times["lagrange2_s"] = time.perf_counter() - T0 - t_l0
    t_main_end = time.perf_counter()

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

    # ---- alt sınır: Lagrange, tam modele eşit çekirdeğin / tam MILP'in dual sınırı, gerekirse zayıf LP
    if not lbs and fallback_bound and not stop.is_set() and remaining() - fin_reserve > 0.3:
        # Lagrange yoksa ve MIP kök LP'yi bitiremediyse: zayıf LP gevşetmesi (kapasite bağlantılı, x ≤ z satırları
        # olmadan) hızlıdır ve geçerli bir alt sınırdır
        t = time.perf_counter()
        weak = MilpModel(inst, linking="weak")
        r = weak.solve(time_limit=max(0.2, min(max(5.0, 0.2 * tl), remaining() - fin_reserve)), relax=True)
        times["fallback_bound_s"] = time.perf_counter() - t
        if r.status == "Optimal" and r.primal_tl is not None:
            add_lb("zayif_lp", r.primal_tl - eps_B)
    ub_rep = rep(best)
    # alt sınır > üst sınır: asla sessizce kırpılmaz (doğrulama #13). Sayısal tolerans içindeyse kayıtla eşitlenir;
    # daha büyükse o kaynak geçersiz sayılır, uyarı yazılır (HASTANE_STRICT_BOUNDS=1 iken AssertionError).
    lb_rejected, lb_adjusted = [], None
    tol_abs = LB_TOL * max(abs(ub_rep), 1.0)
    for src_, v in list(lbs.items()):
        if v > ub_rep + tol_abs:
            lb_rejected.append({"source": src_, "lb_tl": v, "ub_tl": ub_rep})
            del lbs[src_]
            msg = (f"Uyarı: alt sınır ({src_}: {_fmt(v / 1e9, 4)} milyar TL) bulunan çözümden "
                   f"({_fmt(ub_rep / 1e9, 4)} milyar TL) büyük çıktı; geçersiz sayıldı ve raporlanmadı.")
            inst.warnings.append(msg)
            emit({"kind": "uyari", "message_tr": msg})
            if os.environ.get("HASTANE_STRICT_BOUNDS") == "1":
                raise AssertionError(msg)
    lb_src = max(lbs, key=lbs.get) if lbs else None
    lb = lbs[lb_src] if lb_src is not None else None
    if lb is not None and lb > ub_rep:
        lb_adjusted = {"raw_lb_tl": lb, "ub_tl": ub_rep, "reason": "sayisal_tolerans"}
        emit({"kind": "uyari", "message_tr": "Alt sınır üst sınırı sayısal tolerans içinde aştı; eşitlendi."})
        lb = ub_rep
    gap_val = gap_of(ub_rep, lb)

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
                  "hospital": hpos[s.arc_j[a]].tolist(), "share": best.x[a].tolist(), "km": s.arc_d[a].tolist(),
                  "population": layers_mod.flow_population(inst, best, a)}
    for h, sz in zip(hosp, layers_mod.size_info(inst, best)):
        h["size"] = sz
    k["cost_per_capita_tl"] = costs["toplam"] / k["population"] if k["population"] > 0 else None
    early = (T_END - reserve) - t_main_end > max(EARLY_STOP_MARGIN[0], EARLY_STOP_MARGIN[1] * tl)
    if stop.is_set():
        status, status_tr = "durduruldu", "Kullanıcı durdurdu; en iyi bulunan çözüm gösteriliyor."
    elif gap_val is not None and gap_val <= tgap + 1e-9:
        status, status_tr = "optimal", f"Optimal: alt sınırla fark hedef gap'in (%{_fmt(100 * tgap, 2)}) içinde."
    elif mres is None and lag is None:
        status, status_tr = "sezgisel", "Yalnız sezgisel çözüm (MILP ve Lagrange çalıştırılmadı)."
    elif early:
        status, status_tr = ("gap_ulasilamadi",
                             f"Hedef gap'e (%{_fmt(100 * tgap, 2)}) ulaşılamadı: alt ve üst sınır yöntemleri süre "
                             f"dolmadan yakınsadı (hedef bu yöntemlerle sayısal olarak erişilemiyor); en iyi çözüm ve "
                             f"alt sınır gösteriliyor.")
    else:
        status, status_tr = "sure_doldu", "Süre doldu; en iyi bulunan çözüm ve alt sınır gösteriliyor."
    result = dict(base)
    result.update({
        "status": status, "ok": True, "status_tr": status_tr,
        "message_tr": _summary_tr(k, costs, s.R_km, status == "optimal"),
        "solver_status": mres.status if mres is not None else None,
        "solution_source": best_src,
        "certificate_tr": _certificate_tr(ub_rep, lb, gap_val, lb_src, inst),
        "bounds": {"ub_tl": ub_rep, "lb_tl": lb, "gap": gap_val, "lb_source": lb_src,
                   "lb_scope": "k_listesi" if s.k_nearest > 0 else "tam",
                   "objective_tl": float(best.objective_tl(inst)),
                   "model_objective_tl": mobj(best),
                   "budget_eps_tl": eps_B if is_budget else None,
                   "lb_by_source": dict(lbs),
                   "lb_rejected": lb_rejected, "lb_adjusted": lb_adjusted,
                   "lagrange_tl": lbs.get("lagrange_butce" if is_budget else "lagrange"),
                   "mip_kind": "+".join(ran) if ran else "none",
                   "mip_kind_planned": mip_kind,
                   "mip_primal_tl": mres.primal_tl if mres is not None else None,
                   "mip_dual_tl": mres.dual_tl if mres is not None else None,
                   "mip_gap": mres.gap if mres is not None else None,
                   "mip_nodes": mres.nodes if mres is not None else None},
        "lagrange": lag_info or None,
        "budget": budget_info,
        "core_mip": core_info,
        "improve": improve_info or None,
        "seeds": seeds_info or None,
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
        "layers": layers_mod.map_layers(inst, best),
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
    if use_cache and chk["ok"]:
        remember_solution(inst, best)
    times["total_s"] = time.perf_counter() - T0
    return _jsonable(result)


_LB_SOURCE_TR = {"lagrange": "Lagrange gevşetmesi", "lagrange_butce": "bütçe satırının Lagrange gevşetmesi",
                 "mip": "dal-sınır (HiGHS)", "zayif_lp": "zayıf LP gevşetmesi"}


def _certificate_tr(ub: float, lb: float | None, gap: float | None, src: str | None, inst: Instance) -> str:
    """Şartname 7.2 optimallik sertifikası cümlesi (bütçe modunda erişim maliyeti üzerinden)."""
    what = "bulunan çözümün erişim maliyeti" if inst.mode == "butce" else "bulunan çözüm"
    if lb is None or gap is None:
        return (f"{what[0].upper() + what[1:]} {_fmt(ub / 1e9, 2)} milyar TL; alt sınır hesaplanamadı "
                f"(optimallik kanıtlanmadı).")
    scope = (f" (k = {inst.s.k_nearest} en yakın aday listesiyle kurulan model için)" if inst.s.k_nearest > 0 else "")
    return (f"Alt sınır L = {_fmt(lb / 1e9, 2)} milyar TL ({_LB_SOURCE_TR.get(src, src)}), {what} "
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
