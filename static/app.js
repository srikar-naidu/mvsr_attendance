// ==========================================================================
// Bunk Meter — app logic
// ==========================================================================

const TARGET_PCT = 75;

const THEMES = ['light', 'dark', 'glass'];

const state = {
  captchaRequired: false,
  data: null,       // full payload from /api/attendance
  activeTab: 'dashboard',
  activeDayIndex: 0,
};

// -------------------------------------------------------------------------
// Theme
// -------------------------------------------------------------------------

function applyTheme(pref) {
  document.documentElement.setAttribute('data-theme', pref);
}

function initTheme() {
  const savedTheme = localStorage.getItem('bunkmeter-theme') || 'dark';
  applyTheme(THEMES.includes(savedTheme) ? savedTheme : 'dark');
}

const THEME_LABELS = { light: 'Light', dark: 'Dark', glass: 'Glass' };

function setTheme(pref) {
  localStorage.setItem('bunkmeter-theme', pref);
  applyTheme(pref);
  renderThemePicker();
  showToast('Theme set to ' + THEME_LABELS[pref], 'info', '\ud83c\udfa8');
}

// -------------------------------------------------------------------------
// Toasts
// -------------------------------------------------------------------------

function showToast(message, type, icon) {
  const stack = document.getElementById('toast-stack');
  if (!stack) return;
  const toast = document.createElement('div');
  toast.className = 'toast' + (type ? ' ' + type : '');
  toast.innerHTML = '<span>' + (icon || '\u2728') + '</span><span>' + message + '</span>';
  stack.appendChild(toast);
  setTimeout(function () {
    toast.classList.add('leaving');
    setTimeout(function () { toast.remove(); }, 280);
  }, 2400);
}

// -------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------

function alertLevel(pct) {
  if (pct < 75) return 'danger';
  if (pct < 80) return 'warn';
  return 'safe';
}

function alertColorVar(level) {
  return `var(--${level})`;
}

function fmtPct(n) {
  return (Math.round(n * 10) / 10).toString();
}

function monthKeyFromIso(dateIso) {
  return dateIso.slice(0, 7);
}

function monthLabel(key) {
  const [y, m] = key.split('-');
  return new Date(Number(y), Number(m) - 1, 1).toLocaleString('default', { month: 'short' });
}

function isoWeekKey(dateIso) {
  const d = new Date(dateIso + 'T00:00:00');
  const day = (d.getDay() + 6) % 7; // Mon=0
  d.setDate(d.getDate() - day + 3);
  const firstThursday = new Date(d.getFullYear(), 0, 4);
  const week = 1 + Math.round(((d - firstThursday) / 86400000 - 3 + ((firstThursday.getDay() + 6) % 7)) / 7);
  return `${d.getFullYear()}-W${String(week).padStart(2, '0')}`;
}

// -------------------------------------------------------------------------
// Calendar analytics (all computed client-side from real scraped data)
// -------------------------------------------------------------------------

// Portal's day-by-day table lists most-recent-first.
function sortedCalendar(calendar) {
  return [...calendar].sort((a, b) => (a.date < b.date ? 1 : -1));
}

function computeStreak(calendar) {
  const days = sortedCalendar(calendar);
  let count = 0;
  for (const d of days) {
    if (d.summary === 'no_classes') continue;
    if (d.summary === 'present') { count++; continue; }
    break; // hit an absent day
  }
  return count;
}

function computeGrouped(calendar, keyFn, labelFn, limit) {
  const groups = new Map();
  for (const d of calendar) {
    if (d.summary === 'no_classes') continue;
    const key = keyFn(d.date);
    if (!groups.has(key)) groups.set(key, { present: 0, total: 0 });
    const g = groups.get(key);
    g.total++;
    if (d.summary === 'present') g.present++;
  }
  const keys = [...groups.keys()].sort();
  const recent = keys.slice(-limit);
  return recent.map(key => {
    const g = groups.get(key);
    return {
      key,
      label: labelFn(key),
      pct: g.total ? Math.round((g.present / g.total) * 100) : 0,
      present: g.present,
      total: g.total,
    };
  });
}

