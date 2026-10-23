"""Üst sınırı iyileştiren yerel aramalar: çekirdek LP yuvarlaması ve pencere (komşuluk) alt MILP'leri.

1) LP yuvarlaması (lp_round): çekirdek problemin (lagrange.core_instance) güçlü + sınıf başına bağlantılı LP gevşetmesi
   çözülür; z_j ≥ eşik olan adaylar açılır (kapsama onarımı; sayı sınırı varsa en büyük z'li adaylar), sınıflar LP'nin
   baskın y_jk'sından → kesin değerlendirme (lagrange.fast_eval). Faz 3 ölçümü (2 km, 60 sn akışındaki durum: Lagrange
   sezgiseli 382,03 milyar TL, alt sınır 376,0): çekirdek LP 0,9 sn, yuvarlama 0,1 sn → 378,90 (gap %0,8); 3 km'de
   386,67 → 382,91 (LP 1,4 sn). Çekirdek MIP aynı durumda 37 sn'de başlangıcını hiç iyileştiremedi (kök düğümde kesme
   turlarında kaldı).

2) Pencere araması (WindowSearch): bir merkezin çevresindeki en yakın WINDOW_K aday serbest bırakılır (açık/kapalı ve
   sınıf), geri kalan çözüm sabit tutulur ve küçük bir MILP kesin çözülür (HiGHS, süreç içi):
     * D: penceredeki hastanelere atanmış ya da merkeze yarıçap içinde olan talep hücreleri (atamaları serbest);
       D dışındaki hücrelerin atamaları sabit;
     * B (sınır): D'nin kullandığı ya da her D hücresinin en yakın m_border açık hastanesi olan pencere dışı açık
       hastaneler — açık kalmaya zorlanır, sınıf/yatak serbest; D dışından aldıkları sabit yük, yalnız o hastaneye
       bağlı sıfır maliyetli bir sahte talep hücresiyle modellenir;
     * sayı sınırları ve bütçe, pencere dışında sabit kalan hastaneler düşülerek alt probleme taşınır.
   Alt problemin her çözümü tam modelde olurludur (sabit kısım değişmez); iyileşme tam çözüme eklenir (splice).
   Ölçüm (2 km, LP yuvarlamasından sonra, pencere 20 aday, ≤ 1 sn): 378,90 → 378,39 (5 sn) → 378,17 (29 sn);
   pencere başına 0,05–1 sn.
"""
from __future__ import annotations

import dataclasses
import math
import time

import numpy as np

from .instance import Instance, Solution

WINDOW_K = 20             # pencerede serbest bırakılan en yakın aday sayısı
WINDOW_TIME = 1.0         # pencere alt MILP'inin süre sınırı (sn)
WINDOW_RANDOM_SHARE = 0.25  # merkezlerin bu payı rastgele aday (yeni hastane açma fırsatı); kalanı açık hastaneler
M_BORDER = 3              # her D hücresi için tutulan en yakın açık sınır hastanesi


