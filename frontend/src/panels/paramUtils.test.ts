import { describe, expect, it } from 'vitest';

import type { Meta, ParamMeta } from '../api/types.ts';
import { checkValue, fmtDisplay, fromDisplay, niceStep, numberSpec, relevance, shortAltLabel, toDisplay } from './paramUtils.ts';

function param(p: Partial<ParamMeta> & Pick<ParamMeta, 'key' | 'value' | 'unit'>): ParamMeta {
  return {
    label: p.key,
    status: 'varsayim',
    status_tr: 'varsayım',
    sources: [],
    derivation: null,
    rationale: null,
    notes: null,
    range: null,
    range_note: null,
    alternatives: [],
    group: 'X',
    domain: 'x',
    allowed: { kind: 'number', min: 0, max: null, min_inclusive: true, max_inclusive: true },
    structure: false,
    ...p,
  } as ParamMeta;
}

const meta = { defaults: { recommended_time_limit_s: { '2': 60, '3': 15, '5': 15 } } } as unknown as Meta;

describe('numberSpec — gösterim ölçeği', () => {
  it('büyük TL değerleri milyon / milyar TL', () => {
    const fixed = param({ key: 'cost.fixed_per_hospital', value: 250_000_000, unit: 'TL / hastane (2026 fiyatları, KDV dahil)', range: [0, 1_500_000_000] });
    const s = numberSpec(fixed);
    expect(s.displayUnit).toBe('milyon TL');
    expect(toDisplay(s, 250_000_000)).toBe(250);
    expect(fromDisplay(s, 312.5)).toBe(312_500_000);
    expect(fmtDisplay(s, 250_000_000)).toBe('250 milyon TL');
    const budget = param({ key: 'solver.budget_tl', value: 350e9, unit: 'TL (2026 fiyatları; …)', range: [0, 1e12] });
    expect(numberSpec(budget).displayUnit).toBe('milyar TL');
    const perBed = param({ key: 'opex.per_bed_year', value: 8_887_000, unit: 'TL / yatak·yıl', range: [0, 20_000_000] });
    expect(numberSpec(perBed).displayUnit).toBeNull();
  });

  it('oran birimli kesirler yüzde; boyutsuz oran yüzde değil', () => {
    const occ = param({ key: 'demand.hb.target_occupancy', value: 0.85, unit: 'oran (0–1)', range: [0.5, 0.95] });
    const s = numberSpec(occ);
    expect(s.displayUnit).toBe('%');
    expect(toDisplay(s, 0.587)).toBe(58.7);
    expect(fromDisplay(s, 58.7)).toBe(0.587);
    expect(fmtDisplay(s, 0.85)).toBe('%85');
    const disc = param({ key: 'access.discount_rate', value: 0.05, unit: 'oran / yıl (reel)', range: [0, 0.12] });
    expect(numberSpec(disc).displayUnit).toBe('% / yıl');
    const circ = param({ key: 'access.circuity', value: 1.223, unit: 'oran (boyutsuz)', range: [1, 1.5] });
    expect(numberSpec(circ).displayUnit).toBeNull();
  });

  it('adım ve ondalık', () => {
    expect(niceStep(0.015)).toBe(0.02);
    expect(niceStep(7)).toBe(10);
    const b = param({ key: 'demand.beds_per_1000', value: 3.15, unit: 'yatak / 1000 kişi', range: [2, 5], alternatives: [{ label: 'x', value: 3.1458 }] });
    const s = numberSpec(b);
    expect(s.step).toBe(0.02);
    expect(s.decimals).toBe(4);
    const n = param({ key: 'size.min_hospitals', value: 0, unit: 'hastane', range: [0, 300], allowed: { kind: 'integer', min: 0, max: null, min_inclusive: true, max_inclusive: true } });
    expect(numberSpec(n)).toMatchObject({ integer: true, step: 1, decimals: 0 });
  });
});

describe('checkValue', () => {
  const p = param({ key: 'demand.beds_per_1000', value: 3.15, unit: 'yatak / 1000 kişi', range: [2, 5], allowed: { kind: 'number', min: 0, max: null, min_inclusive: false, max_inclusive: true } });
  const s = numberSpec(p);
  it('tanım kümesi dışı hata, aralık dışı uyarı', () => {
    expect(checkValue(p, 0, s).error).toMatch(/tanım kümesi/);
    expect(checkValue(p, 6, s)).toEqual({ outside: true });
    expect(checkValue(p, 3.5, s)).toEqual({});
  });
});

describe('relevance — params.py effective_values ile aynı kurallar', () => {
  const ctx = { overrides: {}, meta, gridKm: 3 };
  const get = (vals: Record<string, unknown>) => (k: string) => vals[k] as never;
  it('mod ve seçeneklere bağlı anahtarlar', () => {
    expect(relevance('solver.budget_tl', get({ 'solver.objective_mode': 'toplam' }), ctx)?.active).toBe(false);
    expect(relevance('solver.budget_tl', get({ 'solver.objective_mode': 'butce' }), ctx)).toBeNull();
    expect(relevance('demand.hb.alos_days', get({ 'demand.mode': 'oran' }), ctx)?.active).toBe(false);
    expect(relevance('demand.beds_per_1000', get({ 'demand.mode': 'hill_burton' }), ctx)?.active).toBe(false);
    expect(relevance('land.price_beta', get({ 'land.include': false }), ctx)?.active).toBe(false);
    expect(relevance('access.circuity', get({ 'access.circuity_mode': 'mesafeye_bagli' }), ctx)?.active).toBe(false);
    expect(relevance('cost.unit.V-A', get({}), ctx)?.active).toBe(false);
  });
  it('yakıttan türetilen c ve ızgaranın önerilen süresi', () => {
    const r = relevance('access.cost_tl_per_km', get({ 'access.fuel_price_tl_per_l': 80, 'access.fuel_l_per_100km': 8 }), { ...ctx, overrides: { 'access.fuel_price_tl_per_l': 80 } });
    expect(r?.note).toContain('6,4');
    expect(relevance('solver.time_limit_s', get({}), { ...ctx, gridKm: 2 })?.note).toContain('60 sn');
  });
});

it('alternatif etiketi kısalır', () => {
  expect(shortAltLabel('2024: 31,3 / 10 (Haber Bülteni 2024 Tablo 12)')).toBe('2024');
  expect(shortAltLabel('2025, sayılardan tam değer: 270.829 yatak')).toBe('2025, sayılardan tam…');
});