function computeWeekly(calendar) {
  return computeGrouped(calendar, isoWeekKey, (k) => 'W' + k.split('-W')[1], 8);
}

function computeMonthly(calendar) {
  return computeGrouped(calendar, monthKeyFromIso, monthLabel, 6);
}

function computePresentVsAbsent(calendar) {
  let present = 0, absent = 0, noClasses = 0;
  calendar.forEach(d => {
    if (d.summary === 'present') present++;
    else if (d.summary === 'absent') absent++;
    else noClasses++;
  });
  return { present, absent, noClasses };
}

// The portal's own colspans for a day don't always sum to the declared 84
// units (7 slots x 12) -- a quirk in the source system, not our parsing.
// We proportionally rescale so slot boundaries stay meaningful either way.
const SLOT_LABELS = [
  '9:30 - 10:30', '10:30 - 11:30', '11:30 - 12:30',
  '12:30 - 13:30', '13:30 - 14:30', '14:30 - 15:30', '15:30 - 16:15',
];
const SLOT_UNITS_TOTAL = 84;
const SLOT_UNIT_SIZE = 12;

function mapDayToSlots(day) {
  const totalWidth = day.segments.reduce((sum, s) => sum + s.width, 0) || 1;
  const scale = SLOT_UNITS_TOTAL / totalWidth;

  // Flatten into 84 virtual units, then bucket into 7 slots of 12.
  const units = [];
  day.segments.forEach(seg => {
    const scaledWidth = Math.round(seg.width * scale);
    for (let i = 0; i < scaledWidth; i++) units.push(seg.status);
  });
  while (units.length < SLOT_UNITS_TOTAL) units.push('-');

  const slots = [];
  for (let i = 0; i < 7; i++) {
    const chunk = units.slice(i * SLOT_UNIT_SIZE, (i + 1) * SLOT_UNIT_SIZE);
    const counts = { P: 0, A: 0, '-': 0 };
    chunk.forEach(s => { counts[s] = (counts[s] || 0) + 1; });
    let status = '-';
    if (counts.P >= counts.A && counts.P > counts['-']) status = 'P';
    else if (counts.A > counts.P && counts.A > counts['-']) status = 'A';
    slots.push({ label: SLOT_LABELS[i], status });
  }
  return slots;
}

// -------------------------------------------------------------------------
// Login flow
// -------------------------------------------------------------------------

const el = (id) => document.getElementById(id);

async function initLogin() {
  const loginError = el('login-error');
  loginError.textContent = '';
  try {
    const res = await fetch('/api/login/start');
    const data = await res.json();
    if (!res.ok) {
      loginError.textContent = data.error || 'Could not reach the portal.';
      return;
    }
    state.captchaRequired = !!data.captcha_required;
    const captchaRow = el('captcha-row');
    const captchaImg = el('captcha-img');
    const captchaInput = el('captcha');
    if (state.captchaRequired) {
      captchaRow.style.display = 'block';
      captchaImg.src = data.captcha_url + '?t=' + Date.now();
      captchaInput.value = '';
    } else {
      captchaRow.style.display = 'none';
      captchaInput.value = '';
    }
  } catch (e) {
    loginError.textContent = 'Could not reach the server.';
  }
}

async function handleLogin() {
  const username = el('username').value.trim();
  const password = el('password').value;
  const captcha = el('captcha').value.trim();
  const loginError = el('login-error');
  const loginBtn = el('login-btn');
  loginError.textContent = '';

  if (!username || !password) {
    loginError.textContent = 'Please enter both fields.';
    return;
  }
  if (state.captchaRequired && !captcha) {
    loginError.textContent = 'Please enter the captcha.';
    return;
  }

  loginBtn.disabled = true;
  loginBtn.textContent = 'Logging in…';

  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, captcha }),
    });
    const data = await res.json();
    el('password').value = '';

    if (!res.ok) {
      loginError.textContent = data.error || 'Login failed.';
      loginBtn.disabled = false;
      loginBtn.textContent = 'Log in';
      if (data.need_restart) await initLogin();
      return;
    }

    await enterApp();
  } catch (e) {
    loginError.textContent = 'Could not reach the server.';
    loginBtn.disabled = false;
    loginBtn.textContent = 'Log in';
  }
}