# ---------------------------------------------------------------- LP yuvarlaması
def lp_round(inst: Instance, core: Instance, sel: np.ndarray, *, linking="strong", time_limit: float = 10.0,
             thresholds=(0.5, 0.4, 0.6), G_tl: np.ndarray | None = None, stop=None,
             eval_time: float = 2.0) -> tuple[list, dict]:
    """Çekirdek LP gevşetmesi + yuvarlama. (tam örnekte olurlu çözümler listesi, bilgi) döndürür."""
    from . import lagrange, milp
    t0 = time.perf_counter()
    info: dict = {"lp_s": None, "lp_bound_tl": None, "tries": []}
    m = milp.MilpModel(core, linking=linking)
    r = m.solve(relax=True, time_limit=max(time_limit, 0.1), cancel_event=stop,
                options={"solver": "simplex"})
    info["lp_s"] = time.perf_counter() - t0
    v = getattr(m, "last_lp_vector", None)
    if r.status != "Optimal" or v is None:
        info["status"] = r.status
        return [], info
    info["lp_bound_tl"] = r.primal_tl          # çekirdek LP sınırı (küresel DEĞİL; yalnız bilgi)
    lay = m.lay
    y = np.asarray(v[:lay.nJ * lay.nK]).reshape(lay.nJ, lay.nK)
    z = np.asarray(v[lay.z0:lay.z0 + lay.nJ])
    g = np.where(np.isfinite(G_tl), G_tl, np.inf) / 1e6 if G_tl is not None else np.zeros(inst.nJ)
    constrained = inst.count_min > 0 or inst.count_max is not None
    out = []
    seen = set()
    for thr in thresholds:
        if stop is not None and stop.is_set():
            break
        if constrained:
            n = int(round(float(z.sum())))
            n = max(n, inst.count_min)
            if inst.count_max is not None:
                n = min(n, inst.count_max)
            om = np.zeros(inst.nJ, dtype=bool)
            om[np.argsort(-z, kind="stable")[:n]] = True
            om |= inst.lock_open
        else:
            om = (z >= thr) | inst.lock_open
            om = lagrange.cover_repair(inst, om, g)
        om &= ~inst.lock_closed
        oj = np.flatnonzero(om)
        key = hash(oj.tobytes())
        if key in seen or not len(oj):
            continue
        seen.add(key)
        if not (oj.size >= inst.count_min and (inst.count_max is None or oj.size <= inst.count_max)):
            continue
        t = time.perf_counter()
        # sınıf seçimli MIP yedeği yalnız ilk eşikte (sabit sınıflı LP olurlu değilse; diğer eşikler yalnız LP, ≈0,1 sn)
        ev = lagrange.fast_eval(inst, oj, np.argmax(y[oj], axis=1), mip_fallback=not out and not info["tries"],
                                mip_time=eval_time)
        info["tries"].append({"thr": thr, "n": int(len(oj)), "ok": bool(ev.ok),
                              "objective_tl": ev.objective_tl if ev.ok else None, "s": time.perf_counter() - t})
        if ev.ok:
            ev.solution.source = "lp_yuvarlama"
            out.append(ev.solution)
    info["time_s"] = time.perf_counter() - t0
    return out, info


# ---------------------------------------------------------------- pencere alt problemi
@dataclasses.dataclass
class Window:
    sub: Instance
    start: Solution            # incumbent'ın alt problemdeki karşılığı
    cand: np.ndarray           # alt problem adayları → tam örnek aday konumları (W ∪ B)
    arcs: np.ndarray           # alt problemin gerçek yayları → tam örnek yay indisleri (sahte yaylar sonda)
    demand: np.ndarray         # serbest talep hücreleri (tam örnek konumları)
    keep_open: np.ndarray      # incumbent'ın açık kümesinde pencere dışında kalanlar (bool, open_j uzunluğunda)
    n_window: int
    n_border: int


