import {defineConfig} from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

const docker = process.env.PPAP_VITE_DOCKER === '1'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'ppap-runtime-config',
      configureServer(server) {
        const apiUrl = (process.env.PPAP_PUBLIC_API_URL || 'http://127.0.0.1:8080').replace(/\/$/, '')
        server.middlewares.use((req, res, next) => {
          if ((req.url || '').split('?')[0] !== '/runtime-config.json') {
            next()
            return
          }
          res.setHeader('Content-Type', 'application/json')
          res.setHeader('Cache-Control', 'no-store')
          res.end(JSON.stringify({api_url: apiUrl}))
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
        addon: path.resolve(__dirname, 'addon.html'),
      },
    },
  },
  server: {
    host: docker ? '0.0.0.0' : '127.0.0.1',
    port: 4173,
    strictPort: true,
    hmr: docker ? {host: '127.0.0.1', clientPort: 4173} : undefined,
    watch: docker ? {usePolling: true, interval: 300} : undefined,
  },
})