async function handleLogout() {
  await fetch('/api/logout', { method: 'POST' });
  el('app-view').style.display = 'none';
  el('login-view').style.display = 'flex';
  el('login-btn').disabled = false;
  el('login-btn').textContent = 'Log in';
  initLogin();
}

async function enterApp() {
  el('login-view').style.display = 'none';
  const appView = el('app-view');
  appView.style.display = 'block';
  await loadAttendance();
}

const ALL_TABS = ['dashboard', 'analytics', 'timetable', 'skip', 'profile'];

function renderSkeletons() {
  el('tab-dashboard').innerHTML =
    '<div class="card skel-row"><div class="skel skel-circle"></div><div style="flex:1"><div class="skel skel-line w-40"></div><div class="skel skel-line w-60"></div></div></div>' +
    '<div class="card skel-row"><div class="skel skel-ring"></div><div style="flex:1"><div class="skel skel-line w-40"></div><div class="skel skel-line w-80"></div></div></div>' +
    '<div class="card"><div class="skel skel-line w-60"></div><div class="skel skel-bar w-100"></div><div class="skel skel-line w-40" style="margin-top:10px"></div></div>' +
    '<div class="card"><div class="skel skel-line w-60"></div><div class="skel skel-bar w-100"></div><div class="skel skel-line w-40" style="margin-top:10px"></div></div>';

  el('tab-analytics').innerHTML =
    '<div class="card skel-row"><div class="skel skel-circle"></div><div style="flex:1"><div class="skel skel-line w-60"></div></div></div>' +
    '<div class="card"><div class="skel skel-line w-40"></div><div class="skel skel-bar w-100" style="height:120px"></div></div>' +
    '<div class="card"><div class="skel skel-line w-40"></div><div class="skel skel-bar w-100" style="height:120px"></div></div>';

  el('tab-timetable').innerHTML =
    '<div class="card"><div class="skel skel-line w-40"></div><div class="skel skel-line w-80"></div>' +
    '<div class="skel skel-bar w-100" style="height:40px;margin-top:16px"></div>' +
    '<div class="skel skel-bar w-100" style="height:40px"></div><div class="skel skel-bar w-100" style="height:40px"></div></div>';

  el('tab-skip').innerHTML =
    '<div class="card"><div class="skel skel-line w-40"></div><div class="skel skel-line w-80"></div>' +
    '<div class="skel skel-bar w-100" style="height:44px;margin-top:16px"></div></div>';

  el('tab-profile').innerHTML =
    '<div class="card skel-row"><div class="skel skel-circle"></div><div style="flex:1"><div class="skel skel-line w-40"></div><div class="skel skel-line w-60"></div></div></div>' +
    '<div class="card"><div class="skel skel-line w-60"></div><div class="skel skel-line w-40"></div><div class="skel skel-line w-80"></div></div>';
}

function buildErrorCard(message) {
  return (
    '<div class="card state-card">' +
      '<div class="state-icon">\u26a0\ufe0f</div>' +
      '<div class="state-title">Could not load your attendance</div>' +
      '<div class="state-msg">' + message + '</div>' +
      '<div class="state-actions">' +
        '<button class="btn-primary" style="margin-top:0" data-action="retry">Retry</button>' +
        '<button class="btn-outline" style="margin-top:0" data-action="logout">Log out</button>' +
      '</div>' +
    '</div>'
  );
}

function renderErrorStates(message) {
  ALL_TABS.forEach(function (tab) {
    el('tab-' + tab).innerHTML = buildErrorCard(message);
  });
  document.querySelectorAll('[data-action="retry"]').forEach(function (btn) {
    btn.addEventListener('click', function () { loadAttendance(); });
  });
  document.querySelectorAll('[data-action="logout"]').forEach(function (btn) {
    btn.addEventListener('click', handleLogout);
  });
}

