// Captures the README screenshots from a running Vegapunk demo instance.
// Usage (from a directory where `playwright` is installed):
//   VP_URL=http://localhost:18100 VP_USER=demo VP_PASS=... VP_OUT=docs/images \
//   VP_CONTAINER=worker VP_FINDING=1 VP_CONVERSATION=<id> node capture.mjs
// CHROME_PATH may point at a Chrome/Chromium binary; otherwise Playwright's own is used.
import { chromium } from 'playwright'
import path from 'node:path'

const env = (name, fallback) => {
  const value = process.env[name] ?? fallback
  if (value === undefined) throw new Error(`Set ${name}`)
  return value
}
const base = env('VP_URL', 'http://localhost:18100')
const out = env('VP_OUT', 'docs/images')
const scale = Number(env('VP_SCALE', '1'))
const container = env('VP_CONTAINER', 'worker')
const finding = process.env.VP_FINDING
const conversation = process.env.VP_CONVERSATION

const browser = await chromium.launch(
  process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
)
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: scale,
  colorScheme: 'dark',
})
const page = await context.newPage()
page.on('console', (m) => m.type() === 'error' && console.warn('console error:', m.text()))

await page.goto(`${base}/sign-in`)
await page.getByLabel('Username').fill(env('VP_USER'))
await page.getByLabel('Password').fill(env('VP_PASS'))
await page.getByRole('button', { name: /sign in/i }).click()
await page.waitForURL((u) => !u.pathname.startsWith('/sign-in'))

async function shot(route, file) {
  await page.goto(`${base}${route}`)
  await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {})
  await page.waitForTimeout(2_000) // let charts draw
  await page.screenshot({ path: path.join(out, file), fullPage: false })
  console.log('saved', file)
}

const only = process.env.VP_ONLY?.split(',')
const want = (f) => !only || only.includes(f)
if (want('overview')) await shot('/', 'overview.png')
if (want('containers')) await shot('/containers', 'containers.png')
if (want('container-detail')) await shot(`/containers/${encodeURIComponent(container)}`, 'container-detail.png')
if (want('findings')) await shot('/findings', 'findings.png')
if (finding && want('finding-detail')) await shot(`/findings/${finding}`, 'finding-detail.png')
if (conversation && want('agent')) await shot(`/agent?c=${encodeURIComponent(conversation)}`, 'agent.png')
if (want('settings')) await shot('/settings', 'settings.png')

await browser.close()
