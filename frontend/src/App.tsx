
import { useEffect, useState, type FormEvent } from 'react';
import './index.css';

type Portal = 'patient' | 'staff' | 'register' | null;

const slides = [
  {
    image: 'https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?auto=format&fit=crop&w=1400&q=85',
    title: 'Care that continues beyond the hospital.',
    text: 'Keep follow-up plans, appointments and helpful next steps connected.',
  },
  {
    image: 'https://images.unsplash.com/photo-1584982751601-97dcc096659c?auto=format&fit=crop&w=1400&q=85',
    title: 'A little support can make the next step easier.',
    text: 'Help patients understand their plan and find the right support.',
  },
  {
    image: 'https://images.unsplash.com/photo-1559839734-2b71ea197ec2?auto=format&fit=crop&w=1400&q=85',
    title: 'People first. Coordination made clearer.',
    text: 'Give care teams a shared view of follow-ups and unresolved tasks.',
  },
  {
    image: 'https://images.unsplash.com/photo-1579684385127-1ef15d508118?auto=format&fit=crop&w=1400&q=85',
    title: 'Make every follow-up easier to navigate.',
    text: 'Bring care instructions, communication and coordination into one place.',
  },
  {
    image: 'https://images.unsplash.com/photo-1573497620053-ea5300f94f21?auto=format&fit=crop&w=1400&q=85',
    title: 'Support that meets people where they are.',
    text: 'Help coordinate appointments, accessibility needs and practical barriers.',
  },
];

type ApiUser = {
  type: 'patient' | 'staff';
  id: string;
  name: string;
  role?: string;
  dept?: string;
  lang?: string;
};

type PatientRecord = { id: string; name: string; age?: number; lang?: string };
type FollowUpRecord = {
  id: string;
  pid: string;
  title: string;
  dept?: string;
  due: string;
  time?: string;
  status: string;
  confirmed?: boolean;
  risk?: { level?: string; score?: number };
};
type DashboardData = {
  patients?: PatientRecord[];
  followups?: FollowUpRecord[];
  tasks?: unknown[];
  rx?: unknown[];
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/+$/, '');
const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true';
const DEMO_PATIENT_PHONE = '9000000001';
const DEMO_PATIENT_OTP = '123456';
const DEMO_STAFF_PASSWORD = 'Demo@2026';

const DEMO_STAFF_ACCOUNTS: Record<string, { role: string; name: string; dept: string }> = {
  ADM001: { role: 'admin', name: 'Demo Administrator', dept: 'Hospital Administration' },
  DOC101: { role: 'doctor', name: 'Dr. Demo', dept: 'General Medicine' },
  CLN401: { role: 'clinician', name: 'Demo Clinician', dept: 'Care Coordination' },
  NUR201: { role: 'nurse', name: 'Demo Nurse', dept: 'Nursing' },
  WB301: { role: 'ward', name: 'Demo Ward Assistant', dept: 'Patient Support' },
  PH501: { role: 'pharmacist', name: 'Demo Pharmacist', dept: 'Pharmacy' },
};