async function loadAttendance() {
  renderSkeletons();
  try {
    const res = await fetch('/api/attendance');
    const data = await res.json();
    if (!res.ok) {
      renderErrorStates(data.error || 'Something went wrong. Please try again.');
      return;
    }
    state.data = data;
    renderAll();
  } catch (e) {
    renderErrorStates('Network error \u2014 check your connection and try again.');
  }
}

async function refreshData(showFeedback) {
  const btn = document.getElementById('refresh-btn');
  if (btn) btn.classList.add('spinning');
  try {
    const res = await fetch('/api/attendance');
    const data = await res.json();
    if (!res.ok) {
      if (showFeedback) showToast(data.error || 'Could not refresh.', 'error', '\u26a0\ufe0f');
      return;
    }
    state.data = data;
    renderAll();
    if (showFeedback) showToast('Attendance refreshed', 'success', '\u2705');
  } catch (e) {
    if (showFeedback) showToast('Network error \u2014 could not refresh.', 'error', '\u26a0\ufe0f');
  } finally {
    if (btn) btn.classList.remove('spinning');
  }
}

// -------------------------------------------------------------------------
// Tabs
// -------------------------------------------------------------------------

function switchTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  el('tab-' + tab).classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderAll() {
  renderDashboard();
  renderAnalytics();
  renderTimetable();
  renderSkipCalculator();
  renderProfile();
}

// -------------------------------------------------------------------------
// Dashboard
// -------------------------------------------------------------------------

function initials(name) {
  if (!name) return '?';
  return name.trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();
}

function ringSvg(pct, size, stroke) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - Math.min(pct, 100) / 100);
  const color = alertColorVar(alertLevel(pct));
  return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '">' +
    '<circle class="ring-track" cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '"></circle>' +
    '<circle class="ring-fill" cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '" style="stroke:' + color + ';stroke-dasharray:' + c + ';stroke-dashoffset:' + offset + '"></circle>' +
    '</svg>';
}

function buildAlertBanners(subjects) {
  const danger = subjects.filter(s => alertLevel(s.percentage) === 'danger');
  const warn = subjects.filter(s => alertLevel(s.percentage) === 'warn');
  let html = '';
  danger.forEach(s => {
    html += '<div class="alert-banner danger">\uD83D\uDD34 Your <b>' + s.subject + '</b> attendance is at ' + fmtPct(s.percentage) + '%, below 75%.</div>';
  });
  warn.forEach(s => {
    html += '<div class="alert-banner warn">\uD83D\uDFE1 Your <b>' + s.subject + '</b> attendance is at ' + fmtPct(s.percentage) + '%, in the risk zone (75-80%).</div>';
  });
  if (!danger.length && !warn.length) {
    html += '<div class="alert-banner safe">\uD83D\uDFE2 All subjects are above 80%. You are in great shape.</div>';
  }
  return html;
}

function buildSubjectRow(s, i) {
  const level = alertLevel(s.percentage);
  const color = alertColorVar(level);
  const bm = s.bunk_meter;
  const verdict = bm.status === 'safe'
    ? 'can bunk ' + bm.can_bunk + ' more'
    : 'must attend next ' + bm.must_attend + ' to recover';
  const absentList = (s.absent_dates && s.absent_dates.length)
    ? '<div class="absent-dates"><b>Absent:</b> ' + s.absent_dates.join(', ') + '</div>'
    : '';
  return (
    '<div class="nested subject-row" style="--i:' + i + '">' +
      '<div class="subject-top">' +
        '<div class="subject-name">' + s.subject + '</div>' +
        '<div class="subject-pct" style="color:' + color + '">' + fmtPct(s.percentage) + '%</div>' +
      '</div>' +
      '<div class="bar-track"><div class="bar-fill" style="width:' + Math.min(s.percentage, 100) + '%; background:' + color + '"></div></div>' +
      '<div class="subject-meta">' + s.attended + '/' + s.held + ' classes \u00b7 ' + s.absent + ' absences \u00b7 ' + verdict + '</div>' +
      absentList +
    '</div>'
  );
}

