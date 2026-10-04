// Filtering is done in the browser over the rows already rendered - phase 1
// volumes are small enough that there is no reason to round-trip the server.
const rows = Array.from(document.querySelectorAll('#events tbody tr'));
const tbody = document.querySelector('#events tbody');
const table = document.getElementById('events');
const search = document.getElementById('search');
const datedOnly = document.getElementById('dated-only');
const countEl = document.getElementById('count');
const emptyEl = document.getElementById('empty');

let sport = '';
let source = '';

// --- near me ------------------------------------------------------------- //
const STORE_ORIGIN = 'eventcrawler.origin';
const STORE_RADIUS = 'eventcrawler.radiusKm';
const locationStatus = document.getElementById('location-status');
const radiusChips = document.getElementById('radius-chips');
const customKm = document.getElementById('custom-km');
const sortWrap = document.getElementById('sort-wrap');
const sortSelect = document.getElementById('sort');
const clearBtn = document.getElementById('clear-location');

let origin = null;     // {lat, lon, label}
let radiusKm = null;   // null = anywhere

for (const row of rows) {
  const lat = parseFloat(row.dataset.lat);
  const lng = parseFloat(row.dataset.lng);
  row._coords = Number.isFinite(lat) && Number.isFinite(lng) ? [lat, lng] : null;
  row._distCell = row.querySelector('td.dist');
  row._km = null;
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(a));
}

function formatKm(km, approx) {
  const text = km < 1 ? '<1 km' : km < 10 ? `${km.toFixed(1)} km` : `${Math.round(km).toLocaleString()} km`;
  return approx ? `~${text}` : text;
}

function computeDistances() {
  for (const row of rows) {
    row._km = origin && row._coords
      ? haversineKm(origin.lat, origin.lon, row._coords[0], row._coords[1])
      : null;
    if (!origin) {
      row._distCell.textContent = '';
    } else if (row._km === null) {
      row._distCell.innerHTML = '<span class="muted">&mdash;</span>';
    } else {
      const approx = row.dataset.approx === '1';
      row._distCell.textContent = formatKm(row._km, approx);
      row._distCell.title = approx ? 'measured to the city centre - the venue may be elsewhere in the city' : '';
    }
  }
}

function reorder() {
  const byDistance = origin && sortSelect.value === 'distance';
  const ordered = byDistance
    ? rows.slice().sort((a, b) => (a._km ?? Infinity) - (b._km ?? Infinity))
    : rows;
  const fragment = document.createDocumentFragment();
  for (const row of ordered) fragment.appendChild(row);
  tbody.appendChild(fragment);
}

function setOrigin(next) {
  origin = next;
  if (origin) localStorage.setItem(STORE_ORIGIN, JSON.stringify(origin));
  else localStorage.removeItem(STORE_ORIGIN);
  table.classList.toggle('no-location', !origin);
  radiusChips.hidden = !origin;
  sortWrap.hidden = !origin;
  clearBtn.hidden = !origin;
  locationStatus.textContent = origin
    ? `Distances from ${origin.label}. "~" means measured to the city centre.`
    : 'Set a location to filter by distance.';
  computeDistances();
  reorder();
  apply();
}

function setRadius(km) {
  radiusKm = km && km > 0 ? km : null;
  if (radiusKm) localStorage.setItem(STORE_RADIUS, String(radiusKm));
  else localStorage.removeItem(STORE_RADIUS);
  let matched = false;
  radiusChips.querySelectorAll('.chip').forEach((chip) => {
    const on = (parseFloat(chip.dataset.km) || null) === radiusKm;
    chip.classList.toggle('active', on);
    matched = matched || on;
  });
  customKm.value = radiusKm && !matched ? radiusKm : '';
  apply();
}

document.getElementById('use-location').addEventListener('click', () => {
  if (!navigator.geolocation) {
    locationStatus.textContent = 'This browser cannot share its location - type a place instead.';
    return;
  }
  locationStatus.textContent = 'Asking the browser for your location…';
  navigator.geolocation.getCurrentPosition(
    (pos) => setOrigin({
      lat: pos.coords.latitude,
      lon: pos.coords.longitude,
      label: `your location (±${Math.round(pos.coords.accuracy)} m)`,
    }),
    (err) => {
      locationStatus.textContent = `Location unavailable (${err.message || 'permission denied'}) - type a place instead.`;
    },
    { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 },
  );
});

document.getElementById('place-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = document.getElementById('place').value.trim();
  if (!query) return;
  locationStatus.textContent = `Looking up "${query}"…`;
  try {
    const response = await fetch(`/geocode?q=${encodeURIComponent(query)}`);
    const data = await response.json();
    if (!response.ok) {
      locationStatus.textContent = data.error || 'Lookup failed.';
      return;
    }
    const short = String(data.label).split(',').slice(0, 3).map((s) => s.trim()).join(', ');
    setOrigin({ lat: data.lat, lon: data.lon, label: short });
  } catch (err) {
    locationStatus.textContent = 'Lookup failed: ' + err.message;
  }
});

clearBtn.addEventListener('click', () => setOrigin(null));

radiusChips.addEventListener('click', (event) => {
  const chip = event.target.closest('.chip');
  if (chip) setRadius(parseFloat(chip.dataset.km) || null);
});
customKm.addEventListener('change', () => setRadius(parseFloat(customKm.value) || null));
sortSelect.addEventListener('change', reorder);

// --- filters ------------------------------------------------------------- //
function apply() {
  const term = search.value.trim().toLowerCase();
  const limit = origin ? radiusKm : null;
  let shown = 0;
  for (const row of rows) {
    const visible =
      (!sport || row.dataset.sport === sport) &&
      (!source || row.dataset.source === source) &&
      (!datedOnly.checked || row.dataset.dated === '1') &&
      (!term || row.dataset.text.includes(term)) &&
      (!limit || (row._km !== null && row._km <= limit));
    row.hidden = !visible;
    if (visible) shown++;
  }
  countEl.textContent = `showing ${shown} of ${rows.length}` +
    (limit ? ` within ${limit.toLocaleString()} km` : '');
  emptyEl.hidden = shown !== 0;
}

function wireChips(containerId, onPick) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.addEventListener('click', (event) => {
    const chip = event.target.closest('.chip');
    if (!chip) return;
    container.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
    chip.classList.add('active');
    onPick(chip);
    apply();
  });
}

wireChips('sport-chips', (chip) => { sport = chip.dataset.sport || ''; });
wireChips('source-chips', (chip) => { source = chip.dataset.source || ''; });
search.addEventListener('input', apply);
datedOnly.addEventListener('change', apply);

const recrawl = document.getElementById('recrawl');
recrawl.addEventListener('click', async () => {
  recrawl.disabled = true;
  recrawl.textContent = 'Crawling…';
  try {
    const response = await fetch('/crawl', { method: 'POST' });
    const data = await response.json();
    if (data.status === 'ok') {
      location.reload();
      return;
    }
    recrawl.textContent = data.message || 'Failed';
  } catch (err) {
    recrawl.textContent = 'Failed: ' + err.message;
  }
  recrawl.disabled = false;
});

try {
  const saved = JSON.parse(localStorage.getItem(STORE_ORIGIN) || 'null');
  const savedRadius = parseFloat(localStorage.getItem(STORE_RADIUS)) || null;
  if (saved && Number.isFinite(saved.lat) && Number.isFinite(saved.lon)) {
    setOrigin(saved);
    setRadius(savedRadius);
  }
} catch (err) {
  localStorage.removeItem(STORE_ORIGIN);
}

apply();
