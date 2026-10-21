"""Izgara verisini yükler; aday kuralı ve kapsama onarımı — yalnız numpy (+ distance modülü üzerinden scipy).

Veri: data/processed/grid/grid_<s>km.npz (pipeline/22_grids.py üretir). Hücre başına diziler (n = hücre sayısı):
  id, ix, iy, x, y (UTM 36N merkez, m), lon, lat, inside_frac, area_km2 (il içi alan), district / district_area
  (TÜİK sırasında ilçe indisi; nüfusa göre — eşitlikte yuvarlanmamış kalibre nüfus, sonra hücredeki ilçe alanı — /
  alana göre), pop<yıl>_<kaynak> (int64; kaynak 'wp' = WorldPop R2025A, 'ghs' = GHS-POP R2023A; yıl 2025 = TÜİK 2025'e
  kalibre, 2030 = senaryo), popx/popy2025_<kaynak> (nüfus ağırlık merkezi, m; kaynak pikseli yoksa hücre merkezi),
  water_pct, wetland_pct, built_pct, water_frac, lc_pixels, nbr8 (8 komşu indisi, yoksa −1),
  nbr_max_pop<yıl>_<kaynak>, admissible, candidate (varsayılan kural).

Aday kuralı (şartname 4.3.6; parametreler data/reference/params/izgara.yaml):
  uygun (admissible) = inside_frac ≥ grid.candidate_min_inside  VE  water_frac < grid.candidate_max_water
  aday               = uygun VE (nüfus ≥ grid.candidate_min_pop
                                 VEYA  8 komşudan en kalabalığının nüfusu ≥ grid.candidate_near_density × s²)
İkinci koşul şartnamedeki "yerleşime yakın" ifadesinin karşılığıdır. Eşik yoğunluk (kişi/km²) olarak verilir ve hücre
alanıyla (s² km², s = hücre kenarı) hücre başına kişiye çevrilir: 1.500 kişi/km² → 2 km 6.000, 3 km 13.500, 5 km 37.500.

Kapsama onarımı: R içinde hiç adayı olmayan her talep hücresi için (nüfusu büyükten küçüğe), nüfus ağırlık merkezine en
yakın UYGUN hücre (çoğu zaman kendisi) adaylara eklenir; önceki eklemeyle zaten kapsanmışsa atlanır.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import numpy as np

from . import distance

ROOT = Path(__file__).resolve().parents[2]
GRID_DIR = ROOT / "data" / "processed" / "grid"
PARAMS_JSON = ROOT / "data" / "reference" / "parameters.json"

SOURCES = ("wp", "ghs")
YEARS = (2025, 2030)
# population.grid_source değeri -> npz kaynak kısaltması
SOURCE_KEYS = {"worldpop_r2025a": "wp", "ghs_pop_r2023a": "ghs"}


def load_params(path: Path = PARAMS_JSON) -> dict:
    """parameters.json (anahtar -> {value, ...})."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _value(params: Mapping, key: str):
    v = params[key]
    return v["value"] if isinstance(v, Mapping) and "value" in v else v


def source_key(name: str) -> str:
    """'worldpop_r2025a' / 'ghs_pop_r2023a' / 'wp' / 'ghs' -> 'wp' | 'ghs'."""
    if name in SOURCES:
        return name
    try:
        return SOURCE_KEYS[name]
    except KeyError:
        raise ValueError(f"bilinmeyen nüfus kaynağı: {name!r}") from None


@dataclass(frozen=True)
class RuleParams:
    """Aday kuralı parametreleri."""

    min_pop: float          # kişi / hücre
    near_density: float     # kişi / km² (komşu hücrenin tüm alanına göre)
    min_inside: float
    max_water: float
    near_rule: bool = True

    def near_threshold(self, cell_km: float) -> float:
        """Yakınlık eşiği, kişi / hücre: near_density × s² (s = hücre kenarı, km)."""
        return self.near_density * float(cell_km) ** 2

    @classmethod
    def from_params(cls, params: Mapping, **override) -> "RuleParams":
        base = dict(min_pop=float(_value(params, "grid.candidate_min_pop")),
                    near_density=float(_value(params, "grid.candidate_near_density")),
                    min_inside=float(_value(params, "grid.candidate_min_inside")),
                    max_water=float(_value(params, "grid.candidate_max_water")))
        base.update(override)
        return cls(**base)


