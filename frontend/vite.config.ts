import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 开发期把后端接口代理到 FastAPI（127.0.0.1:8000），前端走 5173
      // 上线时 npm run build 产物由 FastAPI 托管，无需代理
      '/auth': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/kb': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/sessions': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/chat': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/datasets': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/upload': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/analysis': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/dashboards': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/shares': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/shared': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/schedules': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
