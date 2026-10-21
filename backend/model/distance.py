"""Çalışma zamanı mesafe modülü — yalnız numpy + scipy (sunum dizüstünde geopandas/pyproj yok).

Yol mesafesi (şartname 4.3.7, öğrenci kararı KARARLAR #7):

    d_ij = e_ij · k(e_ij) · ölçek

  e_ij   UTM 36N (EPSG:32636) hücre merkezleri arası kuş uçuşu mesafe (km)
  k(e)   dolambaç katsayısı:
           'mesafeye_bagli' → access.circuity_profile [[e_km, k], ...] noktaları arasında doğrusal enterpolasyon,
                              ilk noktadan kısa / son noktadan uzun mesafede uç değer (pipeline/21_circuity_profile.py
                              circuity_at ile aynı: np.interp)
           'sabit'          → tek katsayı access.circuity
  ölçek  access.circuity_scale (duyarlılık çarpanı, varsayılan 1,0)

Hücre içi yolculuk (i == j): e = s · (√2 + ln(1 + √2)) / 6 ≈ 0,3826 · s alınır. Bu, kenarı s olan bir karede düzgün
dağılmış rastgele bir noktanın karenin merkezine ortalama (beklenen) uzaklığıdır — standart geometrik sonuç:
    E|P − c| = (1/s²) ∫∫_[−s/2, s/2]² √(x² + y²) dx dy = s · (√2 + ln(1 + √2)) / 6.
(Hücre nüfusu hücre içinde düzgün, hastane hücre merkezinde varsayılır; Monte Carlo testi tests/test_phase2_grids.py.)

Seyrek komşu listeleri (şartname 4.3.7, 4.5 N_i): her talep hücresi için d ≤ R olan, YOL mesafesine göre en yakın k aday
tutulur; k'ıncı mesafeye eşit (TIE_TOL_KM içinde) adayların hepsi listede kalır (satır k'dan biraz uzun olabilir).
Güvenli kuş uçuşu ön süzgeci: d = e · k(e) · ölçek ≥ e · min_k · ölçek olduğundan d ≤ R ⇒ e ≤ R / (min_k · ölçek).
Koordinatlar metre (UTM 36N), mesafeler km.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, NamedTuple, Sequence

import numpy as np
from scipy.spatial import cKDTree

MODES = ("mesafeye_bagli", "sabit")

# Kenarı 1 olan karede düzgün noktanın merkeze ortalama uzaklığı: (√2 + ln(1+√2)) / 6 = 0,38259785...
INTRA_CELL_FACTOR = (math.sqrt(2.0) + math.log(1.0 + math.sqrt(2.0))) / 6.0
# k-en-yakın kesiminde eşit mesafe toleransı (km): düzenli ızgarada simetrik hücreler aynı mesafededir
TIE_TOL_KM = 1e-9


def intra_cell_euclid_km(cell_km: float) -> float:
    """Hücre içi ortalama kuş uçuşu mesafe (km): s · (√2 + ln(1+√2)) / 6 ≈ 0,3826 · s."""
    return float(cell_km) * INTRA_CELL_FACTOR


def _value(params: Mapping, key: str):
    v = params[key]
    return v["value"] if isinstance(v, Mapping) and "value" in v else v


@dataclass(frozen=True)
class Circuity:
    """Dolambaç katsayısı k(e) ve yol mesafesi d = e · k(e) · ölçek."""

    mode: str = "mesafeye_bagli"
    profile: tuple[tuple[float, float], ...] = ()
    constant: float | None = None
    scale: float = 1.0

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"geçersiz dolambaç modu: {self.mode!r} (izinli: {MODES})")
        if not (self.scale > 0):
            raise ValueError("dolambaç ölçeği pozitif olmalı")
        if self.mode == "mesafeye_bagli":
            if not self.profile:
                raise ValueError("'mesafeye_bagli' modu için profil gerekli")
            xs = np.array([p[0] for p in self.profile], dtype=float)
            ks = np.array([p[1] for p in self.profile], dtype=float)
            if np.any(np.diff(xs) <= 0) or np.any(ks <= 0):
                raise ValueError("profil mesafeleri artan, katsayıları pozitif olmalı")
        elif not (self.constant is not None and self.constant > 0):
            raise ValueError("'sabit' modu için pozitif katsayı gerekli")
        # np.interp için önbellek (dondurulmuş sınıfta object.__setattr__)
        object.__setattr__(self, "_xs", np.array([p[0] for p in self.profile], dtype=float))
        object.__setattr__(self, "_ks", np.array([p[1] for p in self.profile], dtype=float))

    @classmethod
    def from_params(cls, params: Mapping, *, mode: str | None = None, scale: float | None = None) -> "Circuity":
        """parameters.json sözlüğünden (anahtar -> {value, ...}) ya da düz {anahtar: değer} sözlüğünden kurar."""
        prof = tuple((float(a), float(b)) for a, b in _value(params, "access.circuity_profile"))
        return cls(mode=mode or _value(params, "access.circuity_mode"), profile=prof,
                   constant=float(_value(params, "access.circuity")),
                   scale=float(_value(params, "access.circuity_scale") if scale is None else scale))

    def k(self, e_km) -> np.ndarray:
        """Dolambaç katsayısı k(e) (ölçek hariç)."""
        e = np.asarray(e_km, dtype=float)
        if self.mode == "sabit":
            return np.full(e.shape, float(self.constant))
        return np.interp(e, self._xs, self._ks)

    def road_km(self, e_km) -> np.ndarray:
        """Yol mesafesi d = e · k(e) · ölçek (km)."""
        e = np.asarray(e_km, dtype=float)
        return e * self.k(e) * self.scale

    @property
    def min_factor(self) -> float:
        """d / e oranının alt sınırı (min_k · ölçek); ön süzgeç e ≤ R / min_factor için."""
        kmin = float(self.constant) if self.mode == "sabit" else float(self._ks.min())
        return kmin * self.scale

    def euclid_for_road_km(self, d_km: float, *, tol_km: float = 1e-9) -> float:
        """Ters dönüşüm: road_km(e) = d olan kuş uçuşu e (km), ikiye bölmeyle.

        road_km'nin e ile arttığı varsayılır (varsayılan profilde öyle; tests/test_phase2_grids.py denetler).
        """
        d = float(d_km)
        if d <= 0:
            return 0.0
        lo, hi = 0.0, d / self.min_factor          # road_km(hi) ≥ hi · min_factor = d
        while hi - lo > tol_km:
            mid = 0.5 * (lo + hi)
            if float(self.road_km(mid)) < d:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)


def road_km_pairs(e_km, cell_km: float, circ: Circuity, *, same_tol_km: float = 1e-3) -> np.ndarray:
    """Kuş uçuşu mesafelerden yol mesafesi; e ≈ 0 (aynı hücre) için hücre içi ortalama mesafe kullanılır."""
    e = np.array(e_km, dtype=float, copy=True)
    e[e < same_tol_km] = intra_cell_euclid_km(cell_km)
    return circ.road_km(e)


def pairwise_road_km(a_xy, b_xy, cell_km: float, circ: Circuity) -> np.ndarray:
    """Yoğun (|A| × |B|) yol mesafesi matrisi (km); küçük kümeler için. Koordinatlar metre."""
    a = np.asarray(a_xy, dtype=float).reshape(-1, 2)
    b = np.asarray(b_xy, dtype=float).reshape(-1, 2)
    e = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1]) / 1000.0
    return road_km_pairs(e, cell_km, circ)


class NeighborCSR(NamedTuple):
    """Satır i = talep hücresi; indices = aday konumları (cand_xy sırası); dist_km = yol km, satır içinde artan."""

    indptr: np.ndarray    # int64, uzunluk n_demand + 1
    indices: np.ndarray   # int32
    dist_km: np.ndarray   # float64

    def row(self, i: int) -> tuple[np.ndarray, np.ndarray]:
        s, t = self.indptr[i], self.indptr[i + 1]
        return self.indices[s:t], self.dist_km[s:t]

    @property
    def counts(self) -> np.ndarray:
        return np.diff(self.indptr)


def neighbor_lists(demand_xy, cand_xy, cell_km: float, R_km: float, k: int | None, circ: Circuity,
                   *, same_tol_m: float = 1.0) -> NeighborCSR:
    """Her talep hücresi için yol mesafesi ≤ R olan en yakın k aday (k=None: R içindekilerin hepsi).

    demand_xy, cand_xy: (n, 2) UTM 36N metre. Aynı hücre (kuş uçuşu < same_tol_m) çiftinde hücre içi ortalama mesafe.
    Eşitlikler: k'ıncı en küçük yol mesafesine eşit (TIE_TOL_KM = 1e-9 km içinde) olan adayların HEPSİ tutulur, bu yüzden
    bir satır k'dan uzun olabilir. (Eşitliği aday sırasıyla kesmek düzenli ızgarada belirli bir yöndeki — ör. kuzeydeki —
    adayları sistematik olarak dışarıda bırakırdı.) Satır içinde sıra: yol mesafesi, sonra aday konumu (cand_xy sırası)
    → çıktı deterministik.
    """
    d = np.asarray(demand_xy, dtype=float).reshape(-1, 2)
    c = np.asarray(cand_xy, dtype=float).reshape(-1, 2)
    nd = len(d)
    if nd == 0 or len(c) == 0 or (k is not None and k <= 0):
        return NeighborCSR(np.zeros(nd + 1, dtype=np.int64), np.zeros(0, dtype=np.int32), np.zeros(0))
    e_max_m = R_km / circ.min_factor * 1000.0 * (1.0 + 1e-9)   # güvenli ön süzgeç
    pairs = cKDTree(d).sparse_distance_matrix(cKDTree(c), e_max_m, output_type="ndarray")
    ii = pairs["i"].astype(np.int64)
    jj = pairs["j"].astype(np.int64)
    road = road_km_pairs(pairs["v"] / 1000.0, cell_km, circ, same_tol_km=same_tol_m / 1000.0)
    keep = road <= R_km
    ii, jj, road = ii[keep], jj[keep], road[keep]
    order = np.lexsort((jj, road, ii))            # satır, sonra yol mesafesi, sonra aday konumu
    ii, jj, road = ii[order], jj[order], road[order]
    counts = np.bincount(ii, minlength=nd)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    if k is not None:
        rank = np.arange(len(ii)) - starts[ii]
        # k'ıncı en küçük mesafe (k'dan az adayı olan satırda sınır yok); ona eşit olanlar da tutulur
        kth = np.full(nd, np.inf)
        full = counts >= k
        kth[full] = road[starts[full] + k - 1]
        sel = (rank < k) | (road <= kth[ii] + TIE_TOL_KM)
        ii, jj, road = ii[sel], jj[sel], road[sel]
        counts = np.bincount(ii, minlength=nd)
    indptr = np.concatenate(([0], np.cumsum(counts))).astype(np.int64)
    return NeighborCSR(indptr, jj.astype(np.int32), road.astype(np.float64))


def nearest_euclid(points_xy, cand_xy) -> tuple[np.ndarray, np.ndarray]:
    """Her nokta için kuş uçuşu en yakın adayın konumu ve mesafesi (km)."""
    p = np.asarray(points_xy, dtype=float).reshape(-1, 2)
    c = np.asarray(cand_xy, dtype=float).reshape(-1, 2)
    if len(p) == 0 or len(c) == 0:
        return np.full(len(p), -1, dtype=np.int64), np.full(len(p), np.inf)
    dist, idx = cKDTree(c).query(p, k=1)
    return idx.astype(np.int64), dist / 1000.0


def profile_points(params: Mapping) -> Sequence[tuple[float, float]]:
    """parameters.json'daki dolambaç profili noktaları."""
    return [(float(a), float(b)) for a, b in _value(params, "access.circuity_profile")]
