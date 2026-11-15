import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import type { Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import autoImport from 'unplugin-auto-import/vite'

const enDemoBuild = process.env.EN_DEMO_BUILD === '1'

const sensitiveRootProbePaths = new Set([
  '/.boto',
  '/.gcloud/credentials',
  '/.kube/config',
  '/.s3cfg',
  '/.supabase/config.toml',
  '/.vercel/project.json',
  '/config.json',
  '/heroku.json',
  '/kubeconfig',
  '/local.settings.json',
  '/private.key',
  '/private.pem',
  '/secret.key',
  '/server.key',
  '/server.pem',
  '/settings.json',
  '/settings.local.json',
  '/supabase/config.toml',
  '/terraform.tfstate',
  '/terraform.tfvars',
  '/vercel.json',
  '/wrangler.toml',
])
const sensitiveRootProbePattern = /^\/(?:server|private|secret)\.(?:pem|key|crt)$/i

function blockSensitiveRootProbeRequests(): Plugin {
  return {
    name: 'block-sensitive-root-probe-requests',
    enforce: 'pre',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const pathname = new URL(req.url || '/', 'http://localhost').pathname

        if (sensitiveRootProbePaths.has(pathname) || sensitiveRootProbePattern.test(pathname)) {
          res.statusCode = 404
          res.setHeader('Content-Type', 'text/plain; charset=utf-8')
          res.end('Not found')
          return
        }

        next()
      })
    },
  }
}

function englishDemoDevEntry(): Plugin {
  return {
    name: 'english-demo-dev-entry',
    enforce: 'pre',
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const pathname = new URL(req.url || '/', 'http://localhost').pathname
        if (/^\/en-demo\/(?:|home|login|allProblems|unavailable|problem\/\d+)$/.test(pathname)) {
          req.url = '/en-demo/en-demo.html'
        }
        next()
      })
    },
  }
}


// https://vite.dev/config/
export default defineConfig({
  base: enDemoBuild ? '/en-demo/' : '/',
  build: enDemoBuild ? {
    target: 'esnext',
    outDir: 'dist-en-demo',
    rollupOptions: { input: fileURLToPath(new URL('./en-demo.html', import.meta.url)) },
  } : undefined,
  plugins: [
    ...(enDemoBuild ? [englishDemoDevEntry()] : []),
    blockSensitiveRootProbeRequests(),
    vue(),
    vueJsx(),
    autoImport({
      imports: ['vue', 'vue-router', 'pinia'],
      dts: "./auto-import.d.ts",
      eslintrc: {
        enabled: false,
        filepath: './.eslintrc-auto-import.js',
        globalsPropValue: true,
      },
    }),

  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 3010,
    host: '127.0.0.1',
    strictPort: true,
    proxy: {
      '/en-demo/api': {
        target: process.env.VITE_EN_DEMO_API_TARGET || 'http://127.0.0.1:9002',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/en-demo/, ''),
      },
    },
  },
})
