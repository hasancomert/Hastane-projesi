"""Bütçe modu (butce): bütçe satırının Lagrange gevşetmesiyle alt sınır ve parametrik (ν) serbest çözümler.

Bütçe modu:  z_B = min erişim(x)   s.t.  açılış(x) ≤ B  ve diğer tüm kısıtlar (atama, kapasite, sınıf, R, kilit, sayı).
Bütçe satırı ν ≥ 0 çarpanıyla gevşetilirse
    φ(ν) = min { erişim(x) + ν·açılış(x) }   (diğer kısıtlar)
açılış maliyetleri ν ile ölçeklenmiş SERBEST (toplam) problemdir ve her ν ≥ 0 için φ(ν) − ν·B ≤ z_B (zayıf dualite:
bütçe optimumu x* için erişim(x*) ≥ φ(ν) − ν·açılış(x*) ≥ φ(ν) − ν·B). φ(ν) yerine onun geçerli bir alt sınırı —
ölçeklenmiş örnekte atama satırlarının Lagrange sınırı L_ν ≤ φ(ν) (lagrange.run) — kullanılır:
    LB(ν) = L_ν − ν·B   (erişim maliyeti için geçerli alt sınır; ε eşitlik bozucusu içermez).
φ içbükey olduğundan φ(ν) − ν·B de içbükeydir; log ν üzerinde örüntü aramasıyla en büyüklenir. Yön, alt problemin
açılış maliyetinden (dφ/dν'nin alt gradyanı) gelir: açılış > B → ν artırılır, < B → azaltılır. μ çarpanları ν'ler
arasında sıcak taşınır (açılış payı ν oranında ölçeklenir). Aynı yapı için son en iyi ν ve μ saklanır.
Faz 3 ölçümü (varsayılan 350 milyar TL bütçe, 6 sn'lik ν araması): 5 km'de en iyi ν ≈ 1,6–1,8, alt sınır 53,7 milyar
TL (serbest optimumun açılışı 355,8 > B); 3 km'de ν ≈ 0,4, alt sınır 31,5 milyar TL (serbest optimum bütçeye sığıyor:
açılış 345,3). Erişim maliyeti toplamın ≈%10'u olduğundan serbest problemin Lagrange sınırındaki küçük mutlak hata
(3 km'de birkaç sn'de ≈1 milyar TL) bütçe modunda göreli olarak ≈10 kat büyür. Ölçüm (solve, varsayılan bütçe, ikişer
soğuk çalıştırma): 5 km / 15 sn gap %2,2–2,3; 3 km / 15 sn %6,8–9,1; 2 km / 60 sn %9,9–15,4.
Yan ürün: her ν'deki serbest problemin Lagrange sezgiseli çözümleri; açılışı B'ye sığanlar bütçe modunda olurludur
(ν = 1 serbest modun kendisidir: serbest çözüm bütçeye sığıyorsa bütçe modunun başlangıcı olur).
"""
from __future__ import annotations

import dataclasses
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .instance import Instance, Solution

NU_CACHE_SIZE = 8
_NU_CACHE: "OrderedDict[tuple, tuple[float, np.ndarray]]" = OrderedDict()
NU_MIN, NU_MAX = 1e-3, 1e3
NU_TOL = 0.02              # ν adımı (göreli) bundan küçülünce dur
NU_STEP = 1.6              # ilk ν adımı (çarpan)
FIRST_SHARE = 0.45         # ilk ν'nin (soğuk başlangıç) süre payı
MIN_SLICE_S = 0.4          # sonraki ν'ler için en az süre (sn)
# ν-problemlerinde Lagrange sezgiseli hafif (sabit sınıflı LP + onarım): üst sınırı çekirdek MIP / pencereler verir
LH_LIGHT = {"class_mip_share": 0.0, "local_search": False}


def scaled_instance(inst: Instance, nu: float) -> Instance:
    """Açılış maliyetleri ν ile ölçeklenmiş serbest (toplam) örnek: amaç = erişim + ν·açılış."""
    return dataclasses.replace(inst, mode="toplam", budget=None, fixed=inst.fixed * nu, var=inst.var * nu,
                               from_cache=False)


