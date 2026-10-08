import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { parseEnv } from 'node:util'

export function localWebPort(envFile = fileURLToPath(new URL('../.env', import.meta.url))): number {
  // Match Python: exact root .env and a literal port, without Vite mode/local overrides.
  const env = existsSync(envFile) ? parseEnv(readFileSync(envFile, 'utf8')) : {}
  const rawPort = process.env.DOCUCHECK_WEB_PORT ?? env.DOCUCHECK_WEB_PORT ?? '15173'
  if (!/^[0-9]{1,5}$/.test(rawPort) || Number(rawPort) < 1 || Number(rawPort) > 65535) {
    throw new Error('invalid_web_port')
  }
  return Number(rawPort)
}

export default defineConfig(() => ({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: localWebPort(),
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
}))