@dataclass
class Grid:
    """Bir hücre boyutunun tüm dizileri. g.x, g.pop2025_wp ... ile erişilir."""

    cell_km: float
    arrays: dict[str, np.ndarray] = field(repr=False)

    def __getattr__(self, name: str) -> np.ndarray:
        try:
            return self.__dict__["arrays"][name]
        except KeyError:
            raise AttributeError(name) from None

    @property
    def n(self) -> int:
        return int(self.arrays["x"].shape[0])

    @property
    def xy(self) -> np.ndarray:
        return np.column_stack([self.arrays["x"], self.arrays["y"]])

    def pop(self, source: str = "wp", year: int = 2025) -> np.ndarray:
        if year not in YEARS:
            raise ValueError(f"yıl {YEARS} içinde olmalı: {year}")
        return self.arrays[f"pop{year}_{source_key(source)}"]

    def pop_xy(self, source: str = "wp") -> np.ndarray:
        """2025 nüfus ağırlık merkezleri (kaynak pikseli yoksa hücre merkezi)."""
        s = source_key(source)
        return np.column_stack([self.arrays[f"popx2025_{s}"], self.arrays[f"popy2025_{s}"]])

    def neighbor_max(self, values: np.ndarray) -> np.ndarray:
        """8 komşu hücredeki en büyük değer (komşu yoksa 0; değerler ≥ 0 varsayılır)."""
        nb = self.arrays["nbr8"]
        v = np.asarray(values)
        return np.where(nb >= 0, v[np.maximum(nb, 0)], 0).max(axis=1)

    @property
    def district_keys(self) -> list[str]:
        return [str(s) for s in self.arrays["district_keys"]]

    @property
    def district_names(self) -> list[str]:
        return [str(s) for s in self.arrays["district_names"]]


def grid_path(cell_km: float, grid_dir: Path = GRID_DIR) -> Path:
    return Path(grid_dir) / f"grid_{int(cell_km)}km.npz"


