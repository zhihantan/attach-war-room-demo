import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: Vite serves the SPA on :5173 and proxies /api to FastAPI on :8000.
// Prod: `vite build` -> dist/, served by FastAPI as a single Databricks App.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
