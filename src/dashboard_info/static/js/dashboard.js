// Dashboard preview: polls the same device API an embedded client would
// (/public/devices/{id}/departures, max_departures query param) and renders
// the result in a format inspired by MTA departures boards. All formatting
// happens here, client-side -- the API only carries data.

const deviceId = decodeURIComponent(window.location.pathname.split('/')[2]);
const params = new URLSearchParams(window.location.search);
const maxDepartures = Number(params.get('max_departures')) || 3;
const debug = params.get('debug') === '1';

const POLL_INTERVAL_MS = 10_000;
const RENDER_INTERVAL_MS = 5_000;

// Official MTA bullet colors per service (embedded clients hardcode these too).
const ROUTE_COLORS = {
  A: '#0062CF', C: '#0062CF', E: '#0062CF',
  B: '#EB6800', D: '#EB6800', F: '#EB6800', FX: '#EB6800', M: '#EB6800',
  G: '#799534',
  J: '#8E5C33', Z: '#8E5C33',
  L: '#7C858C',
  N: '#F6BC26', Q: '#F6BC26', R: '#F6BC26', W: '#F6BC26',
  S: '#7C858C', GS: '#7C858C', FS: '#7C858C', H: '#7C858C',
  1: '#D82233', 2: '#D82233', 3: '#D82233',
  4: '#009952', 5: '#009952', 6: '#009952', '6X': '#009952',
  7: '#9A38A1', '7X': '#9A38A1',
  SI: '#08179C',
};
const DARK_BULLETS = new Set(['#F6BC26']); // yellow bullets get black text

const app = document.getElementById('app');
const board = document.createElement('div');
board.className = 'departures-board';
app.appendChild(board);

// ?debug=1: pretty-print the exact JSON payload the device API returned,
// refreshed on every poll alongside the board it generates.
let debugPre = null;
if (debug) {
  debugPre = document.createElement('pre');
  debugPre.className = 'debug-json';
  app.appendChild(debugPre);
}

let departures = [];
let errorMessage = null;

function bulletFor(service) {
  const bullet = document.createElement('span');
  bullet.className = 'service-bullet';
  // Express variants show as a diamond with the base service letter.
  const isExpress = service.length === 2 && service.endsWith('X');
  if (isExpress) bullet.classList.add('is-diamond');
  const color = ROUTE_COLORS[service] || '#6E6E6E';
  bullet.style.background = color;
  bullet.style.color = DARK_BULLETS.has(color) ? '#000' : '#fff';

  const label = document.createElement('span');
  label.className = 'service-bullet-label';
  label.textContent = isExpress ? service[0] : service;
  if (label.textContent.length > 1) label.classList.add('is-wide');
  bullet.appendChild(label);
  return bullet;
}

function rowFor(departure, index) {
  const row = document.createElement('div');
  row.className = 'departure-row';

  const indexEl = document.createElement('span');
  indexEl.className = 'row-index';
  indexEl.textContent = String(index + 1);
  row.appendChild(indexEl);

  row.appendChild(bulletFor(departure.service));

  const terminus = document.createElement('span');
  terminus.className = 'terminus';
  terminus.textContent = departure.terminus;
  row.appendChild(terminus);

  if (departure.stops_at_destination === false) {
    const warning = document.createElement('span');
    warning.className = 'destination-warning';
    warning.textContent = '⚠';
    warning.title = 'This train does not stop at your destination';
    row.appendChild(warning);
  }

  const minutes = document.createElement('span');
  minutes.className = 'minutes';
  const remaining = Math.max(
    0,
    Math.floor((new Date(departure.arrives_at).getTime() - Date.now()) / 60000)
  );
  minutes.appendChild(document.createTextNode(String(remaining)));
  const unit = document.createElement('span');
  unit.className = 'minutes-unit';
  unit.textContent = 'min';
  minutes.appendChild(unit);
  row.appendChild(minutes);

  return row;
}

function render() {
  if (errorMessage) {
    const row = document.createElement('div');
    row.className = 'departure-row empty';
    row.textContent = errorMessage;
    board.replaceChildren(row);
    return;
  }
  if (departures.length === 0) {
    const row = document.createElement('div');
    row.className = 'departure-row empty';
    row.textContent = 'No upcoming departures';
    board.replaceChildren(row);
    return;
  }
  board.replaceChildren(...departures.map((d, i) => rowFor(d, i)));
}

async function poll() {
  try {
    const resp = await fetch(
      `/public/devices/${encodeURIComponent(deviceId)}/departures?max_departures=${maxDepartures}`
    );
    if (resp.ok) {
      departures = await resp.json();
      errorMessage = null;
      if (debugPre) debugPre.textContent = JSON.stringify(departures, null, 2);
    } else {
      const detail = (await resp.json().catch(() => ({}))).detail;
      errorMessage = detail || `Error ${resp.status}`;
      if (debugPre) debugPre.textContent = `HTTP ${resp.status}\n${errorMessage}`;
    }
  } catch {
    errorMessage = 'Server unreachable';
    if (debugPre) debugPre.textContent = errorMessage;
  }
  render();
}

poll();
setInterval(poll, POLL_INTERVAL_MS);
setInterval(render, RENDER_INTERVAL_MS); // countdown decays between polls