function renderDashboard() {
  const d = state.data;
  const s = d.student;
  const o = d.overall;
  const level = alertLevel(o.current_pct);

  let html = '';

  html += '<div class="card profile-card">' +
    '<div class="avatar">' + initials(s.name) + '</div>' +
    '<div>' +
      '<div class="profile-name">' + (s.name || 'Student') + '</div>' +
      '<div class="profile-meta">' + (s.roll_no || '') + (s.section ? ' \u00b7 ' + s.section : '') + '</div>' +
    '</div>' +
  '</div>';

  html += '<div class="card overall-card">' +
    '<div class="ring-wrap">' + ringSvg(o.current_pct, 118, 10) +
      '<div class="ring-center"><div class="pct">' + fmtPct(o.current_pct) + '%</div><div class="lbl">Overall</div></div>' +
    '</div>' +
    '<div class="overall-info">' +
      '<span class="pill ' + level + '">' + (level === 'safe' ? 'On track' : level === 'warn' ? 'At risk' : 'Below 75%') + '</span>' +
      '<div class="overall-verdict">' + (o.status === 'safe'
        ? 'You can bunk <b>' + o.can_bunk + '</b> more classes overall and stay at 75%.'
        : 'Attend the next <b>' + o.must_attend + '</b> classes in a row to reach 75%.') + '</div>' +
    '</div>' +
  '</div>';

  html += buildAlertBanners(d.subjects);

  html += '<div class="section-title">Subjects <span class="hint">' + d.subjects.length + ' total</span></div>';
  html += '<div class="stagger">' + d.subjects.map((s, i) => buildSubjectRow(s, i)).join('') + '</div>';

  el('tab-dashboard').innerHTML = html;
}

function buildDonut(present, absent, noClasses) {
  const total = present + absent + noClasses || 1;
  const pDeg = (present / total) * 360;
  const aDeg = (absent / total) * 360;
  const bg = 'conic-gradient(var(--safe) 0deg ' + pDeg + 'deg, var(--danger) ' + pDeg + 'deg ' + (pDeg + aDeg) + 'deg, var(--border-strong) ' + (pDeg + aDeg) + 'deg 360deg)';
  return '<div class="donut" style="background:' + bg + '"><div class="donut-hole"><div class="pct">' + total + '</div><div class="lbl">Days</div></div></div>';
}

function buildBarChart(items) {
  let cols = '';
  items.forEach(function (item) {
    const level = alertLevel(item.pct);
    const color = alertColorVar(level);
    const h = Math.max(item.pct, 4);
    cols += '<div class="chart-col">';
    cols += '<div class="chart-value" style="color:' + color + '">' + item.pct + '%</div>';
    cols += '<div class="chart-bar-track"><div class="chart-bar" style="height:' + h + '%; background:' + color + '"></div></div>';
    cols += '<div class="chart-label">' + item.label + '</div>';
    cols += '</div>';
  });
  return '<div class="chart-row">' + cols + '</div>';
}

function buildTrendRows(subjects) {
  const sorted = [...subjects].sort((a, b) => a.percentage - b.percentage);
  let html = '';
  sorted.forEach(function (s) {
    const color = alertColorVar(alertLevel(s.percentage));
    html += '<div class="trend-row">';
    html += '<div class="name">' + s.subject + '</div>';
    html += '<div class="track"><div class="fill" style="width:' + Math.min(s.percentage, 100) + '%; background:' + color + '"></div></div>';
    html += '<div class="val" style="color:' + color + '">' + fmtPct(s.percentage) + '%</div>';
    html += '</div>';
  });
  return html;
}