// Synthetic records only. This local presentation mode does not contact a database
// and must never be used for real patient information or clinical operations.
const DEMO_DASHBOARD: DashboardData = {
  patients: [
    { id: 'demo-p001', name: 'Sample Patient 01', age: 34, lang: 'en' },
    { id: 'demo-p002', name: 'Sample Patient 02', age: 52, lang: 'hi' },
    { id: 'demo-p003', name: 'Sample Patient 03', age: 46, lang: 'en' },
    { id: 'demo-p004', name: 'Sample Patient 04', age: 61, lang: 'pa' },
  ],
  followups: [
    { id: 'DEMO-FU-1001', pid: 'demo-p001', title: 'General Medicine Review', dept: 'General Medicine', due: '2026-11-03', time: '10:30', status: 'pending', confirmed: true, risk: { level: 'high', score: 78 } },
    { id: 'DEMO-FU-1002', pid: 'demo-p002', title: 'Diabetes Clinic Follow-up', dept: 'Medicine', due: '2026-11-04', time: '11:00', status: 'pending', confirmed: false, risk: { level: 'medium', score: 54 } },
    { id: 'DEMO-FU-1003', pid: 'demo-p003', title: 'Post-discharge Review', dept: 'General Medicine', due: '2026-10-27', time: '09:45', status: 'completed', confirmed: true, risk: { level: 'low', score: 12 } },
    { id: 'DEMO-FU-1004', pid: 'demo-p004', title: 'Cardiology Follow-up', dept: 'Cardiology', due: '2026-11-06', time: '12:15', status: 'pending', confirmed: false, risk: { level: 'high', score: 82 } },
  ],
  tasks: [
    { id: 'DEMO-T-01', type: 'Wheelchair assistance', status: 'pending' },
    { id: 'DEMO-T-02', type: 'Language support', status: 'in progress' },
  ],
  rx: [
    { id: 'DEMO-RX-01', status: 'pending' },
    { id: 'DEMO-RX-02', status: 'dispensed' },
  ],
};

const ROLE_TITLES: Record<string, string> = {
  admin: 'Hospital administration dashboard',
  doctor: 'Doctor workspace',
  clinician: 'Clinical coordination workspace',
  nurse: 'Nursing workspace',
  ward: 'Ward assistance workspace',
  pharmacist: 'Pharmacy workspace',
};

async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (!API_BASE && !import.meta.env.DEV) {
    throw new Error('Backend URL is not configured. Set VITE_API_BASE_URL in the frontend deployment.');
  }

  const method = (options.method ?? 'GET').toUpperCase();
  const headers = new Headers(options.headers ?? {});
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    headers.set('X-Requested-With', 'AARAMBH');
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    method,
    headers,
    credentials: 'include',
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof payload.error === 'string' ? payload.error : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return payload as T;
}

function normalisePhone(value: string): string {
  const digits = value.replace(/\D/g, '');
  return digits.length === 12 && digits.startsWith('91') ? digits.slice(2) : digits;
}

function Brand() {
  return (
    <a href="#home" className="brand">
      <span className="brand-mark" aria-hidden="true">✚</span>
      <span>
        <b>AARAMBH</b>
        <small>FOLLOW-UP NAVIGATOR</small>
      </span>
    </a>
  );
}

