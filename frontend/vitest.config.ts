import { defineConfig } from 'vitest/config'

// Configuration de test séparée de vite.config.ts : vitest embarque sa propre version de Vite, on évite de
// mêler son pipeline à celui du build. JSX transformé par esbuild (suffisant pour les tests).
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
})
