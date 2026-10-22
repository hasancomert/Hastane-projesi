"""Model örneği: talep hücreleri I, aday hücreler J, yaylar A (i → j, yol km) ve maliyet vektörleri — yalnız numpy/scipy.

İki katman (şartname 4.6):
  Structure  ızgara, senaryo, aday kuralı, R, k, dolambaç ve "aç" kilitleriyle eklenen hücrelere bağlıdır; yavaş kısım
             (aday kuralı, kapsama onarımı, k-en-yakın listeleri). structure_key ile önbelleğe alınır.
  Instance   Structure + parametreler + kilitler → talep q_i, sınıf tablosu, Sabit_jk / Değişken_jk, erişim katsayıları,
             sınırlar. Yalnız maliyet/sınır değiştiren parametreler Structure'ı yeniden kurmaz.

Kilitler: {"open": [hücre kimliği, …], "closed": [...]} (kimlik biçimi '<s>km_<ix>_<iy>', grid 'id' dizisi).
  "open"   hücrede hastane zorunlu (y_j = 1). Aday değilse J'ye eklenir (yapı değişir).
  "closed" hücrede hastane yasak (y_j = 0). Yalnız sınır değişir; J'den çıkarılmaz (bölgedeki tüm adaylar yasaklanırsa
           ön denetim Türkçe bir olurlu-değil mesajı verir).
"""
from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

from . import distance, grid_io
from .params import Params, ParamError, stable_hash

_GRID_CACHE: dict[int, grid_io.Grid] = {}
_STRUCT_CACHE: "OrderedDict[str, Structure]" = OrderedDict()
STRUCT_CACHE_SIZE = 12


class InfeasibleError(ValueError):
    """Ayarlar altında olurlu çözüm yok (mesaj Türkçe)."""


def get_grid(cell_km: float) -> grid_io.Grid:
    k = int(cell_km)
    if k not in _GRID_CACHE:
        _GRID_CACHE[k] = grid_io.load_grid(k)
    return _GRID_CACHE[k]


# ---------------------------------------------------------------- kilitler
@dataclass(frozen=True)
class Locks:
    open: tuple[str, ...] = ()
    closed: tuple[str, ...] = ()

    @classmethod
    def parse(cls, locks: "Locks | Mapping | None") -> "Locks":
        if locks is None:
            return cls()
        if isinstance(locks, Locks):
            return locks
        op = tuple(sorted(set(str(c) for c in (locks.get("open") or ()))))
        cl = tuple(sorted(set(str(c) for c in (locks.get("closed") or ()))))
        both = set(op) & set(cl)
        if both:
            raise ParamError(f"Aynı hücre hem 'aç' hem 'yasak' kilitli: {', '.join(sorted(both))}")
        return cls(open=op, closed=cl)

    def as_dict(self) -> dict:
        return {"open": list(self.open), "closed": list(self.closed)}


# ---------------------------------------------------------------- yapı
@dataclass
class Structure:
    key: str
    cell_km: float
    source: str
    year: int
    R_km: float
    k_nearest: int                # 0 = R içindeki tüm adaylar
    demand_cells: np.ndarray      # ızgara indisi, |I|
    w: np.ndarray                 # nüfus (float), |I|
    cand_cells: np.ndarray        # ızgara indisi, |J| (artan)
    cand_origin: np.ndarray       # 0 = kural, 1 = kapsama onarımı, 2 = 'aç' kilidi
    arc_ptr: np.ndarray           # CSR satır başları (talep sırası), |I|+1
    arc_j: np.ndarray             # aday konumu (cand_cells sırası), |A|
    arc_d: np.ndarray             # yol km, |A|
    arc_i: np.ndarray             # talep konumu, |A|
    density: np.ndarray           # aday hücre nüfus yoğunluğu (kişi/km², il içi alan)
    repair: dict
    build_s: float
    circ: distance.Circuity = field(repr=False)
    demand_xy: np.ndarray = field(default=None, repr=False)   # UTM 36N m (mesafe denetimi için)
    cand_xy: np.ndarray = field(default=None, repr=False)
    cand_ids: np.ndarray = field(default=None, repr=False)     # hücre kimlikleri (str)
    demand_ids: np.ndarray = field(default=None, repr=False)

    @property
    def nI(self) -> int:
        return int(len(self.demand_cells))

    @property
    def nJ(self) -> int:
        return int(len(self.cand_cells))

    @property
    def nA(self) -> int:
        return int(len(self.arc_j))

    @property
    def grid(self) -> grid_io.Grid:
        return get_grid(self.cell_km)

    def arcs_by_candidate(self) -> tuple[np.ndarray, np.ndarray]:
        """Adaya göre sıralı yay sırası ve CSR başları: (order, ptr) — yay order[ptr[j]:ptr[j+1]] adayı j'ye gider."""
        order = np.argsort(self.arc_j, kind="stable")
        ptr = np.concatenate(([0], np.cumsum(np.bincount(self.arc_j, minlength=self.nJ))))
        return order, ptr


