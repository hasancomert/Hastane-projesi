import { describe, expect, it } from 'vitest';

import type { Meta } from '../api/types.ts';
import type { ProblemState } from './appStore.ts';
import { decodeProblem, encodeProblem } from './urlState.ts';

const meta = {
  defaults: { grid_km: 3 },
  parameters: {
    'cost.fixed_per_hospital': { value: 250_000_000 },
    'access.value_of_time_tl_per_h': { value: 150 },
    'size.city_enabled': { value: true },
  },
  scenarios: [
    { id: 'varsayilan', overrides: {} },
    { id: 'erisim_oncelikli', overrides: { 'access.value_of_time_tl_per_h': 260.3 } },
  ],
} as unknown as Meta;

const base: ProblemState = { gridKm: 3, scenarioId: 'varsayilan', overrides: {}, locks: { open: [], closed: [] }, allowOutsideRange: false };

describe('adresteki problem durumu (#…)', () => {
  it('varsayılan problem boş', () => {
    expect(encodeProblem(base, meta)).toBe('');
    expect(decodeProblem('', meta)).toBeNull();
  });
  it('senaryo + değişiklik + kilitler gidip gelir', () => {
    const p: ProblemState = {
      gridKm: 5,
      scenarioId: 'erisim_oncelikli',
      overrides: { 'access.value_of_time_tl_per_h': 260.3, 'cost.fixed_per_hospital': 300_000_000, 'size.city_enabled': false },
      locks: { open: ['5km_1_2'], closed: ['5km_3_4', '5km_0_1'] },
      allowOutsideRange: true,
    };
    const h = encodeProblem(p, meta);
    expect(h).not.toContain('value_of_time'); // senaryonun kendi değeri yazılmaz
    const back = decodeProblem(h, meta)!;
    expect(back.gridKm).toBe(5);
    expect(back.scenarioId).toBe('erisim_oncelikli');
    expect(back.overrides).toEqual(p.overrides);
    expect(back.locks.open).toEqual(['5km_1_2']);
    expect(back.locks.closed).toEqual(['5km_0_1', '5km_3_4']);
    expect(back.allowOutsideRange).toBe(true);
  });
  it('bilinmeyen anahtar, senaryo, ızgara ve başka ızgaranın kilidi yok sayılır', () => {
    const back = decodeProblem('#izgara=7&senaryo=yok&p.bilinmeyen=1&p.cost.fixed_per_hospital=1e8&zorunlu=2km_1_1,3km_2_2', meta)!;
    expect(back.gridKm).toBe(3);
    expect(back.scenarioId).toBe('varsayilan');
    expect(back.overrides).toEqual({ 'cost.fixed_per_hospital': 1e8 });
    expect(back.locks.open).toEqual(['3km_2_2']);
  });
});
