"""Bütçe modu (butce): bütçe satırının Lagrange gevşetmesiyle alt sınır ve parametrik (ν) serbest çözümler.

Bütçe modu:  z_B = min erişim(x)   s.t.  açılış(x) ≤ B  ve diğer tüm kısıtlar (atama, kapasite, sınıf, R, kilit, sayı).
Bütçe satırı ν ≥ 0 çarpanıyla gevşetilirse
    φ(ν) = min { erişim(x) + ν·açılış(x) }   (diğer kısıtlar)
açılış maliyetleri ν ile ölçeklenmiş SERBEST (toplam) problemdir ve her ν ≥ 0 için φ(ν) − ν·B ≤ z_B (zayıf dualite:
bütçe optimumu x* için erişim(x*) ≥ φ(ν) − ν·açılış(x*) ≥ φ(ν) − ν·B). φ(ν) yerine onun geçerli bir alt sınırı —
ölçeklenmiş örnekte atama satırlarının Lagrange sınırı L_ν ≤ φ(ν) (lagrange.run; BOUND_SAFETY payı dahil) — kullanılır:
    LB(ν) = L_ν − ν·B.
Bu doğrudan ERİŞİM maliyetinin alt sınırıdır; ε·açılış eşitlik bozucusu (milp.BUDGET_EPS) bu sınıra girmez. MIP
kaynaklı sınırlar modelin amacı (erişim + ε·açılış) içindir ve solve() onlardan ε·B düşer; burada gerek yok, çünkü
LB(ν) ≤ z_B ≤ erişim(bulunan çözüm) ≤ modelin amacı.

ν araması (x = log ν üzerinde): g(ν) = max_μ L(μ; ν) − ν·B içbükeydir (ortak içbükey fonksiyonun kısmi en büyüğü), ama
her değer sınırlı sürede yakınsatılmış μ ile tahmin edilir (tahmin ≤ g). Alt problemin açılış maliyeti (dg/dν'nin alt
gradyan tahmini) yakınsamamış μ'de güvenilir değil (Faz 3 doğrulaması: 3 km'de ν = 1'de alt problemin açılışı B'yi
aştığı için eski arama yalnız yukarı gitti, oysa en iyi ν < 1 idi). Bu yüzden yön yalnız sınır değerlerinden seçilir:
  (1) ilk ν (aynı yapının önbellekteki en iyi ν'si, yoksa 1 = serbest problem) uzun dilimle;
  (2) İKİ yönde birer deneme, ν·e^{±h} (ikisi aynı μ'den aynı süreyle başlar → aralarında adil karşılaştırma); iyi
      taraf merkezden de iyiyse (merkeze "kararsız" kaybederse — bkz. (3) — bir dilim daha alarak) o yönde adım
      ×EXPAND büyütülerek değer düşene dek gidilir (köşeli ayraç kurulur);
  (3) köşeli ayraç içinde altın oran araması: genişlik NU_TOL_LOG'a inene ya da deneme süresi bitene dek. Sınırlı
      süreli sıcak deneme yakınsamış en iyi noktaya karşı haksızdır: deneme "kararsız" kaybederse (fark, kendi
      diliminin ikinci yarısındaki artışın SLACK_MULT katından küçük) bir dilim daha alır; yine kararsızsa arama durur
      (daha dar ayraçta fark daha da küçülür) ve süre cilaya kalır;
  (4) kalan süre (en az POLISH_SHARE payı) en iyi ν'de μ'nün yakınsatılmasına gider ("cila"): az sayıda ν, her birine
      uzun süre.
Her yeni ν en yakın değerlendirilmiş ν'nin μ'sünden ölçeklenerek sıcak başlar (erişim payı aynı, açılış payı ν oranında);
aynı ν'ye dönülürse onun μ'sünden sürer. Raporlanan sınır değerlendirilen tüm (ν, μ) çiftlerinin en büyüğüdür; her biri
geçerli olduğundan arama hatası yalnız sınırın gücünü etkiler. Aynı yapı için en iyi ν, μ, son adım ve parametre/bütçe
imzası saklanır: solve() aramayı A evresinde başlatır, iyileştirme evresinde arka plan iş parçacığında aynı noktadan
sürdürür (imza aynıysa adım son köşeli ayraçtan, değilse H0'dan).
Yan ürün: her ν'deki serbest problemin Lagrange sezgiseli çözümleri; açılışı B'ye sığanlar bütçe modunda olurludur.
Ölçümler (5 / 3 km, varsayılan bütçe): data/processed/solver_benchmark.json "butce" anahtarı (pipeline/31_benchmark.py
--butce); doğruluk ve arama testi: tests/test_phase3_lagrange.py (oyuncaklarda kaba kuvvet ve yoğun ν ızgarası).
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
_NU_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
NU_MIN, NU_MAX = 1e-3, 1e3
H0 = math.log(2.0)            # ilk deneme adımı (log ν)
H_MIN = math.log(1.05)        # sürdürmede en küçük deneme adımı
NU_TOL_LOG = math.log(1.05)   # köşeli ayraç bundan darsa altın oran araması durur (ayrıca: kararsız deneme)
EXPAND = 1.618                # genişletme çarpanı
GOLD = 0.381966               # altın oran noktası (1 − 1/φ)
FIRST_SHARE = 0.35            # soğuk ilk ν'nin süre payı
FIRST_WARM_SHARE = 0.2        # önbellekten sürdürülen ilk ν'nin süre payı
PROBE_SHARE = 0.12            # deneme ν'si başına süre payı
POLISH_SHARE = 0.3            # sonda en iyi ν'ye ayrılan en az süre payı
MIN_SLICE_S = 0.4             # deneme dilimi en az (sn)
SLACK_MULT = 3.0              # kararsızlık payı = SLACK_MULT × dilimin ikinci yarısındaki artış (3 km geliştirme
                              # izlerinde yakınsamaya kalan artış ikinci yarıdaki artıştan büyüktü: sıcak deneme
                              # alt doğrusal yakınsıyor; oyuncaklarda dilim dolmadan yakınsar → pay 0)
# ν-problemlerinde Lagrange sezgiseli hafif (sabit sınıflı LP + onarım): üst sınırı çekirdek MIP / pencereler verir
LH_LIGHT = {"class_mip_share": 0.0, "local_search": False}
# Lagrange durma ölçütleri (stall_tol, stall_min_s, warmup_iters): soğuk ilk ν / deneme / cila
_STALL = {"ilk": (5e-5, 1.0, 100), "deneme": (1e-4, 0.4, 40), "cila": (2e-5, 1.0, 40)}


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
    phase: str = ""             # ilk | deneme | cila

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class BudgetLagResult:
    ok: bool
    lb_tl: float | None                    # erişim maliyeti için geçerli alt sınır (TL)
    nu: float | None = None                # en iyi alt sınırı veren ν
    mu_tl: np.ndarray | None = None        # o ν'deki çarpanlar (TL / talep hücresi)
    G_min_tl: np.ndarray | None = None     # çekirdek aday seçimi için adayların açılış indirgenmiş maliyetleri
                                           # (lagrange.core_instance / lns.lp_round): değerlendirilen ν'lerden 1'e
                                           # (serbest probleme) en yakın olanınki — bkz. nu_core
    solutions: list = field(default_factory=list)   # ν-problemlerinin çözümleri (serbest problemde olurlu)
    points: list = field(default_factory=list)
    time_s: float = 0.0
    message: str = ""
    bracket: tuple | None = None           # son köşeli ayraç (ν_alt, ν_üst) ya da None
    nu_core: float | None = None           # G_min_tl'nin ν'si


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
    """ν araması (modül açıklaması: ilk ν → iki yönlü deneme → köşeli ayraç → altın oran → en iyi ν'de cila); her ν'de
    μ hacim algoritmasıyla yakınsatılır (lagrange.run), LB(ν) = L_ν − ν·B. Aynı yapının önbellekteki ν/μ'sünden
    sürdürür; sonunda en iyi ν, μ ve adımı saklar.
    solutions: bilinen (bütçe ya da serbest) çözümler — ν-problemlerinin üst sınırı ve μ ölçeklemesi için.
    ub_access_tl(): bütçe modunun güncel en iyi erişim değeri (hedef gap'te durmak için)."""
    from . import lagrange
    T0 = time.perf_counter()
    if inst.mode != "butce" or inst.budget is None:
        return BudgetLagResult(False, None, message="Yalnız bütçe modunda.")
    B = float(inst.budget)
    T = float(time_limit)
    key = (inst.s.key, inst.nK, inst.nI)
    sig = (inst.params.hash(), B)
    known: list[Solution] = [z for z in (solutions or []) if z is not None]
    pts: list[NuPoint] = []
    recs: list[dict] = []                    # değerlendirilmiş ν'ler: x, nu, lb, L, mu, G, op
    sols: list[Solution] = []
    s = inst.s
    x_lo, x_hi = math.log(NU_MIN), math.log(NU_MAX)
    cached = _NU_CACHE.get(key)
    lo = hi = None                               # köşeli ayraç (log ν)
    if cached is None:
        x0, h = 0.0, H0                          # ν = 1: serbest problemin kendisi
    elif cached["sig"] == sig:                   # aynı parametre ve bütçe: önceki aramanın kaldığı yerden
        x0, h = math.log(cached["nu"]), max(cached["h"], H_MIN)
        if cached["bracket"] is not None:
            lo, hi = cached["bracket"]
    else:                                        # parametre/bütçe değişti: önceki en iyi ν'den yeni arama
        x0, h = math.log(cached["nu"]), H0
    x0 = min(max(x0, x_lo), x_hi)

    def acc_by_cell(sol: Solution | None) -> np.ndarray:
        if sol is None:
            cmin = np.full(inst.nI, np.inf)
            np.minimum.at(cmin, s.arc_i, inst.acc)
            return cmin
        return np.bincount(s.arc_i, inst.acc * sol.x, minlength=inst.nI)

    def left() -> float:
        return T - (time.perf_counter() - T0)

    def find(x: float) -> dict | None:
        return next((r for r in recs if abs(r["x"] - x) < 1e-9), None)

    def best_rec() -> dict:
        return max(recs, key=lambda r: r["lb"])

    def done() -> bool:
        if stop is not None and stop.is_set():
            return True
        ub = ub_access_tl() if ub_access_tl is not None else None
        bl = max((r["lb"] for r in recs), default=-math.inf)
        return ub is not None and ub > 0 and (ub - bl) / ub <= target_gap

    def probe_slice() -> float:
        """Deneme dilimi; cila payına dokunmaz."""
        return min(max(MIN_SLICE_S, PROBE_SHARE * T), left() - POLISH_SHARE * T)

    def probe_ok() -> bool:
        """Cila payı düşüldükten sonra en az MIN_SLICE_S'lik bir deneme dilimi kalıyor mu."""
        return probe_slice() >= MIN_SLICE_S and not done()

    def evaluate_nu(x: float, slice_s: float, phase: str) -> dict | None:
        x = min(max(x, x_lo), x_hi)
        nu = math.exp(x)
        inst_nu = scaled_instance(inst, nu)
        cand = known + sols
        start = min(cand, key=lambda z: z.objective_tl(inst_nu)) if cand else None
        own = find(x)
        if own is not None:                      # aynı ν: kendi μ'sünden sürer
            mu0 = own["mu"]
        elif recs:                               # en yakın değerlendirilmiş ν'den ölçeklenmiş μ
            near = min(recs, key=lambda r: abs(r["x"] - x))
            mu0 = _rescale_mu(near["mu"], acc_by_cell(start), nu / near["nu"])
        elif cached is not None:                 # önbellekteki ν'den ölçeklenmiş μ
            mu0 = _rescale_mu(cached["mu"], acc_by_cell(start), nu / cached["nu"])
        else:
            mu0 = None
        # soğuk başlangıç → "ilk" ölçütleri; önbellekten sürdürülen ilk ν → "deneme" ölçütleri
        st_tol, st_min, warm = _STALL["ilk" if mu0 is None else ("deneme" if phase == "ilk" else phase)]
        t = time.perf_counter()
        tl_run = max(min(slice_s, left()), 0.05)
        res = lagrange.run(inst_nu, start=start, time_limit=tl_run, target_gap=1e-6,
                           stop=stop, mu0_tl=mu0, use_cache=False, theta0=0.3 if mu0 is None else 0.1,
                           stall_tol=st_tol, stall_min_s=st_min, warmup_iters=warm,
                           report_every_s=min(0.25, max(0.02, tl_run / 10)), **LH_LIGHT)
        if not res.ok or res.bound_tl is None or not math.isfinite(res.bound_tl) or res.mu is None:
            return own
        lbv = res.bound_tl - nu * B
        op = math.nan
        if res.solution is not None and res.solution is not start:
            op = res.solution.opening_tl(inst)   # birincil çözümün açılışı (yön sırası için; yalnız ipucu)
            sols.append(res.solution)
        if not math.isfinite(op):
            op = _sub_opening(inst, res.best_sub)
        pts.append(NuPoint(nu, res.bound_tl, lbv, op, res.iters, time.perf_counter() - t, phase))
        # kararsızlık payı: dilimin ikinci yarısındaki artış × SLACK_MULT (değerin hâlâ ne kadar yükselebileceğinin kaba
        # ölçüsü); dilim dolmadan biten çalıştırma (yakınsadı / durdu / θ tükendi) için 0
        tr = [(e["t"], e["lb"]) for e in res.trace if e.get("lb") is not None]
        if res.time_s < 0.9 * tl_run and not (stop is not None and stop.is_set()):
            slack = 0.0
        elif tr:
            mid = [v for tt, v in tr if tt <= 0.5 * tr[-1][0]]
            slack = SLACK_MULT * max(res.bound_tl - (mid[-1] if mid else tr[0][1]), 0.0)
        else:
            slack = math.inf
        rec = {"x": x, "nu": nu, "lb": lbv, "L": res.bound_tl, "mu": res.mu, "G": res.G_min_tl, "op": op,
               "slack": slack}
        if own is not None:
            if lbv >= own["lb"]:
                own.update(rec)
            rec = own
        else:
            recs.append(rec)
        if progress_cb is not None:
            try:
                progress_cb({"kind": "butce_lagrange", "nu": nu, "lb": max(r["lb"] for r in recs), "lb_nu": lbv,
                             "L": res.bound_tl, "opening_sub": op, "adim": phase,
                             "t": round(time.perf_counter() - T0, 4)})
            except Exception:
                pass
        return rec

    # (1) ilk ν
    e0 = evaluate_nu(x0, (FIRST_SHARE if cached is None else FIRST_WARM_SHARE) * T, "ilk")
    if e0 is None:
        lo = hi = None
    elif lo is not None and lo - 1e-9 <= e0["x"] <= hi + 1e-9:
        b = e0["x"]                              # sürdürme: önceki köşeli ayraçta altın oran aramasına devam
    else:
        lo = hi = None
        b = e0["x"]
        # (2) iki yönlü deneme: önce alt gradyan ipucunun yönü (süre ancak bir denemeye yetiyorsa o yapılır)
        d = 1.0 if (math.isfinite(e0["op"]) and e0["op"] > B) else -1.0
        tried = []
        for sgn in (d, -d):
            if not probe_ok():
                break
            xp = min(max(b + sgn * h, x_lo), x_hi)
            if abs(xp - b) < 1e-9:
                continue
            ep = evaluate_nu(xp, probe_slice(), "deneme")
            if ep is not None:
                tried.append(ep)
        side = max(tried, key=lambda r: r["lb"]) if tried else None
        if (side is not None and side["lb"] <= find(b)["lb"] and find(b)["lb"] - side["lb"] <= side["slack"]
                and probe_ok()):
            side = evaluate_nu(side["x"], probe_slice(), "deneme") or side   # iyi taraf kararsız: bir dilim daha
        if side is not None and side["lb"] > find(b)["lb"]:
            # iyi tarafa genişlet: değer düşene dek adım ×EXPAND
            sgn = 1.0 if side["x"] > b else -1.0
            prev, cur, step = b, side["x"], h
            while probe_ok():
                step *= EXPAND
                nxt = min(max(cur + sgn * step, x_lo), x_hi)
                if abs(nxt - cur) < 1e-9:        # ν sınırında
                    lo, hi = sorted((prev, cur))
                    break
                en = evaluate_nu(nxt, probe_slice(), "deneme")
                if en is None:
                    break
                if en["lb"] > find(cur)["lb"]:
                    prev, cur = cur, nxt
                else:
                    lo, hi = sorted((prev, nxt))
                    break
            b = cur
        elif len(tried) == 2:                    # merkez iki yandan da iyi
            lo, hi = sorted(r["x"] for r in tried)
    if e0 is not None:
        # (3) altın oran araması (b köşeli ayraçtaki en iyi nokta)
        while lo is not None and hi - lo > NU_TOL_LOG and probe_ok():
            xt = b + GOLD * (hi - b) if hi - b >= b - lo else b - GOLD * (b - lo)
            et = evaluate_nu(xt, probe_slice(), "deneme")
            if et is None:
                break
            if et["lb"] <= find(b)["lb"] and find(b)["lb"] - et["lb"] <= et["slack"] and probe_ok():
                et = evaluate_nu(xt, probe_slice(), "deneme") or et     # kararsız: bir dilim daha (kendi μ'sünden)
            if et["lb"] > find(b)["lb"]:
                lo, hi = (b, hi) if xt > b else (lo, b)
                b = xt
            else:
                lo, hi = (lo, xt) if xt > b else (xt, hi)
                if find(b)["lb"] - et["lb"] <= et["slack"]:
                    break                        # uzatmadan sonra da kararsız: süre cilaya kalır
        # (4) cila: kalan süre en iyi ν'de
        if left() > 0.1 and not done():
            evaluate_nu(best_rec()["x"], left(), "cila")
    if not recs:
        return BudgetLagResult(False, None, points=pts, time_s=time.perf_counter() - T0,
                               message="Bütçe Lagrange sınırı hesaplanamadı.")
    eb = best_rec()
    h_next = max((hi - lo) / 2.0, H_MIN) if lo is not None else h
    if eb["mu"] is not None and np.isfinite(eb["mu"]).all():
        br = (lo, hi) if lo is not None and lo - 1e-9 <= eb["x"] <= hi + 1e-9 else None
        _NU_CACHE[key] = {"nu": eb["nu"], "mu": np.asarray(eb["mu"], dtype=float).copy(), "h": h_next, "sig": sig,
                          "bracket": br}
        _NU_CACHE.move_to_end(key)
        while len(_NU_CACHE) > NU_CACHE_SIZE:
            _NU_CACHE.popitem(last=False)
    # çekirdek adayları en iyi ν'nin değil, 1'e en yakın ν'nin G'siyle: bağlayıcı bütçede bütçe optimumu serbest
    # optimuma yakın bir ağdır (açılış ≈ B); en iyi ν < 1 iken (açılış ucuz) G çok daha yoğun bir ağı öne çıkarıyor.
    # Geliştirme ölçümü (3 km, 15 sn): en iyi ν'nin (≈0,5) G'siyle LP yuvarlaması ve çekirdek MIP pencere aramasının
    # çözümünü hiç iyileştirmedi, ν = 1'in G'siyle çoğu çalıştırmada iyileştirdi; 5 km'de (en iyi ν > 1) fark yoktu.
    ec = min((r for r in recs if r["G"] is not None), key=lambda r: abs(r["x"]), default=eb)
    return BudgetLagResult(True, eb["lb"], eb["nu"], eb["mu"], ec["G"], sols, pts, time.perf_counter() - T0,
                           bracket=(math.exp(lo), math.exp(hi)) if lo is not None else None, nu_core=ec["nu"])
