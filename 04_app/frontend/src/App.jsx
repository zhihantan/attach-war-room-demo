import { useState, useEffect, useRef, useCallback } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import * as api from './api'

const TIERS = ['premium', 'mid', 'budget']
const STAGES = [
  ['offer_show_rate', 'Offer shown'],
  ['quote_start_rate', 'Quote start'],
  ['quote_complete_rate', 'Quote done'],
  ['complete_to_bind_rate', 'Bind'],
  ['activation_rate', 'Activate'],
]
const MARKET_NAMES = { IT: 'Italy', TH: 'Thailand', SG: 'Singapore', GB: 'United Kingdom', US: 'United States', KE: 'Kenya' }
const pct = (v) => (v == null ? '—' : (v * 100).toFixed(1) + '%')
const usd = (v) => (v == null ? '—' : '$' + Math.round(v).toLocaleString())
const SEEN_KEY = 'awr_seen_walkthrough_v1'

// Friendly labels + one-line summaries for the reasoning trace (Tier 1.4).
const TOOL_LABELS = {
  scan_for_anomalies: 'Scanning the whole book',
  partner_funnel_overview: 'Comparing partners',
  funnel_diagnosis: 'Localizing the funnel stage',
  abandonment_reasons: 'Reading abandonment reasons',
  classify_abandonment: 'Classifying the cause',
  get_offer_config: 'Checking live offer config',
  loss_ratio_for: 'Checking the loss-ratio guardrail',
  run_shadow_sim: 'Shadow-testing the fix',
  propose_offer_change: 'Drafting the proposal',
  ship_offer_change: 'Shipping to live checkout',
  rollback_offer_change: 'Rolling back the change',
  query_genie: 'Asking Genie',
  draft_partner_note: 'Drafting partner note',
  recent_activity: 'Recalling what shipped',
}
function traceSummary(name, result) {
  if (!result || typeof result !== 'object') return ''
  if (result.error) return '⚠ ' + result.error
  if (name === 'funnel_diagnosis') return result.largest_stage_drop ? `largest drop: ${result.largest_stage_drop.replace(/_/g, ' ')}` : ''
  if (name === 'loss_ratio_for') return result.loss_ratio != null ? `loss ratio ${pct(result.loss_ratio)} ${result.within_guardrail ? '· OK' : '· OVER'}` : ''
  if (name === 'run_shadow_sim') return result.recovered_gwp_per_month_usd != null ? `+${usd(result.recovered_gwp_per_month_usd)}/mo, attach ${pct(result.attach_delta)}` : ''
  if (name === 'scan_for_anomalies') return result.top_opportunity ? `top: ${result.top_opportunity.partner_name} (${usd(result.top_opportunity.recoverable_gwp_per_month_usd)}/mo)` : `${result.partners_scanned} scanned`
  if (name === 'get_offer_config') return Array.isArray(result.configs) ? `${result.configs.filter((c) => c.impression_enabled).length}/${result.configs.length} impressions on` : ''
  if (name === 'propose_offer_change') return result.blocked_by_guardrail ? 'blocked by guardrail' : 'awaiting approval'
  return ''
}

// Animated count-up for the recovered-GWP tile (Tier 1.1).
function useCountUp(target, ms = 900) {
  const [val, setVal] = useState(target || 0)
  const ref = useRef(target || 0)
  useEffect(() => {
    const from = ref.current, to = target || 0
    if (from === to) { ref.current = to; setVal(to); return }
    let raf, start
    const tick = (t) => {
      if (start == null) start = t
      const p = Math.min(1, (t - start) / ms)
      const eased = 1 - Math.pow(1 - p, 3)
      setVal(from + (to - from) * eased)
      if (p < 1) raf = requestAnimationFrame(tick)
      else ref.current = to
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return val
}

function Dropdown({ label, value, options, onChange }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div className={'dd' + (open ? ' open' : '')} ref={ref}>
      {label && <span className="dd-label">{label}</span>}
      <button type="button" className="dd-trigger" onClick={() => setOpen((o) => !o)}>
        <span>{value || '—'}</span>
        <svg className="dd-caret" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M5 8l5 5 5-5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="dd-menu">
          {options.map((o) => (
            <div key={o} className={'dd-item' + (o === value ? ' sel' : '')}
              onClick={() => { onChange(o); setOpen(false) }}>{o}</div>
          ))}
        </div>
      )}
    </div>
  )
}

