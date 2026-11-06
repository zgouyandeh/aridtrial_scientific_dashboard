import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendDir = dirname(fileURLToPath(import.meta.url));

const STATIC = process.env.VITE_STATIC === '1'; // GitHub Pages snapshot build

export default defineConfig({
  plugins: [react()],
  base: STATIC ? './' : '/static/',
  build: {
    outDir: STATIC ? resolve(frontendDir, 'dist-static') : resolve(frontendDir, '../src/aridtrial/dashboard/static'),
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
