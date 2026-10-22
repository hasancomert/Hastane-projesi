"""Lagrange gevşetmesi: hızlı geçerli alt sınır (optimallik sertifikası) + Lagrange sezgiseli (MIP başlangıcı).

Yalnız numpy; HiGHS yalnız başlangıç çarpanları (zayıf LP) ve kesin değerlendirme LP'leri için.

Gevşetilen kısıt: atama satırları Σ_{a∈A(i)} x_a = 1 (çarpan μ_i, işaretsiz). Kalan problem aday j başına ayrışır
(Geoffrion & McBride 1978; Cornuéjols, Sridharan & Thizy 1991). j ve k sınıfı için kesirli sırt çantası:
    r_a = c_a − μ_i (indirgenmiş erişim maliyeti), p_a = −r_a, ρ_a = p_a / q_i (yatak başına kazanç);
    yaylar ρ azalan; ilk L_k yatak zaten ödenir (B ≥ L_k), aşan her yatak Değişken_jk'ya mal olur, toplam ≤ U_k:
    b = max(min(L_k, W⁺_j), min(U_k, W_j(V_jk))),  G_jk = Sabit_jk + V_jk·max(L_k, b) − P_j(b).
G_j = min_k G_jk; açık: G_j < 0 ('aç' kilitleri her zaman; en az/en çok/sabit p: G sıralamasıyla).
L(μ) = Σ_i μ_i + Σ_{j açık} G_j ≤ z*  — HER μ için geçerli alt sınır (yakınsama doğruluk için gerekmez).
Alt problemde y ikili olduğundan Lagrange duali güçlü LP gevşetmesinden (x_a ≤ z_j) sıkıdır.

Çarpan güncelleme: hacim algoritması (Barahona & Anbil 2000): en iyi nokta μ̂ çevresinde ortalama alt gradyan
v̄ = α g + (1−α) v̄ yönünde Polyak adımı θ (UB − L̂) / ‖v̄‖²; iyileşmede θ ×1,1 (g·v̄ ≥ 0 ise), 5 başarısız adımda
θ ×0,66; α ≤ α₀ = 0,3, her 100 yinelemede yarıya. Başlangıç: zayıf LP'nin (bağlantı satırsız) atama dualleri.
Faz 3 ölçümü (T3): düz Polyak / saptırılmış alt gradyan 2 km'de takılıyor; bu ayarlar 5/3/2 km'de en hızlısı.

Lagrange sezgiseli (UB): (a) her ≥ 20 yinelemede bir, yeni en iyi L'deki açık küme → kapsama onarımı → sınıflar alt
problemden → sabit sınıflı atama/boyut LP'si + sınıf onarımı (fast_eval, ≈0,1 sn); umut verici ya da LP'si olurlu
olmayan kümelerde sınıf seçimli MIP (evaluate, gap 1e-2, ≤ 1 sn, toplam sezgisel süresi payla sınırlı); (b) seyrek:
yaklaşık modelde yerel arama (heuristics.greedy_local) → sınıf seçimli MIP. Sayı sınırlarını sağlamayan çözümler atılır.
Çekirdek problem (core_instance): en iyi μ'deki G_j ile seçilen adaylar ve yakın yaylardan küçük bir MILP; solve() bunu
HiGHS ile çözüp üst sınırı iyileştirir (çekirdeğin alt sınırı küresel değildir).
Bütçe modu (butce) desteklenmez: ok=False döner (çağıran mevcut yola düşer).

Sayısal not: aday içi ρ sıralaması tek anahtarlı (aday + ölçekli ρ) kararlı sıralamadır; ρ'ları ≈1e-13·max ρ'dan az
farklı yaylar eşit sayılır. Bunun L'ye etkisi en çok (ρ farkı × toplam yatak) ≈ birkaç bin TL'dir (≈1e-8 göreli);
raporlanan sınır bu yüzden BOUND_SAFETY kadar aşağı yuvarlanır.
Çarpan önbelleği: aynı yapı (Structure) için son çözümün çarpanları saklanır; yeni çözümde zayıf LP duali ile önbellekteki
çarpanlardan L'si büyük olan başlangıç alınır (parametre değişikliği çarpanları az oynatır; p taraması ve canlı
yeniden çözümler hızlanır).
"""
from __future__ import annotations

import dataclasses
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import milp
from .instance import Instance, Solution

