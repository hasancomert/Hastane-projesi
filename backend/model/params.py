"""Parametre katmanı: parameters.json + kullanıcı değişiklikleri → doğrulanmış değerler ve türetilmiş büyüklükler.

Yalnız standart kütüphane + numpy. Tüm sayılar kayıttan (data/reference/parameters.json) gelir; burada yeni sayı yoktur.

Türetilen büyüklükler (şartname 4.5, öğrenci kararları):
  PV(T, r)  = (1 − (1+r)^−T) / r          (r = 0 → T)
  λ         = PV · v · gidiş-dönüş · (c + zaman değeri / hız)          [TL / (kişi · km^α)]
  c         = access.cost_tl_per_km; kullanıcı yalnız yakıt fiyatını/tüketimini değiştirirse c = fiyat × l/100 km / 100
  q / kişi  = oran:        demand.beds_per_1000 / 1000 · (1 + emniyet payı)
              hill_burton: yatış/1000 · kalış / (365 · hedef doluluk) · (1 + emniyet payı)
  çarpan    = (1 + kdv · kdv_dahil) · (1 + altyapı + donanım)
  Sabit_k   = çarpan · u_k · A0 + F_sabit [+ PV · yıllık sabit işletme]         (TL / hastane)
  Değişken_k= çarpan · u_k · a_k [+ PV · yatak başı yıllık işletme]          (TL / yatak, arsa hariç)
  arsa_j    = arsa_dahil · p_j · land.m2_per_bed,  p_j = taban · (yoğunluk_j / medyan yoğunluk_J)^β   (TL / yatak)
Sınıflar (2026 tebliği): IV-C [B_min, eşik1−1], V-B [eşik1, eşik2−1], V-C [eşik2, B_max], V-D şehir [şehir_min, şehir_max].
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PARAMS_JSON = ROOT / "data" / "reference" / "parameters.json"

OBJECTIVE_MODES = ("toplam", "butce", "sabit_p")
DEMAND_MODES = ("oran", "hill_burton")
GRID_SOURCES = ("worldpop_r2025a", "ghs_pop_r2023a")
SCENARIO_YEARS = (2025, 2030)
CELL_SIZES = (2, 3, 5)

# Yapıyı (talep/aday kümeleri, yaylar) belirleyen anahtarlar; geri kalanı yalnız katsayı/sınır değiştirir (şartname 4.6)
STRUCTURE_KEYS = (
    "population.grid_source", "population.scenario_year",
    "grid.candidate_min_pop", "grid.candidate_near_density", "grid.candidate_min_inside", "grid.candidate_max_water",
    "access.max_distance_km", "grid.k_nearest",
    "access.circuity", "access.circuity_mode", "access.circuity_profile", "access.circuity_scale",
)


class ParamError(ValueError):
    """Geçersiz parametre (mesaj Türkçe, arayüzde gösterilir)."""


# ---------------------------------------------------------------- tür / tanım kümesi
# tür: f = gerçel, i = tamsayı, b = evet/hayır, c = seçenek, p = profil
# (alt, üst, alt_dahil, üst_dahil) — fiziksel/mantıksal tanım kümesi (kaydırıcı aralığı değil; o yalnız uyarı)
_INF = math.inf
SPEC: dict[str, tuple] = {
    "population.province_2025": ("i", 1, _INF),
    "population.province_2030": ("i", 1, _INF),
    "population.grid_source": ("c", GRID_SOURCES),
    "population.scenario_year": ("c", SCENARIO_YEARS),
    "demand.mode": ("c", DEMAND_MODES),
    "demand.beds_per_1000": ("f", 0, _INF, False),
    "demand.safety_margin": ("f", 0, 1),
    "demand.hb.admissions_per_1000": ("f", 0, 1000, False),
    "demand.hb.alos_days": ("f", 0, 365, False),
    "demand.hb.target_occupancy": ("f", 0, 1, False),
    "demand.hb.current_occupancy": ("f", 0, 1),
    "size.general_min_beds": ("i", 1, _INF),
    "size.general_max_beds": ("i", 1, _INF),
    "size.city_min_beds": ("i", 1, _INF),
    "size.city_max_beds": ("i", 1, _INF),
    "size.class_threshold_1": ("i", 2, _INF),
    "size.class_threshold_2": ("i", 2, _INF),
    "size.city_enabled": ("b",),
    "size.min_hospitals": ("i", 0, _INF),
    "size.max_hospitals": ("i", 0, _INF),
    "cost.unit.IV-C": ("f", 0, _INF),
    "cost.unit.V-A": ("f", 0, _INF),
    "cost.unit.V-B": ("f", 0, _INF),
    "cost.unit.V-C": ("f", 0, _INF),
    "cost.unit.V-D": ("f", 0, _INF),
    "cost.m2_per_bed.general": ("f", 0, _INF, False),
    "cost.m2_per_bed.city": ("f", 0, _INF, False),
    "cost.core_area_m2": ("f", 0, _INF),
    "cost.infra_ratio": ("f", 0, 10),
    "cost.equipment_ratio": ("f", 0, 10),
    "cost.vat_rate": ("f", 0, 1),
    "cost.include_vat": ("b",),
    "land.include": ("b",),
    "land.m2_per_bed": ("f", 0, _INF),
    "land.min_parcel_m2": ("f", 0, _INF),
    "land.price_base_tl_m2": ("f", 0, _INF),
    "land.price_beta": ("f", 0, 5),
    "cost.fixed_per_hospital": ("f", 0, _INF),
    "opex.include": ("b",),
    "opex.fixed_per_hospital_year": ("f", 0, _INF),
    "opex.per_bed_year": ("f", 0, _INF),
    "access.visits_per_capita_total": ("f", 0, _INF),
    "access.share_hospital_visits": ("f", 0, 1),
    "access.hospital_visits_per_capita": ("f", 0, _INF),
    "access.round_trip_factor": ("f", 0, _INF),
    "access.fuel_price_tl_per_l": ("f", 0, _INF),
    "access.fuel_l_per_100km": ("f", 0, _INF),
    "access.cost_tl_per_km": ("f", 0, _INF),
    "access.value_of_time_tl_per_h": ("f", 0, _INF),
    "access.speed_kmh": ("f", 0, _INF, False),
    "access.horizon_years": ("f", 0, 200, False),
    "access.discount_rate": ("f", 0, 1),
    "access.circuity": ("f", 0, _INF, False),
    "access.circuity_mode": ("c", ("mesafeye_bagli", "sabit")),
    "access.circuity_profile": ("p",),
    "access.circuity_scale": ("f", 0, _INF, False),
    "access.alpha": ("f", 0, 5, False),
    "access.max_distance_km": ("f", 0, _INF, False),
    "grid.cell_km": ("c", CELL_SIZES),
    "grid.candidate_min_pop": ("f", 0, _INF),
    "grid.k_nearest": ("i", 1, _INF),
    "grid.candidate_min_inside": ("f", 0, 1),
    "grid.candidate_max_water": ("f", 0, 1, False),
    "grid.candidate_near_density": ("f", 0, _INF),
    "solver.time_limit_s": ("f", 0, _INF, False),
    "solver.mip_gap": ("f", 0, 1),
    "solver.objective_mode": ("c", OBJECTIVE_MODES),
    "solver.budget_tl": ("f", 0, _INF),
    "solver.p_fixed": ("i", 1, _INF),
}


def _plain(v):
    """Kayıt girdisi {value: …} ya da düz değer → düz değer."""
    return v["value"] if isinstance(v, Mapping) and "value" in v else v


def _coerce(key: str, v):
    """Tür ve tanım kümesi denetimi; Türkçe hata."""
    spec = SPEC.get(key)
    if spec is None:
        return v
    kind = spec[0]
    if kind == "b":
        if isinstance(v, (bool, np.bool_)):
            return bool(v)
        if isinstance(v, str) and v.lower() in ("true", "false", "evet", "hayır", "hayir"):
            return v.lower() in ("true", "evet")
        raise ParamError(f"'{key}' evet/hayır olmalı (verilen: {v!r})")
    if kind == "c":
        opts = spec[1]
        if isinstance(opts[0], int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ParamError(f"'{key}' için geçersiz seçenek {v!r}; izinli: {list(opts)}") from None
        if v not in opts:
            raise ParamError(f"'{key}' için geçersiz seçenek {v!r}; izinli: {list(opts)}")
        return v
    if kind == "p":
        try:
            prof = [(float(a), float(b)) for a, b in v]
        except (TypeError, ValueError):
            raise ParamError(f"'{key}' [[km, katsayı], …] listesi olmalı") from None
        if not prof or any(b <= 0 for _, b in prof) or any(b2[0] <= b1[0] for b1, b2 in zip(prof, prof[1:])):
            raise ParamError(f"'{key}': mesafeler artan, katsayılar pozitif olmalı")
        return [list(p) for p in prof]
    if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
        raise ParamError(f"'{key}' sayı olmalı (verilen: {v!r})")
    x = float(v)
    if not math.isfinite(x):
        raise ParamError(f"'{key}' sonlu bir sayı olmalı")
    lo, hi = spec[1], spec[2]
    lo_inc = spec[3] if len(spec) > 3 else True
    if x < lo or (x == lo and not lo_inc) or x > hi:
        rel = "≥" if lo_inc else ">"
        raise ParamError(f"'{key}' = {v} geçersiz: {rel} {lo:g}" + (f" ve ≤ {hi:g}" if hi < _INF else "") + " olmalı")
    if kind == "i":
        if abs(x - round(x)) > 1e-9:
            raise ParamError(f"'{key}' tamsayı olmalı (verilen: {v!r})")
        return int(round(x))
    return x


def load_registry(path: Path = PARAMS_JSON) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


_REGISTRY_CACHE: dict[str, dict] = {}


def registry(path: Path = PARAMS_JSON) -> dict:
    """parameters.json (önbellekli; dosya değişirse yeniden okunur)."""
    p = Path(path)
    key = f"{p}:{p.stat().st_mtime_ns}"
    if key not in _REGISTRY_CACHE:
        _REGISTRY_CACHE.clear()
        _REGISTRY_CACHE[key] = load_registry(p)
    return _REGISTRY_CACHE[key]


def stable_hash(obj) -> str:
    """JSON tabanlı kararlı özet (16 hex)."""
    s = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def pv_factor(T: float, r: float) -> float:
    """Bugünkü değer çarpanı: (1 − (1+r)^−T) / r; r = 0 → T."""
    return float(T) if r <= 0 else (1.0 - (1.0 + r) ** (-T)) / r


# ---------------------------------------------------------------- sınıflar
CLASS_CODES = ("IV-C", "V-B", "V-C", "V-D")
CLASS_NAMES = {"IV-C": "küçük", "V-B": "orta", "V-C": "büyük", "V-D": "şehir hastanesi"}


@dataclass(frozen=True)
class SizeClass:
    """Bir boyut sınıfı: yatak aralığı ve hastane/yatak başı maliyet parçaları (TL, arsa hariç)."""

    code: str
    name: str
    L: float
    U: float
    enabled: bool
    unit_cost: float          # TL/m² (tebliğ, KDV hariç)
    m2_per_bed: float
    fixed_parts: dict         # TL / hastane: yapi, altyapi, donanim, kdv, sabit, isletme
    var_parts: dict           # TL / yatak: yapi, altyapi, donanim, kdv, isletme
    disabled_reason: str = ""

    @property
    def fixed(self) -> float:
        return float(sum(self.fixed_parts.values()))

    @property
    def var(self) -> float:
        return float(sum(self.var_parts.values()))

    @property
    def label(self) -> str:
        return f"{self.code} ({self.name}, {int(self.L)}–{int(self.U)} yatak)"


def _construction_parts(base_tl: float, vat: float, infra: float, equip: float) -> dict:
    """KDV hariç yapı bedeli → yapı, altyapı, donanım, KDV parçaları (toplam = çarpan · taban)."""
    return {"yapi": base_tl, "altyapi": infra * base_tl, "donanim": equip * base_tl,
            "kdv": vat * (1.0 + infra + equip) * base_tl}


# ---------------------------------------------------------------- ana nesne
@dataclass
class Params:
    """Doğrulanmış parametre değerleri (anahtar → değer) ve türetilmiş büyüklükler."""

    values: dict[str, Any]
    overrides: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------ kurulum
    @classmethod
    def load(cls, overrides: Mapping[str, Any] | None = None, *, path: Path = PARAMS_JSON) -> "Params":
        reg = registry(path)
        values = {k: _plain(v) for k, v in reg.items()}
        ov = dict(overrides or {})
        warnings: list[str] = []
        for k, v in ov.items():
            if k not in values:
                raise ParamError(f"bilinmeyen parametre: '{k}'")
            values[k] = v
        for k in list(values):
            values[k] = _coerce(k, values[k])
        # kaydırıcı aralığı dışı: yalnız uyarı (duyarlılık analizi aralık dışına çıkabilir)
        for k, v in ov.items():
            rng = reg[k].get("range") if isinstance(reg[k], Mapping) else None
            x = values[k]
            if rng and isinstance(x, (int, float)) and not isinstance(x, bool) and not (rng[0] <= x <= rng[1]):
                warnings.append(f"'{k}' = {x:g} arayüz aralığının [{rng[0]:g}, {rng[1]:g}] dışında")
        # yakıt girdileri değiştiyse ve c verilmediyse c = fiyat × tüketim / 100
        if "access.cost_tl_per_km" not in ov and ({"access.fuel_price_tl_per_l", "access.fuel_l_per_100km"} & set(ov)):
            values["access.cost_tl_per_km"] = values["access.fuel_price_tl_per_l"] * values["access.fuel_l_per_100km"] / 100
        p = cls(values=values, overrides=ov, warnings=warnings)
        p._check_consistency()
        return p

    def with_overrides(self, more: Mapping[str, Any]) -> "Params":
        ov = dict(self.overrides)
        ov.update(more)
        return Params.load(ov)

    def __getitem__(self, key: str):
        return self.values[key]

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def _check_consistency(self) -> None:
        v = self.values
        if v["size.general_min_beds"] > v["size.general_max_beds"]:
            raise ParamError("Genel hastane en az yatak, en çok yataktan büyük olamaz.")
        if v["size.city_min_beds"] > v["size.city_max_beds"]:
            raise ParamError("Şehir hastanesi en az yatak, en çok yataktan büyük olamaz.")
        if v["size.class_threshold_1"] >= v["size.class_threshold_2"]:
            raise ParamError("Tebliğ sınıf eşiği 1, eşik 2'den küçük olmalı.")
        mn, mx = v["size.min_hospitals"], v["size.max_hospitals"]
        if mx and mn and mn > mx:
            raise ParamError(f"En az hastane sayısı ({mn}) en çok hastane sayısından ({mx}) büyük olamaz.")
        if not any(c.enabled for c in self.classes):
            raise ParamError("Hiçbir boyut sınıfı kullanılabilir değil (yatak sınırlarını ve eşikleri kontrol edin).")

    # ------------------------------------------------ türetilmiş
    @property
    def objective_mode(self) -> str:
        return self.values["solver.objective_mode"]

    @property
    def pv(self) -> float:
        return pv_factor(self.values["access.horizon_years"], self.values["access.discount_rate"])

    @property
    def cost_per_km(self) -> float:
        """Kişi·km başına genelleştirilmiş yolculuk maliyeti: c + zaman değeri / hız (TL / km)."""
        v = self.values
        return v["access.cost_tl_per_km"] + v["access.value_of_time_tl_per_h"] / v["access.speed_kmh"]

    @property
    def lam(self) -> float:
        """λ = PV · v · gidiş-dönüş · (c + VOT / hız)  [TL / (kişi · km^α)]."""
        v = self.values
        return self.pv * v["access.hospital_visits_per_capita"] * v["access.round_trip_factor"] * self.cost_per_km

    @property
    def alpha(self) -> float:
        return float(self.values["access.alpha"])

    @property
    def beds_per_person(self) -> float:
        """Kişi başı yatak talebi (q_i = w_i · bu değer)."""
        v = self.values
        if v["demand.mode"] == "hill_burton":
            base = (v["demand.hb.admissions_per_1000"] / 1000.0 * v["demand.hb.alos_days"]
                    / (365.0 * v["demand.hb.target_occupancy"]))
        else:
            base = v["demand.beds_per_1000"] / 1000.0
        return base * (1.0 + v["demand.safety_margin"])

    @property
    def scenario_year(self) -> int:
        return int(self.values["population.scenario_year"])

    @property
    def province_pop(self) -> int:
        return int(self.values[f"population.province_{self.scenario_year}"])

    @property
    def construction_multiplier(self) -> float:
        v = self.values
        return ((1.0 + v["cost.vat_rate"] * (1.0 if v["cost.include_vat"] else 0.0))
                * (1.0 + v["cost.infra_ratio"] + v["cost.equipment_ratio"]))

    @property
    def classes(self) -> list[SizeClass]:
        """Dört sınıf (sıra sabit: IV-C, V-B, V-C, V-D); kullanılamayanlar enabled=False."""
        v = self.values
        vat = v["cost.vat_rate"] if v["cost.include_vat"] else 0.0
        infra, equip = v["cost.infra_ratio"], v["cost.equipment_ratio"]
        A0 = v["cost.core_area_m2"]
        F = v["cost.fixed_per_hospital"]
        opex_on = bool(v["opex.include"])
        pv = self.pv
        gmin, gmax = v["size.general_min_beds"], v["size.general_max_beds"]
        t1, t2 = v["size.class_threshold_1"], v["size.class_threshold_2"]
        ranges = {"IV-C": (gmin, min(t1 - 1, gmax)), "V-B": (max(t1, gmin), min(t2 - 1, gmax)),
                  "V-C": (max(t2, gmin), gmax), "V-D": (v["size.city_min_beds"], v["size.city_max_beds"])}
        out = []
        for code in CLASS_CODES:
            L, U = ranges[code]
            city = code == "V-D"
            u = v[f"cost.unit.{code}"]
            a = v["cost.m2_per_bed.city"] if city else v["cost.m2_per_bed.general"]
            fixed = _construction_parts(u * A0, vat, infra, equip)
            fixed["sabit"] = F
            fixed["isletme"] = pv * v["opex.fixed_per_hospital_year"] if opex_on else 0.0
            var = _construction_parts(u * a, vat, infra, equip)
            var["isletme"] = pv * v["opex.per_bed_year"] if opex_on else 0.0
            reason = ""
            if city and not v["size.city_enabled"]:
                reason = "şehir hastanesi sınıfı kapalı"
            elif L > U:
                reason = f"yatak aralığı boş ({L} > {U})"
            out.append(SizeClass(code=code, name=CLASS_NAMES[code], L=float(L), U=float(max(U, L)),
                                 enabled=not reason, unit_cost=u, m2_per_bed=a, fixed_parts=fixed, var_parts=var,
                                 disabled_reason=reason))
        return out

    @property
    def max_beds(self) -> float:
        return max(c.U for c in self.classes if c.enabled)

    @property
    def min_beds(self) -> float:
        return min(c.L for c in self.classes if c.enabled)

    def land_price(self, density: np.ndarray, ref_density: float) -> np.ndarray:
        """Arsa birim fiyatı p_j = taban · (yoğunluk / medyan)^β (TL/m²); ref ≤ 0 ise taban."""
        v = self.values
        d = np.asarray(density, dtype=float)
        if ref_density <= 0:
            return np.full(d.shape, v["land.price_base_tl_m2"])
        with np.errstate(divide="ignore", invalid="ignore"):
            return v["land.price_base_tl_m2"] * np.power(np.maximum(d, 0.0) / ref_density, v["land.price_beta"])

    def land_cost_per_bed(self, density: np.ndarray, ref_density: float) -> np.ndarray:
        """Arsa maliyeti, TL / yatak (arsa dahil değilse 0)."""
        if not self.values["land.include"]:
            return np.zeros(np.shape(density))
        return self.land_price(density, ref_density) * self.values["land.m2_per_bed"]

    @property
    def count_bounds(self) -> tuple[int, int | None]:
        """(en az, en çok) hastane; 0 = sınır yok → (0, None). Mod sabit_p'de (p, p)."""
        v = self.values
        if self.objective_mode == "sabit_p":
            p = int(v["solver.p_fixed"])
            return p, p
        mn = int(v["size.min_hospitals"])
        mx = int(v["size.max_hospitals"]) or None
        return mn, mx

    @property
    def budget(self) -> float | None:
        """Mod butce'de açılış bütçesi (TL); diğer modlarda None."""
        if self.objective_mode != "butce":
            return None
        return float(self.values["solver.budget_tl"])

    # ------------------------------------------------ özetler
    def structure_values(self) -> dict:
        return {k: self.values[k] for k in STRUCTURE_KEYS}

    def effective_values(self) -> dict:
        """Sonucu etkileyen değerler: seçili moda/seçeneğe göre kullanılmayan anahtarlar çıkarılır (sonuç önbelleği için)."""
        v = dict(self.values)
        mode = v["solver.objective_mode"]
        if mode != "butce":
            v.pop("solver.budget_tl", None)
        if mode != "sabit_p":
            v.pop("solver.p_fixed", None)
        else:
            v.pop("size.min_hospitals", None)
            v.pop("size.max_hospitals", None)
        if v["demand.mode"] == "oran":
            for k in ("demand.hb.admissions_per_1000", "demand.hb.alos_days", "demand.hb.target_occupancy"):
                v.pop(k, None)
        else:
            v.pop("demand.beds_per_1000", None)
        if not v["land.include"]:
            for k in ("land.price_base_tl_m2", "land.price_beta", "land.m2_per_bed"):
                v.pop(k, None)
        if not v["opex.include"]:
            v.pop("opex.fixed_per_hospital_year", None)
            v.pop("opex.per_bed_year", None)
        # bilgi amaçlı / modelde kullanılmayanlar
        for k in ("demand.hb.current_occupancy", "land.min_parcel_m2", "cost.unit.V-A", "population.province_2025",
                  "population.province_2030", "access.visits_per_capita_total", "access.share_hospital_visits",
                  "access.fuel_price_tl_per_l", "access.fuel_l_per_100km"):
            v.pop(k, None)
        if v["access.circuity_mode"] == "mesafeye_bagli":
            v.pop("access.circuity", None)
        else:
            v.pop("access.circuity_profile", None)
        return v

    def hash(self) -> str:
        """Sonucu etkileyen parametrelerin kararlı özeti (kullanılmayan anahtarlar hariç)."""
        return stable_hash(self.effective_values())

    def structure_hash(self) -> str:
        return stable_hash(self.structure_values())

    def derived_summary(self) -> dict:
        """Arayüz ve raporlar için türetilmiş değerler (TL)."""
        return {
            "pv_factor": self.pv,
            "lambda_tl_per_person_km": self.lam,
            "generalized_cost_tl_per_km": self.cost_per_km,
            "beds_per_1000": self.beds_per_person * 1000.0,
            "construction_multiplier": self.construction_multiplier,
            "classes": [{"code": c.code, "name": c.name, "L": c.L, "U": c.U, "enabled": c.enabled,
                         "disabled_reason": c.disabled_reason, "fixed_tl": c.fixed, "var_tl_per_bed": c.var,
                         "fixed_parts": dict(c.fixed_parts), "var_parts": dict(c.var_parts)}
                        for c in self.classes],
        }


def load(overrides: Mapping[str, Any] | None = None) -> Params:
    return Params.load(overrides)
