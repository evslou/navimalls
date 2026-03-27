/**
 * Shop Route Optimizer — Frontend Logic
 * ======================================
 * Uses Yandex Maps JS API 2.1 for map, geocoding, and routing display.
 * Communicates with the Flask backend for TSP computation.
 */

'use strict';

// ------------------------------------- Route visual config (6 colour/style combinations) -------------------------------------
const ROUTE_STYLES = [
  { mode: 'auto',        color: '#e74c3c', strokeWidth: 4 },
  { mode: 'pedestrian',  color: '#27ae60', strokeWidth: 4 },
  { mode: 'masstransit', color: '#2980b9', strokeWidth: 4 },
];

const MODE_LABEL = {
  auto:        '🚗 Авто',
  pedestrian:  '🚶 Пешком',
  masstransit: '🚌 Транспорт',
};

const CRIT_LABEL = { distance: '📏 Расстояние', time: '⏱ Время' };

// ------------------------------------- App state -------------------------------------
let myMap        = null;       // ymaps.Map instance
let startPoint   = null;       // {lat, lon}
let startPlacemark = null;     // ymaps.Placemark for start
let activeRoutes   = [];       // ymaps.multiRouter.MultiRoute objects on map
let tspResults     = [];       // last TSP results from backend
let pickingFromMap = false;    // whether map-click mode is on
let selectedShops  = new Set();// selected shop names

// ------------------------------------- Initialise Yandex Maps -------------------------------------
ymaps.ready(initMap);

function initMap() {
  myMap = new ymaps.Map('map', {
    center: [55.751244, 37.618423],   // Moscow default
    zoom:   11,
    controls: ['zoomControl', 'geolocationControl', 'typeSelector'],
  });

  myMap.events.add('click', onMapClick);
  setupUI();
}

// ------------------------------------- UI wiring -------------------------------------
function setupUI() {
  // Preset shop chips (теперь они в категориях)
  document.querySelectorAll('#shop-categories .chip').forEach(chip => {
    chip.addEventListener('click', () => togglePreset(chip));
  });

  // Custom shop add
  document.getElementById('add-shop-btn').addEventListener('click', addCustomShop);
  document.getElementById('custom-shop-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') addCustomShop();
  });

  // Clear all button
  document.getElementById('clear-all-btn').addEventListener('click', clearAllShops);

  // Geolocation
  document.getElementById('geoloc-btn').addEventListener('click', useGeolocation);

  // Map-click picker
  document.getElementById('map-click-btn').addEventListener('click', toggleMapPick);

  // Main action button
  document.getElementById('search-btn').addEventListener('click', runSearch);
  
  const radiusSlider = document.getElementById('radius-slider');
  const radiusValue = document.getElementById('radius-value');
  if (radiusSlider && radiusValue) {
    radiusSlider.addEventListener('input', (e) => {
      radiusValue.textContent = e.target.value;
    });
  }
}

// ------------------------------------- Shop selection helpers -------------------------------------
function togglePreset(chip) {
  const val = chip.dataset.value;
  if (selectedShops.has(val)) {
    selectedShops.delete(val);
    chip.classList.remove('active');
  } else {
    if (selectedShops.size >= 10) return showError('Максимум 10 магазинов.');
    selectedShops.add(val);
    chip.classList.add('active');
  }
  renderSelectedChips();
  updateSearchBtn();
}

function addCustomShop() {
  const input = document.getElementById('custom-shop-input');
  const val = input.value.trim();
  if (!val) return;
  if (selectedShops.size >= 10) { showError('Максимум 10 магазинов.'); return; }
  selectedShops.add(val);
  input.value = '';
  renderSelectedChips();
  updateSearchBtn();
}

function renderSelectedChips() {
  const container = document.getElementById('selected-chips');
  container.innerHTML = '';
  selectedShops.forEach(name => {
    const chip = document.createElement('span');
    chip.className = 'chip selected-chip';
    chip.innerHTML = `${name} <span class="remove-chip">×</span>`;
    chip.querySelector('.remove-chip').addEventListener('click', () => {
      selectedShops.delete(name);
      // Сбросить активный класс у чипа в категории
      document.querySelectorAll('#shop-categories .chip').forEach(c => {
        if (c.dataset.value === name) c.classList.remove('active');
      });
      renderSelectedChips();
      updateSearchBtn();
    });
    container.appendChild(chip);
  });
}

function clearAllShops() {
  selectedShops.clear();
  // Убираем активный класс со всех чипов в категориях
  document.querySelectorAll('#shop-categories .chip').forEach(chip => {
    chip.classList.remove('active');
  });
  renderSelectedChips();
  updateSearchBtn();
}

