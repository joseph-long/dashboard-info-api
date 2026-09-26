const tbody = document.getElementById('devices-body');
const noDevices = document.getElementById('no-devices');
const enrollForm = document.getElementById('enroll-form');
const enrollStatus = document.getElementById('enroll-status');

function formatTimestamp(iso) {
  if (!iso) return 'never';
  const dt = new Date(iso);
  return Number.isNaN(dt.getTime()) ? iso : dt.toLocaleString();
}

function deviceRow(device) {
  const tr = document.createElement('tr');

  const idCell = document.createElement('td');
  idCell.textContent = device.id;
  tr.appendChild(idCell);

  const lastCell = document.createElement('td');
  lastCell.textContent = formatTimestamp(device.last_request_at);
  tr.appendChild(lastCell);

  const configureCell = document.createElement('td');
  const configureLink = document.createElement('a');
  configureLink.href = `/devices/${encodeURIComponent(device.id)}/configure`;
  configureLink.textContent = 'Configure';
  configureCell.appendChild(configureLink);
  tr.appendChild(configureCell);

  const dashboardCell = document.createElement('td');
  const dashboardLink = document.createElement('a');
  dashboardLink.href = `/devices/${encodeURIComponent(device.id)}/dashboard`;
  dashboardLink.textContent = 'View dashboard';
  dashboardCell.appendChild(dashboardLink);
  tr.appendChild(dashboardCell);

  const deleteCell = document.createElement('td');
  const deleteButton = document.createElement('button');
  deleteButton.type = 'button';
  deleteButton.className = 'secondary';
  deleteButton.textContent = 'Delete';
  deleteButton.addEventListener('click', () => deleteDevice(device.id, tr));
  deleteCell.appendChild(deleteButton);
  tr.appendChild(deleteCell);

  return tr;
}

async function deleteDevice(id, row) {
  if (!window.confirm(`Delete device "${id}"? This also removes its configurations and schedule.`)) {
    return;
  }
  const resp = await fetch(`/api/devices/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (resp.ok) {
    row.remove();
    noDevices.hidden = tbody.children.length > 0;
  } else {
    window.alert(`Delete failed (${resp.status}).`);
  }
}

async function loadDevices() {
  const resp = await fetch('/api/devices');
  const devices = await resp.json();
  tbody.replaceChildren(...devices.map(deviceRow));
  noDevices.hidden = devices.length > 0;
}

enrollForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  enrollStatus.textContent = '';
  enrollStatus.className = 'status-text';
  const input = document.getElementById('device-id');
  const id = input.value.trim();
  if (!id) return;

  const resp = await fetch('/api/devices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  });
  if (resp.status === 201) {
    window.location.href = `/devices/${encodeURIComponent(id)}/configure`;
  } else if (resp.status === 409) {
    enrollStatus.textContent = `Device "${id}" is already enrolled.`;
    enrollStatus.classList.add('error');
  } else {
    enrollStatus.textContent = `Enrollment failed (${resp.status}).`;
    enrollStatus.classList.add('error');
  }
});

loadDevices();