def structure_key(cell_km: float, params: Params, locks: Locks) -> str:
    return stable_hash({"cell_km": int(cell_km), "s": params.structure_values(), "lock_open": list(locks.open)})


def build_structure(cell_km: float, params: Params, locks: Locks) -> Structure:
    t0 = time.perf_counter()
    v = params.values
    g = get_grid(cell_km)
    src = grid_io.source_key(v["population.grid_source"])
    year = params.scenario_year
    pop = g.pop(src, year).astype(float)
    circ = distance.Circuity.from_params(v)
    R = float(v["access.max_distance_km"])
    k = int(v["grid.k_nearest"]) or None           # 0 → R içindeki tüm adaylar
    rule = grid_io.RuleParams.from_params(v)

    dem = np.flatnonzero(pop > 0)
    base = grid_io.candidate_mask(g, rule, src, year)
    rep = grid_io.coverage_repair(g, base, rule, R, circ, src, year)
    mask = rep.mask.copy()
    origin_full = np.full(g.n, -1, dtype=np.int8)
    origin_full[base] = 0
    origin_full[rep.added] = 1
    id_to_idx = {s: i for i, s in enumerate(g.id.tolist())}
    unknown = [c for c in locks.open + locks.closed if c not in id_to_idx]
    if unknown:
        raise ParamError(f"Bilinmeyen hücre kimliği ({int(cell_km)} km ızgarası): {', '.join(unknown[:5])}")
    for c in locks.open:
        i = id_to_idx[c]
        if not mask[i]:
            mask[i] = True
            origin_full[i] = 2
    J = np.flatnonzero(mask)
    xy = g.xy
    nl = distance.neighbor_lists(xy[dem], xy[J], g.cell_km, R, k, circ)
    counts = np.diff(nl.indptr)
    arc_i = np.repeat(np.arange(len(dem), dtype=np.int32), counts)
    with np.errstate(divide="ignore", invalid="ignore"):
        dens = np.where(g.area_km2[J] > 0, pop[J] / g.area_km2[J], 0.0)
    return Structure(key=structure_key(cell_km, params, locks), cell_km=float(g.cell_km), source=src, year=year,
                     R_km=R, k_nearest=k or 0, demand_cells=dem, w=pop[dem], cand_cells=J, cand_origin=origin_full[J],
                     arc_ptr=nl.indptr.astype(np.int64), arc_j=nl.indices.astype(np.int32), arc_d=nl.dist_km,
                     arc_i=arc_i, density=dens, repair=rep.summary(pop), build_s=time.perf_counter() - t0, circ=circ,
                     demand_xy=xy[dem], cand_xy=xy[J], cand_ids=g.id[J], demand_ids=g.id[dem])


def get_structure(cell_km: float, params: Params, locks: Locks) -> tuple[Structure, bool]:
    """Önbellekli yapı; (structure, önbellekten_mi)."""
    key = structure_key(cell_km, params, locks)
    if key in _STRUCT_CACHE:
        _STRUCT_CACHE.move_to_end(key)
        return _STRUCT_CACHE[key], True
    s = build_structure(cell_km, params, locks)
    _STRUCT_CACHE[key] = s
    while len(_STRUCT_CACHE) > STRUCT_CACHE_SIZE:
        _STRUCT_CACHE.popitem(last=False)
    return s, False