def load_grid(cell_km: float, grid_dir: Path = GRID_DIR) -> Grid:
    """grid_<s>km.npz dosyasını yükler (allow_pickle=False; yalnız sayısal ve unicode diziler)."""
    with np.load(grid_path(cell_km, grid_dir), allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    s = float(arrays["cell_km"])
    if abs(s - float(cell_km)) > 1e-9:
        raise ValueError(f"dosyadaki hücre boyutu {s} ≠ {cell_km}")
    return Grid(cell_km=s, arrays=arrays)


# ---------------------------------------------------------------- aday kuralı
def admissible_mask(grid: Grid, min_inside: float, max_water: float) -> np.ndarray:
    """İl içinde (inside_frac ≥ eşik) ve büyük kısmı su olmayan (water_frac < eşik) hücreler."""
    wf = grid.water_frac
    return (grid.inside_frac >= min_inside) & np.isfinite(wf) & (wf < max_water)


def candidate_mask(grid: Grid, rule: RuleParams, source: str = "wp", year: int = 2025) -> np.ndarray:
    """Aday hücreler J (kapsama onarımı hariç)."""
    pop = grid.pop(source, year)
    ok = admissible_mask(grid, rule.min_inside, rule.max_water)
    settled = pop >= rule.min_pop
    if rule.near_rule:
        settled |= grid.neighbor_max(pop) >= rule.near_threshold(grid.cell_km)
    return ok & settled


def demand_mask(grid: Grid, source: str = "wp", year: int = 2025) -> np.ndarray:
    """Talep hücreleri I: nüfusu > 0."""
    return grid.pop(source, year) > 0


# ---------------------------------------------------------------- kapsama
@dataclass
class Coverage:
    covered: np.ndarray            # talep hücresi başına bool (demand sırasında)
    demand_idx: np.ndarray         # talep hücrelerinin ızgara indisleri
    uncovered_idx: np.ndarray      # R içinde adayı olmayan talep hücreleri (ızgara indisi)
    uncovered_pop: int
    total_pop: int

    @property
    def share_covered_pop(self) -> float:
        return 1.0 - self.uncovered_pop / self.total_pop if self.total_pop else 1.0


def coverage(grid: Grid, cand: np.ndarray, R_km: float, circ: distance.Circuity,
             source: str = "wp", year: int = 2025) -> Coverage:
    """Her talep hücresinin R (yol km) içinde en az bir adayı var mı?"""
    pop = grid.pop(source, year)
    dem = np.flatnonzero(pop > 0)
    cidx = np.flatnonzero(cand)
    xy = grid.xy
    nl = distance.neighbor_lists(xy[dem], xy[cidx], grid.cell_km, R_km, 1, circ)
    cov = nl.counts > 0
    unc = dem[~cov]
    return Coverage(covered=cov, demand_idx=dem, uncovered_idx=unc, uncovered_pop=int(pop[unc].sum()),
                    total_pop=int(pop[dem].sum()))


@dataclass
class RepairResult:
    mask: np.ndarray                 # onarılmış aday maskesi
    added: np.ndarray                # eklenen hücre indisleri (ekleme sırasında)
    uncovered_before: np.ndarray     # onarım öncesi kapsanmayan talep hücreleri
    uncovered_after: np.ndarray      # onarım sonrası hâlâ kapsanmayanlar (R içinde uygun hücre yok)
    no_admissible_within_R: np.ndarray  # R içinde hiç UYGUN hücresi olmayan talep hücreleri

    def summary(self, pop: np.ndarray) -> dict:
        return {"uncovered_before_cells": int(len(self.uncovered_before)),
                "uncovered_before_pop": int(pop[self.uncovered_before].sum()),
                "added_cells": int(len(self.added)),
                "uncovered_after_cells": int(len(self.uncovered_after)),
                "uncovered_after_pop": int(pop[self.uncovered_after].sum()),
                "no_admissible_within_R_cells": int(len(self.no_admissible_within_R)),
                "no_admissible_within_R_pop": int(pop[self.no_admissible_within_R].sum())}


def coverage_repair(grid: Grid, cand: np.ndarray, rule: RuleParams, R_km: float, circ: distance.Circuity,
                    source: str = "wp", year: int = 2025) -> RepairResult:
    """Kapsanmayan talep hücreleri için nüfusa en yakın uygun hücreyi aday yapar (deterministik)."""
    pop = grid.pop(source, year)
    xy = grid.xy
    mask = np.asarray(cand, dtype=bool).copy()
    adm = admissible_mask(grid, rule.min_inside, rule.max_water)
    adm_idx = np.flatnonzero(adm)
    before = coverage(grid, mask, R_km, circ, source, year).uncovered_idx

    # R içinde hiç uygun hücre olmayan talep hücreleri (onarım bunları kapsayamaz)
    dem = np.flatnonzero(pop > 0)
    nl_adm = distance.neighbor_lists(xy[dem], xy[adm_idx], grid.cell_km, R_km, 1, circ)
    no_adm = dem[nl_adm.counts == 0]

    added: list[int] = []
    if len(before) and len(adm_idx):
        # nüfusu büyükten küçüğe; eşitlikte ızgara indisi
        order = before[np.lexsort((before, -pop[before]))]
        nearest, _ = distance.nearest_euclid(grid.pop_xy(source)[order], xy[adm_idx])
        for i, a in zip(order, adm_idx[nearest]):
            if added:
                # önceki eklemeler bu hücreyi kapsıyor mu?
                d = distance.pairwise_road_km(xy[[i]], xy[added], grid.cell_km, circ)[0]
                if (d <= R_km).any():
                    continue
            if not mask[a]:
                mask[a] = True
                added.append(int(a))
    after = coverage(grid, mask, R_km, circ, source, year).uncovered_idx
    return RepairResult(mask=mask, added=np.array(added, dtype=np.int64), uncovered_before=before,
                        uncovered_after=after, no_admissible_within_R=no_adm)


def default_candidates(grid: Grid, params: Mapping | None = None, *, source: str | None = None,
                       year: int = 2025, repair: bool = True) -> np.ndarray:
    """Varsayılan parametrelerle aday maskesi (isteğe bağlı kapsama onarımıyla)."""
    params = load_params() if params is None else params
    src = source_key(source or _value(params, "population.grid_source"))
    rule = RuleParams.from_params(params)
    cand = candidate_mask(grid, rule, src, year)
    if repair:
        circ = distance.Circuity.from_params(params)
        R = float(_value(params, "access.max_distance_km"))
        cand = coverage_repair(grid, cand, rule, R, circ, src, year).mask
    return cand
