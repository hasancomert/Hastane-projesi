/** Birim testleri (saf TypeScript: biçimlendiriciler, deposuz yardımcılar). Uçtan uca testler e2e/ altında
 * Playwright ile koşar (npm run test:e2e); burada dahil edilmez. */
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
});
