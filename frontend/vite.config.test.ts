import { afterEach, expect, test, vi } from 'vitest'
import type { ConfigEnv, UserConfig } from 'vite'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import config, { localWebPort } from './vite.config'

const resolve = () => (config as (env: ConfigEnv) => UserConfig)({ command: 'serve', mode: 'development' })
afterEach(() => vi.unstubAllEnvs())

test('configured port is explicit, loopback only and cannot silently drift', () => {
  vi.stubEnv('DOCUCHECK_WEB_PORT', '15174')
  const actual = resolve()
  expect(actual.server?.port).toBe(15174)
  expect(actual.server?.strictPort).toBe(true)
  expect(actual.server?.host).toBe('127.0.0.1')
  expect(actual.envPrefix).toBeUndefined()
})

test.each(['', '0', '65536', '-1', '1e4', '15173/path', '１２３', '${PORT}'])('rejects invalid port %s', value => {
  vi.stubEnv('DOCUCHECK_WEB_PORT', value)
  expect(resolve).toThrow('invalid_web_port')
})

test('uses exact root file, ignores local/mode overrides, and defaults without a file', () => {
  vi.stubEnv('DOCUCHECK_WEB_PORT', undefined)
  const directory = mkdtempSync(join(tmpdir(), 'docucheck-web-port-'))
  try {
    expect(localWebPort(join(directory, '.env'))).toBe(15173)
    writeFileSync(join(directory, '.env'), 'DOCUCHECK_WEB_PORT="15175"\n')
    writeFileSync(join(directory, '.env.local'), 'DOCUCHECK_WEB_PORT=15176\n')
    writeFileSync(join(directory, '.env.development'), 'DOCUCHECK_WEB_PORT=15177\n')
    expect(localWebPort(join(directory, '.env'))).toBe(15175)
  } finally {
    rmSync(directory, { recursive: true, force: true })
  }
})