def window(inst: Instance, sol: Solution, center_xy: np.ndarray, radius_m: float,
           m_border: int = M_BORDER) -> Window | None:
    s = inst.s
    if s.cand_xy is None or s.demand_xy is None:
        return None
    nJ, nI = inst.nJ, inst.nI
    om = np.zeros(nJ, dtype=bool)
    om[sol.open_j] = True
    W = (np.hypot(*(s.cand_xy - center_xy).T) <= radius_m) & ~inst.lock_closed & inst.y_ub.any(axis=1)
    if not W.any():
        return None
    x = sol.x
    used = x > 0
    Dm = np.zeros(nI, dtype=bool)
    Dm[s.arc_i[used & W[s.arc_j]]] = True
    Dm |= np.hypot(*(s.demand_xy - center_xy).T) <= radius_m
    inD = Dm[s.arc_i]
    B = np.zeros(nJ, dtype=bool)
    B[s.arc_j[used & inD]] = True                 # D'nin kullandığı (pencere içi olanlar aşağıda çıkarılır)
    a_open = np.flatnonzero(inD & (om & ~W)[s.arc_j])
    if len(a_open):
        ai = s.arc_i[a_open]
        first = np.r_[True, ai[1:] != ai[:-1]]
        start = np.flatnonzero(first)
        rank = np.arange(len(a_open)) - start[np.cumsum(first) - 1]
        B[s.arc_j[a_open[rank < m_border]]] = True
    B &= ~W & om
    C = W | B
    cidx = np.flatnonzero(C)
    cpos = np.full(nJ, -1, dtype=np.int64)
    cpos[cidx] = np.arange(len(cidx))
    didx = np.flatnonzero(Dm)
    dpos = np.full(nI, -1, dtype=np.int64)
    dpos[didx] = np.arange(len(didx))
    asel = np.flatnonzero(inD & C[s.arc_j])       # talep sırasına göre (CSR korunur)
    out_d = used & ~inD
    ext = np.bincount(s.arc_j[out_d], inst.q[s.arc_i[out_d]] * x[out_d], minlength=nJ)
    pj = np.flatnonzero(B & (ext > 1e-12))        # sınır hastanesinin sabit dış yükü → sahte talep hücresi
    nD, nP = len(didx), len(pj)
    arc_i = np.concatenate([dpos[s.arc_i[asel]], nD + np.arange(nP)]).astype(np.int32)
    arc_j = np.concatenate([cpos[s.arc_j[asel]], cpos[pj]]).astype(np.int32)
    counts = np.bincount(arc_i, minlength=nD + nP)
    if nD == 0 or (counts == 0).any():
        return None
    s2 = dataclasses.replace(
        s, key=f"{s.key}-pencere", demand_cells=np.arange(nD + nP), w=np.concatenate([s.w[didx], np.zeros(nP)]),
        cand_cells=s.cand_cells[cidx], cand_origin=s.cand_origin[cidx],
        arc_ptr=np.concatenate(([0], np.cumsum(counts))).astype(np.int64), arc_j=arc_j,
        arc_d=np.concatenate([s.arc_d[asel], np.zeros(nP)]), arc_i=arc_i, density=s.density[cidx],
        cand_xy=s.cand_xy[cidx], demand_xy=None, cand_ids=s.cand_ids[cidx], demand_ids=None)
    keep_open = ~C[sol.open_j]                    # pencere dışında sabit kalan açık hastaneler
    n_out = int(keep_open.sum())
    budget = None
    if inst.budget is not None:
        budget = inst.budget - float(inst.opening_cost(sol.open_j[keep_open], sol.cls[keep_open],
                                                        sol.beds[keep_open]).sum())
    sub = dataclasses.replace(
        inst, s=s2, q=np.concatenate([inst.q[didx], ext[pj]]), acc=np.concatenate([inst.acc[asel], np.zeros(nP)]),
        fixed=inst.fixed[cidx], var=inst.var[cidx], land_per_bed=inst.land_per_bed[cidx],
        land_price=inst.land_price[cidx], lock_open=inst.lock_open[cidx] | B[cidx], lock_closed=inst.lock_closed[cidx],
        count_min=max(0, inst.count_min - n_out),
        count_max=None if inst.count_max is None else inst.count_max - n_out, budget=budget, from_cache=False,
        warnings=[])
    inw = ~keep_open
    oj2 = cpos[sol.open_j[inw]]
    order = np.argsort(oj2)
    st = Solution(oj2[order], sol.cls[inw][order], sol.beds[inw][order],
                  np.concatenate([x[asel], np.ones(nP)]), source=sol.source)
    return Window(sub, st, cidx, asel, didx, keep_open, int(W.sum()), int(B.sum()))


def splice(inst: Instance, sol: Solution, win: Window, subsol: Solution) -> Solution:
    """Alt problemin çözümünü tam çözüme yerleştirir (pencere dışı sabit kısım aynen kalır)."""
    ko = win.keep_open
    oj = np.concatenate([sol.open_j[ko], win.cand[subsol.open_j]])
    cl = np.concatenate([sol.cls[ko], subsol.cls])
    bd = np.concatenate([sol.beds[ko], subsol.beds])
    o = np.argsort(oj)
    x = sol.x.copy()
    inD = np.zeros(inst.nI, dtype=bool)
    inD[win.demand] = True
    x[inD[inst.s.arc_i]] = 0.0
    x[win.arcs] = subsol.x[:len(win.arcs)]
    return Solution(oj[o], cl[o], bd[o], x, source="pencere")


