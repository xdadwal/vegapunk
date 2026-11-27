import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
    // Coverage instrumentation slows tests and CI runners are slower than a laptop; this only
    // matters for a slow or hung test, so a timing flake does not block the required check.
    testTimeout: 15_000,
    coverage: {
      provider: 'v8',
      reporter: ['text-summary', 'json-summary', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/test-fixtures.ts',
        'src/test-probe.tsx',
        'src/test-setup.ts',
        'src/test-utils.tsx',
        'src/main.tsx',
        'src/types.ts',
        'src/**/*.d.ts',
        'src/**/*.css',
      ],
      // A regression guard, not a target: about 3 points under what was measured on
      // 2026-11-27 (lines 97.8, statements 96.4, functions 96.0, branches 91.3).
      thresholds: {
        lines: 94,
        statements: 93,
        functions: 93,
        branches: 88,
      },
    },
  },
})
