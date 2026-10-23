/**
 * Mantine teması. Inter (yerel, @fontsource-variable/inter) — hiçbir yazı tipi internetten yüklenmez.
 * Projeksiyonda okunur boyutlar: kök yazı boyutu global.css'te ekran genişliğine göre 15–18 px; Mantine ölçüleri rem.
 * Açık/koyu tema: MantineProvider `forceColorScheme` ile depodaki `colorScheme`'i izler (tek doğruluk kaynağı).
 */
import { createTheme, DEFAULT_THEME, type CSSVariablesResolver, type MantineColorsTuple } from '@mantine/core';

import { CHROME } from './palette.ts';

/** Birincil renk: sınıf paletinin mavisiyle (#2a78d6) uyumlu 10 ton. */
const ocean: MantineColorsTuple = [
  '#e8f1fc',
  '#cde2fb',
  '#9ec5f4',
  '#6da7ec',
  '#4a93e8',
  '#3987e5',
  '#2a78d6',
  '#256abf',
  '#1c5cab',
  '#104281',
];

/**
 * Mantine'in turuncu / sarı / yeşil paletlerinin koyu uçları açık zeminde metin için yetersiz (turuncu-9 #d9480f
 * açık turuncu zeminde 3,6:1). Açık temada `-text` ve `-light-color` 9. tonu kullanır; bu dört rengin 8–9. tonları
 * WCAG AA (≥ 4,5:1) sağlayacak kadar koyulaştırıldı (beyazda ve kendi açık zemininde ölçüldü, 2026-10-23).
 */
function darkerTail(c: readonly string[], s8: string, s9: string): MantineColorsTuple {
  return [c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], s8, s9] as unknown as MantineColorsTuple;
}
const orange = darkerTail(DEFAULT_THEME.colors.orange, '#c2410c', '#9a3412');
const yellow = darkerTail(DEFAULT_THEME.colors.yellow, '#a16207', '#854d0e');
const green = darkerTail(DEFAULT_THEME.colors.green, '#2b8a3e', '#1e6b30');
const teal = darkerTail(DEFAULT_THEME.colors.teal, '#087f5b', '#06664a');

export const FONT_STACK = "'Inter Variable', Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

export const theme = createTheme({
  fontFamily: FONT_STACK,
  fontFamilyMonospace: "ui-monospace, 'Cascadia Mono', Consolas, 'Liberation Mono', monospace",
  headings: {
    fontFamily: FONT_STACK,
    fontWeight: '650',
    sizes: {
      h1: { fontSize: '1.5rem', lineHeight: '1.25' },
      h2: { fontSize: '1.25rem', lineHeight: '1.3' },
      h3: { fontSize: '1.0625rem', lineHeight: '1.35' },
      h4: { fontSize: '1rem', lineHeight: '1.4' },
    },
  },
  colors: { ocean, orange, yellow, green, teal },
  primaryColor: 'ocean',
  // açık temada 7. ton: beyaz yazı / beyaz zemin üzerinde 5,4:1 (6. ton #2a78d6 4,4:1 ile AA'nın altında kalıyordu)
  primaryShade: { light: 7, dark: 5 },
  // dolu (filled) rozet ve düğmelerde yazı rengi zemine göre seçilir (ör. dolu teal üzerinde beyaz 2,6:1 yerine siyah)
  autoContrast: true,
  luminanceThreshold: 0.3,
  defaultRadius: 'md',
  // projeksiyon tabanı: 1280 px'te kök 16 px → xs 14 px (ikincil metin), sm 16 px (gövde), md 17 px; 12 px'in altında metin yok
  fontSizes: {
    xs: '0.875rem',
    sm: '1rem',
    md: '1.0625rem',
    lg: '1.1875rem',
    xl: '1.3125rem',
  },
  lineHeights: { xs: '1.4', sm: '1.45', md: '1.5', lg: '1.5', xl: '1.4' },
  breakpoints: {
    xs: '36em',
    sm: '48em',
    md: '62em',
    lg: '75em',
    xl: '100em',
  },
  cursorType: 'pointer',
  components: {
    Tooltip: {
      defaultProps: { withArrow: true, multiline: true, maw: 360, openDelay: 200, transitionProps: { duration: 120 } },
    },
    Badge: { defaultProps: { radius: 'sm' } },
  },
});

/**
 * Okunabilirlik (WCAG AA ≥ 4,5:1): Mantine'in "dimmed" grisi (#868e96, beyazda 3,3:1) ve 6. tondaki renkli metin
 * (ör. turuncu #fd7e14, 2,6:1) projeksiyonda okunmuyordu. İkincil metin palette.ts'teki ikincil mürekkebi kullanır
 * (#52514e açıkta 7,9:1; #c3c2b7 koyuda 8,7:1); renkli metin açık temada 9. ton.
 */
const TEXT_COLORS = ['ocean', 'blue', 'teal', 'green', 'red', 'orange', 'yellow', 'grape', 'gray', 'cyan', 'violet', 'pink', 'indigo', 'lime'] as const;
export const cssVariablesResolver: CSSVariablesResolver = () => {
  const light: Record<string, string> = { '--mantine-color-dimmed': CHROME.light.inkSecondary };
  const dark: Record<string, string> = { '--mantine-color-dimmed': CHROME.dark.inkSecondary };
  for (const c of TEXT_COLORS) {
    light[`--mantine-color-${c}-text`] = `var(--mantine-color-${c}-9)`;
    // çerçeveli (outline) rozet/düğmenin yazısı da 9. ton (7. ton beyazda 3,1:1)
    light[`--mantine-color-${c}-outline`] = `var(--mantine-color-${c}-9)`;
    dark[`--mantine-color-${c}-text`] = `var(--mantine-color-${c}-3)`;
  }
  return { variables: {}, light, dark };
};

/** Düzen kırılımları (px) — App.tsx ve global.css aynı değerleri kullanır. */
export const LAYOUT = {
  /** bu genişliğin altında parametre paneli çekmeceye (drawer) taşınır */
  paramsDrawerBelow: 1200,
  /** bu genişliğin altında sonuç paneli de çekmeceye taşınır (harita tam genişlik) */
  resultDrawerBelow: 900,
} as const;
