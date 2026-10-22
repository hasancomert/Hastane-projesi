"""Çok sınıflı kapasiteli tesis yerleşimi MILP'i — numpy/scipy ile vektörel kurulum, highspy (HiGHS) ile çözüm.

Değişkenler (sütun sırası):
  y_jk ∈ {0,1}   j hücresinde k sınıfı hastane                        [0,        J·K)
  B_jk ≥ 0       yatak (sürekli)                                       [J·K,      2·J·K)
  z_j ∈ [0,1]    hastane açık göstergesi, z_j = Σ_k y_jk (sürekli; y tamsayıyken kendiliğinden tamsayı)
                                                                       [2·J·K,    2·J·K + J)
  x_a ∈ [0,1]    talep hücresi i'nin talebinin j'ye giden payı, a = (i, j) ∈ A   [2·J·K + J, … + |A|)
Kısıtlar (satır sırası):
  atama     Σ_{a∈A(i)} x_a = 1                       ∀ i        |I|
  açık      Σ_k y_jk − z_j = 0                       ∀ j        |J|   (z_j ≤ 1 → hücre başına en fazla 1 hastane)
  alt       B_jk − L_k y_jk ≥ 0                      ∀ j, k     J·K
  üst       B_jk − U_k y_jk ≤ 0                      ∀ j, k     J·K
  kapasite  Σ_{a∈A(j)} q_i x_a − Σ_k B_jk ≤ 0        ∀ j        |J|
  bağlantı  x_a − z_j ≤ 0                            a ∈ A_L    |A_L| (güçlü bağlantı; A_L ⊆ A, bkz. link_arcs)
  sınıf b.  x_a − Σ_k min(1, U_k/q_i)·y_jk ≤ 0        a ∈ A_P    |A_P| (sınıf başına bağlantı; q_i > en küçük U_k olan
                                                                       yaylar, bkz. pc_link_arcs)
  sayı      en_az ≤ Σ_j z_j ≤ en_çok                            1     (sınır yoksa [0, ∞))
  bütçe     Σ Sabit_jk y_jk + Değişken_jk B_jk ≤ bütçe          1     (yalnız butce modunda bağlayıcı)
Amaç (ölçek: milyon TL): toplam/sabit_p → Σ Sabit·y + Değişken·B + Σ erişim_a·x_a;
                         butce → Σ erişim_a·x_a + ε·(açılış) (ε küçük: fazladan yatak almamak için eşitlik bozucu).
Bağlantı biçimi: x_a ≤ Σ_k y_jk yerine x_a ≤ z_j — aynı LP gevşetmesi, yay başına 5 yerine 2 sıfırdan farklı eleman.
Sınıf başına bağlantı (Faz 3 T1 ölçümü): j'de k sınıfı açıksa x_a·q_i ≤ B_jk ≤ U_k → x_a ≤ U_k/q_i; geçerli eşitsizlik,
x_a ≤ z_j'den sıkı (yalnız q_i > min U_k olan ≈1,2–1,7 bin yayda fark eder). 5 km kök LP sınırı 401,11 → 402,18 milyar TL
(kök boşluğunun ≈%63'ü kapanır), kök LP süresi ≈aynı; MIP sonu gap %0,31 → %0,24.
Not: B = L·y + E yerine koyması (aynı çokyüzlü, J·K satır daha az) 5 km'de kök LP'yi 7 sn'den 18 sn'ye uzattığı için
kullanılmadı (dual simpleks, Faz 3 ölçümü).
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import scipy.sparse as sp
import highspy

from .instance import Instance, Solution

SCALE = 1e6            # amaç birimi: milyon TL
BUDGET_EPS = 1e-5      # butce modunda açılış maliyetinin eşitlik bozucu ağırlığı
INF = highspy.kHighsInf

MODEL_STATUS_TR = {
    "Optimal": "optimal",
    "Infeasible": "olurlu değil",
    "Time limit reached": "süre doldu",
    "Interrupted by user": "kullanıcı durdurdu",
    "Unbounded": "sınırsız",
    "Primal infeasible or unbounded": "olurlu değil ya da sınırsız",
    "Solution limit reached": "çözüm sınırı",
    "Iteration limit reached": "iterasyon sınırı",
    "Objective bound": "amaç sınırı",
    "Objective target": "amaç hedefi",
    "Unknown": "bilinmiyor",
    "Not Set": "çözülmedi",
}


@dataclass(frozen=True)
class Layout:
    """Değişken ve kısıt indis haritaları."""

    nI: int
    nJ: int
    nK: int
    nA: int
    nL: int                 # güçlü bağlantı satırı sayısı (bağlantı yayları alt kümesi)
    nP: int = 0             # sınıf başına bağlantı satırı sayısı

    # sütunlar
    @property
    def y0(self) -> int:
        return 0

    @property
    def B0(self) -> int:
        return self.nJ * self.nK

    @property
    def z0(self) -> int:
        return 2 * self.nJ * self.nK

    @property
    def x0(self) -> int:
        return 2 * self.nJ * self.nK + self.nJ

    @property
    def ncol(self) -> int:
        return self.x0 + self.nA

    def y(self, j, k):
        return self.y0 + np.asarray(j) * self.nK + np.asarray(k)

    def B(self, j, k):
        return self.B0 + np.asarray(j) * self.nK + np.asarray(k)

    def z(self, j):
        return self.z0 + np.asarray(j)

    def x(self, a):
        return self.x0 + np.asarray(a)

    # satırlar
    @property
    def r_assign(self) -> int:
        return 0

    @property
    def r_open(self) -> int:
        return self.nI

    @property
    def r_lower(self) -> int:
        return self.nI + self.nJ

    @property
    def r_upper(self) -> int:
        return self.r_lower + self.nJ * self.nK

    @property
    def r_cap(self) -> int:
        return self.r_upper + self.nJ * self.nK

    @property
    def r_link(self) -> int:
        return self.r_cap + self.nJ

    @property
    def r_pclink(self) -> int:
        return self.r_link + self.nL

    @property
    def r_count(self) -> int:
        return self.r_pclink + self.nP

    @property
    def r_budget(self) -> int:
        return self.r_count + 1

    @property
    def nrow(self) -> int:
        return self.r_budget + 1

    def summary(self) -> dict:
        return {"rows": self.nrow, "cols": self.ncol, "binaries": self.nJ * self.nK, "I": self.nI, "J": self.nJ,
                "K": self.nK, "arcs": self.nA, "link_rows": self.nL, "per_class_rows": self.nP}


# ---------------------------------------------------------------- dizi kurulumu
def _open_coefs(inst: Instance) -> tuple[np.ndarray, np.ndarray]:
    """Açılış maliyeti katsayıları (TL): y_jk için Sabit_jk, B_jk için Değişken_jk."""
    return inst.fixed.ravel(), inst.var.ravel()


def _costs(inst: Instance) -> np.ndarray:
    nJK = inst.nJ * inst.nK
    f, v = _open_coefs(inst)
    if inst.mode == "butce":
        f, v = BUDGET_EPS * f, BUDGET_EPS * v
    c = np.concatenate([f, v, np.zeros(inst.nJ), inst.acc]) / SCALE
    assert c.shape[0] == 2 * nJK + inst.nJ + inst.nA
    return c


def _col_bounds(inst: Instance) -> tuple[np.ndarray, np.ndarray]:
    nJ, nK, nA = inst.nJ, inst.nK, inst.nA
    yub = inst.y_ub.astype(float).ravel()
    Bub = (inst.y_ub * inst.U[None, :]).astype(float).ravel()
    zlb = inst.lock_open.astype(float)
    zub = (~inst.lock_closed & inst.class_on.any()).astype(float)
    lb = np.concatenate([np.zeros(nJ * nK), np.zeros(nJ * nK), zlb, np.zeros(nA)])
    ub = np.concatenate([yub, Bub, zub, np.ones(nA)])
    return lb, ub


def _row_bounds(inst: Instance, lay: Layout) -> tuple[np.ndarray, np.ndarray]:
    lo = np.full(lay.nrow, -INF)
    hi = np.full(lay.nrow, INF)
    lo[lay.r_assign:lay.r_assign + lay.nI] = 1.0
    hi[lay.r_assign:lay.r_assign + lay.nI] = 1.0
    lo[lay.r_open:lay.r_open + lay.nJ] = 0.0
    hi[lay.r_open:lay.r_open + lay.nJ] = 0.0
    lo[lay.r_lower:lay.r_lower + lay.nJ * lay.nK] = 0.0
    hi[lay.r_upper:lay.r_upper + lay.nJ * lay.nK] = 0.0
    hi[lay.r_cap:lay.r_cap + lay.nJ] = 0.0
    hi[lay.r_link:lay.r_link + lay.nL] = 0.0
    hi[lay.r_pclink:lay.r_pclink + lay.nP] = 0.0
    lo[lay.r_count] = float(inst.count_min)
    hi[lay.r_count] = INF if inst.count_max is None else float(inst.count_max)
    if inst.budget is not None:
        hi[lay.r_budget] = inst.budget / SCALE
    return lo, hi


def _matrix(inst: Instance, lay: Layout, link: np.ndarray, pc: np.ndarray | None = None) -> sp.csc_matrix:
    s = inst.s
    nI, nJ, nK, nA = lay.nI, lay.nJ, lay.nK, lay.nA
    jj, kk = np.divmod(np.arange(nJ * nK), nK)
    a = np.arange(nA)
    rows, cols, vals = [], [], []

    def add(r, c, v):
        rows.append(np.asarray(r, dtype=np.int64))
        cols.append(np.asarray(c, dtype=np.int64))
        vals.append(np.broadcast_to(np.asarray(v, dtype=float), np.shape(r)).astype(float))

    add(lay.r_assign + s.arc_i, lay.x(a), 1.0)                                  # atama
    add(lay.r_open + jj, lay.y(jj, kk), 1.0)                                     # açık: Σ_k y
    add(lay.r_open + np.arange(nJ), lay.z(np.arange(nJ)), -1.0)                  #       − z
    add(lay.r_lower + np.arange(nJ * nK), lay.B(jj, kk), 1.0)                    # alt: B − L y
    add(lay.r_lower + np.arange(nJ * nK), lay.y(jj, kk), -inst.L[kk])
    add(lay.r_upper + np.arange(nJ * nK), lay.B(jj, kk), 1.0)                    # üst: B − U y
    add(lay.r_upper + np.arange(nJ * nK), lay.y(jj, kk), -inst.U[kk])
    add(lay.r_cap + s.arc_j, lay.x(a), inst.q[s.arc_i])                          # kapasite: Σ q x − Σ B
    add(lay.r_cap + jj, lay.B(jj, kk), -1.0)
    if lay.nL:
        add(lay.r_link + np.arange(lay.nL), lay.x(link), 1.0)                    # bağlantı: x − z
        add(lay.r_link + np.arange(lay.nL), lay.z(s.arc_j[link]), -1.0)
    if lay.nP:
        coef = pc_link_coefs(inst, pc)                                           # (nP, K)
        rr = lay.r_pclink + np.arange(lay.nP)
        add(rr, lay.x(pc), 1.0)                                                  # sınıf b.: x − Σ_k c_k y_k
        for k in range(nK):
            add(rr, lay.y(s.arc_j[pc], k), -coef[:, k])
    add(np.full(nJ, lay.r_count), lay.z(np.arange(nJ)), 1.0)                     # sayı
    fo, vo = _open_coefs(inst)
    add(np.full(nJ * nK, lay.r_budget), lay.y(jj, kk), fo / SCALE)              # bütçe
    add(np.full(nJ * nK, lay.r_budget), lay.B(jj, kk), vo / SCALE)
    A = sp.csc_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                      shape=(lay.nrow, lay.ncol))
    A.sort_indices()
    return A


def link_arcs(inst: Instance, linking) -> np.ndarray:
    """Güçlü bağlantı (x_a ≤ z_j) yazılacak yaylar: 'strong' hepsi, 'weak' hiçbiri, r (int) her talep hücresinin en
    yakın r yayı, dizi ise doğrudan yay indisleri. Bağlantı satırları geçerli eşitsizliklerdir (kapasite kısıtı y_j = 0
    iken x_a = 0'ı zaten zorlar); alt küme seçmek doğruluğu değil yalnız LP sınırını etkiler."""
    s = inst.s
    if isinstance(linking, str):
        if linking == "strong":
            return np.arange(s.nA)
        if linking == "weak":
            return np.zeros(0, dtype=np.int64)
        raise ValueError(f"geçersiz bağlantı biçimi: {linking!r}")
    if isinstance(linking, (int, np.integer)):
        rank = np.arange(s.nA) - s.arc_ptr[s.arc_i]
        return np.flatnonzero(rank < int(linking))
    return np.unique(np.asarray(linking, dtype=np.int64))


def pc_link_arcs(inst: Instance) -> np.ndarray:
    """Sınıf başına bağlantı yazılacak yaylar: talebi açık sınıfların en küçük üst sınırını aşan hücrelerin yayları
    (q_i > min_k U_k); diğerlerinde katsayıların hepsi 1'dir ve satır x_a ≤ z_j ile aynıdır."""
    s = inst.s
    if not inst.class_on.any():
        return np.zeros(0, dtype=np.int64)
    umin = float(inst.U[inst.class_on].min())
    return np.flatnonzero(inst.q[s.arc_i] > umin * (1 + 1e-12)).astype(np.int64)


def pc_link_coefs(inst: Instance, arcs: np.ndarray) -> np.ndarray:
    """(len(arcs), K) katsayılar min(1, U_k / q_i) (kapalı sınıflarda da yazılır: y üst sınırı 0, desen sabit kalır)."""
    q = inst.q[inst.s.arc_i[arcs]]
    return np.minimum(1.0, inst.U[None, :] / np.maximum(q[:, None], 1e-12))


# ---------------------------------------------------------------- sonuç ve iz
@dataclass
class TracePoint:
    t: float
    kind: str              # 'incumbent' | 'log' | 'start'
    primal: float | None   # TL
    dual: float | None     # TL
    gap: float | None
    nodes: int | None

    def as_dict(self) -> dict:
        return {"t": round(self.t, 4), "kind": self.kind, "primal": self.primal, "dual": self.dual,
                "gap": self.gap, "nodes": self.nodes}


@dataclass
class MilpResult:
    status: str                    # HiGHS model durumu (İngilizce)
    status_tr: str
    solution: Solution | None
    primal_tl: float | None        # en iyi çözümün amaç değeri (TL; mod amacı, butce'de ε terimi dahil)
    dual_tl: float | None          # alt sınır (TL)
    gap: float | None
    nodes: int
    time_s: float
    first_incumbent_s: float | None
    first_dual_s: float | None     # ilk anlamlı alt sınırın zamanı (≥ en iyi çözümün yarısı) ≈ kök LP süresi
    trace: list[TracePoint] = field(default_factory=list)
    start_accepted: bool | None = None
    interrupted: bool = False
    model: dict = field(default_factory=dict)
    spawn_s: float | None = None    # izole modda alt sürecin çözüme başlama gecikmesi

    @property
    def has_solution(self) -> bool:
        return self.solution is not None


# ---------------------------------------------------------------- model
class MilpModel:
    """Bir Instance için kurulmuş HiGHS modeli. Maliyet/sınır güncellemesi yerinde yapılır (update)."""

    def __init__(self, inst: Instance, *, linking: "str | int | np.ndarray" = "strong",
                 per_class: bool | None = None, threads: int | None = None) -> None:
        """per_class: sınıf başına bağlantı satırları (None → yalnız linking='strong' iken)."""
        t0 = time.perf_counter()
        self.inst = inst
        self.link = link_arcs(inst, linking)
        self.linking = linking if isinstance(linking, (str, int)) else "custom"
        self.per_class = (isinstance(linking, str) and linking == "strong") if per_class is None else bool(per_class)
        self.pc = pc_link_arcs(inst) if self.per_class else np.zeros(0, dtype=np.int64)
        self.lay = Layout(inst.nI, inst.nJ, inst.nK, inst.nA, int(len(self.link)), int(len(self.pc)))
        self.h = highspy.Highs()
        self.h.setOptionValue("output_flag", False)
        if threads:
            self.h.setOptionValue("threads", int(threads))
        self._A = _matrix(inst, self.lay, self.link, self.pc)
        self._cost = _costs(inst)
        self._lb, self._ub = _col_bounds(inst)
        self._rlo, self._rhi = _row_bounds(inst, self.lay)
        self._integrality = np.zeros(self.lay.ncol, dtype=np.int32)
        self._integrality[: self.lay.nJ * self.lay.nK] = 1
        self._pass()
        self.build_s = time.perf_counter() - t0

    def _pass(self) -> None:
        A = self._A
        st = self.h.passModel(self.lay.ncol, self.lay.nrow, int(A.nnz), 1, 1, 0.0,
                              self._cost, self._lb, self._ub, self._rlo, self._rhi,
                              A.indptr.astype(np.int32), A.indices.astype(np.int32), A.data.astype(float),
                              self._integrality)
        if st == highspy.HighsStatus.kError:
            raise RuntimeError("HiGHS passModel başarısız")

    @property
    def nnz(self) -> int:
        return int(self._A.nnz)

    def size(self) -> dict:
        d = self.lay.summary()
        d["nnz"] = self.nnz
        return d

    # ------------------------------------------------ yerinde güncelleme
    def update(self, inst: Instance) -> dict:
        """Aynı yapıdaki yeni örneğin maliyet/sınır/katsayılarını yerinde uygular; ne değiştiğini döndürür."""
        if inst.s.key != self.inst.s.key or inst.nK != self.lay.nK:
            raise ValueError("update yalnız aynı yapı (Structure) için geçerli")
        if self.per_class and not np.array_equal(pc_link_arcs(inst), self.pc):
            raise ValueError("sınıf başına bağlantı yay kümesi değişmiş; update kullanılamaz")
        lay = self.lay
        changed = {"costs": 0, "col_bounds": 0, "row_bounds": 0, "coeffs": 0, "rebuilt": False}
        c = _costs(inst)
        idx = np.flatnonzero(c != self._cost)
        if len(idx):
            self.h.changeColsCost(len(idx), idx.astype(np.int32), c[idx])
            changed["costs"] = int(len(idx))
        lb, ub = _col_bounds(inst)
        idx = np.flatnonzero((lb != self._lb) | (ub != self._ub))
        if len(idx):
            self.h.changeColsBounds(len(idx), idx.astype(np.int32), lb[idx], ub[idx])
            changed["col_bounds"] = int(len(idx))
        rlo, rhi = _row_bounds(inst, lay)
        idx = np.flatnonzero((rlo != self._rlo) | (rhi != self._rhi))
        if len(idx):
            self.h.changeRowsBounds(len(idx), idx.astype(np.int32), rlo[idx], rhi[idx])
            changed["row_bounds"] = int(len(idx))
        A = _matrix(inst, lay, self.link, self.pc)
        if A.nnz != self._A.nnz or not (np.array_equal(A.indptr, self._A.indptr)
                                        and np.array_equal(A.indices, self._A.indices)):
            raise ValueError("kısıt matrisinin yapısı değişmiş; update kullanılamaz")
        diff = np.flatnonzero(A.data != self._A.data)
        if len(diff) > 20000:
            # çok sayıda katsayı (ör. talep q değişti): modeli yeniden geçirmek changeCoeff döngüsünden hızlı
            self._A, self._cost, self._lb, self._ub, self._rlo, self._rhi = A, c, lb, ub, rlo, rhi
            self.inst = inst
            self._pass()
            changed["coeffs"] = int(len(diff))
            changed["rebuilt"] = True
            return changed
        if len(diff):
            col_of = np.repeat(np.arange(lay.ncol), np.diff(A.indptr))
            for e in diff:
                self.h.changeCoeff(int(A.indices[e]), int(col_of[e]), float(A.data[e]))
            changed["coeffs"] = int(len(diff))
        self._A, self._cost, self._lb, self._ub, self._rlo, self._rhi = A, c, lb, ub, rlo, rhi
        self.inst = inst
        return changed

    # ------------------------------------------------ çözüm vektörü <-> Solution
    def to_vector(self, sol: Solution) -> np.ndarray:
        lay = self.lay
        v = np.zeros(lay.ncol)
        if len(sol.open_j):
            v[lay.y(sol.open_j, sol.cls)] = 1.0
            v[lay.B(sol.open_j, sol.cls)] = sol.beds
            v[lay.z(sol.open_j)] = 1.0
        v[lay.x0:] = sol.x
        return v

    def from_vector(self, v: np.ndarray, source: str = "milp") -> Solution:
        lay = self.lay
        y = np.asarray(v[: lay.nJ * lay.nK]).reshape(lay.nJ, lay.nK)
        B = np.asarray(v[lay.B0: lay.B0 + lay.nJ * lay.nK]).reshape(lay.nJ, lay.nK)
        j, k = np.nonzero(y > 0.5)
        order = np.argsort(j)
        j, k = j[order], k[order]
        beds = B[j, k]
        # sayısal temizlik: sınıf sınırlarına kırp
        beds = np.clip(beds, self.inst.L[k], self.inst.U[k])
        x = np.clip(np.asarray(v[lay.x0: lay.x0 + lay.nA], dtype=float), 0.0, 1.0)
        x[x < 1e-9] = 0.0
        # kapalı hücreye giden yaylarda çözücünün olurluluk toleransı kadar kalan pay (≤ 1e-6) temizlenir
        is_open = np.zeros(lay.nJ, dtype=bool)
        is_open[j] = True
        x[~is_open[self.inst.s.arc_j]] = 0.0
        return Solution(open_j=j.astype(np.int64), cls=k.astype(np.int64), beds=beds, x=x, source=source)

    def set_start(self, sol: Solution) -> bool:
        """MIP başlangıcı (tam vektör). Olurluluğu çağıran denetlemeli: olurlu olmayan başlangıç sessizce atılır."""
        v = self.to_vector(sol)
        idx = np.arange(self.lay.ncol, dtype=np.int32)
        st = self.h.setSolution(self.lay.ncol, idx, v)
        return st != highspy.HighsStatus.kError

    def objective_of(self, sol: Solution) -> float:
        """Modelin amaç fonksiyonu (TL) — butce modunda ε terimi dahil."""
        return float(self._cost @ self.to_vector(sol)) * SCALE

    # ------------------------------------------------ çözüm
    def solve(self, *, time_limit: float | None = None, gap: float | None = None, abs_gap: float | None = None,
              start: Solution | None = None, progress_cb: Callable[[dict], None] | None = None,
              cancel_event: threading.Event | None = None, log_interval: float = 0.5,
              options: dict | None = None, relax: bool = False, isolate: bool = False) -> MilpResult:
        """Modeli çözer (ayrı iş parçacığında h.run()). relax=True: LP gevşetmesi (tamsayılık kaldırılır).

        isolate=True: çözüm ayrı bir SÜREÇTE yapılır (multiprocessing, spawn); iptal ve süre aşımında süreç sonlandırılır.
        Gerekçe (Faz 3 ölçümü, highspy 1.15.1): MIP'in kök LP'si sırasında kesme geri çağrıları (cbSimplexInterrupt /
        cbMipInterrupt) çağrılmaz; aynı süreçte iptal ancak kök LP bittikten sonra etkili olur (3 km'de ≈140 sn).
        İzole modda en son bulunan çözüm ve alt sınır kuyruktan alınır; süreç başlatma ek yükü ≈0,5–1 sn.
        """
        if isolate and not relax:
            return self._solve_isolated(time_limit=time_limit, gap=gap, abs_gap=abs_gap, start=start,
                                        progress_cb=progress_cb, cancel_event=cancel_event,
                                        log_interval=log_interval, options=options)
        h = self.h
        lay = self.lay
        tl = float(time_limit) if time_limit else math.inf
        h.setOptionValue("time_limit", tl if math.isfinite(tl) else INF)
        if gap is not None:
            h.setOptionValue("mip_rel_gap", float(gap))
        if abs_gap is not None:
            h.setOptionValue("mip_abs_gap", float(abs_gap))
        h.setOptionValue("output_flag", True)
        h.setOptionValue("log_to_console", False)
        h.setOptionValue("mip_min_logging_interval", float(log_interval))
        for k, v in (options or {}).items():
            h.setOptionValue(k, v)
        if relax:
            h.changeColsIntegrality(lay.ncol, np.arange(lay.ncol, dtype=np.int32),
                                    np.zeros(lay.ncol, dtype=np.uint8))
        trace: list[TracePoint] = []
        state = {"first_inc": None, "first_dual": None, "interrupted": False}
        stop = cancel_event or threading.Event()
        deadline = [math.inf]
        lock = threading.Lock()

        def emit(tp: TracePoint) -> None:
            with lock:
                trace.append(tp)
            if progress_cb is not None:
                try:
                    progress_cb(tp.as_dict())
                except Exception:   # ilerleme bildirimi çözümü bozmamalı
                    pass

        def _f(x):
            return None if x is None or not math.isfinite(x) or abs(x) >= 1e50 else float(x) * SCALE

        def on_improving(e) -> None:
            d = e.data_out
            if state["first_inc"] is None:
                state["first_inc"] = d.running_time
            emit(TracePoint(d.running_time, "incumbent", _f(d.objective_function_value),
                            _f(d.mip_dual_bound), _gap(d.mip_gap), int(d.mip_node_count)))

        def on_log(e) -> None:
            d = e.data_out
            dual = _f(d.mip_dual_bound)
            prim = _f(d.mip_primal_bound)
            # ilk ANLAMLI alt sınır (kök LP çözüldükten sonra): önsüzgecin önemsiz sınırı (ör. ≈0) sayılmaz
            if dual is not None and state["first_dual"] is None and (prim is None or dual >= 0.5 * prim):
                state["first_dual"] = d.running_time
            emit(TracePoint(d.running_time, "log", _f(d.mip_primal_bound), dual, _gap(d.mip_gap),
                            int(d.mip_node_count)))

        def on_interrupt(e) -> None:
            if stop.is_set() or time.perf_counter() > deadline[0]:
                state["interrupted"] = True
                e.interrupt()

        subs = [(h.cbMipImprovingSolution, on_improving), (h.cbMipLogging, on_log),
                (h.cbMipInterrupt, on_interrupt), (h.cbSimplexInterrupt, on_interrupt),
                (h.cbIpmInterrupt, on_interrupt)]
        for cb, fn in subs:
            cb.subscribe(fn)
        start_ok = None
        if start is not None and not relax:
            start_ok = self.set_start(start)
        err: list[BaseException] = []

        def runner() -> None:
            try:
                h.run()
            except BaseException as ex:  # noqa: BLE001
                err.append(ex)
            finally:
                try:
                    highspy.Highs.resetGlobalScheduler(False)
                except Exception:
                    pass

        t0 = time.perf_counter()
        deadline[0] = t0 + tl + 1.0 if math.isfinite(tl) else math.inf
        th = threading.Thread(target=runner, name="highs-solve", daemon=True)
        th.start()
        th.join()
        wall = time.perf_counter() - t0
        for cb, fn in subs:
            cb.unsubscribe(fn)
        if err:
            raise err[0]
        ms = h.getModelStatus()
        status = h.modelStatusToString(ms)
        info = h.getInfo()
        sol = None
        primal = dual = g = None
        vec = None
        if info.primal_solution_status == 2:   # kSolutionStatusFeasible
            vec = np.asarray(h.getSolution().col_value)
        if relax:
            if vec is not None:
                primal = dual = float(info.objective_function_value) * SCALE
            self.last_lp_vector = vec
            h.changeColsIntegrality(lay.ncol, np.arange(lay.ncol, dtype=np.int32), self._integrality.astype(np.uint8))
        else:
            if vec is not None:
                sol = self.from_vector(vec)
                primal = _f(info.objective_function_value)
            dual = _f(info.mip_dual_bound)
            g = _gap(info.mip_gap) if sol is not None else None
        h.setOptionValue("output_flag", False)
        return MilpResult(status=status, status_tr=MODEL_STATUS_TR.get(status, status), solution=sol,
                          primal_tl=primal, dual_tl=dual, gap=g, nodes=int(getattr(info, "mip_node_count", 0) or 0),
                          time_s=wall, first_incumbent_s=state["first_inc"], first_dual_s=state["first_dual"],
                          trace=trace, start_accepted=start_ok, interrupted=state["interrupted"],
                          model=self.size())

    def _solve_isolated(self, *, time_limit, gap, abs_gap, start, progress_cb, cancel_event, log_interval,
                        options) -> MilpResult:
        """solve(isolate=True): ayrı süreçte çözüm; iptal/süre aşımında süreç sonlandırılır (bkz. solve)."""
        run = self.start_isolated(time_limit=time_limit, gap=gap, abs_gap=abs_gap, start=start,
                                  progress_cb=progress_cb, log_interval=log_interval, options=options)
        stop = cancel_event or threading.Event()
        while not run.done:
            run.poll(timeout=0.05)
            if not run.done and (stop.is_set() or time.perf_counter() > run.deadline):
                run.terminate("Interrupted by user" if stop.is_set() else "Time limit reached")
        return run.result()

    def start_isolated(self, *, time_limit=None, gap=None, abs_gap=None, start: Solution | None = None,
                       progress_cb: Callable[[dict], None] | None = None, log_interval: float = 0.5,
                       options: dict | None = None) -> "IsolatedRun":
        """Ayrı süreçte çözümü BAŞLATIR ve hemen döner (çağıran aynı anda başka iş yapabilir, ör. Lagrange
        yinelemeleri); poll() ile olaylar okunur, incumbent() son çözümü verir, terminate() durdurur, result() sonuç."""
        tl = float(time_limit) if time_limit else math.inf
        opts = {"time_limit": tl if math.isfinite(tl) else INF, "mip_min_logging_interval": float(log_interval)}
        if gap is not None:
            opts["mip_rel_gap"] = float(gap)
        if abs_gap is not None:
            opts["mip_abs_gap"] = float(abs_gap)
        opts.update(options or {})
        A = self._A
        payload = {"ncol": self.lay.ncol, "nrow": self.lay.nrow, "nnz": int(A.nnz), "cost": self._cost,
                   "lb": self._lb, "ub": self._ub, "rlo": self._rlo, "rhi": self._rhi,
                   "start_": A.indptr.astype(np.int32), "index": A.indices.astype(np.int32),
                   "value": A.data.astype(float), "integrality": self._integrality, "options": opts,
                   "start": None if start is None else self.to_vector(start)}
        return IsolatedRun(self, payload, tl, progress_cb)


class IsolatedRun:
    """Ayrı süreçte çalışan bir HiGHS çözümünün tutamağı (MilpModel.start_isolated)."""

    def __init__(self, model: "MilpModel", payload: dict, tl: float, progress_cb) -> None:
        import multiprocessing as mp
        self.model = model
        self.progress_cb = progress_cb
        ctx = mp.get_context("spawn")
        self.q = ctx.Queue()
        self.proc = ctx.Process(target=_isolated_worker, args=(payload, self.q), daemon=True)
        self.trace: list[TracePoint] = []
        self.st = {"first_inc": None, "first_dual": None, "interrupted": False, "start_ok": None, "vec": None,
                   "primal": None, "dual": None, "gap": None, "nodes": 0, "status": "Not Set", "t_run0": None}
        self.done = False
        self.n_inc = 0                  # incumbent sayacı (yeni çözüm geldi mi?)
        self.t0 = time.perf_counter()
        self.proc.start()
        self.deadline = self.t0 + tl + 3.0 if math.isfinite(tl) else math.inf

    def _emit(self, tp: TracePoint) -> None:
        self.trace.append(tp)
        if self.progress_cb is not None:
            try:
                self.progress_cb(tp.as_dict())
            except Exception:
                pass

    def poll(self, timeout: float = 0.0) -> None:
        """Kuyruktaki olayları işler (timeout > 0 ise ilk olayı o kadar bekler)."""
        import queue as _queue
        st = self.st
        first = True
        while not self.done:
            try:
                msg = self.q.get(timeout=timeout) if (first and timeout > 0) else self.q.get_nowait()
            except _queue.Empty:
                if not self.proc.is_alive() and self.q.empty() and not self.done:
                    self.done = True
                    if st["status"] == "Not Set":
                        raise RuntimeError("HiGHS alt süreci beklenmedik biçimde sonlandı")
                break
            first = False
            kind = msg[0]
            if kind == "started":
                st["start_ok"] = msg[1]
                st["t_run0"] = time.perf_counter() - self.t0
            elif kind == "inc":
                _, rt, obj, dual, g, nodes, vec = msg
                if st["first_inc"] is None:
                    st["first_inc"] = rt
                st["vec"], st["primal"], st["nodes"] = vec, _tl(obj), nodes
                st["dual"] = _tl(dual) if _tl(dual) is not None else st["dual"]
                self.n_inc += 1
                self._emit(TracePoint(rt, "incumbent", _tl(obj), _tl(dual), _gap(g), nodes))
            elif kind == "log":
                _, rt, prim, dual, g, nodes = msg
                d, pr = _tl(dual), _tl(prim)
                if d is not None:
                    st["dual"] = d
                    if st["first_dual"] is None and (pr is None or d >= 0.5 * pr):
                        st["first_dual"] = rt
                st["nodes"] = nodes
                self._emit(TracePoint(rt, "log", pr, d, _gap(g), nodes))
            elif kind == "done":
                _, status, obj, dual, g, nodes, vec = msg
                st["status"] = status
                if vec is not None:
                    st["vec"], st["primal"] = vec, _tl(obj)
                st["dual"] = _tl(dual) if _tl(dual) is not None else st["dual"]
                st["gap"], st["nodes"] = _gap(g), nodes
                self.done = True
            elif kind == "error":
                self.proc.join(5)
                self.done = True
                raise RuntimeError(f"HiGHS alt süreci hata verdi: {msg[1]}")

    def incumbent(self) -> tuple[Solution | None, float | None]:
        """Şimdiye dek bulunan en iyi çözüm (modelin Solution'ı) ve amaç değeri (TL)."""
        v = self.st["vec"]
        return (self.model.from_vector(v) if v is not None else None), self.st["primal"]

    def terminate(self, status: str = "Interrupted by user") -> None:
        if self.done:
            return
        self.poll()
        if self.done:
            return
        self.st["interrupted"] = True
        self.st["status"] = status
        self.proc.terminate()
        self.done = True

    def result(self) -> MilpResult:
        self.proc.join(5)
        if self.proc.is_alive():
            self.proc.kill()
        st = self.st
        wall = time.perf_counter() - self.t0
        sol = self.model.from_vector(st["vec"]) if st["vec"] is not None else None
        g = st["gap"]
        if g is None and sol is not None and st["primal"] and st["dual"] is not None:
            g = abs(st["primal"] - st["dual"]) / max(abs(st["primal"]), 1e-9)
        return MilpResult(status=st["status"], status_tr=MODEL_STATUS_TR.get(st["status"], st["status"]),
                          solution=sol, primal_tl=st["primal"] if sol is not None else None, dual_tl=st["dual"],
                          gap=g if sol is not None else None, nodes=int(st["nodes"] or 0), time_s=wall,
                          first_incumbent_s=st["first_inc"], first_dual_s=st["first_dual"], trace=self.trace,
                          start_accepted=st["start_ok"], interrupted=st["interrupted"], model=self.model.size(),
                          spawn_s=st["t_run0"])


def _isolated_worker(payload: dict, q) -> None:
    """Ayrı süreçte HiGHS MIP çözümü; olayları kuyruğa yazar (yalnız numpy + highspy kullanır)."""
    try:
        import highspy as hs
        h = hs.Highs()
        h.setOptionValue("output_flag", True)
        h.setOptionValue("log_to_console", False)
        for k, v in payload["options"].items():
            h.setOptionValue(k, v)
        h.passModel(payload["ncol"], payload["nrow"], payload["nnz"], 1, 1, 0.0, payload["cost"], payload["lb"],
                    payload["ub"], payload["rlo"], payload["rhi"], payload["start_"], payload["index"],
                    payload["value"], payload["integrality"])

        def on_inc(e):
            d = e.data_out
            q.put(("inc", d.running_time, d.objective_function_value, d.mip_dual_bound, d.mip_gap,
                   int(d.mip_node_count), np.asarray(d.mip_solution, dtype=float).copy()))

        def on_log(e):
            d = e.data_out
            q.put(("log", d.running_time, d.mip_primal_bound, d.mip_dual_bound, d.mip_gap, int(d.mip_node_count)))

        h.cbMipImprovingSolution.subscribe(on_inc)
        h.cbMipLogging.subscribe(on_log)
        ok = None
        if payload.get("start") is not None:
            n = payload["ncol"]
            ok = h.setSolution(n, np.arange(n, dtype=np.int32), payload["start"]) != hs.HighsStatus.kError
        q.put(("started", ok))
        h.run()
        info = h.getInfo()
        vec = np.asarray(h.getSolution().col_value, dtype=float) if info.primal_solution_status == 2 else None
        q.put(("done", h.modelStatusToString(h.getModelStatus()), info.objective_function_value,
               info.mip_dual_bound, info.mip_gap, int(info.mip_node_count), vec))
    except BaseException as ex:  # noqa: BLE001
        q.put(("error", repr(ex)))


def _tl(x) -> float | None:
    """Ölçekli amaç (milyon TL) → TL; sonsuz/tanımsız → None."""
    if x is None or not math.isfinite(x) or abs(x) >= 1e50:
        return None
    return float(x) * SCALE


def _gap(g) -> float | None:
    if g is None or not math.isfinite(g) or g >= 1e50:
        return None
    return float(g)