SCALE = milp.SCALE       # milyon TL
CLASS_MIP_GAP = 1e-2     # Lagrange sezgiselinde sınıf seçimli değerlendirmenin gap'i (1e-3 çoğu kez 1 sn'de bitmiyor;
                         # 1e-2 ≈0,2–0,4 sn); en iyi çözüm sonunda solve() cilasında 1e-3 ile yeniden değerlendirilir
BOUND_SAFETY = 1e-8      # raporlanan alt sınır (1 − BOUND_SAFETY) ile çarpılır (bkz. sayısal not)
MU_CACHE_SIZE = 8
_MU_CACHE: "OrderedDict[tuple, tuple[str, np.ndarray]]" = OrderedDict()


@dataclass
class SubResult:
    L: float                 # milyon TL
    G: np.ndarray            # (J, K) milyon TL
    Gmin: np.ndarray         # (J,)
    kbest: np.ndarray        # (J,)
    open_: np.ndarray        # (J,) bool
    x: np.ndarray            # (A,)
    g: np.ndarray            # (I,) alt gradyan 1 − Σ x


class Subproblem:
    """Vektörel alt problem (aday başına kesirli sırt çantası, sınıf seçimi)."""

    def __init__(self, inst: Instance) -> None:
        s = inst.s
        self.inst = inst
        self.nI, self.nJ, self.nK, self.nA = inst.nI, inst.nJ, inst.nK, inst.nA
        self.ai = s.arc_i.astype(np.int64)
        self.aj = s.arc_j.astype(np.int64)
        self.c = inst.acc / SCALE
        self.q = inst.q.astype(float)
        self.qa = self.q[self.ai]
        self.F = inst.fixed / SCALE
        self.V = inst.var / SCALE
        self.L, self.U = inst.L.astype(float), inst.U.astype(float)
        self.allowed = inst.y_ub & ~inst.lock_closed[:, None]
        self.lock_open = inst.lock_open.copy()
        self.cmin, self.cmax = inst.count_min, inst.count_max
        cnt = np.bincount(self.aj, minlength=self.nJ)
        self.seg_start = np.r_[0, np.cumsum(cnt)[:-1]].astype(np.int64)
        self.seg_end = self.seg_start + cnt
        self.seg_of = np.repeat(np.arange(self.nJ), cnt)
        self.perm = np.argsort(self.aj, kind="stable")    # yinelemeler arasında korunur (sıcak sıralama)
        self.calls = 0
        self.time_s = 0.0

    def solve(self, mu: np.ndarray) -> SubResult:
        with np.errstate(invalid="ignore", over="ignore", divide="ignore"):
            return self._solve(mu)

    def _solve(self, mu: np.ndarray) -> SubResult:
        t0 = time.perf_counter()
        r = self.c - mu[self.ai]
        rho = -r / self.qa
        rmax = max(float(rho.max()), 1e-30)
        key = self.aj + 0.999 * (1.0 - np.clip(rho / rmax, 0.0, 1.0))      # aday içinde ρ azalan
        key[r >= 0] = self.aj[r >= 0] + 0.9995
        perm = self.perm[np.argsort(key[self.perm], kind="stable")]       # neredeyse sıralı → hızlı
        self.perm = perm
        j = self.seg_of
        rr = r[perm]
        pos = rr < 0
        w = np.where(pos, self.qa[perm], 0.0)
        p = np.where(pos, -rr, 0.0)
        rh = np.where(pos, rho[perm], -1.0)
        cw0 = np.r_[0.0, np.cumsum(w)]
        base = cw0[self.seg_start]
        cw_prev = cw0[:-1] - base[j]
        Wpos = cw0[self.seg_end] - base
        wsafe = np.where(w > 0, w, 1.0)
        G = np.full((self.nJ, self.nK), np.inf)
        frs: list = [None] * self.nK
        for k in range(self.nK):
            ok = self.allowed[:, k]
            if not ok.any():
                continue
            Vk = self.V[:, k]
            WV = np.bincount(j, w * (rh > Vk[j]), minlength=self.nJ)
            b = np.maximum(np.minimum(self.L[k], Wpos), np.minimum(self.U[k], WV))
            fr = np.where(pos, np.clip((b[j] - cw_prev) / wsafe, 0.0, 1.0), 0.0)
            P = np.bincount(j, p * fr, minlength=self.nJ)
            G[:, k] = np.where(ok, self.F[:, k] + Vk * np.maximum(self.L[k], b) - P, np.inf)
            frs[k] = fr
        kbest = np.argmin(G, axis=1)
        Gmin = G[np.arange(self.nJ), kbest]
        open_ = self._select(Gmin)
        Lval = float(mu.sum() + Gmin[open_].sum())
        x = np.zeros(self.nA)
        for k in range(self.nK):
            if frs[k] is not None:
                sel = open_[j] & (kbest[j] == k)
                x[perm[sel]] = frs[k][sel]
        g = 1.0 - np.bincount(self.ai, x, minlength=self.nI)
        self.calls += 1
        self.time_s += time.perf_counter() - t0
        return SubResult(Lval, G, Gmin, kbest, open_, x, g)

    def _select(self, Gmin: np.ndarray) -> np.ndarray:
        fin = np.isfinite(Gmin)
        open_ = (fin & (Gmin < 0)) | self.lock_open
        n = int(open_.sum())
        if n < self.cmin:
            cand = np.flatnonzero(fin & ~open_)
            open_[cand[np.argsort(Gmin[cand])[: self.cmin - n]]] = True
        elif self.cmax is not None and n > self.cmax:
            free = np.flatnonzero(open_ & ~self.lock_open)
            keep = max(self.cmax - int(self.lock_open.sum()), 0)
            open_[free[np.argsort(Gmin[free])[keep:]]] = False
        return open_