function WelcomeModal({ profile, onClose }) {
  const slides = profile?.walkthrough || []
  const [i, setI] = useState(0)
  if (!slides.length) return null
  const s = slides[i]
  const last = i === slides.length - 1
  return (
    <div className="overlay" onClick={onClose}>
      <div className="welcome" onClick={(e) => e.stopPropagation()}>
        <div className="dots">
          {slides.map((_, k) => <span key={k} className={'dot-i' + (k === i ? ' on' : '')} onClick={() => setI(k)} />)}
        </div>
        <div className="welcome-icon">{s.icon}</div>
        <h2>{s.title}</h2>
        {s.body && <p>{s.body}</p>}
        {s.bullets?.length > 0 && (
          <ul className="welcome-bullets">{s.bullets.map((b, k) => <li key={k}>{b}</li>)}</ul>
        )}
        <div className="welcome-actions">
          <button className="link-btn" onClick={onClose}>Skip</button>
          <div className="spacer" />
          {i > 0 && <button className="btn ghost" onClick={() => setI(i - 1)}>Back</button>}
          <button className="btn" onClick={() => (last ? onClose() : setI(i + 1))}>{last ? "Let's go" : 'Next ▶'}</button>
        </div>
      </div>
    </div>
  )
}

function About({ profile }) {
  const a = profile?.about
  const acct = profile?.account || {}
  const guardrail = profile?.guardrail_loss_ratio ?? 0.70
  const ref = useRef(null)
  // sleek scroll-reveal: fade/slide each section in as it enters the viewport
  useEffect(() => {
    const root = ref.current
    if (!root) return
    const els = root.querySelectorAll('.reveal')
    if (!('IntersectionObserver' in window)) { els.forEach((e) => e.classList.add('in')); return }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target) } })
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' })
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [a])
  if (!a) return <div className="about" ref={ref}><div className="typing">Loading…</div></div>
  const ING_ICON = { Agent: '🤖', 'AI/BI Genie': '🧞', Lakebase: '🐘', 'Foundation Model APIs': '✨' }
  return (
    <div className="about" ref={ref}>
      <section className="about-hero reveal">
        <div className="about-eyebrow">{acct.name} · {acct.product}</div>
        <h1>{acct.product}</h1>
        <p className="about-tagline">{acct.tagline}</p>
        {acct.synthetic_notice && <div className="about-synth">🔒 {acct.synthetic_notice}</div>}
      </section>

      <section className="about-card reveal">
        <h2>Why it exists</h2>
        {(Array.isArray(a.purpose) ? a.purpose : [a.purpose]).map((p, i) => (
          <p key={i} className="about-body">{p}</p>
        ))}
        {a.glossary?.length > 0 && (
          <div className="glossary">
            <div className="gloss-head">Key terms, in plain English</div>
            <div className="gloss-grid">
              {a.glossary.map((g, i) => (
                <div key={i} className="gloss-item">
                  <span className="gloss-term">{g.term}</span>
                  <span className="gloss-def">{g.def}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {a.audience && <p className="about-aud">{a.audience}</p>}
      </section>

      <section className="about-card reveal">
        <h2>How it works — one closed loop</h2>
        <div className="loop-flow">
          {a.loop?.map((s, i) => (
            <div key={i} className="loop-step">
              <div className="loop-num">{i + 1}</div>
              <div className="loop-name">{s.step}</div>
              <div className="loop-detail">{s.detail}</div>
              {i < a.loop.length - 1 && <span className="loop-arrow">→</span>}
            </div>
          ))}
        </div>
      </section>

      <section className="about-card reveal">
        <h2>Architecture</h2>
        <p className="about-body">{a.stack}</p>
        <div className="arch">
          <div className="arch-tier browser">
            <div className="arch-label">Browser</div>
            <div className="arch-box">React + Vite SPA<span>agent chat · funnel · guardrail · checkout · recovered-GWP</span></div>
          </div>
          <div className="arch-conn"><span>/api · SSE + JSON</span><div className="arch-line" /></div>
          <div className="arch-tier arch-app">
            <div className="arch-label">Databricks App (single)</div>
            <div className="arch-box">FastAPI backend<span>hosts the agent loop · all Databricks credentials stay server-side</span></div>
          </div>
          <div className="arch-conn"><div className="arch-line" /></div>
          <div className="arch-tier ingredients">
            <div className="arch-label">Databricks platform</div>
            <div className="arch-pillars">
              <div className="pillar"><b>Foundation Model APIs</b><span>reason · classify</span></div>
              <div className="pillar"><b>AI/BI Genie</b><span>metric views (governed KPIs)</span></div>
              <div className="pillar"><b>Lakebase</b><span>Postgres — offer_config serving + ACID state</span></div>
              <div className="pillar"><b>Unity Catalog</b><span>Delta — sessions · policies · claims</span></div>
            </div>
          </div>
        </div>
        <div className="agent-loop-chip">
          <span className="alc-label">Agent loop</span>
          <code>funnel_diagnosis → get_offer_config → loss_ratio_for → run_shadow_sim → propose_offer_change → <b>human approval</b> → ship_offer_change (ACID) → checkout reads new config → recovered GWP</code>
        </div>
      </section>

      <section className="about-card reveal">
        <h2>Four ingredients, all load-bearing</h2>
        <div className="ing-grid">
          {a.ingredients?.map((ing, i) => (
            <div key={i} className="ing-card">
              <div className="ing-head"><span className="ing-icon">{ING_ICON[ing.name] || '◆'}</span><div><b>{ing.name}</b><span className="ing-tech">{ing.tech}</span></div></div>
              <div className="ing-role">{ing.role}</div>
              <div className="ing-why">↳ {ing.why}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="about-card reveal">
        <h2>Data &amp; guardrail</h2>
        <p className="about-body">{a.data}</p>
        <p className="about-body">Every shipped change is checked against a <b>{Math.round(guardrail * 100)}% loss-ratio guardrail</b> — the agent will refuse to ship a fix that buys attach with underpriced cover. The shadow-sim is deterministic and the loss-ratio elasticity is derived from the claims book, so the numbers are defensible, not LLM-guessed.</p>
        {profile?.genie_url && <a className="about-link" href={profile.genie_url} target="_blank" rel="noreferrer">Open the governed Genie space ↗</a>}
      </section>
    </div>
  )
}

function GenieModal({ result, onClose }) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className="genie-modal" onClick={(e) => e.stopPropagation()}>
        <h3>🧞 Same number, asked in AI/BI Genie</h3>
        <p className="hint">Your analysts can ask this themselves — the agent and Genie read the same governed metric views.</p>
        {result === 'loading' && <div className="typing">asking Genie…</div>}
        {result && result !== 'loading' && (
          <>
            {result.error && <div className="errbox">{result.error}</div>}
            {result.narrative && <div className="genie-narr">{result.narrative}</div>}
            {result.sql && <pre className="genie-sql">{result.sql}</pre>}
            {result.rows?.length > 0 && (
              <div className="genie-rows">
                <table>
                  <thead><tr>{result.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                  <tbody>{result.rows.slice(0, 6).map((r, k) => <tr key={k}>{r.map((v, m) => <td key={m}>{String(v)}</td>)}</tr>)}</tbody>
                </table>
              </div>
            )}
          </>
        )}
        <button className="btn ghost" style={{ marginTop: 12 }} onClick={onClose}>Close</button>
      </div>
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState('war')
  const [profile, setProfile] = useState(null)
  const [partners, setPartners] = useState([])
  const [partner, setPartner] = useState('')   // set from the profile's hero branch once partners load
  const [tier, setTier] = useState('mid')
  const [funnel, setFunnel] = useState(null)
  const [loss, setLoss] = useState(null)
  const [checkout, setCheckout] = useState(null)
  const [recovered, setRecovered] = useState(null)
  const [errs, setErrs] = useState({})
  const [loadingSlice, setLoadingSlice] = useState(true)
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [input, setInput] = useState('')
  const [proposal, setProposal] = useState(null)
  const [timeline, setTimeline] = useState(null)
  const [showWelcome, setShowWelcome] = useState(false)
  const [health, setHealth] = useState(null)
  const [genie, setGenie] = useState(null)
  const [flip, setFlip] = useState(false)
  const convId = useRef('demo-' + Math.random().toString(36).slice(2, 8))
  const scrollRef = useRef(null)
  const prevShown = useRef(null)

  const refreshSlice = useCallback(async () => {
    if (!partner) return   // wait until a real partner is selected (set from the profile on load)
    setLoadingSlice(true)
    const out = await Promise.allSettled([
      api.getFunnel(partner, tier), api.getLossRatio(partner, tier), api.getCheckout(partner, tier),
    ])
    const [f, l, c] = out
    const e = {}
    if (f.status === 'fulfilled') setFunnel(f.value); else { setFunnel(null); e.funnel = f.reason.message }
    if (l.status === 'fulfilled') setLoss(l.value); else { setLoss(null); e.loss = l.reason.message }
    if (c.status === 'fulfilled') {
      // pulse the checkout when the offer flips from hidden -> shown (the ship moment)
      if (prevShown.current === false && c.value.offer_shown) { setFlip(true); setTimeout(() => setFlip(false), 1400) }
      prevShown.current = c.value.offer_shown
      setCheckout(c.value)
    } else { setCheckout(null); e.checkout = c.reason.message }
    setErrs(e)
    setLoadingSlice(false)
  }, [partner, tier])

  const refreshGwp = useCallback(() => api.getRecoveredGwp().then(setRecovered).catch(() => {}), [])
  const refreshTimeline = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([api.getScenarios(), api.getAudit()])
      setTimeline({ scenarios: s, audit: a })
    } catch { setTimeline({ scenarios: [], audit: [] }) }
  }, [])

  useEffect(() => {
    api.getProfile().then(setProfile).catch(() => {})
    api.getPartners().then(setPartners).catch(() => {})
    refreshGwp()
    api.getHealth().then(setHealth).catch(() => setHealth({ ok: false }))
    if (!localStorage.getItem(SEEN_KEY)) setShowWelcome(true)
  }, [refreshGwp])
  // Default the selected partner to the profile's hero branch (e.g. the impressions
  // story) once the partner list loads. Only fires when the current value isn't a
  // real partner in the book — so it fixes a stale/empty default but never overrides
  // a user's pick, and it re-skins automatically with the profile.
  useEffect(() => {
    if (!partners.length) return
    const names = partners.map((p) => p.partner_name)
    if (names.includes(partner)) return
    const hero = profile?.branches?.[0]
    setPartner(hero && names.includes(hero.partner) ? hero.partner : names[0])
    if (hero?.tier) setTier(hero.tier)
  }, [partners, profile, partner])

  useEffect(() => { refreshSlice() }, [refreshSlice])
  useEffect(() => { scrollRef.current?.scrollTo({ top: 1e9, behavior: 'smooth' }) }, [messages])

  function closeWelcome() { localStorage.setItem(SEEN_KEY, '1'); setShowWelcome(false) }

  async function send(text) {
    if (!text || busy) return
    setBusy(true); setInput('')
    setMessages((m) => [...m, { role: 'user', content: text }, { role: 'assistant', content: '', steps: [] }])
    await api.streamChat(convId.current, text, (ev) => {
      setMessages((m) => {
        const copy = m.slice(); const last = { ...copy[copy.length - 1] }
        if (ev.type === 'tool_call') last.steps = [...(last.steps || []), { name: ev.name, args: ev.args, done: false }]
        else if (ev.type === 'tool_result') {
          if (last.steps?.length) {
            const steps = last.steps.slice()
            const idx = steps.map((s) => s.name).lastIndexOf(ev.name)
            if (idx >= 0) steps[idx] = { ...steps[idx], done: true, summary: traceSummary(ev.name, ev.result) }
            last.steps = steps
          }
          if (ev.name === 'propose_offer_change' && ev.result?.scenario_id) {
            setProposal({ id: ev.result.scenario_id, sim: ev.result.simulation, title: ev.result.title, blocked: ev.result.blocked_by_guardrail })
          }
        } else if (ev.type === 'delta') last.content = (last.content || '') + ev.content
        else if (ev.type === 'final') { if (ev.content) last.content = ev.content }
        else if (ev.type === 'notice') last.notice = ev.message
        else if (ev.type === 'error') last.content = (last.content || '') + '\n\n⚠️ ' + ev.message
        copy[copy.length - 1] = last
        return copy
      })
    })
    setBusy(false)
    refreshSlice(); refreshGwp(); if (timeline) refreshTimeline()
  }

  function runBranch(b) {
    setPartner(b.partner); setTier(b.tier)
    api.logEvent('branch', { id: b.id }, convId.current)
    send(b.prompt)
  }

  async function approve() {
    if (!proposal || proposal.blocked) return
    const res = await api.approveScenario(proposal.id, 'demo_user', convId.current)
    setProposal(null)
    await refreshSlice(); refreshGwp(); refreshTimeline()
    setMessages((m) => [...m, { role: 'system', content: res.shipped
      ? `✅ Shipped — ${res.configs_updated} offer configs flipped; recovered GWP +${usd(res.projected_gwp_delta_usd)}/mo`
      : `❌ ${res.error || 'ship failed'}` }])
  }

  async function reset() {
    if (busy) return
    await api.resetDemo().catch(() => {})
    setMessages([]); setProposal(null); setTimeline(null); prevShown.current = null
    convId.current = 'demo-' + Math.random().toString(36).slice(2, 8)
    await refreshSlice(); refreshGwp()
  }

  async function openGenie() {
    setGenie('loading')
    try {
      const q = `For ${partner} ${tier}-tier devices, how have offer-shown rate and attach rate changed in the last 45 days vs before?`
      setGenie(await api.askGenie(q))
    } catch (e) { setGenie({ error: e.message }) }
  }

  const accent = profile?.theme?.accent || '#ff5a36'
  const brandName = profile?.account?.name || 'bolttech'
  const productName = profile?.account?.product || 'Attach War-Room'
  const branches = profile?.branches || []
  const followups = profile?.followups || []
  const guardrail = profile?.guardrail_loss_ratio ?? 0.70

  // local-currency rendering for the selected partner's market (Tier 3.4)
  const marketName = MARKET_NAMES[partners.find((p) => p.partner_name === partner)?.market_id]
  const ccy = profile?.currencies?.[marketName]
  const fmtLocal = (v) => {
    if (v == null || !ccy || ccy.code === 'USD') return null
    const local = v / ccy.usd_rate
    return ccy.symbol + Math.round(local).toLocaleString()
  }

  const chartData = funnel && !funnel.error
    ? STAGES.map(([k, label]) => ({ stage: label, prior: funnel.prior?.[k], recent: funnel.recent?.[k] })) : []
  const lr = loss?.loss_ratio
  const needle = lr != null ? Math.min(100, (lr / 1.0) * 100) : 0
  const recoveredVal = recovered?.recovered_gwp_per_month_usd || 0
  const animated = useCountUp(recoveredVal)
  const annualized = recoveredVal * 12
  const attachDrop = funnel?.prior && funnel?.recent ? (funnel.prior.attach_rate - funnel.recent.attach_rate) : null
  const leaking = checkout && checkout.offer_shown === false && attachDrop > 0.01

  return (
    <div className="app">
      {showWelcome && <WelcomeModal profile={profile} onClose={closeWelcome} />}
      {genie && <GenieModal result={genie} onClose={() => setGenie(null)} />}

      <header className="header">
        <div className="brand"><span className="dot" style={{ background: accent }} />{brandName} <small>{productName}</small></div>
        <span className={'health ' + (health?.ok ? 'ok' : health ? 'bad' : 'wait')}
          title={health?.offline ? 'Offline demo mode (fixtures)' : health?.ok ? 'All systems ready' : 'Backend not ready — check /api/health'} />
        <nav className="tabs">
          <button className={'tab' + (tab === 'war' ? ' on' : '')} onClick={() => setTab('war')}>War Room</button>
          <button className={'tab' + (tab === 'about' ? ' on' : '')} onClick={() => setTab('about')}>About</button>
        </nav>
        <div className="spacer" />
        {tab === 'war' && (
          <>
            <div className="selectors">
              <Dropdown label="Partner" value={partner} options={partners.map((p) => p.partner_name)} onChange={setPartner} />
              <Dropdown label="Tier" value={tier} options={TIERS} onChange={setTier} />
            </div>
            <button className="icon-btn" title="Replay walkthrough" onClick={() => setShowWelcome(true)}>?</button>
            <button className="icon-btn" title="Reset the demo to the broken starting state" onClick={reset} disabled={busy}>↺ Reset</button>
            <div className={'gwp-tile' + (recoveredVal ? ' live' : '')}>
              <div className="label">Recovered GWP / month</div>
              <div className={'val' + (recoveredVal ? ' live' : '')}>{usd(animated)}</div>
              <div className="sub">{recoveredVal
                ? `≈ ${usd(annualized)}/yr · ${recovered?.shipped_count || 0} fix(es) shipped`
                : `${recovered?.shipped_count || 0} fix(es) shipped`}</div>
            </div>
          </>
        )}
      </header>

      {tab === 'about' && <About profile={profile} />}

      <div className="main" style={tab === 'about' ? { display: 'none' } : undefined}>
        {/* ---- left: agent ---- */}
        <div className="card chat">
          <h3>🛡️ War-Room Agent</h3>
          <p className="hint">Diagnose → shadow-test → approve → ship. Reasoning shown live.</p>
          <div className="chat-scroll" ref={scrollRef}>
            {messages.length === 0 && <div className="typing">Pick a scenario below, or ask why attach changed.</div>}
            {messages.map((m, i) => (
              <div key={i} className={'msg ' + m.role}>
                {m.role === 'assistant' ? (
                  <>
                    {m.steps?.length > 0 && (
                      <div className="steps">
                        {m.steps.map((s, k) => (
                          <span key={k} className={'chip' + (s.done ? '' : ' run')} title={s.summary || ''}>
                            🔧 {TOOL_LABELS[s.name] || s.name}{s.done && s.summary ? ` — ${s.summary}` : ''}
                          </span>
                        ))}
                      </div>
                    )}
                    {m.notice && <div className="notice">⚠️ {m.notice}</div>}
                    {m.content
                      ? <div className="bubble"><ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown></div>
                      : (busy && i === messages.length - 1 && <div className="typing">thinking…</div>)}
                  </>
                ) : m.role === 'system' ? m.content : m.content}
              </div>
            ))}
          </div>
          <div className="branches">
            {branches.map((b) => (
              <button key={b.id} className={'branch-btn' + (b.kind === 'guardrail' ? ' guard' : '')} disabled={busy}
                onClick={() => runBranch(b)} title={b.blurb}>{b.label}</button>
            ))}
          </div>
          <div className="prompts">
            {followups.map((p) => <button key={p} className="prompt-btn" disabled={busy} onClick={() => send(p)}>{p}</button>)}
          </div>
          <div className="composer">
            <input value={input} placeholder="Ask the war-room…" disabled={busy}
              onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send(input)} />
            <button className="btn" disabled={busy || !input} onClick={() => send(input)}>Send</button>
          </div>
        </div>

        {/* ---- right: metrics + checkout ---- */}
        <div className="col">
          {leaking && (
            <div className="stakes">
              <b>● Silent GWP leak.</b> The protection offer isn’t showing for {partner} {tier}-tier
              ({checkout.products_enabled}/{checkout.products_total} configs live) — attach is down {pct(attachDrop)} vs prior. Customers never see the offer.
            </div>
          )}
          <div className="card">
            <div className="card-head">
              <h3>Funnel diagnosis — {partner} · {tier}</h3>
              <button className="link-btn" onClick={openGenie} title="Reconcile this in AI/BI Genie">Verify in Genie ↗</button>
            </div>
            <p className="hint">Stage conversion, last 45 days vs prior. Genie metric views.</p>
            {errs.funnel ? <div className="errbox">Couldn’t load funnel — {errs.funnel}</div>
              : loadingSlice && !funnel ? <div className="skeleton chart-sk" />
              : (
              <>
                <div className="legend"><span><i style={{ background: '#3c4a68' }} />Prior</span><span><i style={{ background: accent }} />Last 45d</span></div>
                <ResponsiveContainer width="100%" height={210}>
                  <BarChart data={chartData} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                    <XAxis dataKey="stage" tick={{ fontSize: 11, fill: '#9fb0cc' }} axisLine={{ stroke: '#243049' }} tickLine={false} />
                    <YAxis tickFormatter={(v) => Math.round(v * 100) + '%'} tick={{ fontSize: 11, fill: '#76849e' }} domain={[0, 1]} axisLine={false} tickLine={false} />
                    <Tooltip formatter={(v) => pct(v)} cursor={{ fill: 'rgba(255,255,255,.04)' }}
                      contentStyle={{ background: '#131a2c', border: '1px solid #2e3a54', borderRadius: 10, color: '#e7edf6' }}
                      labelStyle={{ color: '#aab6cc' }} itemStyle={{ color: '#e7edf6' }} />
                    <Bar dataKey="prior" fill="#3c4a68" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="recent" radius={[4, 4, 0, 0]}>
                      {chartData.map((d, i) => {
                        const drop = (d.prior || 0) - (d.recent || 0)
                        return <Cell key={i} fill={drop > 0.08 ? accent : '#38bdf8'} />
                      })}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
                {funnel?.largest_stage_drop && (
                  <div className="diag">Largest drop at <b>{funnel.largest_stage_drop.replace(/_/g, ' ')}</b>
                    {funnel.recent && funnel.prior &&
                      <> — offer-shown {pct(funnel.prior.offer_show_rate)} → {pct(funnel.recent.offer_show_rate)}, attach {pct(funnel.prior.attach_rate)} → {pct(funnel.recent.attach_rate)}.</>}
                  </div>
                )}
              </>
            )}
          </div>

          <div className="card">
            <h3>Loss-ratio guardrail</h3>
            <p className="hint">Slice loss ratio vs the {Math.round(guardrail * 100)}% ship guardrail.</p>
            {errs.loss ? <div className="errbox">Couldn’t load loss ratio — {errs.loss}</div> : (
            <>
              <div className="gauge">
                <div className="track"><div className="fill" style={{ width: needle + '%' }} /><div className="gline" /><div className="needle" style={{ left: needle + '%' }} /></div>
                <div className="gauge-row"><span>0%</span><span style={{ color: lr > guardrail ? 'var(--red)' : 'var(--green)', fontWeight: 700 }}>{pct(lr)} {lr > guardrail ? '· OVER' : '· OK'}</span><span>100%</span></div>
              </div>
              <div className="kpis">
                <div className="kpi"><div className="k">Loss ratio</div><div className="v" style={{ color: lr > guardrail ? 'var(--red)' : 'var(--green)' }}>{pct(lr)}</div></div>
                <div className="kpi"><div className="k">Claims freq</div><div className="v">{pct(loss?.claims_frequency)}</div></div>
                <div className="kpi"><div className="k">GWP</div><div className="v">{usd(loss?.gwp_usd)}</div></div>
              </div>
            </>
            )}
          </div>

          {proposal && (
            <div className={'card proposal' + (proposal.blocked ? ' blocked' : '')}>
              <h4>{proposal.blocked ? '⛔ Proposed change — BLOCKED by guardrail' : '📋 Proposed change — awaiting approval'}</h4>
              <div className="row"><span>{proposal.title}</span><span className={'tag ' + (proposal.blocked ? 'bad' : 'ok')}>{proposal.blocked ? `loss ratio > ${guardrail}` : 'within guardrail'}</span></div>
              <div className="row"><span>Attach</span><b>{pct(proposal.sim?.current_attach)} → {pct(proposal.sim?.projected_attach)} ({proposal.sim?.attach_delta >= 0 ? '+' : ''}{pct(proposal.sim?.attach_delta)})</b></div>
              <div className="row"><span>Recovered GWP / month</span><b>{usd(proposal.sim?.recovered_gwp_per_month_usd)}{fmtLocal(proposal.sim?.recovered_gwp_per_month_usd) ? <span className="local"> ≈ {fmtLocal(proposal.sim?.recovered_gwp_per_month_usd)}</span> : null}</b></div>
              <div className="row"><span>Projected loss ratio</span><b style={{ color: proposal.sim?.within_guardrail ? 'var(--green)' : 'var(--red)' }}>{pct(proposal.sim?.projected_loss_ratio)}</b></div>
              <button className="btn" style={{ width: '100%', marginTop: 10 }} disabled={proposal.blocked} onClick={approve}>
                {proposal.blocked ? 'Cannot ship — breaches guardrail' : '✅ Approve & ship to live checkout'}
              </button>
            </div>
          )}

          <div className="card">
            <h3>Simulated partner checkout</h3>
            <p className="hint">Reads the live offer_config from Lakebase — flips the instant the agent ships.</p>
            {errs.checkout ? <div className="errbox">Couldn’t load checkout — {errs.checkout}</div> : (
            <div className="checkout">
              <div className="cart">
                <div className="phone" />
                <div><div style={{ fontWeight: 700 }}>{partner} checkout</div><div className="line">{tier}-tier device · add protection?{ccy && ccy.code !== 'USD' ? ` · ${ccy.code}` : ''}</div></div>
              </div>
              <div className={'offer ' + (checkout?.offer_shown ? 'shown' : 'hidden') + (flip ? ' flip' : '')}>
                <div className="badge">{checkout?.offer_shown ? '● Protection offer shown' : '○ No offer shown'}</div>
                {checkout?.offer_shown
                  ? <><div className="ttl">Device Protection — Screen + Theft</div><div className="px">Deductible tier: {checkout?.deductible_tier_shown} · {checkout?.products_enabled}/{checkout?.products_total} products live</div></>
                  : <div className="px">Impressions disabled ({checkout?.products_enabled}/{checkout?.products_total} products) — customers never see the offer.</div>}
              </div>
            </div>
            )}
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Decision &amp; audit timeline</h3>
              <button className="link-btn" onClick={() => (timeline ? setTimeline(null) : refreshTimeline())}>{timeline ? 'Hide' : 'Show'}</button>
            </div>
            <p className="hint">Every proposed → shipped → rolled-back decision, from the Lakebase audit log.</p>
            {timeline && (
              (timeline.audit?.length || timeline.scenarios?.length)
                ? <div className="timeline">
                    {timeline.audit.map((a) => (
                      <div key={'a' + a.audit_id} className="tl-row">
                        <span className="tl-dot" />
                        <div>
                          <div className="tl-title">{a.field_changed}: <b>{a.before_value} → {a.after_value}</b> <span className="muted">({a.partner_id} {a.device_tier})</span></div>
                          <div className="tl-sub">{a.rationale}</div>
                        </div>
                      </div>
                    ))}
                    {timeline.scenarios.filter((s) => s.status !== 'shipped').map((s) => (
                      <div key={'s' + s.scenario_id} className="tl-row">
                        <span className={'tl-dot ' + s.status} />
                        <div><div className="tl-title">{s.title} <span className={'tag ' + (s.within_guardrail ? 'ok' : 'bad')}>{s.status}</span></div></div>
                      </div>
                    ))}
                  </div>
                : <div className="typing">No decisions yet — ship a fix to see it logged here.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