function buildThemeChips() {
  const current = localStorage.getItem('bunkmeter-theme') || 'dark';
  const swatches = {
    light: 'linear-gradient(135deg, #f4f3f8, #ffffff)',
    dark: 'linear-gradient(135deg, #0e0f14, #1e2029)',
    glass: 'linear-gradient(135deg, #8c6eff, #46dcbe)',
  };
  const labels = { light: '\u2600\ufe0f Light', dark: '\ud83c\udf19 Dark', glass: '\ud83e\uddca Glass' };
  let html = '';
  THEMES.forEach(function (t) {
    const active = t === current ? ' active' : '';
    html += '<div class="theme-chip' + active + '" data-theme-choice="' + t + '">';
    html += '<div class="swatch" style="background:' + swatches[t] + '"></div>';
    html += '<div class="lbl">' + labels[t] + '</div>';
    html += '</div>';
  });
  return html;
}

function renderThemePicker() {
  const wrap = document.getElementById('theme-picker-wrap');
  if (!wrap) return;
  wrap.innerHTML =
    '<div class="eyebrow">Appearance</div>' +
    '<div class="theme-grid">' + buildThemeChips() + '</div>';

  wrap.querySelectorAll('.theme-chip').forEach(function (chip) {
    chip.addEventListener('click', function () { setTheme(chip.dataset.themeChoice); });
  });
}

function renderProfile() {
  const d = state.data;
  const s = d.student;
  const o = d.overall;

  let html = '';
  html += '<div class="card profile-card">';
  html += '<div class="avatar">' + initials(s.name) + '</div>';
  html += '<div><div class="profile-name">' + (s.name || 'Student') + '</div>';
  html += '<div class="profile-meta">' + fmtPct(o.current_pct) + '% overall attendance</div></div>';
  html += '</div>';

  html += '<div class="card">';
  html += '<div class="eyebrow">Student details</div>';
  html += '<div class="info-row"><span class="k">Roll number</span><span class="v">' + (s.roll_no || '\u2014') + '</span></div>';
  html += '<div class="info-row"><span class="k">Section</span><span class="v">' + (s.section || '\u2014') + '</span></div>';
  html += '<div class="info-row"><span class="k">Father\u2019s name</span><span class="v">' + (s.father_name || '\u2014') + '</span></div>';
  if (s.address) {
    html += '<div class="info-row"><span class="k">Address</span><span class="v">' + s.address + '</span></div>';
  }
  html += '</div>';

  html += '<div class="card" id="theme-picker-wrap"></div>';

  html += '<div class="card">';
  html += '<div class="eyebrow">Session</div>';
  html += '<p class="subject-meta">Your winnou password is never stored \u2014 only this session stays signed in, and it expires automatically after 30 minutes of inactivity.</p>';
  html += '<button class="btn-outline" id="logout-btn">Log out</button>';
  html += '</div>';

  el('tab-profile').innerHTML = html;
  renderThemePicker();
  el('logout-btn').addEventListener('click', handleLogout);
}

function buildSkipOptions(subjects) {
  let html = '<option value="__overall__">Overall attendance</option>';
  subjects.forEach(function (s, i) {
    html += '<option value="' + i + '">' + s.subject + '</option>';
  });
  return html;
}

function renderSkipResult(selection) {
  const d = state.data;
  let subject, label;
  if (selection === '__overall__') {
    subject = d.overall;
    label = 'Overall';
  } else {
    subject = d.subjects[Number(selection)];
    label = subject.subject;
  }

  const bm = selection === '__overall__' ? d.overall : subject.bunk_meter;
  const pct = selection === '__overall__' ? d.overall.current_pct : subject.percentage;
  const color = alertColorVar(alertLevel(pct));

  let verdict;
  if (bm.status === 'safe') {
    verdict = 'You can miss <b>' + bm.can_bunk + '</b> more class' + (bm.can_bunk === 1 ? '' : 'es') + ' and remain at or above 75%.';
  } else {
    verdict = 'You need to attend the next <b>' + bm.must_attend + '</b> class' + (bm.must_attend === 1 ? '' : 'es') + ' in a row to reach 75%.';
  }

  const html =
    '<div class="skip-result">' +
      '<div class="eyebrow">' + label + '</div>' +
      '<div class="big-pct" style="color:' + color + '">' + fmtPct(pct) + '%</div>' +
      '<div class="verdict">' + verdict + '</div>' +
    '</div>';

  el('skip-result-wrap').innerHTML = html;
}