def clear_cache() -> None:
    _STRUCT_CACHE.clear()


# ---------------------------------------------------------------- örnek
@dataclass
class Instance:
    """Bir çözümün tüm sayısal girdileri. Maliyetler TL; mesafeler km; yatak sürekli."""

    s: Structure
    params: Params
    locks: Locks
    q: np.ndarray                 # yatak talebi, |I|
    classes: list                 # params.SizeClass (K = 4, sıra sabit)
    L: np.ndarray                 # K
    U: np.ndarray                 # K
    class_on: np.ndarray          # K bool
    fixed: np.ndarray             # (J, K) TL / hastane
    var: np.ndarray               # (J, K) TL / yatak (arsa dahil)
    land_per_bed: np.ndarray      # J, TL / yatak
    land_price: np.ndarray        # J, TL / m²
    acc: np.ndarray               # A, TL (λ · w_i · d^α)
    lock_open: np.ndarray         # J bool
    lock_closed: np.ndarray       # J bool
    mode: str
    count_min: int
    count_max: int | None
    budget: float | None
    from_cache: bool = False
    warnings: list = field(default_factory=list)

    @property
    def nI(self) -> int:
        return self.s.nI

    @property
    def nJ(self) -> int:
        return self.s.nJ

    @property
    def nK(self) -> int:
        return len(self.classes)

    @property
    def nA(self) -> int:
        return self.s.nA

    @property
    def y_ub(self) -> np.ndarray:
        """(J, K) açılabilir mi: sınıf açık VE hücre yasak değil."""
        return (self.class_on[None, :] & ~self.lock_closed[:, None])

    @property
    def total_demand(self) -> float:
        return float(self.q.sum())

    def opening_cost(self, j: np.ndarray, k: np.ndarray, beds: np.ndarray) -> np.ndarray:
        """Açılış maliyeti (TL) = Sabit_jk + Değişken_jk · yatak."""
        return self.fixed[j, k] + self.var[j, k] * beds

    def cell_ids(self, cand_pos: np.ndarray) -> list[str]:
        return [str(x) for x in self.s.cand_ids[np.asarray(cand_pos, dtype=int)]]

    def precheck(self) -> None:
        """Hızlı olurluluk denetimi; olurlu değilse Türkçe InfeasibleError."""
        s = self.s
        openable = ~self.lock_closed
        reach = np.zeros(s.nI, dtype=bool)
        np.logical_or.at(reach, s.arc_i, openable[s.arc_j])
        no_arc = np.diff(s.arc_ptr) == 0
        if (~reach).any():
            bad = np.flatnonzero(~reach)
            pop = int(round(s.w[bad].sum()))
            cause = ("yasaklanan (kilitli) hücreler nedeniyle" if not no_arc[bad].all()
                     else "aday kümesinde")
            ids = [str(x) for x in s.demand_ids[bad[:5]]]
            raise InfeasibleError(
                f"Olurlu çözüm yok: {len(bad)} talep hücresinin ({_fmt_int(pop)} kişi) R = {_fmt(s.R_km)} km içinde "
                f"açılabilir aday hastane yeri kalmadı ({cause}). Örnek hücreler: {', '.join(ids)}. "
                f"Bu bölgedeki yasakları kaldırın ya da azami erişim mesafesini (R) artırın.")
        n_open = int(self.lock_open.sum())
        n_avail = int(openable.sum())
        Umax = float(self.U[self.class_on].max())
        Q = self.total_demand
        need = math.ceil(Q / Umax - 1e-9)
        if self.count_max is not None:
            if n_open > self.count_max:
                raise InfeasibleError(f"Olurlu çözüm yok: 'aç' kilitli hücre sayısı ({n_open}) en çok hastane "
                                      f"sayısını ({self.count_max}) aşıyor.")
            if need > self.count_max:
                city = "şehir hastanesi sınıfı kapalıyken " if not self.class_on[-1] else ""
                raise InfeasibleError(
                    f"Olurlu çözüm yok: toplam yatak talebi {_fmt(Q, 1)} yatak; {city}en büyük hastane "
                    f"{_fmt_int(Umax)} yatak olabildiğinden en az {need} hastane gerekir, ama en çok hastane sayısı "
                    f"{self.count_max}. Sınırı artırın" + (" ya da şehir hastanesi sınıfını açın." if city else "."))
        if self.count_min > n_avail:
            raise InfeasibleError(f"Olurlu çözüm yok: en az {self.count_min} hastane isteniyor ama açılabilir aday "
                                  f"hücre sayısı {n_avail}.")
        if need > n_avail:
            raise InfeasibleError(f"Olurlu çözüm yok: talep için en az {need} hastane gerekir, açılabilir aday "
                                  f"hücre sayısı {n_avail}.")
        if self.budget is not None:
            lb = self.opening_cost_lower_bound()
            if self.budget < lb:
                raise InfeasibleError(
                    f"Olurlu çözüm yok: bütçe {_fmt_tl(self.budget)}, ama toplam yatak talebini ({_fmt(Q, 1)} yatak) "
                    f"karşılamanın en düşük açılış maliyeti en az {_fmt_tl(lb)}. Bütçeyi artırın.")

    def opening_cost_lower_bound(self) -> float:
        """Açılış maliyeti için basit alt sınır: talep × en ucuz yatak + gereken en az hastane × en ucuz sabit."""
        ub = self.y_ub
        if not ub.any():
            return math.inf
        Umax = float(self.U[self.class_on].max())
        n_min = max(math.ceil(self.total_demand / Umax - 1e-9), int(self.lock_open.sum()), self.count_min, 1)
        return float(self.total_demand * self.var[ub].min() + n_min * self.fixed[ub].min())


