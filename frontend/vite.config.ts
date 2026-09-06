import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // VITE_API_PROXY lets `npm run dev:prodapi` (mode "prodapi", see
  // .env.prodapi) point the /api proxy at the deployed backend so UI changes
  // can be checked against real data without running the Docker stack.
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_PROXY || 'http://localhost:8000'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      rollupOptions: {
        output: {
          // Stable vendor chunks so app-only deploys don't invalidate library bytes.
          // Function form: the object form let React's CJS internals get hoisted
          // into vendor-router, leaving vendor-react nearly empty.
          manualChunks(id) {
            if (!id.includes('/node_modules/')) return undefined
            const pkg = id.split('/node_modules/').pop()!.split('/')[0]
            if (pkg === 'react' || pkg === 'react-dom' || pkg === 'scheduler') return 'vendor-react'
            if (pkg.startsWith('react-router')) return 'vendor-router'
            if (pkg === 'axios') return 'vendor-axios'
            if (
              pkg === 'recharts' ||
              pkg.startsWith('d3-') ||
              pkg === 'victory-vendor' ||
              pkg === 'react-smooth' ||
              pkg === 'react-transition-group' ||
              pkg === 'lodash'
            ) {
              return 'vendor-recharts'
            }
            return undefined
          },
        },
      },
    },
  }
})