function renderSkipCalculator() {
  const d = state.data;
  let html = '';
  html += '<div class="card">';
  html += '<div class="eyebrow">Can I skip?</div>';
  html += '<h2 style="font-size:1.2rem">Pick a subject</h2>';
  html += '<p class="subject-meta" style="margin-top:6px">See exactly how many classes you can miss, or must attend, to stay at 75%.</p>';
  html += '<select class="skip-select" id="skip-select" style="margin-top:16px">' + buildSkipOptions(d.subjects) + '</select>';
  html += '<div id="skip-result-wrap"></div>';
  html += '</div>';

  el('tab-skip').innerHTML = html;

  const select = el('skip-select');
  select.addEventListener('change', function () { renderSkipResult(select.value); });
  renderSkipResult('__overall__');
}

function getTimetableDays() {
  return sortedCalendar(state.data.calendar).slice(0, 14);
}

function renderTimetable() {
  const days = getTimetableDays();
  if (!days.length) {
    el('tab-timetable').innerHTML = '<div class="card"><p class="loading">No day-by-day history available yet.</p></div>';
    return;
  }
  if (state.activeDayIndex >= days.length) state.activeDayIndex = 0;
  const activeDay = days[state.activeDayIndex];

  let html = '';
  html += '<div class="card">';
  html += '<div class="eyebrow">Period Log</div>';
  html += '<h2 style="font-size:1.15rem">' + activeDay.label + '</h2>';
  html += '<p class="subject-meta" style="margin-top:4px">Shows attendance status per period \u2014 subject names aren\u2019t available for this view, only the summary table has those.</p>';
  html += '<div class="day-switcher" id="day-switcher">' + buildDayChips(days) + '</div>';
  html += buildPeriodList(activeDay);
  html += '</div>';

  el('tab-timetable').innerHTML = html;

  document.querySelectorAll('#day-switcher .day-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      state.activeDayIndex = Number(chip.dataset.dayIndex);
      renderTimetable();
    });
  });
}

function renderAnalytics() {
  const d = state.data;
  const streak = computeStreak(d.calendar);
  const weekly = computeWeekly(d.calendar);
  const monthly = computeMonthly(d.calendar);
  const pva = computePresentVsAbsent(d.calendar);

  let html = '';

  html += '<div class="card streak-card">';
  html += '<div class="streak-flame">\uD83D\uDD25</div>';
  html += '<div><div class="streak-num">' + streak + '-day streak</div>';
  html += '<div class="streak-lbl">Consecutive days fully present</div></div>';
  html += '</div>';

  html += '<div class="card"><div class="section-title" style="margin-top:0">Weekly attendance</div>';
  html += weekly.length ? buildBarChart(weekly) : '<p class="loading">Not enough data yet.</p>';
  html += '</div>';

  html += '<div class="card"><div class="section-title" style="margin-top:0">Monthly attendance</div>';
  html += monthly.length ? buildBarChart(monthly) : '<p class="loading">Not enough data yet.</p>';
  html += '</div>';

  html += '<div class="card"><div class="section-title" style="margin-top:0">Present vs absent days</div>';
  html += '<div class="donut-wrap">' + buildDonut(pva.present, pva.absent, pva.noClasses);
  html += '<div class="legend-list">';
  html += '<div class="legend-item"><span class="legend-dot" style="background:var(--safe)"></span>Present<span class="n">' + pva.present + '</span></div>';
  html += '<div class="legend-item"><span class="legend-dot" style="background:var(--danger)"></span>Absent<span class="n">' + pva.absent + '</span></div>';
  html += '<div class="legend-item"><span class="legend-dot" style="background:var(--border-strong)"></span>No classes<span class="n">' + pva.noClasses + '</span></div>';
  html += '</div></div></div>';

  html += '<div class="card"><div class="section-title" style="margin-top:0">Subject-wise trend</div>';
  html += buildTrendRows(d.subjects);
  html += '</div>';

  el('tab-analytics').innerHTML = html;
}