def clear_cache() -> None:
    _NU_CACHE.clear()


@dataclass
class NuPoint:
    nu: float
    L_tl: float                 # φ(ν) için geçerli alt sınır (TL)
    lb_tl: float                # L_ν − ν·B
    opening_sub_tl: float       # alt problem çözümünün açılış maliyeti (ölçeksiz, TL)
    iters: int
    time_s: float

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class BudgetLagResult:
    ok: bool
    lb_tl: float | None                    # erişim maliyeti için geçerli alt sınır (TL)
    nu: float | None = None                # en iyi alt sınırı veren ν
    mu_tl: np.ndarray | None = None        # o ν'deki çarpanlar (TL / talep hücresi)
    G_min_tl: np.ndarray | None = None     # o ν'de adayların açılış indirgenmiş maliyetleri (çekirdek aday seçimi;
                                           # lagrange.core_instance bu alanı kullanır)
    solutions: list = field(default_factory=list)   # ν-problemlerinin çözümleri (serbest problemde olurlu)
    points: list = field(default_factory=list)
    time_s: float = 0.0
    message: str = ""


def _sub_opening(inst: Instance, sub) -> float:
    """Lagrange alt problem çözümünün (en iyi μ'de) ölçeksiz açılış maliyeti (TL)."""
    if sub is None or sub.bed is None:
        return math.nan
    j = np.flatnonzero(sub.open_)
    k = sub.kbest[j]
    return float((inst.fixed[j, k] + inst.var[j, k] * sub.bed[j, k]).sum())


def _rescale_mu(mu_tl: np.ndarray, acc_i: np.ndarray, r: float) -> np.ndarray:
    """ν → r·ν için sıcak başlangıç: erişim payı aynı, kalan (açılış) payı r ile ölçeklenir."""
    return acc_i + r * (mu_tl - acc_i)


