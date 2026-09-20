import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The FastAPI service (backend/). Same-origin in dev, so no CORS setup.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
