// Filtering is done in the browser over the rows already rendered - phase 1
// volumes are small enough that there is no reason to round-trip the server.
const rows = Array.from(document.querySelectorAll('#events tbody tr'));
const search = document.getElementById('search');
const datedOnly = document.getElementById('dated-only');
const countEl = document.getElementById('count');
const emptyEl = document.getElementById('empty');

let sport = '';
let source = '';

function apply() {
  const term = search.value.trim().toLowerCase();
  let shown = 0;
  for (const row of rows) {
    const visible =
      (!sport || row.dataset.sport === sport) &&
      (!source || row.dataset.source === source) &&
      (!datedOnly.checked || row.dataset.dated === '1') &&
      (!term || row.dataset.text.includes(term));
    row.hidden = !visible;
    if (visible) shown++;
  }
  countEl.textContent = `showing ${shown} of ${rows.length}`;
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

apply();
