import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/',
  server: {port: 5173, proxy: {'/runtime-config.json': 'http://127.0.0.1:4173'}},
})