function App() {
  const [slide, setSlide] = useState(0);
  const [paused, setPaused] = useState(false);
  const [portal, setPortal] = useState<Portal>(null);
  const [lang, setLang] = useState('English');
  const [showPassword, setShowPassword] = useState(false);
  const [otpSent, setOtpSent] = useState(false);
  const [notice, setNotice] = useState('');
  const [loggedIn, setLoggedIn] = useState(false);
  const [mobile, setMobile] = useState('');
  const [staffId, setStaffId] = useState('');
  const [staffRole, setStaffRole] = useState('doctor');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [user, setUser] = useState<ApiUser | null>(null);
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);

  useEffect(() => {
    if (paused || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const timer = window.setInterval(() => {
      setSlide((current) => (current + 1) % slides.length);
    }, 5000);

    return () => window.clearInterval(timer);
  }, [paused]);

  useEffect(() => {
    if (DEMO_MODE) return;
    let mounted = true;
    async function restoreSession() {
      try {
        const session = await apiRequest<{ user: ApiUser | null }>('/api/session');
        if (!session.user || !mounted) return;
        const data = await apiRequest<DashboardData>('/api/bootstrap');
        if (mounted) {
          setUser(session.user);
          setDashboard(data);
          setLoggedIn(true);
        }
      } catch {
        // The landing page remains available when the API is not configured.
      }
    }
    void restoreSession();
    return () => { mounted = false; };
  }, []);

  function openPortal(value: Portal) {
    setPortal(value);
    setNotice('');
    setOtpSent(false);
  }

  function activateDemoSession(nextUser: ApiUser) {
    const data: DashboardData = nextUser.type === 'patient'
      ? {
          patients: DEMO_DASHBOARD.patients?.filter((patient) => patient.id === 'demo-p001'),
          followups: DEMO_DASHBOARD.followups?.filter((item) => item.pid === 'demo-p001'),
          tasks: [],
          rx: [],
        }
      : DEMO_DASHBOARD;
    setUser(nextUser);
    setDashboard(data);
    setLoggedIn(true);
    setPortal(null);
    setNotice('');
    setQuery('');
  }

  async function completeLogin(nextUser: ApiUser) {
    if (DEMO_MODE) {
      activateDemoSession(nextUser);
      return;
    }
    const data = await apiRequest<DashboardData>('/api/bootstrap');
    setUser(nextUser);
    setDashboard(data);
    setLoggedIn(true);
    setPortal(null);
    setNotice('');
    setQuery('');
  }

  async function submitOtp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (portal === 'register') {
      setNotice('New patient registration must be completed by authorised hospital staff. Please contact the hospital.');
      return;
    }
    const phone = normalisePhone(mobile);
    if (!/^[6-9]\d{9}$/.test(phone)) {
      setNotice('Enter a valid 10-digit Indian mobile number.');
      return;
    }
    setBusy(true);
    setNotice('');
    try {
      if (DEMO_MODE) {
        if (phone !== DEMO_PATIENT_PHONE) {
          setNotice(`For this presentation demo, use the sample mobile number ${DEMO_PATIENT_PHONE}.`);
          return;
        }
        setMobile(phone);
        setOtpSent(true);
        setNotice(`Presentation demo OTP: ${DEMO_PATIENT_OTP}. Synthetic sample account only.`);
        return;
      }
      await apiRequest<{ ok: boolean }>('/api/auth/patient/request-otp', {
        method: 'POST',
        body: JSON.stringify({ phone }),
      });
      setMobile(phone);
      setOtpSent(true);
      setNotice('If this number is registered and SMS delivery is configured, an OTP will arrive shortly.');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Could not request an OTP. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  async function verifyOtp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const otp = String(form.get('otp') ?? '').trim();
    const phone = normalisePhone(mobile);
    if (!/^\d{6}$/.test(otp)) {
      setNotice('Enter the 6-digit OTP.');
      return;
    }
    setBusy(true);
    setNotice('');
    try {
      if (DEMO_MODE) {
        if (phone !== DEMO_PATIENT_PHONE || otp !== DEMO_PATIENT_OTP) {
          throw new Error('Use the sample mobile number and presentation OTP shown on this screen.');
        }
        activateDemoSession({ type: 'patient', id: 'demo-p001', name: 'Sample Patient 01', lang: 'en' });
        return;
      }
      const result = await apiRequest<{ ok: boolean; user: ApiUser }>('/api/auth/patient/verify', {
        method: 'POST',
        body: JSON.stringify({ phone, otp }),
      });
      await completeLogin(result.user);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'OTP verification failed.');
    } finally {
      setBusy(false);
    }
  }

  async function submitStaff(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get('pw') ?? '');
    if (!staffId.trim() || !password) {
      setNotice('Enter your staff ID and password.');
      return;
    }
    setBusy(true);
    setNotice('');
    try {
      if (DEMO_MODE) {
        const accountId = staffId.trim().toUpperCase();
        const account = DEMO_STAFF_ACCOUNTS[accountId];
        if (!account || account.role !== staffRole || password !== DEMO_STAFF_PASSWORD) {
          throw new Error('Demo sign-in failed. Check the staff ID, selected role and presentation password shown below.');
        }
        activateDemoSession({ type: 'staff', id: accountId, name: account.name, role: account.role, dept: account.dept });
        return;
      }
      const result = await apiRequest<{ ok: boolean; user: ApiUser }>('/api/auth/staff/login', {
        method: 'POST',
        body: JSON.stringify({ id: staffId.trim(), pw: password, role: staffRole }),
      });
      await completeLogin(result.user);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Staff sign-in failed.');
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    try {
      if (!DEMO_MODE) {
        await apiRequest('/api/auth/logout', { method: 'POST', body: JSON.stringify({}) });
      }
    } catch {
      // Clear the local view even if the network is unavailable.
    }
    setLoggedIn(false);
    setUser(null);
    setDashboard(null);
    setPortal(null);
  }

  const followups = dashboard?.followups ?? [];
  const patients = dashboard?.patients ?? [];
  const patientById = new Map(patients.map((patient) => [patient.id, patient] as const));
  const visibleFollowups = followups.filter((item) => {
    const patientName = patientById.get(item.pid)?.name ?? user?.name ?? '';
    return `${patientName} ${item.id} ${item.title}`.toLowerCase().includes(query.toLowerCase());
  });
  const pendingCount = followups.filter((item) => item.status === 'pending').length;
  const completedCount = followups.filter((item) => item.status === 'completed').length;
  const highRiskCount = followups.filter((item) => item.risk?.level === 'high').length;

  if (loggedIn) {
    return (
      <div className="demo-dashboard">
        {DEMO_MODE && (
          <div className="demo-mode-banner">
            PRESENTATION DEMO · Synthetic sample records only · No live database or hospital data is connected
          </div>
        )}
        <header className="dash-top">
          <Brand />
          <span className="demo-tag">{user?.type === 'patient' ? 'PATIENT PORTAL' : (user?.role ?? 'STAFF').toUpperCase()}</span>
          <button className="btn btn-light" onClick={() => void signOut()}>
            Sign out
          </button>
        </header>

        <main className="dash-content">
          <div className="eyebrow">CARE COORDINATION WORKSPACE</div>
          <h1>{user?.type === 'patient' ? `Welcome, ${user.name}` : (ROLE_TITLES[user?.role ?? ''] ?? 'Care team dashboard')}</h1>
          <p className="subhead">
            {user?.type === 'patient' ? 'Your sample follow-up plan and next steps.' : DEMO_MODE ? `Signed in as ${user?.name ?? 'staff'}${user?.dept ? ` · ${user.dept}` : ''}. Explore this role-based presentation workspace.` : `Signed in as ${user?.name ?? 'staff'}${user?.dept ? ` · ${user.dept}` : ''}. Follow-up records are loaded from the secure backend.`}
          </p>

          <div className="stats">
            <article className="stat-card"><span>Patients in view</span><strong>{patients.length}</strong><small>{DEMO_MODE ? 'Synthetic sample records' : 'Authorised records'}</small></article>
            <article className="stat-card"><span>Pending follow-ups</span><strong>{pendingCount}</strong><small>Require coordination</small></article>
            <article className="stat-card"><span>Completed follow-ups</span><strong>{completedCount}</strong><small>Recorded outcomes</small></article>
            <article className="stat-card"><span>High coordination risk</span><strong>{highRiskCount}</strong><small>Staff review required</small></article>
          </div>

          <section className="case-card">
            <div className="case-head">
              <div>
                <h2>Follow-up case registry</h2>
                <p>{DEMO_MODE ? 'Synthetic sample cases for presentation. No real patient data.' : 'Records returned for the authenticated account.'}</p>
              </div>
              <input
                placeholder="Search cases..."
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Patient / Case ID</th>
                    <th>Follow-up</th>
                    <th>Due</th>
                    <th>Status</th>
                    <th>Priority</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleFollowups.map((item) => {
                    const patientName = patientById.get(item.pid)?.name ?? user?.name ?? 'Patient';
                    const riskLevel = item.risk?.level ?? '—';
                    return (
                      <tr key={item.id}>
                        <td><b>{patientName}</b><small>{item.id}</small></td>
                        <td>{item.title}</td>
                        <td>{item.due}{item.time ? ` · ${item.time}` : ''}</td>
                        <td><span className="status">{item.status}</span></td>
                        <td><span className={`risk ${riskLevel.toLowerCase()}`}>{riskLevel}</span></td>
                      </tr>
                    );
                  })}
                  {visibleFollowups.length === 0 && (
                    <tr><td colSpan={5}>No follow-up records are available for this account.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <p className="safety-note">
            {DEMO_MODE ? 'Presentation-only workspace using synthetic records. Not connected to a hospital system and not for clinical decisions.' : 'Secure authenticated workspace · Risk flags are administrative coordination signals, not medical diagnoses.'}
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className="site" id="home">
      <div className="announcement">
        <span className="announcement-dot" />
        {DEMO_MODE ? 'Presentation preview · Synthetic sample data' : 'Connected, compassionate healthcare follow-up'}
        <span className="announcement-pill">{DEMO_MODE ? 'DEMO MODE' : 'HACKATHON DEMO 2026'}</span>
      </div>

      <header className="site-header">
        <Brand />

        <nav className="desktop-nav">
          <a href="#how">How it works</a>
          <a href="#portals">Portals</a>
          <a href="#support">Help &amp; support</a>
        </nav>

        <div className="header-actions">
          <label className="lang">
            <span className="sr-only">Language</span>
            <select value={lang} onChange={(event) => setLang(event.target.value)}>
              <option>English</option>
              <option>हिंदी</option>
            </select>
          </label>

          <button className="btn btn-outline" onClick={() => openPortal('staff')}>
            Hospital login ↗
          </button>
          <button className="btn btn-primary" onClick={() => openPortal('patient')}>
            Patient portal →
          </button>
        </div>
      </header>

      <main>
        <section className="hero">
          <div className="hero-copy">
            <div className="eyebrow">A MORE HUMAN FOLLOW-UP JOURNEY</div>
            <h1>Care that fits <em>real life.</em></h1>
            <p className="hero-description">
              AARAMBH helps patients and hospitals bridge the follow-up gap
              with clearer instructions, personalized support and transparent
              coordination.
            </p>

            <div className="hero-actions">
              <button className="btn btn-primary btn-large" onClick={() => openPortal('patient')}>
                Open patient portal →
              </button>
              <button className="btn btn-outline btn-large" onClick={() => openPortal('staff')}>
                Hospital administration ↗
              </button>
            </div>

            <div className="trust-line">✓ Designed around privacy, clarity and human care</div>

            <div className="hero-points">
              <div><span>01</span>Track follow-ups</div>
              <div><span>02</span>Understand next steps</div>
              <div><span>03</span>Coordinate support</div>
            </div>
          </div>

          <div className="hero-visual">
            <div className="photo-frame">
              <img
                src={slides[slide].image}
                alt="Healthcare consultation"
                key={slides[slide].image}
              />
              <div className="photo-wash" />
              <div className="photo-label">✳ CARE, CONNECTED</div>

              <div className="photo-caption">
                <span className="caption-number">0{slide + 1} / 05</span>
                <h2>{slides[slide].title}</h2>
                <p>{slides[slide].text}</p>
              </div>

              <div className="slide-controls">
                <button aria-label="Previous slide" onClick={() => setSlide((s) => (s + slides.length - 1) % slides.length)}>←</button>
                <button aria-label={paused ? 'Resume slideshow' : 'Pause slideshow'} onClick={() => setPaused((p) => !p)}>{paused ? '▶' : 'Ⅱ'}</button>
                <button aria-label="Next slide" onClick={() => setSlide((s) => (s + 1) % slides.length)}>→</button>
              </div>
            </div>
            <div className="floating-note">
              <b>One connected journey</b>
              <small>People, plans and next steps</small>
            </div>
          </div>
        </section>

        <section className="photo-strip">
          <div className="strip-intro">
            <span>CARE IN EVERY STEP</span>
            <b>Support that moves with you.</b>
          </div>
          <div className="strip-track">
            {slides.map((item, index) => (
              <button
                className={`strip-item ${slide === index ? 'selected' : ''}`}
                key={item.title}
                onClick={() => setSlide(index)}
              >
                <img src={item.image} alt="" />
                <span>0{index + 1}</span>
                <b>{['Doctor consultation', 'Follow-up visit', 'Care team coordination', 'Care plan review', 'Practical support'][index]}</b>
              </button>
            ))}
          </div>
        </section>

        <section className="section portals" id="portals">
          <div className="section-heading">
            <div>
              <div className="eyebrow">YOUR JOURNEY, YOUR WAY</div>
              <h2>One platform. Two connected experiences.</h2>
            </div>
            <p>Start in the space designed for your role.</p>
          </div>

          <div className="portal-grid">
            <article className="portal-card patient-card">
              <span className="portal-kicker">FOR PATIENTS &amp; FAMILIES</span>
              <h3>Your care, made easier to follow.</h3>
              <p>Appointments, follow-up instructions and support in one place.</p>
              <ul>
                <li>Upcoming follow-ups and reminders</li>
                <li>Understand your next steps</li>
                <li>Ask for coordination help</li>
              </ul>
              <button className="btn btn-primary" onClick={() => openPortal('patient')}>
                Continue as patient →
              </button>
              <small>Mobile number and OTP sign-in</small>
              <button className="text-button" onClick={() => openPortal('register')}>
                New patient? Register here
              </button>
            </article>

            <article className="portal-card hospital-card">
              <span className="portal-kicker">FOR AUTHORIZED HOSPITAL STAFF</span>
              <h3>Keep every follow-up in view.</h3>
              <p>Manage cases, validate records and coordinate support.</p>
              <ul>
                <li>Follow-up case registry</li>
                <li>Record validation and task tracking</li>
                <li>Role-based staff workspace</li>
              </ul>
              <button className="btn btn-dark" onClick={() => openPortal('staff')}>
                Access hospital portal ↗
              </button>
              <small>Staff ID and password</small>
            </article>
          </div>
        </section>

        <section className="section how" id="how">
          <div className="eyebrow">SIMPLE BY DESIGN</div>
          <h2>From a care plan to the next right step.</h2>
          <div className="steps">
            {[
              ['01', 'Record', 'Capture the clinician-provided follow-up plan.'],
              ['02', 'Validate', 'Identify missing or conflicting information.'],
              ['03', 'Flag', 'Highlight possible coordination delays for review.'],
              ['04', 'Coordinate', 'Help staff arrange practical support.'],
              ['05', 'Close the loop', 'Record verified follow-up outcomes.'],
            ].map(([number, title, description]) => (
              <article className="step" key={number}>
                <span>{number}</span>
                <div><h3>{title}</h3><p>{description}</p></div>
              </article>
            ))}
          </div>
          <p className="human-note">
            Technology supports care teams. It does not replace clinical judgment.
          </p>
        </section>

        <section className="support-band" id="support">
          <div>
            <div className="eyebrow">HERE FOR EVERY STEP</div>
            <h2>Not sure where to begin?</h2>
            <p>Choose your portal to find the right next step.</p>
          </div>
          <div className="support-actions">
            <button className="btn btn-white" onClick={() => openPortal('patient')}>I’m a patient →</button>
            <button className="btn btn-outline-white" onClick={() => openPortal('staff')}>I’m hospital staff ↗</button>
          </div>
        </section>
      </main>

      <footer className="footer">
        <Brand />
        <p>© 2026 AARAMBH · AI Synergy Hackathon</p>
        <small>Follow-up coordination support · Risk flags do not replace clinical judgement</small>
      </footer>

      {portal && (
        <div className="modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setPortal(null);
        }}>
          <section className="auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-title">
            <button className="modal-close" onClick={() => setPortal(null)} aria-label="Close">×</button>
            <Brand />

            <div className="auth-eyebrow">
              {portal === 'staff' ? 'AUTHORIZED STAFF ACCESS' : portal === 'register' ? 'PATIENT REGISTRATION' : 'PATIENT SECURE ACCESS'}
            </div>

            <h2 id="auth-title">
              {portal === 'staff' ? 'Welcome back, care team.' : portal === 'register' ? 'Let’s get you started.' : 'Your care journey, in reach.'}
            </h2>

            <p className="auth-intro">
              {portal === 'staff'
                ? 'Sign in using your unique staff ID.'
                : portal === 'register'
                  ? 'Create a patient profile after mobile verification.'
                  : 'Enter your mobile number to request a one-time password.'}
            </p>

            {portal === 'staff' ? (
              <form onSubmit={submitStaff}>
                <label className="field">
                  Staff ID
                  <input value={staffId} onChange={(event) => setStaffId(event.target.value)} placeholder="Enter your staff ID" required autoComplete="username" />
                </label>
                <label className="field">
                  Staff role
                  <select value={staffRole} onChange={(event) => setStaffRole(event.target.value)}>
                    <option value="doctor">Doctor</option>
                    <option value="clinician">Clinician</option>
                    <option value="nurse">Nurse</option>
                    <option value="ward">Ward / Patient assistant</option>
                    <option value="pharmacist">Pharmacist</option>
                    <option value="admin">Administrator</option>
                  </select>
                </label>
                <label className="field">
                  Password
                  <div className="password-wrap">
                    <input name="pw" type={showPassword ? 'text' : 'password'} placeholder="Enter password" required autoComplete="current-password" />
                    <button type="button" onClick={() => setShowPassword((value) => !value)}>{showPassword ? 'Hide' : 'Show'}</button>
                  </div>
                </label>
                <button type="button" className="text-button" onClick={() => setNotice('Account recovery will be available after secure authentication is configured.')}>
                  Forgot password?
                </button>
                <button className="btn btn-dark auth-submit" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in securely →'}</button>
                {DEMO_MODE ? (
                  <p className="auth-hint demo-credentials">
                    <b>Presentation demo password:</b> {DEMO_STAFF_PASSWORD}<br />
                    Admin: ADM001 · Doctor: DOC101 · Clinician: CLN401<br />
                    Nurse: NUR201 · Ward: WB301 · Pharmacist: PH501<br />
                    Select the matching role above. Synthetic demo access only.
                  </p>
                ) : (
                  <p className="auth-hint">Staff accounts must be created and authorized by the hospital administrator.</p>
                )}
              </form>
            ) : portal === 'register' ? (
              <div>
                <p className="auth-hint">Self-registration is not enabled in this release. Patient records must be created and verified by authorised hospital staff.</p>
                <button type="button" className="btn btn-primary auth-submit" onClick={() => openPortal('patient')}>
                  Return to patient sign in
                </button>
              </div>
            ) : (
              <form onSubmit={otpSent ? verifyOtp : submitOtp}>
                <label className="field">
                  Mobile number
                  <input value={mobile} onChange={(event) => setMobile(event.target.value)} placeholder="+91 98765 43210" required inputMode="tel" autoComplete="tel" />
                </label>
                {otpSent && (
                  <label className="field">
                    One-time password (OTP)
                    <input name="otp" placeholder="Enter 6-digit OTP" inputMode="numeric" autoComplete="one-time-code" minLength={6} maxLength={6} pattern="[0-9]{6}" required />
                  </label>
                )}
                <button className="btn btn-primary auth-submit" type="submit" disabled={busy}>
                  {busy ? 'Please wait…' : otpSent ? 'Verify OTP →' : 'Send OTP →'}
                </button>
                <button type="button" className="text-button centered" onClick={() => openPortal('register')}>
                  New patient? Register here
                </button>
                {DEMO_MODE && (
                  <p className="auth-hint demo-credentials">
                    <b>Presentation patient:</b> {DEMO_PATIENT_PHONE}<br />
                    <b>Demo OTP:</b> {DEMO_PATIENT_OTP}<br />
                    Synthetic sample account only.
                  </p>
                )}
              </form>
            )}

            {notice && <div className="auth-notice" role="status">{notice}</div>}

            <div className="demo-disclosure">
              {DEMO_MODE ? (
                <><b>Presentation mode:</b> This frontend uses hard-coded synthetic demo identities and sample records only. It does not call a backend, database, Supabase or SMS provider. Do not enter real patient information.</>
              ) : (
                <><b>Secure access:</b> Sign-in is verified by the AARAMBH backend. Patient OTP delivery requires Twilio configuration; staff accounts must first be created by an administrator.</>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export default App;
