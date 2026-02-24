/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  css: {
    devSourcemap: true,
  },
  build: {
    outDir: '../src/tasca/web/dist',
    emptyOutDir: true,
    // Target modern browsers that support :has() selector
    // This prevents LightningCSS from stripping it
    cssTarget: ['chrome105', 'safari15.4', 'firefox121'],
    target: 'es2020',
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})