def _fmt(x: float, nd: int = 1) -> str:
    """Türkçe sayı biçimi: 1.234,5"""
    s = f"{x:,.{nd}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _fmt_int(x: float) -> str:
    return _fmt(round(x), 0)


def _fmt_tl(x: float) -> str:
    if abs(x) >= 1e9:
        return f"{_fmt(x / 1e9, 1)} milyar TL"
    return f"{_fmt(x / 1e6, 1)} milyon TL"


def build_instance(cell_km: float | None = None, params: Params | Mapping | None = None,
                   locks: Locks | Mapping | None = None, *, structure: Structure | None = None) -> Instance:
    """Parametrelerden örnek kurar (yapı önbellekten)."""
    p = params if isinstance(params, Params) else Params.load(params)
    lk = Locks.parse(locks)
    km = int(cell_km if cell_km is not None else p["grid.cell_km"])
    if km not in (2, 3, 5):
        raise ParamError(f"Hücre boyutu 2, 3 ya da 5 km olmalı (verilen: {cell_km})")
    cached = False
    if structure is None:
        structure, cached = get_structure(km, p, lk)
    s = structure
    q = s.w * p.beds_per_person
    classes = p.classes
    L = np.array([c.L for c in classes])
    U = np.array([c.U for c in classes])
    on = np.array([c.enabled for c in classes])
    med = float(np.median(s.density)) if s.nJ else 0.0
    land_price = (p.land_price(s.density, med) if p["land.include"] else np.zeros(s.nJ))
    land_bed = p.land_cost_per_bed(s.density, med)
    fixed = np.tile(np.array([c.fixed for c in classes]), (s.nJ, 1))
    var = np.array([c.var for c in classes])[None, :] + land_bed[:, None]
    acc = p.lam * s.w[s.arc_i] * np.power(s.arc_d, p.alpha)
    pos = {str(c): j for j, c in enumerate(s.cand_ids)}
    lock_open = np.zeros(s.nJ, dtype=bool)
    lock_closed = np.zeros(s.nJ, dtype=bool)
    warnings: list[str] = list(p.warnings)
    for c in lk.open:
        if c not in pos:
            raise ParamError(f"Bilinmeyen hücre kimliği: {c}")
        lock_open[pos[c]] = True
        if s.source != "toy" and s.cand_origin[pos[c]] == 2:
            warnings.append(f"'{c}' aday kuralına göre aday değil; kullanıcı kilidiyle aday kümesine eklendi")
    for c in lk.closed:
        j = pos.get(c)
        if j is None:
            if s.source != "toy" and c not in set(s.grid.id.tolist()):
                raise ParamError(f"Bilinmeyen hücre kimliği: {c}")
            warnings.append(f"'{c}' zaten aday değil; yasak kilidi etkisiz")
        else:
            lock_closed[j] = True
    cmin, cmax = p.count_bounds
    return Instance(s=s, params=p, locks=lk, q=q, classes=classes, L=L, U=U, class_on=on, fixed=fixed, var=var,
                    land_per_bed=land_bed, land_price=land_price, acc=acc, lock_open=lock_open,
                    lock_closed=lock_closed, mode=p.objective_mode, count_min=cmin, count_max=cmax, budget=p.budget,
                    from_cache=cached, warnings=warnings)


