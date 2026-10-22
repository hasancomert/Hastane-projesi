"""Faz 3 — parametre katmanı ve örnek (instance) önbelleği.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase3_params.py
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from backend.model import instance
from backend.model.params import PARAMS_JSON, ParamError, Params, pv_factor

NEW_KEYS = ("population.scenario_year", "demand.mode", "size.city_enabled", "size.min_hospitals",
            "size.max_hospitals", "solver.objective_mode", "solver.budget_tl", "solver.p_fixed")


def test_new_keys_registered_as_model_choices():
    reg = json.loads(PARAMS_JSON.read_text(encoding="utf-8"))
    for k in NEW_KEYS:
        assert k in reg, k
        assert reg[k]["status"] == "model_secimi" and reg[k]["rationale"], k
        assert "GEÇİCİ" not in reg[k]["rationale"], k
    p = Params.load()
    assert p["solver.objective_mode"] == "toplam" and p["demand.mode"] == "oran"
    assert p["population.scenario_year"] == 2025 and p["size.city_enabled"] is True
    assert p.count_bounds == (0, None) and p.budget is None


def test_derived_defaults():
    p = Params.load()
    assert p.pv == pytest.approx((1 - 1.05 ** -25) / 0.05)
    assert p.pv == pytest.approx(14.0939, rel=1e-5)
    assert p.lam == pytest.approx(1902.6, rel=1e-4)
    assert p.construction_multiplier == pytest.approx(1.68)
    assert p.beds_per_person * 5_910_320 == pytest.approx(18617.508, rel=1e-9)
    cl = {c.code: c for c in p.classes}
    assert [(c.L, c.U) for c in p.classes] == [(50, 199), (200, 399), (400, 999), (1000, 4000)]
    exp_fixed = {"IV-C": 478.68, "V-B": 497.60, "V-C": 525.27, "V-D": 552.09}      # milyon TL
    exp_var = {"IV-C": 15.537, "V-B": 16.822, "V-C": 18.702, "V-D": 25.121}        # milyon TL / yatak
    for code in exp_fixed:
        assert cl[code].fixed / 1e6 == pytest.approx(exp_fixed[code], abs=0.01)
        assert cl[code].var / 1e6 == pytest.approx(exp_var[code], abs=0.001)
        # parçalar: yapı + altyapı + donanım + KDV = çarpan × yapı
        fp = cl[code].fixed_parts
        assert fp["yapi"] + fp["altyapi"] + fp["donanim"] + fp["kdv"] == pytest.approx(1.68 * fp["yapi"])
        assert fp["sabit"] == 250e6 and fp["isletme"] == 0


def test_pv_zero_rate():
    assert pv_factor(25, 0.0) == 25.0


def test_hill_burton_and_margin():
    p = Params.load({"demand.mode": "hill_burton"})
    assert p.beds_per_person * 1000 == pytest.approx(158.23 * 4.0 / (365 * 0.85), rel=1e-9)
    p2 = Params.load({"demand.mode": "hill_burton", "demand.safety_margin": 0.1})
    assert p2.beds_per_person == pytest.approx(1.1 * p.beds_per_person)
    p3 = Params.load({"demand.safety_margin": 0.2})
    assert p3.beds_per_person * 1000 == pytest.approx(3.15 * 1.2)


def test_opex_and_land_enter_costs():
    p = Params.load({"opex.include": True, "opex.fixed_per_hospital_year": 1e8})
    c = p.classes[0]
    assert c.fixed_parts["isletme"] == pytest.approx(p.pv * 1e8)
    assert c.var_parts["isletme"] == pytest.approx(p.pv * 8_887_000)
    q = Params.load({"land.include": True})
    lp = q.land_cost_per_bed(np.array([0.0, 78.3, 4 * 78.3]), 78.3)
    assert lp[1] == pytest.approx(2000 * 130) and lp[2] == pytest.approx(2000 * 130 * 2)   # β = 0,5
    assert lp[0] == 0.0


@pytest.mark.parametrize("ov,needle", [
    ({"no.such.key": 1}, "bilinmeyen"),
    ({"access.max_distance_km": -5}, "geçersiz"),
    ({"size.city_enabled": "belki"}, "evet/hayır"),
    ({"solver.objective_mode": "maks"}, "seçenek"),
    ({"grid.k_nearest": 2.5}, "tamsayı"),
    ({"size.general_min_beds": 500, "size.general_max_beds": 400}, "büyük olamaz"),
    ({"size.min_hospitals": 50, "size.max_hospitals": 10}, "büyük olamaz"),
    ({"size.class_threshold_1": 400, "size.class_threshold_2": 300}, "eşik"),
])
def test_override_validation(ov, needle):
    with pytest.raises(ParamError) as e:
        Params.load(ov)
    assert needle in str(e.value)


def test_out_of_slider_range_is_warning_only():
    p = Params.load({"cost.fixed_per_hospital": 2e9})       # aralık [0, 1,5 milyar]
    assert p.warnings and "aralığının" in p.warnings[0]


def test_fuel_inputs_update_cost_per_km():
    p = Params.load({"access.fuel_price_tl_per_l": 80.0})
    assert p["access.cost_tl_per_km"] == pytest.approx(80.0 * 8.91 / 100)
    p2 = Params.load({"access.fuel_price_tl_per_l": 80.0, "access.cost_tl_per_km": 7.0})
    assert p2["access.cost_tl_per_km"] == 7.0


def test_class_toggles():
    p = Params.load({"size.city_enabled": False})
    assert [c.enabled for c in p.classes] == [True, True, True, False]
    assert p.max_beds == 999
    p2 = Params.load({"size.general_max_beds": 350})          # V-C boş
    assert [c.enabled for c in p2.classes] == [True, True, False, True]
    assert p2.classes[1].U == 350


def test_hashes_split_structure_and_costs():
    a = Params.load()
    b = Params.load({"cost.fixed_per_hospital": 3e8, "access.alpha": 1.4})
    c = Params.load({"access.max_distance_km": 40})
    assert a.structure_hash() == b.structure_hash() != c.structure_hash()
    assert a.hash() != b.hash()
    # modda kullanılmayan anahtarlar sonuç özetini değiştirmez
    assert a.hash() == Params.load({"solver.budget_tl": 1.0, "solver.p_fixed": 3}).hash()
    assert a.hash() != Params.load({"solver.objective_mode": "sabit_p", "solver.p_fixed": 3}).hash()


def test_instance_structure_cache_and_costs():
    i1 = instance.build_instance(5)
    i2 = instance.build_instance(5, {"cost.fixed_per_hospital": 3e8, "access.alpha": 1.4})
    assert i2.from_cache and i2.s is i1.s
    assert np.array_equal(i1.s.arc_j, i2.s.arc_j)
    assert not np.allclose(i1.acc, i2.acc)
    assert i2.fixed[0, 0] - i1.fixed[0, 0] == pytest.approx(5e7)
    i3 = instance.build_instance(5, {"access.max_distance_km": 25})
    assert i3.s is not i1.s and i3.nA < i1.nA
    # 5 km varsayılan boyutlar (Faz 2 özetleriyle aynı)
    assert (i1.nI, i1.nJ, i1.nA) == (1073, 454, 27639)
    assert i1.total_demand == pytest.approx(18617.508, rel=1e-9)
    # erişim katsayısı: λ · w · d
    a = 123
    assert i1.acc[a] == pytest.approx(i1.params.lam * i1.s.w[i1.s.arc_i[a]] * i1.s.arc_d[a])


def test_instance_locks():
    i0 = instance.build_instance(5)
    cid = str(i0.s.cand_ids[10])
    i1 = instance.build_instance(5, locks={"closed": [cid]})
    assert i1.s is i0.s and i1.lock_closed[10] and not i1.y_ub[10].any()
    # aday olmayan bir hücreyi 'aç' kilidi J'ye ekler (yapı değişir)
    g = i0.s.grid
    mask = g.admissible.copy()
    mask[i0.s.cand_cells] = False
    target = str(g.id[np.flatnonzero(mask)[0]])
    i2 = instance.build_instance(5, locks={"open": [target]})
    assert i2.s is not i0.s and i2.nJ == i0.nJ + 1 and i2.lock_open.sum() == 1
    with pytest.raises(ParamError):
        instance.build_instance(5, locks={"open": ["5km_0_0"]})
    with pytest.raises(ParamError):
        instance.build_instance(5, locks={"open": [cid], "closed": [cid]})
