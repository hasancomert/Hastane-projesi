"""Hızlı sezgisel: açgözlü ekleme + yerel arama (aç / kapat / takas) → olurlu çözüm (MIP başlangıcı ve yedek çözüm).

Yaklaşık maliyet modeli (vektörel, numpy):
  * her talep hücresi en yakın AÇIK adaya atanır (yaylar satır içinde yol mesafesine göre sıralı; α > 0 olduğundan en
    yakın = en ucuz erişim);
  * hastane maliyeti h_j(b) = min_k { Sabit_jk + Değişken_jk · max(b, L_k) : b ≤ U_k } (b = atanan yatak talebi);
    hiçbir sınıfa sığmazsa büyük bir taşma cezası eklenir;
  * R içinde açık hastanesi olmayan talep hücresi için büyük ceza (yalnız kurulumun başında).
Yerel arama bittiğinde açık küme evaluate.evaluate ile KESİN olarak değerlendirilir (sınıf seçimi + kapasiteli atama
LP/MIP'i) → kısıtları sağlayan tam çözüm. Modlar: toplam (açılış + erişim), butce (erişim + ε·açılış, açılış ≤ bütçe),
sabit_p / en az–en çok sayısı, kilitler.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .evaluate import EvalResult, evaluate
from .instance import Instance, Solution
from .milp import BUDGET_EPS

TOL = 1e-6          # TL cinsinden göreli iyileşme eşiği (toplam maliyete göre ölçeklenir)


@dataclass
class HeuristicResult:
    ok: bool
    solution: Solution | None
    objective_tl: float | None
    approx_objective_tl: float | None
    time_s: float
    construct_s: float = 0.0
    local_s: float = 0.0
    eval_s: float = 0.0
    moves: dict = field(default_factory=dict)
    message: str = ""
    eval: EvalResult | None = None


class _State:
    __slots__ = ("open", "c", "arc", "cur", "b", "h", "hk", "acc_total", "open_total", "unassigned")


class ApproxModel:
    """Yaklaşık maliyet modeli ve hamle değerlendirmeleri."""

    def __init__(self, inst: Instance) -> None:
        s = inst.s
        self.inst = inst
        self.nI, self.nJ, self.nA = inst.nI, inst.nJ, inst.nA
        self.arc_i = s.arc_i.astype(np.int64)
        self.arc_j = s.arc_j.astype(np.int64)
        self.ptr = s.arc_ptr
        self.acc = inst.acc
        self.q = inst.q
        allowed = inst.y_ub
        self.allowed = allowed
        self.F = np.where(allowed, inst.fixed, np.inf)
        self.V = np.where(allowed, inst.var, np.inf)
        self.L, self.U = inst.L, inst.U
        Ucap = np.where(allowed, inst.U[None, :], -np.inf)
        self.kmax = np.argmax(Ucap, axis=1)
        self.Umax = Ucap.max(axis=1)
        vmax = float(inst.var[allowed].max()) if allowed.any() else 0.0
        # σ: bir yatağın talebini (1 / kişi başı yatak kişi) bir hücre öteye taşımanın erişim maliyeti (TL / yatak)
        p = inst.params
        self.sigma = p.lam * (1.0 / max(p.beds_per_person, 1e-12)) * float(s.cell_km) ** p.alpha
        # atanmamış talep cezası (hücre başına sabit, en pahalı hastanenin 10 katı): kapsama her şeyden önce gelir
        fmax = float(inst.fixed[allowed].max()) if allowed.any() else 0.0
        self.pen = np.full(self.nI, 10.0 * (fmax + vmax * float(inst.U.max())) + float(inst.acc.max()) + 1.0)
        self.openable = ~inst.lock_closed & allowed.any(axis=1)
        self.w_open = BUDGET_EPS if inst.mode == "butce" else 1.0

    # ------------------------------------------------ hastane maliyeti
    def hcost(self, j: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Hastane maliyeti h_j(b) ve sınıfı. Sınıf üst sınırını aşan talep "yumuşak taşma" sayılır: aşan her yatak
        Değişken_k + σ'ya mal olur (σ ≈ talebin bir hücre ötedeki komşu hastaneye kaydırılmasının erişim maliyeti).
        Kesin değerlendirmede (evaluate) bu talep gerçekten komşulara bölünür."""
        j = np.asarray(j, dtype=np.int64)
        b = np.asarray(b, dtype=float)
        if j.size == 0:
            return np.zeros(0), np.zeros(0, dtype=np.int64)
        bb = b[:, None]
        U = self.U[None, :]
        ok = self.allowed[j]
        F = np.where(ok, self.F[j], 0.0)
        V = np.where(ok, self.V[j], 0.0)
        within = F + V * np.maximum(bb, self.L[None, :])
        over = F + V * U + (V + self.sigma) * (bb - U)
        cost = np.where(ok, np.where(bb <= U + 1e-9, within, over), np.inf)
        k = np.argmin(cost, axis=1)
        return cost[np.arange(len(j)), k], k

    # ------------------------------------------------ atama
    def first_open(self, open_mask: np.ndarray, skip: np.ndarray | None = None) -> np.ndarray:
        """Her satırda en yakın açık yayın indisi (yoksa −1); skip verilirse o yaylar atlanır."""
        m = open_mask[self.arc_j]
        if skip is not None:
            m = m.copy()
            m[skip[skip >= 0]] = False
        idx = np.flatnonzero(m)
        out = np.full(self.nI, -1, dtype=np.int64)
        if len(idx):
            rows = self.arc_i[idx]
            first = np.ones(len(idx), dtype=bool)
            first[1:] = rows[1:] != rows[:-1]
            out[rows[first]] = idx[first]
        return out

    def state(self, open_mask: np.ndarray) -> _State:
        st = _State()
        st.open = open_mask.copy()
        st.arc = self.first_open(open_mask)
        has = st.arc >= 0
        st.c = np.where(has, self.arc_j[np.maximum(st.arc, 0)], -1)
        st.cur = np.where(has, self.acc[np.maximum(st.arc, 0)], self.pen)
        st.unassigned = int((~has).sum())
        st.b = np.bincount(st.c[has], self.q[has], minlength=self.nJ)
        oj = np.flatnonzero(open_mask)
        st.h = np.zeros(self.nJ)
        st.hk = np.full(self.nJ, -1, dtype=np.int64)
        if len(oj):
            st.h[oj], st.hk[oj] = self.hcost(oj, st.b[oj])
        st.acc_total = float(st.cur.sum())
        st.open_total = float(st.h.sum())
        return st

    def objective(self, st: _State) -> float:
        return st.acc_total + self.w_open * st.open_total

    # ------------------------------------------------ hamleler
    def eval_add(self, st: _State, allowed_new: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Her kapalı aday için açılırsa (Δerişim, Δaçılış); geçersizlerde +inf."""
        nJ = self.nJ
        dacc = np.zeros(nJ)
        dopen = np.full(nJ, np.inf)
        cand = allowed_new & ~st.open
        if not cand.any():
            return np.full(nJ, np.inf), dopen
        # satır içinde yaylar (mesafe, aday) sırasında: yeni aday, mevcut atamadan ÖNCE geliyorsa talep ona geçer
        # (state() ile aynı eşitlik kuralı; düzenli ızgarada eşit mesafeler çoktur)
        cur_arc = st.arc[self.arc_i]
        m = cand[self.arc_j] & ((cur_arc < 0) | (np.arange(self.nA) < cur_arc))
        a = np.flatnonzero(m)
        i = self.arc_i[a]
        j = self.arc_j[a]
        qa = self.q[i]
        gain = st.cur[i] - self.acc[a]
        dacc -= np.bincount(j, gain, minlength=nJ)
        bnew = np.bincount(j, qa, minlength=nJ)
        cj = np.flatnonzero(cand)
        hnew, _ = self.hcost(cj, bnew[cj])
        dopen[cj] = hnew
        old = st.c[i]
        has = old >= 0
        if has.any():
            key = j[has] * nJ + old[has]
            uniq, inv = np.unique(key, return_inverse=True)
            mq = np.bincount(inv, qa[has])
            jj, cc = np.divmod(uniq, nJ)
            hc, _ = self.hcost(cc, np.maximum(st.b[cc] - mq, 0.0))
            dopen += np.bincount(jj, hc - st.h[cc], minlength=nJ)
        dacc[~cand] = np.inf
        return dacc, dopen

    def eval_drop(self, st: _State, droppable: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Her açık aday kapatılırsa (Δerişim, Δaçılış); talebi ikinci en yakın açık adaya gider."""
        nJ = self.nJ
        sec = self.first_open(st.open, skip=st.arc)
        assigned = st.c >= 0
        i = np.flatnonzero(assigned)
        j = st.c[i]
        s2 = sec[i]
        dacc = np.full(nJ, np.inf)
        dopen = np.full(nJ, np.inf)
        oj = np.flatnonzero(st.open & droppable)
        if not len(oj):
            return dacc, dopen
        bad = np.bincount(j[s2 < 0], minlength=nJ) > 0           # ikinci seçeneği olmayan talep → olurlu değil
        ok = s2 >= 0
        i, j, s2 = i[ok], j[ok], s2[ok]
        da = np.bincount(j, self.acc[s2] - st.cur[i], minlength=nJ)
        r = self.arc_j[s2]
        key = j * nJ + r
        uniq, inv = np.unique(key, return_inverse=True)
        mq = np.bincount(inv, self.q[i])
        jj, rr = np.divmod(uniq, nJ)
        hr, _ = self.hcost(rr, st.b[rr] + mq)
        do = np.bincount(jj, hr - st.h[rr], minlength=nJ) - st.h
        valid = st.open & droppable & ~bad
        dacc[valid] = da[valid]
        dopen[valid] = do[valid]
        return dacc, dopen


def _budget_ok(model: ApproxModel, st: _State, dopen: np.ndarray) -> np.ndarray:
    b = model.inst.budget
    if b is None:
        return np.ones_like(dopen, dtype=bool)
    return st.open_total + dopen <= b


def greedy_local(inst: Instance, *, time_budget: float = 5.0, start_open: np.ndarray | None = None,
                 swap: bool = True, stop=None) -> tuple[np.ndarray, dict]:
    """Yaklaşık modelde açgözlü ekleme + yerel arama; açık maske ve hamle sayaçları döndürür."""
    t0 = time.perf_counter()
    M = ApproxModel(inst)
    cmin, cmax = inst.count_min, inst.count_max
    cmax_eff = cmax if cmax is not None else inst.nJ
    locked = inst.lock_open
    moves = {"add": 0, "drop": 0, "swap": 0, "construct_add": 0, "sweeps": 0}
    open_mask = locked.copy() if start_open is None else (start_open | locked) & (M.openable | locked)
    st = M.state(open_mask)

    # ---- kurulum: kapsanmamış talep varken, sayı alt sınırının altındayken ya da ekleme kazançlıyken ekle
    while int(st.open.sum()) < cmax_eff:
        dacc, dopen = M.eval_add(st, M.openable)
        delta = dacc + M.w_open * dopen
        feas = np.isfinite(delta) & _budget_ok(M, st, dopen)
        if not feas.any():
            break
        must = st.unassigned > 0 or int(st.open.sum()) < cmin
        j = int(np.argmin(np.where(feas, delta, np.inf)))
        if not must and delta[j] >= -1.0:
            break
        st.open[j] = True
        st = M.state(st.open)
        moves["construct_add"] += 1
    construct_s = time.perf_counter() - t0
    tol = TOL * max(abs(M.objective(st)), 1.0)

    # ---- yerel arama
    def try_add_drop() -> bool:
        nonlocal st
        n = int(st.open.sum())
        best = (0.0, None, None)
        if n < cmax_eff:
            dacc, dopen = M.eval_add(st, M.openable)
            d = np.where(_budget_ok(M, st, dopen), dacc + M.w_open * dopen, np.inf)
            j = int(np.argmin(d))
            if d[j] < best[0] - tol:
                best = (float(d[j]), "add", j)
        if n > max(cmin, 1):
            dacc, dopen = M.eval_drop(st, ~locked)
            d = np.where(_budget_ok(M, st, dopen), dacc + M.w_open * dopen, np.inf)
            j = int(np.argmin(d))
            if d[j] < best[0] - tol:
                best = (float(d[j]), "drop", j)
        if best[1] is None:
            return False
        st.open[best[2]] = best[1] == "add"
        st = M.state(st.open)
        moves[best[1]] += 1
        return True

    def swap_sweep() -> bool:
        nonlocal st
        improved = False
        base = M.objective(st)
        order = np.flatnonzero(st.open & ~locked)
        # az talep alan hastaneler önce (takas adayı olma olasılığı yüksek)
        order = order[np.argsort(st.b[order])]
        for j in order:
            if not busy():
                break
            if not st.open[j]:
                continue
            tmp = st.open.copy()
            tmp[j] = False
            st2 = M.state(tmp)
            allowed_new = M.openable.copy()
            allowed_new[j] = False
            dacc, dopen = M.eval_add(st2, allowed_new)
            d = dacc + M.w_open * dopen
            if inst.budget is not None:
                d = np.where(st2.open_total + dopen <= inst.budget, d, np.inf)
            k = int(np.argmin(d))
            if np.isfinite(d[k]) and M.objective(st2) + d[k] < base - tol:
                tmp[k] = True
                st = M.state(tmp)
                base = M.objective(st)
                moves["swap"] += 1
                improved = True
        return improved

    def busy() -> bool:
        return time.perf_counter() - t0 < time_budget and not (stop is not None and stop.is_set())

    while busy():
        while busy() and try_add_drop():
            pass
        if not swap:
            break
        moves["sweeps"] += 1
        if not swap_sweep():
            break
    moves["approx_objective_tl"] = M.objective(st)
    moves["unassigned"] = st.unassigned
    moves["construct_s"] = construct_s
    return st.open, moves


def heuristic(inst: Instance, *, time_budget: float = 5.0, start_open: np.ndarray | None = None,
              swap: bool = True, repair_steps: int = 5, eval_gap: float = 1e-3,
              eval_time: float = 5.0, stop=None) -> HeuristicResult:
    """Açgözlü + yerel arama, ardından kesin değerlendirme (evaluate). Olurlu çözüm bulamazsa ok=False."""
    t0 = time.perf_counter()
    open_mask, moves = greedy_local(inst, time_budget=time_budget, start_open=start_open, swap=swap, stop=stop)
    t1 = time.perf_counter()
    M = None
    ev = evaluate(inst, np.flatnonzero(open_mask), gap=eval_gap, time_limit=eval_time)
    tries = 0
    # olurlu değilse onar: bütçe modunda en az erişim kaybıyla bir hastane kapat (açılış maliyeti düşer), diğer
    # durumlarda (kapasite/sınıf taşması) en iyi eklemeyi yap
    while not ev.ok and tries < repair_steps:
        M = M or ApproxModel(inst)
        st = M.state(open_mask)
        if inst.budget is not None and int(open_mask.sum()) > max(inst.count_min, 1):
            dacc, dopen = M.eval_drop(st, ~inst.lock_open)
            d = np.where(np.isfinite(dacc), dacc, np.inf)
            if not np.isfinite(d).any():
                break
            open_mask[int(np.argmin(d))] = False
        elif int(open_mask.sum()) < (inst.count_max or inst.nJ):
            dacc, dopen = M.eval_add(st, M.openable)
            d = dacc + M.w_open * dopen
            if not np.isfinite(d).any():
                break
            open_mask[int(np.argmin(d))] = True
        else:
            break
        ev = evaluate(inst, np.flatnonzero(open_mask), gap=eval_gap, time_limit=eval_time)
        tries += 1
    t2 = time.perf_counter()
    moves["repair_adds"] = tries
    if not ev.ok:
        return HeuristicResult(False, None, None, moves.get("approx_objective_tl"), t2 - t0,
                               construct_s=moves["construct_s"], local_s=t1 - t0 - moves["construct_s"],
                               eval_s=t2 - t1, moves=moves, message=ev.message, eval=ev)
    sol = ev.solution
    sol.source = "heuristic"
    return HeuristicResult(True, sol, ev.objective_tl, moves.get("approx_objective_tl"), t2 - t0,
                           construct_s=moves["construct_s"], local_s=t1 - t0 - moves["construct_s"],
                           eval_s=t2 - t1, moves=moves, eval=ev)
