// Records the frames of the README walkthrough (demo.gif) from a running Vegapunk demo instance.
// Usage (from a directory where `playwright` is installed):
//   VP_URL=http://localhost:18200 VP_USER=demo VP_PASS=... VP_FINDING=1 \
//   VP_FRAMES=/tmp/vp-frames node make-demo.mjs
// then assemble the GIF with make-demo.py. CHROME_PATH may point at a Chrome/Chromium binary.
// The finding (VP_FINDING) must not have a conversation yet: the tour asks the agent about it.
// Each frame is a 1280x720 screenshot; frames.json lists them with how long each is shown and how
// long the agent took (make-demo.py adds a caption saying the wait was shortened).
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const env = (name, fallback) => {
  const value = process.env[name] ?? fallback
  if (value === undefined) throw new Error(`Set ${name}`)
  return value
}
const base = env('VP_URL', 'http://localhost:18200')
const out = env('VP_FRAMES')
const finding = env('VP_FINDING')
const container = env('VP_CONTAINER', 'jobs')
const answerTimeout = Number(env('VP_ANSWER_TIMEOUT', '300')) * 1000
fs.mkdirSync(out, { recursive: true })

const browser = await chromium.launch(
  process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
)
const context = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  deviceScaleFactor: 1,
  colorScheme: 'dark',
})
const page = await context.newPage()
page.on('console', (m) => m.type() === 'error' && console.warn('console error:', m.text()))

const frames = []
async function frame(ms, note) {
  const file = `frame-${String(frames.length).padStart(2, '0')}.png`
  await page.screenshot({ path: path.join(out, file) })
  frames.push({ file, ms, note })
  console.log('frame', file, ms, note)
}
async function settle(wait = 1_500) {
  await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {})
  await page.waitForTimeout(wait) // let charts draw
}
const nav = (name) =>
  page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: new RegExp(`^${name}`) })

await page.goto(`${base}/sign-in`)
await page.getByLabel('Username').fill(env('VP_USER'))
await page.getByLabel('Password').fill(env('VP_PASS'))
await page.getByRole('button', { name: /sign in/i }).click()
await page.waitForURL((u) => !u.pathname.startsWith('/sign-in'))

// 1. Overview
await settle(2_500)
await frame(1800, 'overview')
await page.mouse.move(700, 400)
await page.mouse.wheel(0, 400)
await page.waitForTimeout(800)
await frame(1800, 'overview chart')

// 2. Containers
await nav('Containers').click()
await page.waitForURL('**/containers')
await settle()
await frame(1600, 'containers')

// 3. Container detail
await page.getByRole('link', { name: container, exact: true }).first().click()
await page.waitForURL(`**/containers/${container}`)
await settle(2_500)
await page.mouse.move(700, 400)
await page.mouse.wheel(0, 330)
await page.waitForTimeout(800)
await frame(2200, 'container detail')

// 4. Findings
await nav('Findings').click()
await page.waitForURL('**/findings')
await settle()
await frame(1600, 'findings')

// 5. Finding detail
await page.locator(`a[href="/findings/${finding}"]`).first().click()
await page.waitForURL(`**/findings/${finding}`)
await settle()
await frame(2600, 'finding detail')

// 6. Ask the agent about it
await page.getByRole('link', { name: 'Ask agent about this finding' }).click()
await page.waitForURL('**/agent**')
await settle()
await frame(1800, 'agent with finding context')

// 7. Send, keep a few frames while it works, then the answer
const question = process.env.VP_QUESTION
if (question) {
  await page.getByLabel('Message the agent').fill(question)
  await frame(1400, 'question typed')
}
await page.getByRole('button', { name: 'Send' }).click()
const started = Date.now()
const stopButton = page.getByRole('button', { name: 'Stop' })
await stopButton.waitFor({ timeout: 10_000 })
let lastSteps = ''
let working = 0
while (await stopButton.isVisible()) {
  if (Date.now() - started > answerTimeout) throw new Error('The agent did not answer in time')
  const steps = await page.locator('.vp-msg').last().innerText().catch(() => '')
  const changed = steps !== lastSteps
  if ((working === 0 || changed) && working < 4) {
    await frame(1000, `agent working ${Math.round((Date.now() - started) / 1000)} s`)
    working += 1
    lastSteps = steps
  }
  await page.waitForTimeout(1_000)
}
const seconds = Math.round((Date.now() - started) / 1000)
await settle(1_000)

await page.locator('.vp-msg').last().scrollIntoViewIfNeeded().catch(() => {})
await frame(4000, 'answer')

fs.writeFileSync(path.join(out, 'frames.json'), JSON.stringify({ seconds, frames }, null, 2))
await browser.close()
