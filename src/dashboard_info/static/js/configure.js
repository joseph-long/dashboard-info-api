const deviceId = decodeURIComponent(window.location.pathname.split('/')[2]);

const rowsContainer = document.getElementById('config-rows');
const saveStatus = document.getElementById('save-status');
const stationsBanner = document.getElementById('stations-banner');

let stations = []; // [{id, name, routes}]
const stationById = new Map();

function stationLabel(station) {
  return `${station.name} (${station.routes.join(', ')})`;
}

// A text input with a filtered dropdown of stations; the chosen station id
// is kept on input.dataset.stationId. Typing a non-matching value clears it.
function makeStationPicker(placeholder) {
  const wrap = document.createElement('div');
  wrap.className = 'station-picker';

  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = placeholder;
  input.autocomplete = 'off';
  wrap.appendChild(input);

  const list = document.createElement('ul');
  list.className = 'station-picker-list';
  list.hidden = true;
  wrap.appendChild(list);

  function closeList() {
    list.hidden = true;
    list.replaceChildren();
  }

  function openList(matches) {
    list.replaceChildren(
      ...matches.slice(0, 30).map((station) => {
        const li = document.createElement('li');
        li.textContent = stationLabel(station);
        li.addEventListener('mousedown', (event) => {
          event.preventDefault(); // keep focus logic simple: select before blur
          input.value = station.name;
          input.dataset.stationId = station.id;
          input.dispatchEvent(new Event('station-changed'));
          closeList();
        });
        return li;
      })
    );
    list.hidden = matches.length === 0;
  }

  input.addEventListener('input', () => {
    input.dataset.stationId = '';
    input.dispatchEvent(new Event('station-changed'));
    const query = input.value.trim().toLowerCase();
    if (!query) {
      closeList();
      return;
    }
    openList(stations.filter((s) => s.name.toLowerCase().includes(query)));
  });
  input.addEventListener('focus', () => {
    if (input.value.trim()) input.dispatchEvent(new Event('input'));
  });
  input.addEventListener('blur', () => closeList());

  return { wrap, input };
}

function makeField(labelText, control) {
  const field = document.createElement('div');
  field.className = 'field';
  const label = document.createElement('label');
  label.textContent = labelText;
  field.appendChild(label);
  field.appendChild(control);
  return field;
}

function makeNumberInput(placeholder) {
  const input = document.createElement('input');
  input.type = 'number';
  input.min = '0';
  input.max = '120';
  input.placeholder = placeholder;
  return input;
}

function makeDirectionSelect() {
  const select = document.createElement('select');
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = '—';
  placeholder.disabled = true;
  select.appendChild(placeholder);
  for (const value of ['uptown', 'downtown']) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
  return select;
}

function addConfigRow(config) {
  const row = document.createElement('div');
  row.className = 'config-row';

  const header = document.createElement('div');
  header.className = 'config-row-header';
  const title = document.createElement('h3');
  title.textContent = 'Configuration';
  const removeButton = document.createElement('button');
  removeButton.type = 'button';
  removeButton.className = 'secondary';
  removeButton.textContent = 'Remove';
  removeButton.addEventListener('click', () => row.remove());
  header.appendChild(title);
  header.appendChild(removeButton);
  row.appendChild(header);

  const fields = document.createElement('div');
  fields.className = 'field-row';

  const origin = makeStationPicker('Origin station');
  const service = document.createElement('select');
  const direction = makeDirectionSelect();
  const destination = makeStationPicker('none');
  const destWalk = makeNumberInput('min');
  origin.input.classList.add('origin-input');
  service.classList.add('service-select');
  direction.classList.add('direction-select');
  destination.input.classList.add('dest-input');
  destWalk.classList.add('dest-walk-input');

  fields.appendChild(makeField('Origin station', origin.wrap));
  fields.appendChild(makeField('Service', service));
  fields.appendChild(makeField('Direction', direction));
  fields.appendChild(makeField('Destination station (optional)', destination.wrap));
  fields.appendChild(makeField('Destination walk time (min)', destWalk));
  row.appendChild(fields);

  function refreshServices(keepValue) {
    const station = stationById.get(origin.input.dataset.stationId);
    const routes = station ? station.routes : [];
    const previous = keepValue ? service.value : null;
    service.replaceChildren(
      ...routes.map((route) => {
        const option = document.createElement('option');
        option.value = route;
        option.textContent = route;
        return option;
      })
    );
    if (previous && routes.includes(previous)) service.value = previous;
    service.disabled = routes.length === 0;
  }

  origin.input.addEventListener('station-changed', () => refreshServices(false));

  if (config) {
    const originStation = stationById.get(config.origin_station_id);
    if (originStation) {
      origin.input.value = originStation.name;
      origin.input.dataset.stationId = originStation.id;
    } else if (config.origin_station_id) {
      origin.input.value = config.origin_station_id;
      origin.input.dataset.stationId = config.origin_station_id;
    }
    refreshServices(true);
    if (config.service) service.value = config.service;
    if (config.direction) direction.value = config.direction;
    const destStation = stationById.get(config.destination_station_id);
    if (destStation) {
      destination.input.value = destStation.name;
      destination.input.dataset.stationId = destStation.id;
    }
    if (config.destination_walk_minutes != null) {
      destWalk.value = String(config.destination_walk_minutes);
    }
  } else {
    refreshServices(false);
    direction.value = '';
  }

  rowsContainer.appendChild(row);
}

