import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { TanStackRouterVite } from '@tanstack/router-plugin/vite'
import tailwindcss from '@tailwindcss/vite'
import basicSsl from '@vitejs/plugin-basic-ssl'

export default defineConfig({
  plugins: [
    basicSsl(),
    TanStackRouterVite(),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: 'https://asterisk.getaipilot.com',
        changeOrigin: true,
        secure: false,
      },
      '/ws': {
        target: 'https://asterisk.getaipilot.com',
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  }
})
