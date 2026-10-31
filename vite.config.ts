import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Relative base works for GitHub Pages project sites and local static servers.
export default defineConfig({
  plugins: [svelte()],
  base: './',
  build: {
    outDir: 'docs',
    emptyOutDir: true,
  },
})