function readRows() {
  const configs = [];
  for (const row of rowsContainer.querySelectorAll('.config-row')) {
    const [originInput, serviceSelect, directionSelect, destInput, destWalkInput] =
      row.querySelectorAll('input, select');
    const originId = originInput.dataset.stationId;
    const serviceValue = serviceSelect.value;
    const directionValue = directionSelect.value;
    if (!originId || !serviceValue || !directionValue) {
      throw new Error(
        'Every configuration needs an origin station, a service and a direction.'
      );
    }
    configs.push({
      origin_station_id: originId,
      service: serviceValue,
      direction: directionValue,
      destination_station_id: destInput.dataset.stationId || null,
      destination_walk_minutes:
        destWalkInput.value === '' ? null : Number(destWalkInput.value),
    });
  }
  return configs;
}

function setStatus(text, kind) {
  saveStatus.textContent = text;
  saveStatus.className = `status-text ${kind || ''}`;
}

const scheduleStatus = document.getElementById('schedule-status');

function setScheduleStatus(text, kind) {
  scheduleStatus.textContent = text;
  scheduleStatus.className = `status-text ${kind || ''}`;
}

function fillSchedule(device) {
  document.getElementById('commute-start').value = device.commute_start || '';
  document.getElementById('commute-end').value = device.commute_end || '';
  document.getElementById('dim-start').value = device.dim_start || '';
  document.getElementById('dim-end').value = device.dim_end || '';
  document.getElementById('dim-brightness').value =
    device.dim_brightness == null ? '' : String(device.dim_brightness);
  document.getElementById('off-start').value = device.off_start || '';
  document.getElementById('off-end').value = device.off_end || '';
}

async function load() {
  document.getElementById('device-id-label').textContent = deviceId;
  document.getElementById('dashboard-link').href =
    `/devices/${encodeURIComponent(deviceId)}/dashboard`;

  const [deviceResp, stationsResp] = await Promise.all([
    fetch(`/api/devices/${encodeURIComponent(deviceId)}`),
    fetch('/api/stations'),
  ]);
  if (deviceResp.status === 404) {
    setStatus(`Unknown device: ${deviceId}`, 'error');
    document.getElementById('save-config').disabled = true;
    return;
  }
  const device = await deviceResp.json();
  const stationsData = await stationsResp.json();

  stations = stationsData.stations;
  for (const station of stations) stationById.set(station.id, station);
  stationsBanner.hidden = stations.length > 0;

  const lastRequest = document.getElementById('last-request');
  lastRequest.textContent = device.last_request_at
    ? `last request ${new Date(device.last_request_at).toLocaleString()}`
    : 'no requests yet';

  for (const config of device.configurations) addConfigRow(config);
  if (device.configurations.length === 0) addConfigRow(null);

  fillSchedule(device);
}

document.getElementById('add-config').addEventListener('click', () => addConfigRow(null));

document.getElementById('save-config').addEventListener('click', async () => {
  let configs;
  try {
    configs = readRows();
  } catch (err) {
    setStatus(err.message, 'error');
    return;
  }
  const resp = await fetch(`/api/devices/${encodeURIComponent(deviceId)}/configurations`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ configurations: configs }),
  });
  if (resp.ok) {
    setStatus('Saved.', 'ok');
  } else {
    const detail = (await resp.json().catch(() => ({}))).detail;
    setStatus(detail || `Save failed (${resp.status}).`, 'error');
  }
});

document.getElementById('save-schedule').addEventListener('click', async () => {
  const val = (id) => document.getElementById(id).value.trim();
  const brightnessRaw = val('dim-brightness');
  const payload = {
    commute_start: val('commute-start') || null,
    commute_end: val('commute-end') || null,
    dim_start: val('dim-start') || null,
    dim_end: val('dim-end') || null,
    dim_brightness: brightnessRaw === '' ? null : Number(brightnessRaw),
    off_start: val('off-start') || null,
    off_end: val('off-end') || null,
  };
  const resp = await fetch(`/api/devices/${encodeURIComponent(deviceId)}/schedule`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (resp.ok) {
    setScheduleStatus('Saved.', 'ok');
  } else {
    const detail = (await resp.json().catch(() => ({}))).detail;
    setScheduleStatus(detail || `Save failed (${resp.status}).`, 'error');
  }
});

document.getElementById('refresh-gtfs').addEventListener('click', async () => {
  const status = document.getElementById('refresh-status');
  status.textContent = 'Fetching…';
  const resp = await fetch('/api/gtfs/refresh', { method: 'POST' });
  if (resp.ok) {
    status.textContent = 'Done. Reloading…';
    window.location.reload();
  } else {
    status.textContent = 'Fetch failed.';
  }
});

load();
