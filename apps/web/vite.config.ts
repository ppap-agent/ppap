import {defineConfig} from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'
import {readFileSync, readdirSync} from 'node:fs'
import {writeDesignTokens} from './scripts/build-tokens.ts'

const docker = process.env.PPAP_VITE_DOCKER === '1'

export default defineConfig({
  // The workbook worker is loaded on demand; discover its dependencies before opening a reader.
  optimizeDeps: {include: ['xlsx', 'xlsx/dist/cpexcel.full.mjs']},
  plugins: [
    // The runtime theme is generated from tokens/ before any stylesheet is read (docs/DESIGN_SYSTEM.md).
    {name: 'ppap-design-tokens', enforce: 'pre', async buildStart() { await writeDesignTokens() }},
    react(),
    tailwindcss(),
    {
      name: 'ppap-pdf-assets',
      apply: 'build',
      generateBundle() {
        for (const folder of ['cmaps', 'standard_fonts', 'wasm']) {
          const source = path.resolve(__dirname, 'node_modules/pdfjs-dist', folder)
          for (const file of readdirSync(source, {withFileTypes: true}).filter(file => file.isFile())) {
            this.emitFile({type: 'asset', fileName: `pdfjs/${folder}/${file.name}`, source: readFileSync(path.join(source, file.name))})
          }
        }
      },
    },
    {
      name: 'ppap-runtime-config',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const url = new URL(req.url || '/', 'http://127.0.0.1')
          if (url.pathname !== '/runtime-config.json' && url.pathname !== '/addon/runtime-config.json') {
            next()
            return
          }
          res.setHeader('Content-Type', 'application/json')
          res.setHeader('Cache-Control', 'no-store')
          res.end(JSON.stringify({api_url: '/api'}))
        })
      },
    },
  ],
  base: '/',
  experimental: {
    // The Docs add-on runs in a frame inside Feishu, where no edition cookie is sent, and the
    // production proxy only routes /addon without one. Its page therefore loads everything
    // from /addon/…; scripts and styles then find their siblings relatively.
    renderBuiltUrl(filename, {hostType, hostId}) {
      if (hostType === 'html') return (hostId.endsWith('addon.html') ? '/addon/' : '/') + filename
      return {relative: true}
    },
  },
  resolve: {alias: {'@': path.resolve(__dirname, './src')}},
  build: {
    rollupOptions: {
      input: {
        app: path.resolve(__dirname, 'index.html'),
        addon: path.resolve(__dirname, 'addon.html'),
      },
    },
  },
  server: {
    proxy: {
      '/api': {target: process.env.PPAP_PUBLIC_API_URL || 'http://127.0.0.1:8080', changeOrigin: true, rewrite: path => path.replace(/^\/api/, '')},
    },
    host: docker ? '0.0.0.0' : '127.0.0.1',
    port: 4173,
    strictPort: true,
    hmr: docker ? {host: '127.0.0.1', clientPort: 4173} : undefined,
    watch: docker ? {usePolling: true, interval: 300} : undefined,
  },
})
