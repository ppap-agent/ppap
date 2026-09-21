import {defineConfig} from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {port: 5173, proxy: {'/runtime-config.json': 'http://127.0.0.1:4173'}},
})
