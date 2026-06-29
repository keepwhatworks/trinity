/* Trinity Local — Launchpad app shell. Routes between the launchpad surface and a
   council review page; "Launch Council" creates a running council in the rail. */

const ACCENTS = [
  {id: 'teal',  name: 'Muted teal', dot: '#4f9095', vars: {green: '#4f9095', deep: '#34666b', fill: '#3f777c', soft: '#e1edec'}},
  {id: 'slate', name: 'Slate blue', dot: '#5b7fa6', vars: {green: '#5b7fa6', deep: '#45628a', fill: '#5878a0', soft: '#e5ebf3'}},
  {id: 'mauve', name: 'Dusty mauve', dot: '#9a7089', vars: {green: '#9a7089', deep: '#7d566f', fill: '#94697f', soft: '#efe7ec'}},
  {id: 'sage',  name: 'Sage', dot: '#5f8e72', vars: {green: '#5f8e72', deep: '#4a7259', fill: '#5a8a6d', soft: '#e3ede5'}},
];

function applyAccent(a) {
  const r = document.documentElement.style;
  r.setProperty('--green', a.vars.green);
  r.setProperty('--green-deep', a.vars.deep);
  r.setProperty('--green-fill', a.vars.fill);
  r.setProperty('--green-fill-h', a.vars.deep);
  r.setProperty('--green-soft', a.vars.soft);
  r.setProperty('--success', a.vars.green);
}

function AccentSwitcher({accent, onPick}) {
  return (
    <div style={{position: 'fixed', bottom: 22, right: 22, zIndex: 60,
      background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 18,
      padding: '12px 14px', boxShadow: 'var(--shadow)'}}>
      <div style={{fontSize: 11, fontWeight: 600, letterSpacing: '.12em', textTransform: 'uppercase',
        color: 'var(--muted)', marginBottom: 9}}>Try an accent</div>
      <div style={{display: 'flex', gap: 8}}>
        {ACCENTS.map(a => (
          <button key={a.id} onClick={() => onPick(a)} title={a.name}
            style={{width: 26, height: 26, borderRadius: '50%', cursor: 'pointer',
              background: a.dot, border: accent === a.id ? '2px solid var(--ink)' : '2px solid transparent',
              outline: accent === a.id ? '1px solid var(--line)' : 'none', outlineOffset: 2, transition: '.15s'}} />
        ))}
      </div>
    </div>
  );
}

function App() {
  const [view, setView] = React.useState('launchpad'); // 'launchpad' | 'council'
  const [task, setTask] = React.useState('Should we use SQLite or DuckDB for this analytics workload?');
  const [councils, setCouncils] = React.useState([]);
  const [activeId, setActiveId] = React.useState(null);
  const [animateBars, setAnimateBars] = React.useState(false);
  const [toast, setToast] = React.useState(null);
  const [accent, setAccent] = React.useState(() => localStorage.getItem('trinity-accent') || 'teal');

  React.useEffect(() => {
    const a = ACCENTS.find(x => x.id === accent) || ACCENTS[0];
    applyAccent(a);
    localStorage.setItem('trinity-accent', accent);
  }, [accent]);

  React.useEffect(() => {
    const t = setTimeout(() => setAnimateBars(true), 350);
    return () => clearTimeout(t);
  }, []);

  function launch() {
    const t = task.trim();
    if (!t) return;
    const id = 'c' + Date.now();
    const council = {id, task: t, status: 'running', winner: 'claude', when: 'just now'};
    setCouncils(cs => [council, ...cs]);
    setActiveId(id);
    setView('council');
    // resolve to "done" after the streamed synthesis completes
    setTimeout(() => {
      setCouncils(cs => cs.map(c => c.id === id ? {...c, status: 'done'} : c));
    }, 3400);
  }

  function openCouncil(id) {
    setActiveId(id);
    setView('council');
  }

  function newCouncil() {
    setActiveId(null);
    setView('launchpad');
    setTimeout(() => {
      const el = document.querySelector('.composer');
      if (el) el.focus();
    }, 60);
  }

  function copyLens(title) {
    setToast('Copied principle: “' + title + '”');
    setTimeout(() => setToast(null), 1800);
  }

  const active = councils.find(c => c.id === activeId);

  return (
    <div className="app">
      <Sidebar
        councils={councils}
        activeId={activeId}
        onNew={newCouncil}
        onOpen={openCouncil}
      />
      <div className="content">
        {view === 'launchpad' && (
          <React.Fragment>
            <StatusBar />
            <div className="col">
              <Hero task={task} setTask={setTask} onLaunch={launch} />
              <InfoCard />
              <RoutingCard animate={animateBars} />
              <LensCards onCopy={copyLens} />
              <NewModelCard />
            </div>
          </React.Fragment>
        )}
        {view === 'council' && active && (
          <CouncilReview council={active} onBack={() => setView('launchpad')} />
        )}
      </div>

      {toast && (
        <div style={{
          position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)',
          background: 'var(--green-fill)', color: '#fbfdfa', padding: '12px 22px',
          borderRadius: 999, fontSize: 14, fontWeight: 600,
          boxShadow: '0 10px 30px rgba(60,72,86,.3)', zIndex: 50
        }}>✓ {toast}</div>
      )}

      <AccentSwitcher accent={accent} onPick={a => setAccent(a.id)} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