function buildDayChips(days) {
  let html = '';
  days.forEach(function (d, i) {
    const active = i === state.activeDayIndex ? ' active' : '';
    html += '<div class="day-chip' + active + '" data-day-index="' + i + '">' + d.weekday + ' ' + d.date.slice(8, 10) + '/' + d.date.slice(5, 7) + '</div>';
  });
  return html;
}

function buildPeriodList(day) {
  const slots = mapDayToSlots(day);
  let html = '<div class="period-list">';
  slots.forEach(function (slot, i) {
    const statusClass = slot.status === 'P' ? 'status-P' : slot.status === 'A' ? 'status-A' : 'status-none';
    const badgeColor = slot.status === 'P' ? 'var(--safe)' : slot.status === 'A' ? 'var(--danger)' : 'var(--text-muted)';
    const badgeBg = slot.status === 'P' ? 'var(--safe-soft)' : slot.status === 'A' ? 'var(--danger-soft)' : 'var(--bg-elevated-2)';
    const label = slot.status === 'P' ? 'Present' : slot.status === 'A' ? 'Absent' : 'No class';
    html += '<div class="period-row ' + statusClass + '">';
    html += '<div class="period-slot">' + slot.label + '</div>';
    html += '<div class="period-name">Period ' + (i + 1) + '</div>';
    html += '<div class="period-status-badge" style="color:' + badgeColor + '; background:' + badgeBg + '">' + label + '</div>';
    html += '</div>';
  });
  html += '</div>';
  return html;
}

function wireNav() {
  document.querySelectorAll('.nav-btn[data-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () { switchTab(btn.dataset.tab); });
  });
}

// -------------------------------------------------------------------------
// Pull-to-refresh (mobile) + manual refresh button
// -------------------------------------------------------------------------

function rubberband(overshoot, dimension, constant) {
  constant = constant || 0.55;
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
}

function wireRefresh() {
  const refreshBtn = document.getElementById('refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function () {
      if (state.data) refreshData(true);
    });
  }

  const indicator = document.getElementById('pull-indicator');
  if (!indicator) return;

  const PULL_THRESHOLD = 68;
  const PULL_MAX = 110;
  let startY = null;
  let pulling = false;

  document.addEventListener('touchstart', function (e) {
    if (window.scrollY <= 0 && state.data && state.activeTab !== null) {
      startY = e.touches[0].clientY;
      pulling = true;
    }
  }, { passive: true });

  document.addEventListener('touchmove', function (e) {
    if (!pulling) return;
    const delta = e.touches[0].clientY - startY;
    if (delta <= 0) return;
    const damped = rubberband(delta, PULL_MAX);
    indicator.style.transform = 'translateY(' + damped + 'px) translateX(-50%)';
    indicator.style.opacity = String(Math.min(damped / PULL_THRESHOLD, 1));
    indicator.classList.toggle('ready', damped >= PULL_THRESHOLD * 0.9);
  }, { passive: true });

  document.addEventListener('touchend', function () {
    if (!pulling) return;
    pulling = false;
    const wasReady = indicator.classList.contains('ready');
    indicator.style.transition = 'transform 300ms var(--ease-spring), opacity 300ms var(--ease-out)';
    indicator.style.transform = 'translateY(0) translateX(-50%)';
    indicator.style.opacity = '0';
    indicator.classList.remove('ready');
    if (wasReady) {
      indicator.classList.add('spinning');
      indicator.style.opacity = '1';
      refreshData(true).then(function () {
        indicator.classList.remove('spinning');
      });
    }
    setTimeout(function () { indicator.style.transition = ''; }, 320);
  });
}

function init() {
  initTheme();
  wireNav();
  wireRefresh();

  el('login-btn').addEventListener('click', handleLogin);
  el('captcha-refresh').addEventListener('click', initLogin);
  el('password').addEventListener('keydown', function (e) { if (e.key === 'Enter') handleLogin(); });
  el('captcha').addEventListener('keydown', function (e) { if (e.key === 'Enter') handleLogin(); });

  initLogin();
}

document.addEventListener('DOMContentLoaded', init);
