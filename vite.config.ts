import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Relative base works for GitHub Pages project sites and local static servers.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'docs',
    emptyOutDir: true,
  },
})
