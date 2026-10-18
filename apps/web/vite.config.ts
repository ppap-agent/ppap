import {defineConfig} from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'
import {readFileSync} from 'node:fs'

const docker = process.env.PPAP_VITE_DOCKER === '1'
const preview = process.env.PPAP_PREVIEW === '1'

export default defineConfig({
  cacheDir: preview ? 'node_modules/.vite-preview' : 'node_modules/.vite',
  plugins: [
    react(),
    tailwindcss(),
    ...(preview ? [{
      name: 'ppap-local-preview',
      transformIndexHtml(html: string) {
        return html.replace('<head>', '<head><script>document.documentElement.classList.toggle("dark", document.cookie.includes("ppap_preview_theme=dark"))</script>')
      },
      configureServer(server: {middlewares: {use: (handler: (req: import('node:http').IncomingMessage, res: import('node:http').ServerResponse, next: () => void) => void) => void}}) {
        server.middlewares.use((req, res, next) => {
          const pathname = new URL(req.url || '/', 'http://127.0.0.1').pathname
          if (pathname === '/api' || pathname.startsWith('/api/')) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({error: 'preview_has_no_business_api'}))
            return
          }
          if (pathname === '/__preview') {
            res.setHeader('Content-Type', 'text/html; charset=utf-8')
            res.setHeader('Cache-Control', 'no-store')
            res.end(readFileSync(path.resolve(__dirname, 'preview/workbench.html')))
            return
          }
          if (pathname === '/runtime-config.json') {
            res.setHeader('Content-Type', 'application/json')
            res.setHeader('Cache-Control', 'no-store')
            res.end(JSON.stringify({api_url: ''}))
            return
          }
          next()
        })
      },
    }] : []),
    {
      name: 'ppap-runtime-config',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const url = new URL(req.url || '/', 'http://127.0.0.1')
          if (!preview && ['/audit', '/audit/', '/__audit-native'].includes(url.pathname)) {
            req.url = '/audit.html' + url.search
          }
          if (url.pathname !== '/runtime-config.json') {
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
  resolve: {alias: {'@': path.resolve(__dirname, './src')}},
  build: {
    rollupOptions: {
      input: {
        app: path.resolve(__dirname, 'index.html'),
        audit: path.resolve(__dirname, 'audit.html'),
        addon: path.resolve(__dirname, 'addon.html'),
      },
    },
  },
  server: {
    proxy: preview ? undefined : {
      '/api': {target: process.env.PPAP_PUBLIC_API_URL || 'http://127.0.0.1:8080', changeOrigin: true, rewrite: path => path.replace(/^\/api/, '')},
    },
    host: preview ? '127.0.0.1' : docker ? '0.0.0.0' : '127.0.0.1',
    port: preview ? 4185 : 4173,
    strictPort: true,
    hmr: docker ? {host: '127.0.0.1', clientPort: 4173} : undefined,
    watch: docker ? {usePolling: true, interval: 300} : undefined,
  },
})
