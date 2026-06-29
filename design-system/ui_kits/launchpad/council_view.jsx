/* Trinity Local — Council review page (Terminal Control-Desk skin). */

const MEMBERS = [
  {name: 'Claude', model: 'Opus 4.8', winner: true,
   body: 'DuckDB. For an analytical scan workload the columnar engine wins decisively — vectorized execution over the whole column beats SQLite\'s row store. Caveat: SQLite has the simpler ops story if you\'ll run this alone.'},
  {name: 'Codex', model: 'GPT-5.5', winner: false,
   body: 'DuckDB is the right call for analytics — built for OLAP scans, zero-copy Arrow, and parallel aggregation. SQLite is general-purpose; you\'d fight it on large group-bys.'},
  {name: 'Gemini', model: '3.1 Pro', winner: false,
   body: 'Go with DuckDB. Analytical workloads favor its columnar layout and out-of-core execution. SQLite shines for transactional, embedded, single-writer use — not this.'},
];

const CLAIMS_AGREE = [
  'DuckDB wins on analytical scan speed for this workload.',
  'Columnar + vectorized execution is the deciding factor over a row store.',
];

const CLAIM_SPLIT = {
  claim: 'SQLite\'s simpler ops story should change the recommendation.',
  forP: ['Claude'], againstP: ['Codex', 'Gemini'],
  why: 'You\'ve shipped solo before and kept picking the lower-ops option — so the chairman weights this split toward "SQLite if you\'ll operate it alone."',
};

function MemberCard({m, revealed}) {
  return (
    <div className={"member" + (revealed && m.winner ? " win" : "")}>
      <div className="m-head">
        <span className="m-name">{m.name}</span>
        {revealed && m.winner
          ? <span className="badge success">✓ Lens pick</span>
          : <span className="m-model">{m.model}</span>}
      </div>
      {revealed
        ? <div className="m-body">{m.body}</div>
        : <div className="thinking"><span className="dot" /><span className="dot" /><span className="dot" /> thinking…</div>}
    </div>
  );
}

function CouncilReview({council, onBack}) {
  const running = council.status === 'running';
  const [phase, setPhase] = React.useState(running ? 0 : 3);

  React.useEffect(() => {
    if (!running) { setPhase(3); return; }
    setPhase(0);
    const t1 = setTimeout(() => setPhase(1), 1100);
    const t2 = setTimeout(() => setPhase(2), 2100);
    const t3 = setTimeout(() => setPhase(3), 3200);
    return () => { clearTimeout(t1); clearTimeout(t2); clearTimeout(t3); };
  }, [council.id]);

  const membersRevealed = phase >= 1;
  const verdictReady = phase >= 3;

  return (
    <div>
      <header className="topbar">
        <button className="topbar-back" onClick={onBack}>← Launchpad</button>
        <span className="topbar-title">Council review</span>
        <span className="topbar-spacer" />
        <span className="topbar-action">View full thread ↗</span>
      </header>

      <div className="col">
        <section className="card raised">
          <Eyebrow>Council</Eyebrow>
          <h2 style={{fontSize: 26, marginTop: 12, lineHeight: 1.25}}>{council.task}</h2>

          <div style={{marginTop: 20}}>
            {!verdictReady ? (
              <div className="thinking" style={{fontSize: 14}}>
                <span className="dot" /><span className="dot" /><span className="dot" />
                {phase < 2 ? ' members answering…' : ' chairman synthesizing…'}
              </div>
            ) : (
              <div>
                <div className="label" style={{marginBottom: 10}}>Verdict</div>
                <div className="verdict">
                  <span className="w-label">Winner: <b>Claude</b></span>
                  <span className="badge success">Lens pick</span>
                  <span className="badge info">Confidence: high</span>
                  <span className="badge">Runner-up: Codex</span>
                </div>
              </div>
            )}
          </div>
        </section>

        <section className="card">
          <Eyebrow>Members · answered together</Eyebrow>
          <div className="members" style={{marginTop: 16}}>
            {MEMBERS.map(m => <MemberCard key={m.name} m={m} revealed={membersRevealed} />)}
          </div>
        </section>

        {verdictReady && (
          <section className="card raised">
            <Eyebrow>Chairman synthesis</Eyebrow>
            <h2 style={{marginTop: 12, marginBottom: 4}}>Where they agree, and where they split</h2>

            <div style={{marginTop: 18}}>
              <div className="label" style={{marginBottom: 8}}>Agreed claims</div>
              {CLAIMS_AGREE.map((c, i) => (
                <div className="claim agree" key={i}>
                  <span className="ck">✓</span>
                  <div><div className="c-text">{c}</div></div>
                </div>
              ))}
            </div>

            <div style={{marginTop: 22}}>
              <div className="label" style={{marginBottom: 8}}>Where they split</div>
              <div className="claim split">
                <span className="ck">≠</span>
                <div>
                  <div className="c-text">{CLAIM_SPLIT.claim}</div>
                  <div className="c-providers">
                    {CLAIM_SPLIT.forP.map(p => <span key={p} className="tinychip for">+ {p}</span>)}
                    {CLAIM_SPLIT.againstP.map(p => <span key={p} className="tinychip against">− {p}</span>)}
                  </div>
                  <div className="c-why"><b>Why it matters for you:</b> {CLAIM_SPLIT.why}</div>
                </div>
              </div>
            </div>

            <div className="refine">
              <div className="label" style={{marginBottom: 8}}>What should it have been instead?</div>
              <div className="rwrap">
                <input placeholder="Refine inline — the post-pivot signal trains your lens…" />
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

Object.assign(window, {CouncilReview, MemberCard});