// ------------------------------------- Start-point helpers -------------------------------------
function setStartPoint(lat, lon, label) {
  startPoint = { lat, lon, label: label || `${lat.toFixed(5)}, ${lon.toFixed(5)}` };

  if (startPlacemark) myMap.geoObjects.remove(startPlacemark);
  startPlacemark = new ymaps.Placemark([lat, lon], { hintContent: 'Старт', balloonContent: startPoint.label }, {
    preset: 'islands#redDotIconWithCaption',
    iconCaptionMaxWidth: '150',
  });
  myMap.geoObjects.add(startPlacemark);
  myMap.panTo([lat, lon], { flying: true });

  document.getElementById('start-coords-label').textContent = startPoint.label;
  document.getElementById('start-info').classList.remove('hidden');
  updateSearchBtn();
}

function useGeolocation() {
  clearError();
  if (!navigator.geolocation) { showError('Геолокация не поддерживается браузером.'); return; }
  navigator.geolocation.getCurrentPosition(
    pos => setStartPoint(pos.coords.latitude, pos.coords.longitude, 'Моё местоположение'),
    err => showError('Не удалось получить геолокацию: ' + err.message),
    { timeout: 8000 }
  );
}

function toggleMapPick() {
  const btn = document.getElementById('map-click-btn');
  pickingFromMap = !pickingFromMap;
  btn.classList.toggle('active', pickingFromMap);
  btn.textContent = pickingFromMap ? '✅ Кликните на карте…' : '🖱 Кликнуть на карте';
  myMap.cursors.push(pickingFromMap ? 'crosshair' : 'default');
}

function onMapClick(e) {
  if (!pickingFromMap) return;
  const [lat, lon] = e.get('coords');
  setStartPoint(lat, lon, `Выбрано на карте (${lat.toFixed(4)}, ${lon.toFixed(4)})`);
  toggleMapPick();   // turn off picking mode
}

function updateSearchBtn() {
  const btn = document.getElementById('search-btn');
  btn.disabled = !(startPoint && selectedShops.size > 0);
}

// ------------------------------------- Main flow: search → TSP → display -------------------------------------
async function runSearch() {
  let currentPoints = null;
  clearError();
  clearRoutes();
  document.getElementById('results-section').classList.add('hidden');
  showSpinner(true);
  const radiusKm = parseFloat(document.getElementById('radius-slider').value);
  const radius = radiusKm * 1000; // в метрах

  try {
    // 1️⃣ Find shops near start
    const searchResp = await fetchJSON('/api/search-shops', {
      shops:  [...selectedShops],
      origin: { lat: startPoint.lat, lon: startPoint.lon },
      radius: radius,
    });

    // Collect all found shop locations (take best hit per query)
    const points = [{ lat: startPoint.lat, lon: startPoint.lon, label: startPoint.label }];
    const notFound = [];

    for (const shopResult of searchResp.shops) {
      if (shopResult.results && shopResult.results.length > 0) {
        const best = shopResult.results[0];
        points.push({ lat: best.lat, lon: best.lon, label: `${shopResult.query}: ${best.name}` });
      } else {
        notFound.push(shopResult.query);
      }
    }
    currentPoints = points; // сохраняем

    if (points.length < 2) {
      showSpinner(false);
      showError('Ни один из выбранных магазинов не найден в радиусе 10 км.');
      return;
    }

    if (notFound.length) {
      showError(`Не найдено: ${notFound.join(', ')}. Маршрут строится для найденных.`);
    }

    // 2️⃣ Solve TSP on backend
    const tspResp = await fetchJSON('/api/solve-tsp', { points });
    tspResults = tspResp.results;

    function formatOrder(order, points) {
      return order.map(idx => points[idx].label).join(' → ');
    }

    const orders = tspResults.map(res => formatOrder(res.order, currentPoints));
    const allSame = orders.every((val, i, arr) => val === arr[0]);
    const summaryDiv = document.getElementById('route-order-summary');

    if (allSame) {
      summaryDiv.innerHTML = `<strong>Порядок посещения:</strong> ${orders[0]}`;
    } else {
      summaryDiv.innerHTML = `<strong>Порядок посещения (различается для видов транспорта):</strong><br>
        🚗 Авто: ${orders[0]}<br>
        🚶 Пешком: ${orders[1]}<br>
        🚌 Транспорт: ${orders[2]}`;
    }

    // 4️⃣ Render results table
    renderResultsTable(tspResults);
    document.getElementById('results-section').classList.remove('hidden');

    // 3️⃣ Draw all 6 routes on the map
    await drawAllRoutes(tspResults);

  } catch (err) {
    showError('Ошибка: ' + err.message);
  } finally {
    showSpinner(false);
  }
}

