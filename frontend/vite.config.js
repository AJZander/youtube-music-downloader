import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // dev is same-origin too: /api -> local backend
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