def _cache_key(inst: Instance) -> tuple:
    return (inst.s.key, inst.nK)


def remember(inst: Instance, mu_tl: np.ndarray) -> None:
    """Çarpanları (TL / talep hücresi) yapı anahtarıyla önbelleğe yazar."""
    key = _cache_key(inst)
    _MU_CACHE[key] = (inst.params.hash(), np.asarray(mu_tl, dtype=float) / SCALE)
    _MU_CACHE.move_to_end(key)
    while len(_MU_CACHE) > MU_CACHE_SIZE:
        _MU_CACHE.popitem(last=False)


def clear_cache() -> None:
    _MU_CACHE.clear()


def start_multipliers(inst: Instance, sub: "Subproblem | None" = None, *, use_cache: bool = True,
                      mu0_tl: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Başlangıç çarpanları (milyon TL): verilen μ0, önbellek (aynı parametreler) ya da zayıf LP duali / önbellekten
    L'si büyük olanı. (μ, bilgi) döndürür."""
    t0 = time.perf_counter()
    if mu0_tl is not None:
        return np.asarray(mu0_tl, dtype=float) / SCALE, {"source": "verilen", "init_s": time.perf_counter() - t0}
    cached = _MU_CACHE.get(_cache_key(inst)) if use_cache else None
    if cached is not None and len(cached[1]) == inst.nI and cached[0] == inst.params.hash():
        return cached[1].copy(), {"source": "onbellek_ayni", "init_s": time.perf_counter() - t0}
    mu = init_multipliers(inst)
    info = {"source": "zayif_lp"}
    if cached is not None and len(cached[1]) == inst.nI:
        sub = sub or Subproblem(inst)
        l_lp, l_c = sub.solve(mu).L, sub.solve(cached[1]).L
        info.update({"L_lp_tl": l_lp * SCALE, "L_cache_tl": l_c * SCALE})
        if l_c > l_lp:
            mu, info["source"] = cached[1].copy(), "onbellek"
    info["init_s"] = time.perf_counter() - t0
    return mu, info


def init_multipliers(inst: Instance) -> np.ndarray:
    """Zayıf LP (bağlantı satırsız) atama satırı dualleri, milyon TL (3 km ≈ 0,45 sn, 2 km ≈ 1 sn)."""
    m = milp.MilpModel(inst, linking="weak")
    r = m.solve(relax=True, options={"solver": "simplex"})
    if r.status != "Optimal":
        sub = Subproblem(inst)
        cmin = np.full(inst.nI, np.inf)
        np.minimum.at(cmin, sub.ai, sub.c)
        return cmin + float(np.where(sub.allowed, sub.F / sub.U[None, :] + sub.V, np.inf).min()) * sub.q
    return np.asarray(m.h.getSolution().row_dual)[: inst.nI].copy()


# ---------------------------------------------------------------- Lagrange sezgiseli
def cover_repair(inst: Instance, open_mask: np.ndarray, Gmin: np.ndarray, max_adds: int = 200) -> np.ndarray:
    """Kapsanmayan talep hücresi kalmayana dek, ilk kapsanmayan hücrenin listesindeki en küçük G'li adayı aç."""
    s = inst.s
    om = open_mask.copy()
    openable = inst.y_ub.any(axis=1) & ~inst.lock_closed
    g = np.where(np.isfinite(Gmin), Gmin, np.inf)
    for _ in range(max_adds):
        cov = np.zeros(inst.nI, dtype=bool)
        cov[s.arc_i[om[s.arc_j]]] = True
        bad = np.flatnonzero(~cov)
        if not len(bad):
            break
        i = bad[0]
        a = np.arange(s.arc_ptr[i], s.arc_ptr[i + 1])
        a = a[openable[s.arc_j[a]]]
        if not len(a):
            break
        om[s.arc_j[a][np.argmin(g[s.arc_j[a]])]] = True
    return om


def _count_ok(inst: Instance, n: int) -> bool:
    return n >= inst.count_min and (inst.count_max is None or n <= inst.count_max)


def fast_eval(inst: Instance, open_idx: np.ndarray, cls: np.ndarray, *, mip_fallback: bool = True,
              repair_rounds: int = 3, approx=None):
    """Sınıflar sabit atama/boyut LP'si (evaluate, ≈0,05 sn); olurlu değilse isteğe bağlı sınıf seçimli MIP (gap 1e-2).

    Sınıf onarımı: LP çözümündeki yükle (atanan yatak) her hastanenin sınıfı yaklaşık maliyetle (heuristics.ApproxModel
    .hcost: Sabit + Değişken·max(yük, L_k), üst sınırı aşan yük komşuya kaydırma bedeliyle) yeniden seçilir ve LP
    tekrarlanır; iyileşme durunca biter. Faz 3 ölçümü: alt problemden gelen sınıflar sık sık bir üst sınıfta kalıyor
    (ör. 200 yataklı V-B yerine 199 yataklı IV-C daha ucuz); onarımsız Lagrange sezgiseli 2 km'de %1'e yakın pahalıydı."""
    from .evaluate import evaluate
    from .heuristics import ApproxModel
    open_idx = np.asarray(open_idx, dtype=np.int64)
    ev = evaluate(inst, open_idx, cls=np.asarray(cls, dtype=np.int64))
    if not ev.ok:
        return evaluate(inst, open_idx, gap=1e-2, time_limit=5.0) if mip_fallback else ev
    M = approx or ApproxModel(inst)
    s = inst.s
    for _ in range(repair_rounds):
        sol = ev.solution
        a = np.flatnonzero(sol.x > 0)
        load = np.bincount(s.arc_j[a], inst.q[s.arc_i[a]] * sol.x[a], minlength=inst.nJ)[sol.open_j]
        _, k_new = M.hcost(sol.open_j, load)
        if np.array_equal(k_new, sol.cls):
            break
        ev2 = evaluate(inst, sol.open_j, cls=k_new)
        if not ev2.ok or not ev2.objective_tl < ev.objective_tl * (1 - 1e-12):
            break
        ev = ev2
    return ev


# ---------------------------------------------------------------- sonuç
@dataclass
class LagrangeResult:
    ok: bool
    bound_tl: float | None          # geçerli alt sınır (TL)
    ub_tl: float | None             # en iyi çözümün amaç değeri (TL)
    gap: float | None
    solution: Solution | None       # en iyi çözüm (girdi start ya da Lagrange sezgiseli)
    source: str = ""
    iters: int = 0
    time_s: float = 0.0
    init_s: float = 0.0
    lh_calls: int = 0
    lh_time_s: float = 0.0
    sub_ms: float = 0.0
    mu: np.ndarray | None = None    # en iyi çarpanlar (TL / talep hücresi): hücrenin talebinin marjinal değeri
    G_min_tl: np.ndarray | None = None   # en iyi μ'de aday başına açılışın indirgenmiş maliyeti (TL)
    best_sub: SubResult | None = field(default=None, repr=False)
    trace: list = field(default_factory=list)
    message: str = ""
    init_source: str = ""


def run(inst: Instance, *, ub_tl: float | None = None, start: Solution | None = None, time_limit: float = 10.0,
        target_gap: float = 0.01, stop=None, progress_cb: Callable[[dict], None] | None = None,
        mu0_tl: np.ndarray | None = None, use_cache: bool = True, theta0: float = 0.3, alpha0: float = 0.3,
        red_after: int = 5, warmup_iters: int = 100, direct_every: int = 20, ls_first_s: float = 1.0,
        ls_period: float = 0.25, ls_time: float = 1.0, report_every_s: float = 0.25,
        heuristics_on: "bool | Callable[[], bool]" = True, class_mip_time: float = 1.0, class_mip_share: float = 0.4,
        promising: float = 0.01, stall_tol: float = 0.0, stall_min_s: float = 2.0,
        local_search: bool = True, ub_feed: Callable[[], float | None] | None = None, feed_every_s: float = 0.1,
        extra_stop: Callable[[float, float], bool] | None = None) -> LagrangeResult:
    """Hacim algoritması + Lagrange sezgiseli; (UB − L)/UB ≤ target_gap, süre ya da stop ile durur.

    mu0_tl: başlangıç çarpanları (TL / talep hücresi; ör. önceki çalıştırmanın res.mu'su). Verilmezse
    start_multipliers (zayıf LP duali / önbellek). Sonuç çarpanları önbelleğe yazılır (use_cache).
    heuristics_on bir fonksiyon da olabilir (her yinelemede sorulur; ör. çekirdek MIP iyileştiremezse sezgiseli aç).
    ub_feed: dışarıdan üst sınır (TL; ör. aynı anda ayrı süreçte çalışan çekirdek MIP'in incumbent'ı) — adım boyu ve
    durma ölçütü min(iç UB, dış UB) ile hesaplanır; sonuçtaki ub_tl / solution yalnız Lagrange'ın kendi çözümüdür.
    extra_stop(L_tl, UB_tl) True dönerse durur.
    stall_tol > 0: L son max(2 sn, geçen sürenin %30'u) içinde stall_tol·|L|'den az arttıysa (en az stall_min_s sn ve
    2·warmup_iters yineleme sonra) durur — sınır durulduğunda süreyi çekirdek MIP'e bırakmak için.
    Sınıf seçimli değerlendirme (evaluate, sınıf MIP'i, gap CLASS_MIP_GAP, ≤ class_mip_time sn): doğrudan sezgiselin LP
    sonucu olurlu değilse ya da eldeki UB'nin (1 + promising) katından iyiyse ve sezgisellerin toplam süresi geçen
    sürenin class_mip_share'ini aşmıyorsa; yerel arama sezgiselinde her zaman. Faz 3 ölçümü: sınıf MIP'i aynı açık kümede sabit sınıflı LP + onarımdan
    %0,1–1,3 ucuz çözüm veriyor (3 km ≈1 sn, 2 km ≈0,3–1 sn)."""
    from .evaluate import evaluate
    from .heuristics import ApproxModel, greedy_local
    T0 = time.perf_counter()
    if inst.mode == "butce":
        return LagrangeResult(False, None, None, None, None, message="Bütçe modunda Lagrange sınırı uygulanmadı.")
    sub = Subproblem(inst)
    approx = ApproxModel(inst)
    mu0, init_info = start_multipliers(inst, sub, use_cache=use_cache, mu0_tl=mu0_tl)
    init_s = time.perf_counter() - T0
    best_sol = start
    UB = (ub_tl if ub_tl is not None else (start.objective_tl(inst) if start is not None else math.inf)) / SCALE
    src = "baslangic" if start is not None else ""
    seen: set = set()
    lh = {"calls": 0, "time": 0.0, "last_ls": -math.inf, "last_direct": -10 ** 9, "class_mip": 0}
    trace: list = []

    def accept(sol: Solution | None, tag: str) -> None:
        nonlocal UB, best_sol, src
        if sol is None or not _count_ok(inst, sol.n_open):
            return
        v = sol.objective_tl(inst) / SCALE
        if v < UB * (1 - 1e-12):
            UB, best_sol, src = v, sol, tag

    ext = {"ub": math.inf, "last": -math.inf}

    def ub_eff() -> float:
        return min(UB, ext["ub"])

    def target() -> float:
        # UB yoksa (başlangıç çözümü yok): L̂'nin %3 üstü (ilk sezgisel UB gelene kadar)
        u = ub_eff()
        return u if math.isfinite(u) else abs(L_hat) * 1.03

    mu_hat = mu0.copy()
    S = sub.solve(mu_hat)
    L_hat, S_hat = S.L, S
    vbar, alpha, theta, n_red = S.g.copy(), alpha0, theta0, 0
    it, last_rep = 0, -math.inf
    from collections import deque
    hist: deque = deque()
    while True:
        el = time.perf_counter() - T0
        if ub_feed is not None and el - ext["last"] >= feed_every_s:
            ext["last"] = el
            try:
                v = ub_feed()
            except Exception:
                v = None
            if v is not None and math.isfinite(v):
                ext["ub"] = min(ext["ub"], v / SCALE)
        L_safe = L_hat - BOUND_SAFETY * abs(L_hat)
        U = ub_eff()
        gap = (U - L_safe) / U if math.isfinite(U) and U > 0 else math.inf
        if el - last_rep >= report_every_s:
            last_rep = el
            ev = {"kind": "lagrange", "t": round(el, 4), "iter": it, "lb": L_safe * SCALE,
                  "ub": U * SCALE if math.isfinite(U) else None, "gap": gap if math.isfinite(gap) else None}
            trace.append(ev)
            if progress_cb is not None:
                try:
                    progress_cb(ev)
                except Exception:
                    pass
        if gap <= target_gap or el >= time_limit or (stop is not None and stop.is_set()) or theta < 1e-5:
            break
        if extra_stop is not None and math.isfinite(U) and extra_stop(L_safe * SCALE, U * SCALE):
            break
        if stall_tol > 0:
            hist.append((el, L_hat))
            if el >= stall_min_s and it >= 2 * warmup_iters:
                w = max(2.0, 0.3 * el)
                while len(hist) > 1 and hist[1][0] <= el - w:
                    hist.popleft()
                if hist[0][0] <= el - w and L_hat - hist[0][1] < stall_tol * abs(L_hat):
                    break
        nrm = float(vbar @ vbar)
        if nrm < 1e-12 or not math.isfinite(nrm):
            break
        with np.errstate(over="ignore", invalid="ignore"):
            mu = mu_hat + theta * max(target() - L_hat, 1e-9 * abs(L_hat)) / nrm * vbar
        if not np.isfinite(mu).all():
            break
        S = sub.solve(mu)
        it += 1
        h_on = heuristics_on() if callable(heuristics_on) else heuristics_on
        if not math.isfinite(S.L):               # çarpanlar ıraksadı (ör. model olurlu değil): dur
            break
        diff = S.g - vbar
        den = float(diff @ diff)
        a = alpha if den <= 0 else float(np.clip(-(vbar @ diff) / den, alpha / 10, alpha))
        vbar = a * S.g + (1 - a) * vbar
        if S.L > L_hat:
            if float(S.g @ vbar) >= 0:
                theta = min(theta * 1.1, 2.0)
            mu_hat, L_hat, S_hat, n_red = mu, S.L, S, 0
            # (a) ucuz doğrudan sezgisel: yeni en iyi L'deki açık küme
            if h_on and it >= warmup_iters and it - lh["last_direct"] >= direct_every:
                lh["last_direct"] = it
                t = time.perf_counter()
                om = cover_repair(inst, S.open_, S.Gmin)
                oj = np.flatnonzero(om)
                key = hash(oj.tobytes())
                if key not in seen and _count_ok(inst, len(oj)):
                    seen.add(key)
                    ev_ = fast_eval(inst, oj, S.kbest[oj], mip_fallback=False, approx=approx)
                    if not ev_.ok:                # alt problemin sınıfları kapasiteye yetmiyor: yaklaşık modelin sınıfları
                        ev_ = fast_eval(inst, oj, approx.state(om).hk[oj], mip_fallback=False, approx=approx)
                    if ev_.ok:
                        accept(ev_.solution, "lagrange_lp")
                    el_ = time.perf_counter() - T0
                    if ((not ev_.ok or ev_.objective_tl / SCALE < UB * (1 + promising) + 1e-9)
                            and lh["time"] + (time.perf_counter() - t) < class_mip_share * el_):
                        ev2 = evaluate(inst, oj, gap=CLASS_MIP_GAP, time_limit=class_mip_time)
                        if ev2.ok:
                            accept(ev2.solution, "lagrange_mip")
                        lh["class_mip"] += 1
                    lh["calls"] += 1
                lh["time"] += time.perf_counter() - t
        else:
            n_red += 1
            if n_red >= red_after:
                theta, n_red = theta * 0.66, 0
        if it % 100 == 0:
            alpha = max(alpha / 2, 1e-5)
        # (b) seyrek yerel arama sezgiseli (yaklaşık model) — süreye bağlı
        el = time.perf_counter() - T0
        if (h_on and local_search and it >= warmup_iters and el >= ls_first_s
                and el - lh["last_ls"] >= max(1.0, ls_period * el)):
            t = time.perf_counter()
            om = cover_repair(inst, S_hat.open_, S_hat.Gmin)
            om2, _ = greedy_local(inst, time_budget=ls_time, start_open=om, swap=True, stop=stop)
            oj2 = np.flatnonzero(om2)
            key = hash(oj2.tobytes())
            if key not in seen and _count_ok(inst, len(oj2)):
                seen.add(key)
                ev_ = evaluate(inst, oj2, gap=CLASS_MIP_GAP, time_limit=class_mip_time)
                if not ev_.ok:
                    ev_ = fast_eval(inst, oj2, approx.state(om2).hk[oj2], approx=approx)
                if ev_.ok:
                    accept(ev_.solution, "lagrange_ls")
                lh["class_mip"] += 1
            lh["calls"] += 1
            lh["time"] += time.perf_counter() - t
            lh["last_ls"] = time.perf_counter() - T0
    if best_sol is not None and best_sol.source in ("", "evaluate"):
        best_sol.source = src
    L_rep = L_hat - BOUND_SAFETY * abs(L_hat)
    gap = (UB - L_rep) / UB if math.isfinite(UB) and UB > 0 else None
    U = ub_eff()
    ev = {"kind": "lagrange", "t": round(time.perf_counter() - T0, 4), "iter": it, "lb": L_rep * SCALE,
          "ub": U * SCALE if math.isfinite(U) else None,
          "gap": (U - L_rep) / U if math.isfinite(U) and U > 0 else None, "final": True}
    trace.append(ev)
    if progress_cb is not None:
        try:
            progress_cb(ev)
        except Exception:
            pass
    with np.errstate(over="ignore", invalid="ignore"):
        mu_tl, g_tl = mu_hat * SCALE, S_hat.Gmin * SCALE
    if use_cache and np.isfinite(mu_tl).all():
        remember(inst, mu_tl)
    return LagrangeResult(True, L_rep * SCALE, UB * SCALE if math.isfinite(UB) else None, gap, best_sol, src, it,
                          time.perf_counter() - T0, init_s, lh["calls"], lh["time"],
                          sub.time_s / max(sub.calls, 1) * 1e3, mu_tl, g_tl, S_hat, trace,
                          init_source=init_info["source"])


def candidate_subset(inst: Instance, res: LagrangeResult, n_keep: int, must=None) -> np.ndarray:
    """Kısıtlı MIP için aday maskesi: en iyi μ'de açılış indirgenmiş maliyeti G_j en küçük n_keep aday ∪ zorunlular
    (ör. eldeki en iyi çözümün açık kümesi) ∪ 'aç' kilitleri (Faz 3 T1: 2 km'de N = 200 ile sezgisel 390,4 → 380,2)."""
    g = np.where(np.isfinite(res.G_min_tl), res.G_min_tl, np.inf)
    keep = np.zeros(inst.nJ, dtype=bool)
    keep[np.argsort(g, kind="stable")[:n_keep]] = True
    if must is not None:
        keep[np.asarray(must, dtype=np.int64)] = True
    keep &= np.isfinite(g) | inst.lock_open
    keep |= inst.lock_open
    return keep


def core_instance(inst: Instance, res: LagrangeResult, incumbent: Solution | None, *, n_keep: int = 150,
                  m: int = 8) -> tuple[Instance, np.ndarray]:
    """Çekirdek problem (Lagrange yönlendirmeli): adaylar = G_j en küçük n_keep aday ∪ incumbent'in açık hücreleri ∪ 'aç'
    kilitleri; yaylar = her talep hücresinin tutulan adaylar arasındaki en yakın m yayı ∪ incumbent'in açık hücrelerine
    giden tüm yayları (incumbent çekirdekte olurlu kalır, MIP başlangıcı olur). Aynı amaç ve kısıtlarla kurulan küçük
    MILP'in her çözümü tam modelde de olurludur (x çekirdek dışı yaylarda 0). (Çekirdek örnek, yay indisleri) döndürür.
    Faz 3 ölçümü (3 km, N = 150, m = 8): 157 aday, 14.653 yay; kök LP 1,3 sn; 2,4 sn'de 382,09, 11 sn'de 381,59 milyar TL
    (Lagrange sezgiseli 383,5; tam MILP 200 sn'de 381,64)."""
    s = inst.s
    keep = candidate_subset(inst, res, n_keep, must=incumbent.open_j if incumbent is not None else None)
    ok = keep[s.arc_j]
    idx = np.flatnonzero(ok)
    row_first = np.full(s.nI, -1, dtype=np.int64)
    first = np.ones(len(idx), dtype=bool)
    first[1:] = s.arc_i[idx[1:]] != s.arc_i[idx[:-1]]
    row_first[s.arc_i[idx[first]]] = np.flatnonzero(first)
    rank = np.full(s.nA, np.iinfo(np.int64).max)
    rank[idx] = np.arange(len(idx)) - row_first[s.arc_i[idx]]
    msk = ok & (rank < m)
    if incumbent is not None:
        io = np.zeros(inst.nJ, dtype=bool)
        io[incumbent.open_j] = True
        msk |= io[s.arc_j]
    # tutulan adayı olmayan talep hücresi: en yakın açılabilir yayı (ve adayını) ekle
    cnt = np.bincount(s.arc_i[msk], minlength=s.nI)
    if (cnt == 0).any():
        openable = inst.y_ub.any(axis=1)
        for i in np.flatnonzero(cnt == 0):
            a = np.arange(s.arc_ptr[i], s.arc_ptr[i + 1])
            a = a[openable[s.arc_j[a]]]
            if len(a):
                msk[a[0]] = True
                keep[s.arc_j[a[0]]] = True
    sel = np.flatnonzero(msk)
    counts = np.bincount(s.arc_i[sel], minlength=s.nI)
    s2 = dataclasses.replace(s, key=f"{s.key}-cekirdek-{int(keep.sum())}-{len(sel)}",
                             arc_ptr=np.concatenate(([0], np.cumsum(counts))).astype(np.int64),
                             arc_j=s.arc_j[sel], arc_d=s.arc_d[sel], arc_i=s.arc_i[sel])
    core = dataclasses.replace(inst, s=s2, acc=inst.acc[sel], lock_closed=inst.lock_closed | ~keep, from_cache=False)
    return core, sel


def solution_to_core(sol: Solution, sel: np.ndarray) -> Solution:
    return Solution(sol.open_j, sol.cls, sol.beds, sol.x[sel], source=sol.source)


def solution_from_core(sol: Solution, sel: np.ndarray, nA: int) -> Solution:
    x = np.zeros(nA)
    x[sel] = sol.x
    return Solution(sol.open_j, sol.cls, sol.beds, x, source=sol.source)


def fixing(inst: Instance, res: LagrangeResult, ub_tl: float) -> dict | None:
    """Lagrange indirgenmiş maliyetleriyle geçerli sabitleme (z < UB olan her çözüm için); sayı sınırı yokken.
    kapalı: L + G_j > UB (G_j > 0, 'aç' kilitli değil); açık: L − G_j > UB (G_j < 0); (j,k) yasak: L + G_jk − G_j⁺ > UB
    (G_j⁺: j alt problemde açıksa G_j, değilse 0). Faz 3 ölçümü: hücre sabitleme ≈0 (sabit maliyet ≈0,13% · z),
    (j,k) çiftlerinin %47–66'sı sabitlenebiliyor."""
    if inst.count_min > 0 or inst.count_max is not None or res.best_sub is None or res.bound_tl is None:
        return None
    S = res.best_sub
    slack = (ub_tl - res.bound_tl) / SCALE
    g = np.where(np.isfinite(S.Gmin), S.Gmin, np.inf)
    closed = (np.maximum(g, 0.0) > slack) & ~inst.lock_open
    fopen = (-np.minimum(g, 0.0)) > slack
    base = np.where(S.open_, g, 0.0)
    cls_out = (S.G - base[:, None]) > slack
    return {"closed": closed, "open": fopen, "cls_out": cls_out & inst.y_ub, "slack_tl": slack * SCALE}
