/* Trinity Local — Launchpad parts (Terminal Control-Desk skin).
   Mono-forward, prompt-led labels, sharp panels. Cosmetic recreation. */

function Die({size = 17}) {
  const s = size;
  const pip = Math.max(2.5, s * 0.18);
  const pts = [[0.28, 0.28], [0.5, 0.5], [0.72, 0.72]];
  return (
    <span className="hero-die" style={{width: s, height: s}}>
      {pts.map(([x, y], i) => (
        <span key={i} style={{left: x * s - pip / 2, top: y * s - pip / 2, width: pip, height: pip}} />
      ))}
    </span>
  );
}

/* soft tracked eyebrow (dash drawn in CSS) */
function Eyebrow({children, tone = ""}) {
  return <span className={"eyebrow " + tone}>{children}</span>;
}

function StatusBar() {
  return (
    <div className="statusbar">
      <span className="seg">Claude</span>
      <span className="seg">Codex</span>
      <span className="seg dim">Gemini</span>
      <span className="sp" />
      <span className="right">Lens ready</span>
      <span>·</span>
      <span className="right">45 councils</span>
      <span>·</span>
      <span className="right">Local · v1.7</span>
    </div>
  );
}

function Sidebar({councils, activeId, onNew, onOpen}) {
  return (
    <aside className="sidebar">
      <div className="rail-label">Councils</div>
      <button className="rail-new" onClick={onNew}>
        <span className="pr">+</span> Ask a new council
      </button>
      <input className="rail-search" placeholder="Search councils…" />
      {councils.length === 0 ? (
        <div className="rail-empty">No councils yet — ask one above, and take a breath.</div>
      ) : (
        <div className="rail-list">
          {councils.map(c => (
            <button
              key={c.id}
              className={"rail-item" + (c.id === activeId ? " active" : "")}
              onClick={() => onOpen(c.id)}
            >
              <div className="ri-task">{c.task}</div>
              <div className="ri-meta">
                {c.status === 'running'
                  ? <span style={{color: 'var(--warning)'}}>● running</span>
                  : <span style={{color: 'var(--green)'}}>● {c.winner}</span>}
                <span>· {c.when}</span>
              </div>
            </button>
          ))}
        </div>
      )}
      <div className="rail-spacer" />
      <div className="rail-foot">Local · MIT · v1.7 — your transcripts never leave this machine.</div>
    </aside>
  );
}

function Hero({task, setTask, onLaunch}) {
  return (
    <section className="card raised hero">
      <div className="hero-head">
        <div className="hero-eyebrow">
          <Die size={17} />
          <Eyebrow>Trinity</Eyebrow>
        </div>
        <div className="gear" title="Settings">⚙</div>
      </div>
      <h1>Run a <span className="accent">council</span>.</h1>
      <p className="sub">Ask all three. Keep what works. One prompt — Claude, ChatGPT, and Gemini all answer, and the chairman finds the calm verdict for you.</p>
      <p className="sub-small">No new app · no service · no API key</p>

      <div className="label composer-label">Your question</div>
      <div className="composer-wrap">
        <textarea
          className="composer"
          placeholder="Ask a council question…"
          value={task}
          onChange={e => setTask(e.target.value)}
        />
      </div>
      <div style={{marginTop: 18}}>
        <button className="btn primary" onClick={onLaunch} disabled={!task.trim()}>
          Launch council
        </button>
      </div>
      <p className="below-cta">
        Every model you use — frontier and local — answers in parallel. A local chairman
        synthesizes: agreed claims, disagreed claims with <em>why_matters</em>, picked winner.
        Or drive Trinity from inside Claude Code — type <span className="mono">/trinity</span> after{' '}
        <span className="mono">trinity-local install-mcp</span>.
      </p>
    </section>
  );
}