def toy_instance(*, demand_xy: Sequence, w: Sequence, cand_xy: Sequence, params: Params, cell_km: float = 3.0,
                 R_km: float | None = None, k: int | None = None, locks: Locks | Mapping | None = None) -> Instance:
    """Testler için ızgarasız küçük örnek (koordinatlar metre). Kimlikler: talep 'd<i>', aday 'c<j>'."""
    dxy = np.asarray(demand_xy, dtype=float).reshape(-1, 2)
    cxy = np.asarray(cand_xy, dtype=float).reshape(-1, 2)
    circ = distance.Circuity.from_params(params.values)
    R = float(params["access.max_distance_km"] if R_km is None else R_km)
    nl = distance.neighbor_lists(dxy, cxy, cell_km, R, k, circ)
    counts = np.diff(nl.indptr)
    w = np.asarray(w, dtype=float)
    s = Structure(key=f"toy-{stable_hash([dxy.tolist(), w.tolist(), cxy.tolist(), R, k])}", cell_km=cell_km,
                  source="toy", year=params.scenario_year, R_km=R, k_nearest=k or 0,
                  demand_cells=np.arange(len(dxy)), w=w, cand_cells=np.arange(len(cxy)),
                  cand_origin=np.zeros(len(cxy), dtype=np.int8), arc_ptr=nl.indptr.astype(np.int64),
                  arc_j=nl.indices.astype(np.int32), arc_d=nl.dist_km,
                  arc_i=np.repeat(np.arange(len(dxy), dtype=np.int32), counts),
                  density=np.ones(len(cxy)), repair={}, build_s=0.0, circ=circ, demand_xy=dxy, cand_xy=cxy,
                  cand_ids=np.array([f"c{j}" for j in range(len(cxy))]),
                  demand_ids=np.array([f"d{i}" for i in range(len(dxy))]))
    return build_instance(cell_km=3, params=params, locks=locks, structure=s)


# ---------------------------------------------------------------- çözüm
@dataclass
class Solution:
    """Bir çözüm (aday konumları üzerinden): açık hücreler, sınıf indisi, yatak, yay payları x (|A|, yoğun)."""

    open_j: np.ndarray            # int, artan
    cls: np.ndarray               # int, open_j ile aynı uzunluk (classes sırası)
    beds: np.ndarray              # float
    x: np.ndarray                 # float, |A|
    source: str = ""

    @classmethod
    def empty(cls, nA: int) -> "Solution":
        return cls(np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(nA))

    def opening_tl(self, inst: "Instance") -> float:
        return float(inst.opening_cost(self.open_j, self.cls, self.beds).sum()) if len(self.open_j) else 0.0

    def access_tl(self, inst: "Instance") -> float:
        return float(inst.acc @ self.x)

    def objective_tl(self, inst: "Instance") -> float:
        """Modun amaç değeri (TL): toplam/sabit_p → açılış + erişim; butce → erişim."""
        if inst.mode == "butce":
            return self.access_tl(inst)
        return self.opening_tl(inst) + self.access_tl(inst)

    @property
    def n_open(self) -> int:
        return int(len(self.open_j))