class WindowSearch:
    """Pencere araması durumu: merkez sırası, çözülmüş (değişmemiş) pencerelerin atlanması, sayaçlar."""

    def __init__(self, inst: Instance, *, k: int = WINDOW_K, seed: int = 0, linking="strong") -> None:
        self.inst = inst
        self.k = int(k)
        self.rng = np.random.default_rng(seed)
        self.linking = linking
        self.order: list[int] = []
        self.solved: set = set()                  # (merkez, pencere içi açık küme) → kesin çözüldü, iyileşme yok
        self.stats = {"windows": 0, "improved": 0, "skipped": 0, "time_s": 0.0, "gain_tl": 0.0, "timeouts": 0}
        self.exhausted = False                    # bir tur boyunca hiçbir pencere iyileşme vermedi ve hepsi kesin
        self._round_improved = False
        self._round_open = 0

    def _next_center(self, sol: Solution) -> int:
        if not self.order:
            if self._round_open and not self._round_improved and self.stats["timeouts"] == 0:
                self.exhausted = True
            self._round_improved = False
            self.stats["timeouts"] = 0
            js = sol.open_j.copy()
            self.rng.shuffle(js)
            n_rand = int(round(WINDOW_RANDOM_SHARE * len(js)))
            cand = np.flatnonzero(~self.inst.lock_closed & self.inst.y_ub.any(axis=1))
            extra = self.rng.choice(cand, size=min(n_rand, len(cand)), replace=False) if len(cand) else []
            self.order = [int(j) for j in js] + [int(j) for j in extra]
            self.rng.shuffle(self.order)
            self._round_open = len(self.order)
        return self.order.pop()

    def step(self, sol: Solution, *, time_limit: float = WINDOW_TIME, stop=None) -> tuple[Solution | None, dict]:
        """Bir pencere çözer; tam modelde olurlu ve daha iyi bir çözüm bulursa döndürür (denetim çağırana aittir)."""
        from . import milp
        inst = self.inst
        s = inst.s
        t0 = time.perf_counter()
        c = self._next_center(sol)
        d = np.sort(np.hypot(*(s.cand_xy - s.cand_xy[c]).T))
        r = float(d[min(self.k, len(d) - 1)]) + 1.0
        win = window(inst, sol, s.cand_xy[c], r)
        info = {"center": c, "t": 0.0}
        if win is None:
            self.stats["skipped"] += 1
            return None, info
        key = (c, hash(win.start.open_j.tobytes()), hash(win.start.cls.tobytes()), hash(win.start.x.tobytes()),
               len(win.demand))
        if key in self.solved:
            self.stats["skipped"] += 1
            return None, info
        m = milp.MilpModel(win.sub, linking=self.linking)
        obj_in = m.objective_of(win.start)
        res = m.solve(time_limit=max(time_limit, 0.05), gap=1e-6, start=win.start, cancel_event=stop)
        self.stats["windows"] += 1
        info.update({"W": win.n_window, "B": win.n_border, "D": int(len(win.demand)), "status": res.status})
        out = None
        if res.status != "Optimal":
            self.stats["timeouts"] += 1
        if res.solution is not None and res.primal_tl is not None and res.primal_tl < obj_in - max(1e-9 * abs(obj_in), 1.0):
            out = splice(inst, sol, win, res.solution)
            info["gain_tl"] = obj_in - res.primal_tl
        elif res.status == "Optimal":
            self.solved.add(key)
        dt = time.perf_counter() - t0
        self.stats["time_s"] += dt
        info["t"] = dt
        return out, info

    def accepted(self, gain_tl: float) -> None:
        self.stats["improved"] += 1
        self.stats["gain_tl"] += gain_tl
        self._round_improved = True
        self.exhausted = False