function InfoCard() {
  return (
    <section className="card rail-l info">
      <Eyebrow tone="info">Cross-bootstrap · Optional</Eyebrow>
      <h2>Install the Chrome extension for browser capture</h2>
      <p className="desc">
        Captures conversations from claude.ai / chatgpt.com / gemini.google.com into{' '}
        <span className="mono">~/.trinity/conversations/</span>, and ships Trinity's Python via
        Chrome's auto-update path so you don't have to <span className="mono">git pull</span> for new
        versions. <a href="#">See the sideload steps →</a>
      </p>
    </section>
  );
}

const ROUTING = [
  {k: 'REFRAME', sub: 'wanted a different frame', v: 0.81},
  {k: 'REDIRECT', sub: 'wanted a different shape', v: 0.80},
  {k: 'SHARPENING', sub: 'wanted more precision', v: 0.78},
  {k: 'COMPRESSION', sub: 'wanted it shorter', v: 0.48},
];

function RoutingCard({animate}) {
  return (
    <section className="card">
      <Eyebrow>Routing</Eyebrow>
      <h2>Which model wins for which question</h2>
      <p className="desc">
        Per-axis strength for each provider, computed from your own council preferences. Here:{' '}
        <em>claude</em> on your last 45 prompts.
      </p>
      <div className="bars">
        {ROUTING.map(r => (
          <div className="bar-row" key={r.k}>
            <div className="bar-top">
              <span className="bar-k">{r.k}<small>{r.sub}</small></span>
              <span className="bar-v">{r.v.toFixed(2)}</span>
            </div>
            <div className="bar-track">
              <div className="bar-fill" style={{width: (animate ? r.v * 100 : 0) + '%'}} />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

const NEW_MODELS = [
  {name: 'Claude Opus 4.8', desc: "Anthropic's latest flagship — adopted as Trinity's default Claude voice.", cmd: 'eval-run --target claude'},
  {name: 'GPT-5.5 (Codex)', desc: "OpenAI's codex-tuned GPT-5.5, dispatched via the Codex CLI.", cmd: 'eval-run --target codex'},
  {name: 'Gemini 3.1 Pro', desc: "Google's flagship, dispatched via the Antigravity (agy) CLI.", cmd: 'eval-run --target antigravity'},
];

function NewModelCard() {
  return (
    <section className="card rail-l green tint">
      <Eyebrow tone="warm">New model</Eyebrow>
      <h2>Score them against YOUR taste</h2>
      <p className="desc">
        A number no lab can produce — only the layer above all three sees your cross-provider
        rejection signal. One run builds a shareable eval-card.
      </p>
      <div style={{marginTop: 14}}>
        {NEW_MODELS.map(m => (
          <div className="model-row" key={m.name}>
            <div className="mr-name">{m.name}</div>
            <div className="mr-desc">{m.desc}</div>
            <span className="eval-pill">{m.cmd}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

const LENS = [
  {t: 'Compress, then ship', why: 'You reliably cut the model\'s answer in half and keep the back third. Brevity over completeness.'},
  {t: 'Lower-ops wins ties', why: 'When two options tie on power, you pick the one you can operate alone. You\'ve shipped solo before.'},
  {t: 'Name the wedge', why: 'You reframe "is it good" into "what\'s structurally true here that competitors can\'t copy."'},
  {t: 'Show the work', why: 'You reject claims that can\'t trace back to a source. Traceability over confidence.'},
];

function LensCards({onCopy}) {
  return (
    <section className="card">
      <Eyebrow>Your lens</Eyebrow>
      <h2>The principles you encode by what you redirect</h2>
      <p className="desc">
        Distilled from the transcripts already on your disk — title + why-it-matters, no verbatim
        prompts. Copy any principle to socials; the model/user context stays local.
      </p>
      <div className="lens-grid">
        {LENS.map(l => (
          <div className="lens-card" key={l.t}>
            <button className="lc-copy" onClick={() => onCopy(l.t)}>Copy</button>
            <div className="lc-title">{l.t}</div>
            <div className="lc-why">{l.why}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

Object.assign(window, {Die, Eyebrow, StatusBar, Sidebar, Hero, InfoCard, RoutingCard, NewModelCard, LensCards});
