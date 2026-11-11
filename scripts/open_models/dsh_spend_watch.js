// Spend cap for a dsh run inside the agent container.
//
//   node dsh_spend_watch.js <sessions-dir> <dsh-pid> <since-ms> <cap-usd> <price-json> <capped-flag-file>
//
// dsh has no spend limit of its own ("no built-in turn budget", its agent-loop README).
// Every 10 s this reads every session log under <sessions-dir> (plain JSONL; the profile
// patch turns compression off), prices the usage of each model call logged at or after
// <since-ms> (assistant/message and compaction/summary `data.usage`, the `usage` chunk of
// an assistant/attempt) with <price-json> ({input, output, cache_read, cache_write} per
// million tokens), and once the total passes <cap-usd> writes <capped-flag-file> and sends
// SIGTERM to dsh (SIGKILL 20 s later). dsh flushes its session log on SIGTERM. Calls in
// flight at the kill are billed but never logged, so a capped run under-counts by those.
// It exits when dsh exits. It prints one line per minute; it never reads the environment.
'use strict'
const fs = require('fs')
const path = require('path')

const [root, pidArg, sinceArg, capArg, priceArg, flagFile] = process.argv.slice(2)
const pid = Number(pidArg), since = Number(sinceArg), cap = Number(capArg), price = JSON.parse(priceArg)
const CALLS = new Set(['assistant/message', 'assistant/attempt', 'compaction/summary'])

function logs(dir) {
  let out = []
  let entries = []
  try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { return out }
  for (const e of entries) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) out = out.concat(logs(p))
    else if (/^session\.v\d+\.jsonl$/.test(e.name)) out.push(p)
  }
  return out
}

function usageOf(ev) {
  const d = ev.data || {}
  if (ev.type === 'assistant/attempt') {
    let u = null
    for (const r of d.stream || []) if (r && r.chunk && r.chunk.type === 'usage') u = r.chunk.usage
    return u
  }
  return d.usage || null
}

const n = (u, k) => (typeof u[k] === 'number' ? u[k] : 0)

function spend() {
  let usd = 0, calls = 0
  const seen = new Set()
  for (const file of logs(root)) {
    let text = ''
    try { text = fs.readFileSync(file, 'utf8') } catch { continue }
    for (const line of text.split('\n')) {
      let ev
      try { ev = JSON.parse(line) } catch { continue }
      if (!ev || !CALLS.has(ev.type) || (ev.time || 0) < since) continue
      const id = ev.data && ev.data.message && ev.data.message.id
      if (id) { if (seen.has(id)) continue; seen.add(id) }
      const u = usageOf(ev)
      if (!u) continue
      calls += 1
      usd += (n(u, 'inputTokens') * price.input + n(u, 'cacheReadTokens') * price.cache_read
        + n(u, 'cacheWriteTokens') * price.cache_write + n(u, 'outputTokens') * price.output) / 1e6
    }
  }
  return { usd, calls }
}

function alive() { try { process.kill(pid, 0); return true } catch { return false } }

let lastPrint = 0
const timer = setInterval(() => {
  if (!alive()) {
    const s = spend()
    console.log(`dsh_spend_watch: dsh exited; ${s.calls} calls, $${s.usd.toFixed(3)} of $${cap.toFixed(2)}`)
    clearInterval(timer)
    return
  }
  const s = spend()
  if (Date.now() - lastPrint > 60000) {
    console.log(`dsh_spend_watch: ${s.calls} calls, $${s.usd.toFixed(3)} of $${cap.toFixed(2)}`)
    lastPrint = Date.now()
  }
  if (s.usd > cap) {
    console.log(`dsh_spend_watch: cap $${cap.toFixed(2)} passed ($${s.usd.toFixed(3)}, ${s.calls} calls); stopping dsh`)
    try { fs.writeFileSync(flagFile, JSON.stringify({ cap, usd: s.usd, calls: s.calls, time: Date.now() }) + '\n') } catch {}
    try { process.kill(pid, 'SIGTERM') } catch {}
    setTimeout(() => { try { process.kill(pid, 'SIGKILL') } catch {} ; process.exit(0) }, 20000)
    clearInterval(timer)
  }
}, 10000)