// ------------------------------------- Route drawing -------------------------------------
async function drawAllRoutes(results) {
  clearRoutes();
  renderLegend();

  const drawPromises = results.map((res, idx) => {
    return new Promise(resolve => {
      const style = ROUTE_STYLES[idx];
      const routingMode = res.mode === 'masstransit' ? 'masstransit' : res.mode;

      const waypoints = res.waypoints.map(p => [p.lat, p.lon]);

      const multiRoute = new ymaps.multiRouter.MultiRoute(
        {
          referencePoints: waypoints,
          params: { routingMode },
        },
        {
          boundsAutoApply: idx === 0,
          routeActiveStrokeColor:  style.color,
          routeActiveStrokeWidth:  style.strokeWidth,
          routeStrokeColor:        style.color + '55',
          wayPointStartIconColor:  style.color,
          wayPointFinishIconColor: style.color,
          waypointIconLayout:      'default#image',
          pinVisible:              idx === 0,
        }
      );

      multiRoute.model.events.once('requestsuccess', () => {
        const activeRoute = multiRoute.getActiveRoute();
        if (activeRoute) {
          const props = activeRoute.properties.getAll();
          // Сохраняем реальные значения
          results[idx]._actualDistance = props.distance ? props.distance.text : '—';
          results[idx]._actualDuration = props.duration ? props.duration.text : '—';
          updateTableRow(idx, results[idx]);
        }
        resolve();
      });

      multiRoute.model.events.once('requestfail', () => resolve());

      myMap.geoObjects.add(multiRoute);
      activeRoutes.push(multiRoute);
    });
  });

  await Promise.race([
    Promise.all(drawPromises),
    new Promise(r => setTimeout(r, 10_000)),
  ]);
}

// ------------------------------------- Results table -------------------------------------
function renderResultsTable(results) {
  const tbody = document.getElementById('results-body');
  tbody.innerHTML = '';

  results.forEach((res, idx) => {
    const style = ROUTE_STYLES[idx];
    const tr = document.createElement('tr');
    tr.id = `row-${idx}`;
    tr.innerHTML = `
      <td><span class="badge-mode mode-${res.mode}">${MODE_LABEL[res.mode]}</span></td>
      <td id="dist-${idx}">—</td>
      <td id="time-${idx}">—</td>
      <td>
        <button class="btn-show-route" data-idx="${idx}"
          style="background:${style.color}" onclick="focusRoute(${idx})">
          Показать
        </button>
       </td>`;
    tbody.appendChild(tr);
  });
}

function updateTableRow(idx, res) {
  const distEl = document.getElementById(`dist-${idx}`);
  const timeEl = document.getElementById(`time-${idx}`);
  if (distEl) distEl.textContent = res._actualDistance || '—';
  if (timeEl) timeEl.textContent = res._actualDuration || '—';
}

function focusRoute(idx) {
  // Bring chosen route to front by hiding others temporarily
  activeRoutes.forEach((r, i) => {
    r.options.set('routeActiveStrokeWidth', i === idx ? 6 : 2);
    r.options.set('routeActiveStrokeColor', i === idx ? ROUTE_STYLES[i].color : ROUTE_STYLES[i].color + '44');
  });
  if (activeRoutes[idx]) {
    const route = activeRoutes[idx].getActiveRoute();
    if (route) myMap.setBounds(route.getBounds(), { checkZoomRange: true, duration: 400 });
  }
}

// ------------------------------------- Legend -------------------------------------
function renderLegend() {
  const legend = document.getElementById('legend');
  legend.innerHTML = '';
  ROUTE_STYLES.forEach((s, i) => {
    const item = document.createElement('div');
    item.className = 'legend-item';
    item.innerHTML = `
      <div class="legend-color" style="background:${s.color}; width:24px; height:4px;"></div>
      <span>${MODE_LABEL[s.mode]}</span>`;
    legend.appendChild(item);
  });
}

// ------------------------------------- Utilities -------------------------------------
async function fetchJSON(url, body) {
  const resp = await fetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    throw new Error(err.error || resp.statusText);
  }
  return resp.json();
}

function showSpinner(on) {
  document.getElementById('spinner').classList.toggle('hidden', !on);
  document.getElementById('search-btn').disabled = on;
}

function showError(msg) {
  const box = document.getElementById('error-box');
  box.textContent = msg;
  box.classList.remove('hidden');
}

function clearError() {
  document.getElementById('error-box').classList.add('hidden');
}

function clearRoutes() {
  activeRoutes.forEach(r => myMap.geoObjects.remove(r));
  activeRoutes = [];
}