def run(inst: Instance, *, time_limit: float, stop=None, solutions: list | None = None, target_gap: float = 0.01,
        ub_access_tl: Callable[[], float | None] | None = None,
        progress_cb: Callable[[dict], None] | None = None) -> BudgetLagResult:
    """ν üzerinde örüntü araması (log ν): her ν'de μ hacim algoritmasıyla yakınsatılır (lagrange.run; μ en yakın
    değerlendirilmiş ν'den ölçeklenerek sıcak başlar), LB(ν) = L_ν − ν·B hesaplanır. En iyi noktadan yön alt problemin
    açılış maliyetinden (> B → ν artır), adım NU_STEP; iyileşirse adım ×1,2, iyileşmezse ×0,5 (LB içbükey; ölçüm
    hatası yalnız ν seçimini etkiler, raporlanan her değer geçerli bir alt sınırdır). Aynı yapı için en iyi ν ve μ
    saklanır (sonraki bütçe çözümleri oradan başlar).
    solutions: bilinen (bütçe ya da serbest) çözümler — ν-problemlerinin üst sınırı ve μ ölçeklemesi için.
    ub_access_tl(): bütçe modunun güncel en iyi erişim değeri (hedef gap'te durmak için)."""
    from . import lagrange
    T0 = time.perf_counter()
    if inst.mode != "butce" or inst.budget is None:
        return BudgetLagResult(False, None, message="Yalnız bütçe modunda.")
    B = float(inst.budget)
    key = (inst.s.key, inst.nK, inst.nI)
    known: list[Solution] = list(solutions or [])
    pts: list[NuPoint] = []
    evals: dict[float, dict] = {}             # ln ν → {lb, mu, G, op}
    sols: list[Solution] = []
    s = inst.s
    cached = _NU_CACHE.get(key)
    nu0 = cached[0] if cached is not None else 1.0

    def acc_by_cell(sol: Solution | None) -> np.ndarray:
        if sol is None:
            cmin = np.full(inst.nI, np.inf)
            np.minimum.at(cmin, s.arc_i, inst.acc)
            return cmin
        return np.bincount(s.arc_i, inst.acc * sol.x, minlength=inst.nI)

    def left() -> float:
        return time_limit - (time.perf_counter() - T0)

    def evaluate_nu(x: float) -> dict | None:
        nu = math.exp(x)
        inst_nu = scaled_instance(inst, nu)
        cand = known + sols
        start = min(cand, key=lambda z: z.objective_tl(inst_nu)) if cand else None
        if evals:                                # en yakın değerlendirilmiş ν'den ölçeklenmiş μ
            xn = min(evals, key=lambda z: abs(z - x))
            mu0 = _rescale_mu(evals[xn]["mu"], acc_by_cell(start), nu / math.exp(xn))
            first = False
        else:
            mu0 = cached[1].copy() if cached is not None and abs(math.log(cached[0]) - x) < 1e-12 else None
            first = True
        slice_s = left() * FIRST_SHARE if first else max(MIN_SLICE_S, left() / 3.0)
        t = time.perf_counter()
        res = lagrange.run(inst_nu, start=start, time_limit=max(min(slice_s, left()), 0.05), target_gap=1e-6,
                           stop=stop, mu0_tl=mu0, use_cache=False, theta0=0.3 if mu0 is None else 0.1,
                           stall_tol=5e-5 if first else 1e-4, stall_min_s=1.0 if first else 0.4,
                           warmup_iters=100 if first else 40, **LH_LIGHT)
        if not res.ok or res.bound_tl is None or not math.isfinite(res.bound_tl) or res.mu is None:
            return None
        lbv = res.bound_tl - nu * B
        op = _sub_opening(inst, res.best_sub)
        if not math.isfinite(op) and res.solution is not None:
            op = res.solution.opening_tl(inst)
        pts.append(NuPoint(nu, res.bound_tl, lbv, op, res.iters, time.perf_counter() - t))
        if res.solution is not None and res.solution is not start:
            sols.append(res.solution)
        e = {"lb": lbv, "mu": res.mu, "G": res.G_min_tl, "op": op, "nu": nu}
        evals[x] = e
        if progress_cb is not None:
            try:
                progress_cb({"kind": "butce_lagrange", "nu": nu, "lb": lbv, "L": res.bound_tl, "opening_sub": op,
                             "t": round(time.perf_counter() - T0, 4)})
            except Exception:
                pass
        return e

    def done() -> bool:
        if stop is not None and stop.is_set():
            return True
        ub = ub_access_tl() if ub_access_tl is not None else None
        bl = max((e["lb"] for e in evals.values()), default=-math.inf)
        return ub is not None and ub > 0 and (ub - bl) / ub <= target_gap

    x = math.log(nu0)
    ex = evaluate_nu(x)
    h = math.log(NU_STEP)
    while ex is not None and left() > MIN_SLICE_S and not done() and h > math.log(1.0 + NU_TOL):
        d = 1.0 if (math.isfinite(ex["op"]) and ex["op"] > B) else -1.0
        side = [abs(z - x) for z in evals if (z - x) * d > 1e-12]
        step = min(h, 0.5 * min(side)) if side else h     # o yönde değerlendirilmiş nokta varsa en çok yarı yol
        y = min(max(x + d * step, math.log(NU_MIN)), math.log(NU_MAX))
        if abs(y - x) < 1e-12:
            break
        ey = evaluate_nu(y)
        if ey is None:
            break
        if ey["lb"] > ex["lb"]:
            x, ex = y, ey
            h = step * 1.2
        else:
            h = step * 0.5
    if not evals:
        return BudgetLagResult(False, None, points=pts, time_s=time.perf_counter() - T0,
                               message="Bütçe Lagrange sınırı hesaplanamadı.")
    xb = max(evals, key=lambda z: evals[z]["lb"])
    eb = evals[xb]
    if eb["mu"] is not None and np.isfinite(eb["mu"]).all():
        _NU_CACHE[key] = (eb["nu"], np.asarray(eb["mu"], dtype=float).copy())
        _NU_CACHE.move_to_end(key)
        while len(_NU_CACHE) > NU_CACHE_SIZE:
            _NU_CACHE.popitem(last=False)
    return BudgetLagResult(True, eb["lb"], eb["nu"], eb["mu"], eb["G"], sols, pts, time.perf_counter() - T0)
