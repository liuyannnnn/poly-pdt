import { defineConfig } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

const frontendPort = Number(process.env.FRONTEND_PORT || process.env.FRONT_PORT || 8088)
const backendPort = process.env.BACKEND_PORT || '8000'
const backendOrigin = process.env.VITE_BACKEND_ORIGIN || process.env.BACKEND_ORIGIN || `http://127.0.0.1:${backendPort}`

export default defineConfig({
  plugins: [
    // The React and Tailwind plugins are both required for Make, even if
    // Tailwind is not being actively used – do not remove them
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // Alias @ to the src directory
      '@': path.resolve(__dirname, './src'),
    },
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ['**/*.svg', '**/*.csv'],
  server: {
    host: '0.0.0.0',
    port: frontendPort,
    proxy: {
      '/api': {
        target: backendOrigin,
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
