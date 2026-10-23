import { describe, expect, it } from 'vitest';

import {
  DASH,
  fmtBeds,
  fmtBillion,
  fmtCompact,
  fmtDuration,
  fmtFixed,
  fmtGap,
  fmtInt,
  fmtKm,
  fmtKm2,
  fmtMoney,
  fmtMoneyDelta,
  fmtNumber,
  fmtParamValue,
  fmtPeople,
  fmtPercent,
  fmtPercentShort,
  fmtSigned,
  moneyParts,
} from './number.ts';

describe('sayılar', () => {
  it('şartnamedeki biçim: 1.234.567,8', () => {
    expect(fmtNumber(1234567.8)).toBe('1.234.567,8');
    expect(fmtInt(5910320)).toBe('5.910.320');
    expect(fmtFixed(3.2048, 2)).toBe('3,20');
  });
  it('geçersiz değerler tire olur', () => {
    expect(fmtNumber(Number.NaN)).toBe(DASH);
    expect(fmtInt(null)).toBe(DASH);
    expect(fmtMoney(undefined)).toBe(DASH);
    expect(fmtPercent(Number.POSITIVE_INFINITY)).toBe(DASH);
  });
  it('eksi işareti tipografik', () => {
    expect(fmtNumber(-1234.5)).toBe('−1.234,5');
    expect(fmtSigned(1.5)).toBe('+1,5');
    expect(fmtSigned(-1.5)).toBe('−1,5');
    expect(fmtSigned(0)).toBe('0');
  });
  it('kısa yazım', () => {
    expect(fmtCompact(5910320)).toBe('5,9 milyon');
    expect(fmtCompact(18617.5)).toBe('18,6 bin');
    expect(fmtCompact(950)).toBe('950');
  });
});

describe('para', () => {
  it('birim büyüklüğe göre seçilir', () => {
    expect(fmtMoney(381314289391.98)).toBe('381,31 milyar TL');
    expect(fmtMoney(250_000_000)).toBe('250 milyon TL');
    expect(fmtMoney(1_303_685_866.9)).toBe('1,3 milyar TL');
    expect(fmtMoney(48_750)).toBe('48.750 TL');
    expect(fmtMoney(1.2e12)).toBe('1,2 trilyon TL');
  });
  it('birim sabitlenebilir', () => {
    expect(fmtBillion(36_037_500_729.1)).toBe('36,04 milyar TL');
    expect(fmtMoney(250_000_000, { unit: 'milyar TL', digits: 2 })).toBe('0,25 milyar TL');
    expect(moneyParts(64_516.69)).toEqual({ value: '64.517', unit: 'TL', scale: 1 });
  });
  it('farklar işaretli', () => {
    expect(fmtMoneyDelta(8_022_746_793.75)).toBe('+8,02 milyar TL');
    expect(fmtMoneyDelta(-20_000_000)).toBe('−20 milyon TL');
  });
});

describe('yüzde ve gap', () => {
  it('yüzde işareti önde', () => {
    expect(fmtPercent(0.9497568)).toBe('%95,0');
    expect(fmtPercentShort(0.5)).toBe('%50');
    expect(fmtPercentShort(0.125)).toBe('%12,5');
  });
  it('gap', () => {
    expect(fmtGap(0)).toBe('%0');
    expect(fmtGap(0.000620048)).toBe('%0,06');
    expect(fmtGap(0.00005)).toBe('< %0,01');
    expect(fmtGap(0.0511)).toBe('%5,11');
    expect(fmtGap(null)).toBe(DASH);
  });
});

describe('birimli değerler', () => {
  it('mesafe, alan, kişi, yatak', () => {
    expect(fmtKm(3.204808)).toBe('3,2 km');
    expect(fmtKm2(25647.883)).toBe('25.647,9 km²');
    expect(fmtPeople(16856.98)).toBe('16.857 kişi');
    expect(fmtBeds(53.0995)).toBe('53 yatak');
  });
  it('süre', () => {
    expect(fmtDuration(4.143)).toBe('4,1 sn');
    expect(fmtDuration(15)).toBe('15 sn');
    expect(fmtDuration(125)).toBe('2 dk 5 sn');
    expect(fmtDuration(3600)).toBe('1 sa 0 dk');
  });
  it('parametre değerleri', () => {
    expect(fmtParamValue(true)).toBe('evet');
    expect(fmtParamValue(false)).toBe('hayır');
    expect(fmtParamValue('oran')).toBe('oran');
    expect(fmtParamValue(250_000_000, 'TL / hastane (2026 fiyatları, KDV dahil)')).toBe('250 milyon TL');
    expect(fmtParamValue(40_500, 'TL/m² (2026; KDV hariç)')).toBe('40.500');
    expect(fmtParamValue(0.05, 'oran / yıl (reel)')).toBe('%5');
    expect(fmtParamValue(0.85, 'oran (0–1)')).toBe('%85');
    expect(fmtParamValue(1.223, 'oran (boyutsuz)')).toBe('1,22');
    expect(fmtParamValue(3.15, 'yatak / 1000 kişi')).toBe('3,15');
    expect(fmtParamValue(2025, 'yıl (seçenek: 2025 | 2030)')).toBe('2025');
    expect(fmtParamValue(25, 'yıl')).toBe('25');
    expect(fmtParamValue([[3.06, 1.38]], '[kuş uçuşu km, katsayı] çiftleri')).toBe('1 nokta');
  });
});
