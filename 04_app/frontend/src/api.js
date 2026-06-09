// Typed-ish API client. The browser only ever talks to /api (FastAPI); all
// Databricks credentials stay server-side. Every call surfaces HTTP errors so the
// UI can render error states instead of silently showing stale data.
async function j(p, opts) {
  const r = await fetch(p, opts)
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail || '' } catch { /* non-JSON */ }
    throw new Error(`${r.status} ${r.statusText}${detail ? ` — ${detail}` : ''}`)
  }
  return r.json()
}
const qp = (o) => Object.entries(o).filter(([, v]) => v != null && v !== '')
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
const post = (p, body) => j(p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

export const getProfile = () => j('/api/profile')
export const getHealth = () => j('/api/health')
export const getPartners = () => j('/api/partners')
export const getOverview = (days = 30) => j(`/api/overview?days=${days}`)
export const getFunnel = (partner, device_tier, recent_days = 45) => j(`/api/funnel?${qp({ partner, device_tier, recent_days })}`)
export const getLossRatio = (partner, device_tier) => j(`/api/lossratio?${qp({ partner, device_tier })}`)
export const getCheckout = (partner, device_tier) => j(`/api/checkout?${qp({ partner, device_tier })}`)
export const getRecoveredGwp = () => j('/api/recovered_gwp')
export const getScenarios = () => j('/api/scenarios')
export const getAudit = () => j('/api/audit')
export const scanBook = (days = 45) => j(`/api/scan?days=${days}`)
export const askGenie = (q) => j(`/api/genie?${qp({ q })}`)
export const resetDemo = () => post('/api/reset', {})
export const rollback = (scenario_id, conversation_id = null) => post('/api/rollback', { scenario_id, conversation_id })
export const logEvent = (name, detail = null, conversation_id = null) =>
  post('/api/event', { name, detail, conversation_id }).catch(() => {})
export const approveScenario = (scenario_id, approved_by = 'demo_user', conversation_id = null) =>
  post('/api/approve', { scenario_id, approved_by, conversation_id })

// Stream the agent over SSE; onEvent receives each {type, ...} event
// (delta | tool_call | tool_result | final | notice | error | done).
export async function streamChat(conversation_id, message, onEvent) {
  let resp
  try {
    resp = await fetch('/api/chat', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ conversation_id, message }),
    })
  } catch (e) {
    onEvent({ type: 'error', message: 'Network error reaching the agent — ' + e.message })
    return
  }
  if (!resp.ok || !resp.body) {
    onEvent({ type: 'error', message: `Agent unavailable (${resp.status})` })
    return
  }
  const reader = resp.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i)
      buf = buf.slice(i + 2)
      const line = chunk.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try { onEvent(JSON.parse(line.slice(6))) } catch { /* ignore partial */ }
    }
  }
}
