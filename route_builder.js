/* ════════════════════════════════════════════════════════════════════
 * route_builder.js — Route Builder con Drag & Drop + AI Chat (staging)
 *
 * Se inyecta en staging.html por build_map_v6.py, DESPUÉS del JS de v5.
 * Reemplaza la experiencia de toggleAssignPanel() con un panel de 3
 * columnas (prioridades | mapa | hunter lanes) + chat con Claude.
 *
 * Reutiliza del mapa v5 (sin modificarlo):
 *   HUNTER_DATA, HUNTERS_LIST, window.THE_MAP, _assignments,
 *   _hunterColorMap, _ASSIGN_COLORS, getISOWeek(), weekLabel(),
 *   saveAssignmentsToStorage(), generateHunterLinks()
 *
 * Backend opcional (api_server.py): GET/POST {API}/api/assignments,
 * POST {API}/api/chat. Sin backend → localStorage + export CSV.
 * ════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ── Config ────────────────────────────────────────────────────────
  function apiUrl() {
    return (localStorage.getItem('rb_api_url') ||
            (window.RB_CONFIG && window.RB_CONFIG.apiUrl) || '').replace(/\/$/, '');
  }

  var ZONES = [];            // [{hex_id, hex_code, zona, tier, rank, gap, demanda_dia, usuarios, combined_score, lat, lng}]
  var ZONE_BY_ID = {};
  var _hunterById = {};      // {user_id: {id, nombre, apellido, email}}
  var openLanes = [];        // user_ids con lane abierta
  var dbAssigned = {};       // hex_id → true (asignación que vino de la BD)
  var rbLayers = null;       // L.layerGroup con hexes asignados + rutas
  var hoverLayer = null;
  var chatHistory = [];      // [{role, content}]
  var curFilter = 'all', curQuery = '';
  var _currentWeek = '';     // semana ISO activa en el Route Builder (ej. "2026-W25")

  // ISO week string para cualquier fecha
  function isoWeekOf(d) {
    var tmp = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    var dow = tmp.getUTCDay() || 7;
    tmp.setUTCDate(tmp.getUTCDate() + 4 - dow);
    var yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1));
    var w = Math.ceil((((tmp - yearStart) / 86400000) + 1) / 7);
    return tmp.getUTCFullYear() + '-W' + String(w).padStart(2, '0');
  }

  // Lunes de la semana ISO dada (ej. "2026-W25" → Date)
  function mondayOf(isoWeek) {
    var m = isoWeek.match(/^(\d{4})-W(\d{2})$/);
    if (!m) return new Date();
    var year = parseInt(m[1]), week = parseInt(m[2]);
    var jan4 = new Date(year, 0, 4);
    var monday = new Date(jan4);
    monday.setDate(jan4.getDate() - (jan4.getDay() || 7) + 1 + (week - 1) * 7);
    return monday;
  }

  // "2026-W24" → "16-20 Jun 2026"
  var MESES = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
  function weekLabel(isoWeek) {
    var mon = mondayOf(isoWeek);
    var fri = new Date(mon); fri.setDate(mon.getDate() + 4);
    var label = mon.getDate();
    if (mon.getMonth() !== fri.getMonth()) label += ' ' + MESES[mon.getMonth()];
    return label + '-' + fri.getDate() + ' ' + MESES[fri.getMonth()] + ' ' + fri.getFullYear();
  }

  function changeWeek(delta) {
    var mon = mondayOf(_currentWeek);
    mon.setDate(mon.getDate() + delta * 7);
    _currentWeek = isoWeekOf(mon);
    var wk = document.getElementById('rb-week');
    if (wk) wk.textContent = weekLabel(_currentWeek);
    // Limpiar estado y recargar desde DB para la nueva semana
    window._assignments = {};
    dbAssigned = {};
    renderAll();
    loadFromDB();
  }

  // ── Modelo de zonas desde HUNTER_DATA ─────────────────────────────
  function buildZones() {
    if (!window.HUNTER_DATA || !HUNTER_DATA.features) return;
    ZONES = HUNTER_DATA.features.map(function (f) {
      var p = f.properties;
      var tier = (p.zona || '').trim().charAt(0).toUpperCase();   // 'A','B','C',...
      return {
        hex_id: p.hex_id, hex_code: p.hex_code || '', zona: p.zona || '',
        tier: tier, rank: p.rank, gap: p.gap || 0,
        demanda_dia: p.demanda_dia || 0, usuarios: p.usuarios || 0,
        combined_score: p.combined_score || 0,
        lat: p.lat, lng: p.lng,
      };
    }).sort(function (a, b) { return b.combined_score - a.combined_score; });
    ZONE_BY_ID = {};
    ZONES.forEach(function (z) { ZONE_BY_ID[z.hex_id] = z; });
  }

  // ── Malla completa: cualquier hex de HUNTER_GRID_IDS es asignable ─
  function gridCodeOf(hexId) {
    var ids = window.HUNTER_GRID_IDS;
    if (!ids) return null;
    var lo = 0, hi = ids.length - 1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (ids[mid] === hexId) return 'HEX-' + String(mid + 1).padStart(5, '0');
      ids[mid] < hexId ? (lo = mid + 1) : (hi = mid - 1);
    }
    return null;
  }

  function gridIdByCode(code) {
    var m = String(code).toUpperCase().match(/^HEX-?0*(\d{1,5})$/);
    if (!m || !window.HUNTER_GRID_IDS) return null;
    return window.HUNTER_GRID_IDS[parseInt(m[1], 10) - 1] || null;
  }

  function ensureZone(hexId) {
    if (ZONE_BY_ID[hexId]) return ZONE_BY_ID[hexId];
    if (!window.h3 || !window.h3.cellToLatLng) return null;
    var code = gridCodeOf(hexId);
    if (!code) return null;                      // fuera de CDMX+Edomex
    var c = window.h3.cellToLatLng(hexId);
    var z = {
      hex_id: hexId, hex_code: code, zona: 'S Sin señal', tier: 'S',
      rank: 0, gap: 0, demanda_dia: 0, usuarios: 0, combined_score: 0,
      lat: c[0], lng: c[1], empty: true,
    };
    ZONE_BY_ID[hexId] = z;
    return z;
  }

  function featureOf(hexId) {
    var f = HUNTER_DATA.features.find(function (x) { return x.properties.hex_id === hexId; });
    if (f || !window.h3) return f;
    var b = window.h3.cellToBoundary(hexId);
    b.push(b[0]);
    return {
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [b.map(function (p) { return [p[1], p[0]]; })] },
      properties: { hex_id: hexId },
    };
  }

  function assignedHunterOf(hexId) {
    var hunters = Object.keys(window._assignments || {});
    for (var i = 0; i < hunters.length; i++) {
      var list = _assignments[hunters[i]];
      if (list && list.some(function (z) { return z.hex_id === hexId; })) return hunters[i];
    }
    return null;
  }

  function _displayName(uid) {
    var h = _hunterById[uid];
    return h ? h.nombre : String(uid);
  }

  function _applyHunters(list) {
    _hunterById = {};
    (list || []).forEach(function (h) { _hunterById[String(h.id)] = h; });
    window.HUNTERS_LIST = Object.values(_hunterById).map(function (h) { return h.nombre; });
    renderHunterPicker();
  }

  function loadHunters() {
    // Fallback inmediato desde datos bakeados en el HTML
    var baked = window.RB_CONFIG && window.RB_CONFIG.hunters;
    if (baked && baked.length) { _applyHunters(baked); }

    // Si hay API pública, refresca desde la BD (puede haber cambios desde el último build)
    var api = apiUrl();
    if (!api) return;
    fetch(api + '/api/hunters')
      .then(function (r) { return r.json(); })
      .then(function (data) { _applyHunters(data.hunters); })
      .catch(function () {});
  }

  function hunterColor(h) {
    if (!window._hunterColorMap[h]) {
      var keys = Object.keys(_hunterById);
      var i = keys.indexOf(String(h));
      _hunterColorMap[h] = _ASSIGN_COLORS[(i >= 0 ? i : Object.keys(_hunterColorMap).length) % _ASSIGN_COLORS.length];
    }
    return _hunterColorMap[h];
  }

  // ── Estado → mapa (capas propias, no las de v5) ───────────────────
  function syncMap() {
    if (!window.THE_MAP) return;
    if (rbLayers) { THE_MAP.removeLayer(rbLayers); }
    rbLayers = L.layerGroup();
    Object.keys(_assignments).forEach(function (h) {
      var color = hunterColor(h);
      var zones = _assignments[h];
      zones.forEach(function (z, i) {
        var f = featureOf(z.hex_id);
        if (!f) return;
        rbLayers.addLayer(L.geoJSON(f, {
          pane: 'heatHexPane',
          style: {
            color: color, weight: dbAssigned[z.hex_id] ? 2 : 3,
            dashArray: dbAssigned[z.hex_id] ? '5 4' : null,
            fillColor: color, fillOpacity: 0.45, opacity: 0.9,
          },
        }).bindTooltip('<b style="color:' + color + '">' + _displayName(h) + '</b> · Parada #' + (i + 1) +
                       '<br>' + (z.hex_code || z.hex_id) + ' · Gap ' + z.gap, { sticky: true }));
        rbLayers.addLayer(L.marker([z.lat, z.lng], {
          icon: L.divIcon({
            className: '', iconSize: [18, 18], iconAnchor: [9, 9],
            html: '<div style="background:' + color + ';color:#fff;border-radius:50%;width:18px;height:18px;' +
                  'display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;' +
                  'border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.5)">' + (i + 1) + '</div>',
          }),
        }));
      });
      if (zones.length >= 2) {
        rbLayers.addLayer(L.polyline(zones.map(function (z) { return [z.lat, z.lng]; }),
          { color: color, weight: 2.5, opacity: 0.7, dashArray: '8 4', lineJoin: 'round' }));
      }
    });
    rbLayers.addTo(THE_MAP);
    if (typeof saveAssignmentsToStorage === 'function') saveAssignmentsToStorage();
  }

  // ── Mutaciones de estado ──────────────────────────────────────────
  function assignZone(hexId, hunter, index) {
    var z = ensureZone(hexId);
    if (!z) return;
    unassignZone(hexId, true);
    if (!_assignments[hunter]) _assignments[hunter] = [];
    var entry = {
      hex_id: z.hex_id, hex_code: z.hex_code, rank: z.rank, zona: z.zona, gap: z.gap,
      lat: z.lat, lng: z.lng, demanda_dia: z.demanda_dia, usuarios: z.usuarios,
      combined_score: z.combined_score,
      week: _currentWeek,
      days: [],
    };
    if (index === undefined || index < 0 || index > _assignments[hunter].length) {
      _assignments[hunter].push(entry);
    } else {
      _assignments[hunter].splice(index, 0, entry);
    }
    renderAll();
  }

  function unassignZone(hexId, skipRender) {
    Object.keys(_assignments).forEach(function (h) {
      _assignments[h] = _assignments[h].filter(function (z) { return z.hex_id !== hexId; });
      if (!_assignments[h].length) delete _assignments[h];
    });
    delete dbAssigned[hexId];
    if (!skipRender) renderAll();
  }

  function reorderLane(hunter, orderedIds) {
    var list = _assignments[hunter] || [];
    var byId = {};
    list.forEach(function (z) { byId[z.hex_id] = z; });
    _assignments[hunter] = orderedIds.map(function (id) { return byId[id]; }).filter(Boolean);
    renderAll();
  }

  function toggleZoneDay(hexId, hunter, day) {
    if (!_assignments[hunter]) return;
    var z = _assignments[hunter].find(function (z) { return z.hex_id === hexId; });
    if (!z) return;
    if (!z.days) z.days = z.day_of_week ? [z.day_of_week] : [];
    var d = parseInt(day, 10);
    var idx = z.days.indexOf(d);
    if (idx >= 0) z.days.splice(idx, 1);
    else z.days.push(d);
    if (typeof saveAssignmentsToStorage === 'function') saveAssignmentsToStorage();
    renderLanes();
  }

  function renderAll() {
    renderPriorityList();
    renderLanes();
    syncMap();
    if (typeof updateAssignedSummary === 'function') { try { updateAssignedSummary(); } catch (e) {} }
  }

  // ── Drag & drop de paneles ────────────────────────────────────────
  function makeDraggable(panel, handle) {
    handle.style.cursor = 'grab';
    var startX, startY, startL, startT;
    handle.addEventListener('mousedown', function (e) {
      if (e.target.tagName === 'BUTTON') return; // no interferir con botones
      e.preventDefault();
      var rect = panel.getBoundingClientRect();
      // Convertir posición actual a left/top+height fijos (sin bottom, el panel perdería su altura acotada)
      panel.style.right = 'auto';
      panel.style.bottom = 'auto';
      panel.style.height = rect.height + 'px';
      panel.style.left = rect.left + 'px';
      panel.style.top  = rect.top  + 'px';
      startX = e.clientX; startY = e.clientY;
      startL = rect.left; startT = rect.top;
      handle.style.cursor = 'grabbing';
      function onMove(e) {
        panel.style.left = Math.max(0, startL + e.clientX - startX) + 'px';
        panel.style.top  = Math.max(0, startT + e.clientY - startY) + 'px';
      }
      function onUp() {
        handle.style.cursor = 'grab';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  // ── UI: contenedor + estilos ──────────────────────────────────────
  var CSS = [
    '#rb-left,#rb-right{position:fixed;top:64px;bottom:190px;z-index:1500;background:#0f172a;',
    '  border-radius:12px;box-shadow:0 6px 28px rgba(0,0,0,.55);font-family:system-ui,sans-serif;',
    '  display:none;flex-direction:column;overflow:hidden;color:#e2e8f0;}',
    '#rb-left{left:10px;width:300px}',
    '#rb-right{right:10px;width:320px}',
    '#rb-left.open,#rb-right.open{display:flex}',
    '.rb-head{background:#1e293b;padding:9px 12px;font-size:11px;font-weight:700;letter-spacing:.5px;',
    '  display:flex;justify-content:space-between;align-items:center;flex-shrink:0}',
    '.rb-body{flex:1;overflow-y:auto!important;padding:8px}',
    '.rb-filters{display:flex;gap:4px;padding:8px 8px 0;flex-wrap:wrap;flex-shrink:0}',
    '.rb-fbtn{font-size:9px;padding:3px 8px;border-radius:10px;border:1px solid #334155;background:none;',
    '  color:#94a3b8;cursor:pointer}',
    '.rb-fbtn.on{background:#f97316;border-color:#f97316;color:#fff;font-weight:700}',
    '#rb-search{margin:8px;padding:5px 8px;background:#1e293b;border:1px solid #334155;border-radius:6px;',
    '  color:#e2e8f0;font-size:11px;flex-shrink:0}',
    '.rb-card{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:6px 8px;margin-bottom:5px;',
    '  cursor:grab;font-size:10px;display:flex;align-items:center;gap:6px}',
    '.rb-card:active{cursor:grabbing}',
    '.rb-card .tier{font-size:9px;font-weight:800;padding:1px 5px;border-radius:4px;flex-shrink:0}',
    '.rb-card .hx{font-family:monospace;font-weight:700;color:#7dd3fc}',
    '.rb-card .meta{color:#94a3b8;font-size:9px;flex:1}',
    '.rb-card .who{font-size:9px;font-weight:700}',
    '.rb-card.assigned{opacity:.55}',
    '.rb-lane{border:1.5px dashed #334155;border-radius:10px;margin-bottom:10px;overflow:hidden}',
    '.rb-lane-head{padding:6px 9px;font-size:11px;font-weight:700;display:flex;align-items:center;gap:6px}',
    '.rb-lane-zones{min-height:34px;padding:5px}',
    '.rb-lane-zones .rb-card{cursor:grab;margin-bottom:4px}',
    '.rb-card.fromdb{border-style:dashed;border-width:1.5px}',
    '.rb-ord{background:#334155;color:#fff;border-radius:50%;width:16px;height:16px;display:flex;',
    '  align-items:center;justify-content:center;font-size:9px;font-weight:700;flex-shrink:0}',
    '.rb-lane-btns{display:flex;gap:5px;padding:5px 8px 8px}',
    '.rb-lane-btns button,.rb-hbtn{font-size:9px;padding:3px 8px;border-radius:5px;border:1px solid #334155;',
    '  background:none;color:#94a3b8;cursor:pointer}',
    '.rb-lane-btns button:hover,.rb-hbtn:hover{background:#1e3a52}',
    '.rb-lane-zones.rb-drop-over{background:rgba(249,115,22,.13);outline:1.5px dashed #f97316;outline-offset:-2px;border-radius:6px}',
    '#rb-map-drag-badge{position:fixed;z-index:9999;display:none;align-items:center;gap:5px;',
    '  background:#0f172a;border:1.5px solid #f97316;border-radius:8px;padding:5px 11px;',
    '  font-size:10px;font-weight:700;cursor:grab;user-select:none;white-space:nowrap;',
    '  box-shadow:0 2px 10px rgba(0,0,0,.6);pointer-events:auto;transition:opacity .08s}',
    '#rb-chat{position:fixed;left:50%;transform:translateX(-50%);bottom:12px;width:min(680px,90vw);z-index:1500;',
    '  background:#0f172a;border-radius:12px;box-shadow:0 6px 28px rgba(0,0,0,.55);display:none;',
    '  flex-direction:column;font-family:system-ui,sans-serif;max-height:170px;color:#e2e8f0}',
    '#rb-chat.open{display:flex}',
    '#rb-chat-msgs{flex:1;overflow-y:auto;padding:8px 12px;font-size:11px;min-height:60px}',
    '.rb-msg{margin-bottom:7px;line-height:1.45;white-space:pre-wrap}',
    '.rb-msg.user{color:#7dd3fc}.rb-msg.ai{color:#e2e8f0}.rb-msg.sys{color:#64748b;font-size:10px}',
    '.rb-action-btn{display:inline-block;margin:2px 4px 2px 0;font-size:10px;padding:3px 9px;border-radius:6px;',
    '  border:1px solid #16a34a;background:#052e16;color:#4ade80;cursor:pointer;font-weight:600}',
    '#rb-chat-bar{display:flex;gap:6px;padding:8px 10px;border-top:1px solid #1e293b;flex-shrink:0}',
    '#rb-chat-input{flex:1;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#e2e8f0;',
    '  font-size:11px;padding:6px 9px}',
    '#rb-chat-send{background:#0f4c81;color:#fff;border:none;border-radius:6px;padding:6px 14px;',
    '  cursor:pointer;font-size:11px;font-weight:600}',
    '.rb-week-nav{background:none;border:1px solid #334155;color:#94a3b8;border-radius:4px;',
    '  padding:1px 6px;cursor:pointer;font-size:12px;line-height:1}',
    '.rb-week-nav:hover{background:#1e3a52;color:#e2e8f0}',
  ].join('\n');

  var TIER_BADGE = {
    'A': 'background:#450a0a;color:#f87171', 'B': 'background:#431407;color:#fb923c',
    'C': 'background:#422006;color:#fbbf24', 'D': 'background:#052e16;color:#4ade80',
    'E': 'background:#1e293b;color:#94a3b8', 'S': 'background:#1e293b;color:#94a3b8',
  };

  function buildUI() {
    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    var left = document.createElement('div');
    left.id = 'rb-left';
    left.innerHTML =
      '<div class="rb-head"><span>📋 PRIORIDADES <span id="rb-zcount" style="color:#f97316"></span></span>' +
      '<button onclick="window.toggleRouteBuilder()" style="background:none;border:none;color:#fff;cursor:pointer">✕</button></div>' +
      '<div class="rb-filters">' +
      '<button class="rb-fbtn on" data-f="all">Todas</button>' +
      '<button class="rb-fbtn" data-f="free">Sin asignar</button>' +
      '<button class="rb-fbtn" data-f="A">Tier A</button>' +
      '<button class="rb-fbtn" data-f="B">Tier B</button></div>' +
      '<input id="rb-search" placeholder="🔍 Buscar HEX-0042 o zona…">' +
      '<div class="rb-body"><div id="rb-priority-list"></div></div>';
    document.body.appendChild(left);

    var right = document.createElement('div');
    right.id = 'rb-right';
    right.innerHTML =
      '<div class="rb-head"><span style="display:flex;align-items:center;gap:5px">🏃 HUNTER LANES' +
      ' <button class="rb-week-nav" id="rb-prev-week" title="Semana anterior">‹</button>' +
      ' <span id="rb-week" style="font-family:monospace;color:#7dd3fc"></span>' +
      ' <button class="rb-week-nav" id="rb-next-week" title="Semana siguiente">›</button></span>' +
      '<button id="rb-cfg" title="Configurar API" style="background:none;border:none;color:#64748b;cursor:pointer">⚙</button></div>' +
      '<div id="rb-hunter-picker" style="padding:8px;display:flex;flex-wrap:wrap;gap:4px;flex-shrink:0;border-bottom:1px solid #1e293b"></div>' +
      '<div class="rb-body"><div id="rb-lanes"></div></div>' +
      '<div style="padding:8px;border-top:1px solid #1e293b;display:flex;gap:6px;flex-shrink:0">' +
      '<button id="rb-save-db" style="flex:1;background:#14532d;color:#86efac;border:none;border-radius:6px;' +
      'padding:7px;cursor:pointer;font-size:11px;font-weight:700">💾 Guardar asignación en DB</button>' +
      '<button id="rb-export" class="rb-hbtn" title="Exportar CSV">⬇ CSV</button></div>';
    document.body.appendChild(right);

    var chat = document.createElement('div');
    chat.id = 'rb-chat';
    chat.innerHTML =
      '<div id="rb-chat-msgs"><div class="rb-msg sys">🤖 Asistente de rutas — pregunta p.ej. ' +
      '"Agrupa las zonas sin asignar en rutas de máx 8 hexes contiguos" o "Sugiere la mejor ruta para Anel".</div></div>' +
      '<div id="rb-chat-bar"><input id="rb-chat-input" placeholder="Pregunta al asistente de rutas…">' +
      '<button id="rb-chat-send">Enviar</button></div>';
    document.body.appendChild(chat);

    // Eventos
    left.querySelectorAll('.rb-fbtn').forEach(function (b) {
      b.onclick = function () {
        left.querySelectorAll('.rb-fbtn').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
        curFilter = b.getAttribute('data-f');
        renderPriorityList();
      };
    });
    document.getElementById('rb-search').oninput = function () {
      curQuery = this.value.trim().toLowerCase(); renderPriorityList();
    };
    document.getElementById('rb-save-db').onclick = saveToDB;
    document.getElementById('rb-export').onclick = function () {
      if (typeof exportAssignments === 'function') exportAssignments();
    };
    document.getElementById('rb-cfg').onclick = function () {
      var cur = apiUrl();
      var v = prompt('URL del API server (vacío = solo localStorage):', cur);
      if (v !== null) { localStorage.setItem('rb_api_url', v.trim()); chatSys('API configurado: ' + (v.trim() || '(ninguno)')); }
    };
    document.getElementById('rb-prev-week').onclick = function () { changeWeek(-1); };
    document.getElementById('rb-next-week').onclick = function () { changeWeek(1); };
    makeDraggable(right, right.querySelector('.rb-head'));
    document.getElementById('rb-chat-send').onclick = sendChat;
    document.getElementById('rb-chat-input').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') sendChat();
    });

    renderHunterPicker();
  }

  // ── Columna izquierda ─────────────────────────────────────────────
  function cardHTML(z, opts) {
    var who = assignedHunterOf(z.hex_id);
    var tier = z.tier || 'S';
    var badge = TIER_BADGE[tier] || TIER_BADGE.E;
    var dot = tier === 'A' ? '🔴' : tier === 'B' ? '🟠' : tier === 'C' ? '🟡' : tier === 'D' ? '🟢' : '';
    return '<span class="tier" style="' + badge + '">' + dot + tier + '</span>' +
      '<span class="hx">' + (z.hex_code || z.hex_id.slice(-4)) + '</span>' +
      '<span class="meta">Dem ' + z.demanda_dia + '/d · Gap ' + z.gap + ' · 👥' + z.usuarios + '</span>' +
      (opts && opts.order ? '' :
        (who ? '<span class="who" style="color:' + hunterColor(who) + '">' + who + '</span>' : ''));
  }

  function renderPriorityList() {
    var el = document.getElementById('rb-priority-list');
    if (!el) return;
    var rows = ZONES.filter(function (z) {
      var who = assignedHunterOf(z.hex_id);
      if (curFilter === 'free' && who) return false;
      if ((curFilter === 'A' || curFilter === 'B') && z.tier !== curFilter) return false;
      if (curQuery) {
        var hay = ((z.hex_code || '') + ' ' + z.zona + ' ' + z.hex_id).toLowerCase();
        if (hay.indexOf(curQuery) < 0) return false;
      }
      return true;
    });
    // Búsqueda por código de la malla completa: si no hay match entre las
    // zonas con señal, resolver el HEX-XXXXX contra HUNTER_GRID_IDS.
    if (!rows.length && curQuery) {
      var gid = gridIdByCode(curQuery);
      var gz = gid && ensureZone(gid);
      if (gz) rows = [gz];
    }
    document.getElementById('rb-zcount').textContent = rows.length;
    el.innerHTML = '';
    rows.slice(0, 250).forEach(function (z) {
      var d = document.createElement('div');
      d.className = 'rb-card' + (assignedHunterOf(z.hex_id) ? ' assigned' : '');
      d.setAttribute('data-hex', z.hex_id);
      d.innerHTML = cardHTML(z);
      d.style.cursor = 'pointer';
      d.onmouseenter = function () { highlightHex(z); };
      d.onmouseleave = clearHighlight;
      d.onclick = function () { zoomToHex(z); };
      el.appendChild(d);
    });
    if (window.Sortable) {
      if (el._sortable) el._sortable.destroy();
      el._sortable = Sortable.create(el, {
        group: { name: 'rb', pull: true, put: true },
        sort: false, animation: 120,
        onAdd: function (evt) {     // tarjeta arrastrada de una lane de vuelta → desasignar
          var hexId = evt.item.getAttribute('data-hex');
          unassignZone(hexId);
        },
        onEnd: function () {
          var body = document.querySelector('.rb-body');
          if (body) body.style.overflow = '';
        },
      });
    }
  }

  function highlightHex(z) {
    clearHighlight();
    var f = featureOf(z.hex_id);
    if (!f || !window.THE_MAP) return;
    hoverLayer = L.geoJSON(f, {
      pane: 'heatHexPane',
      style: { color: '#fde047', weight: 4, fillColor: '#fde047', fillOpacity: 0.35 },
    }).addTo(THE_MAP);
  }
  function clearHighlight() {
    if (hoverLayer && window.THE_MAP) { THE_MAP.removeLayer(hoverLayer); hoverLayer = null; }
  }
  function zoomToHex(z) {
    if (!window.THE_MAP || !z.lat || !z.lng) return;
    THE_MAP.setView([z.lat, z.lng], 15, { animate: true, duration: 0.4 });
    highlightHex(z);
  }

  // ── Columna derecha: picker + lanes ───────────────────────────────
  function renderHunterPicker() {
    var el = document.getElementById('rb-hunter-picker');
    if (!el) return;
    el.innerHTML = '';
    Object.keys(_hunterById).forEach(function (uid) {
      var h = _hunterById[uid];
      var lbl = document.createElement('label');
      lbl.style.cssText = 'display:flex;align-items:center;gap:3px;font-size:10px;color:#cbd5e1;cursor:pointer;' +
        'border:1px solid #334155;border-radius:10px;padding:2px 8px';
      var on = openLanes.indexOf(uid) >= 0;
      lbl.innerHTML = '<input type="checkbox" style="margin:0"' + (on ? ' checked' : '') + '>' +
        '<span style="width:7px;height:7px;border-radius:50%;background:' + hunterColor(uid) + '"></span>' + h.nombre;
      lbl.querySelector('input').onchange = function () {
        if (this.checked) { if (openLanes.indexOf(uid) < 0) openLanes.push(uid); }
        else { openLanes = openLanes.filter(function (x) { return x !== uid; }); }
        renderLanes();
      };
      el.appendChild(lbl);
    });
  }

  function renderLanes() {
    var el = document.getElementById('rb-lanes');
    if (!el) return;
    el.innerHTML = '';
    // Abrir lanes automáticamente para hunters con asignaciones
    Object.keys(_assignments).forEach(function (h) {
      if (openLanes.indexOf(h) < 0) openLanes.push(h);
    });
    openLanes.forEach(function (h) {
      var color = hunterColor(h);
      var zones = _assignments[h] || [];
      var lane = document.createElement('div');
      lane.className = 'rb-lane';
      lane.style.borderColor = color;
      var DAY_LABELS_SHORT = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie'];
      var dayGroups = [[], [], [], [], [], []];
      zones.forEach(function (z) {
        var ds = z.days && z.days.length ? z.days : (z.day_of_week ? [z.day_of_week] : []);
        if (!ds.length) { dayGroups[0].push(z); return; }
        ds.forEach(function (d) { if (d >= 1 && d <= 5) dayGroups[d].push(z); });
      });
      var mapsBtns = '';
      for (var d = 1; d <= 5; d++) {
        if (dayGroups[d].length) {
          var dUrl = 'https://www.google.com/maps/dir/' +
            dayGroups[d].map(function (z) { return z.lat + ',' + z.lng; }).join('/');
          mapsBtns += '<button onclick="window.open(\'' + dUrl + '\',\'_blank\')">🗺 ' +
            DAY_LABELS_SHORT[d - 1] + '</button>';
        }
      }
      if (!mapsBtns && zones.length) {
        var allUrl = 'https://www.google.com/maps/dir/' +
          zones.map(function (z) { return z.lat + ',' + z.lng; }).join('/');
        mapsBtns = '<button onclick="window.open(\'' + allUrl + '\',\'_blank\')">🗺 Google Maps</button>';
      }
      var hName = _displayName(h);
      lane.innerHTML =
        '<div class="rb-lane-head" style="color:' + color + '">' +
        '<span style="width:9px;height:9px;border-radius:50%;background:' + color + '"></span>' +
        hName + ' <span style="color:#64748b;font-weight:400">· ' + zones.length + ' zonas</span></div>' +
        '<div class="rb-lane-zones" data-hunter="' + h + '"></div>' +
        '<div class="rb-lane-btns">' + mapsBtns +
        '<button class="rb-lane-share" data-h="' + h + '">🔗 Link hunter</button></div>';
      el.appendChild(lane);
      var zEl = lane.querySelector('.rb-lane-zones');
      zones.forEach(function (z, i) {
        var d = document.createElement('div');
        d.className = 'rb-card' + (dbAssigned[z.hex_id] ? ' fromdb' : '');
        if (dbAssigned[z.hex_id]) d.style.borderColor = color;
        d.setAttribute('data-hex', z.hex_id);
        var zz = ZONE_BY_ID[z.hex_id] || ensureZone(z.hex_id) || z;
        var DAY_KEYS = ['L', 'M', 'X', 'J', 'V'];
        var zDays = z.days && z.days.length ? z.days : (z.day_of_week ? [z.day_of_week] : []);
        var daySelHtml = '<span style="display:flex;gap:2px;margin-left:4px;flex-shrink:0">' +
          DAY_KEYS.map(function (dl, di) {
            var dn = di + 1;
            var active = zDays.indexOf(dn) >= 0;
            return '<button data-dayhex="' + z.hex_id + '" data-hunter="' + h + '" data-day="' + dn + '" ' +
              'style="font-size:8px;width:16px;height:16px;padding:0;border-radius:3px;cursor:pointer;border:1px solid ' +
              (active ? '#0ea5e9' : '#334155') + ';background:' + (active ? '#0ea5e9' : 'transparent') +
              ';color:' + (active ? '#fff' : '#64748b') + '" title="' + ['Lun','Mar','Mié','Jue','Vie'][di] + '">' +
              dl + '</button>';
          }).join('') + '</span>';
        d.innerHTML = '<span class="rb-ord" style="background:' + color + '">' + (i + 1) + '</span>' +
          cardHTML(zz, { order: true }) + daySelHtml +
          '<button data-rmhex="' + z.hex_id + '" style="margin-left:auto;flex-shrink:0;background:none;border:none;' +
          'color:#64748b;cursor:pointer;font-size:13px;padding:0 2px;line-height:1" title="Quitar zona">✕</button>';
        d.querySelector('[data-rmhex]').onclick = function (e) {
          e.stopPropagation();
          unassignZone(this.getAttribute('data-rmhex'));
        };
        d.querySelectorAll('[data-dayhex]').forEach(function (btn) {
          btn.onclick = function (e) {
            e.stopPropagation();
            toggleZoneDay(this.getAttribute('data-dayhex'), this.getAttribute('data-hunter'), this.getAttribute('data-day'));
          };
        });
        zEl.appendChild(d);
      });
      if (window.Sortable) {
        Sortable.create(zEl, {
          group: { name: 'rb', pull: true, put: true },
          animation: 120,
          onAdd: function (evt) {
            assignZone(evt.item.getAttribute('data-hex'), h, evt.newIndex);
          },
          onUpdate: function () {
            reorderLane(h, Array.prototype.map.call(zEl.children, function (c) {
              return c.getAttribute('data-hex');
            }));
          },
          onEnd: function () {
            var body = document.querySelector('.rb-body');
            if (body) body.style.overflow = '';
          },
        });
      }
    });
    el.querySelectorAll('.rb-lane-share').forEach(function (b) {
      b.onclick = function () {
        if (typeof generateHunterLinks === 'function') {
          generateHunterLinks();   // mantiene el flujo existente (links base64 por hunter)
          var c = document.getElementById('az-links-container');
          var btn = c && c.querySelector('.az-copy-link');
          chatSys(btn ? 'Links generados — usa el panel clásico (🗺) para copiarlos, o se copió el primero.' :
                        'No hay asignaciones para generar links.');
        }
      };
    });
  }

  // ── DB integration ────────────────────────────────────────────────
  function loadFromDB() {
    var api = apiUrl();
    if (!api) return;
    fetch(api + '/api/assignments?week=' + encodeURIComponent(_currentWeek))
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        var rows = data.assignments || [];
        if (!rows.length) return;
        rows.forEach(function (r) {
          if (!ZONE_BY_ID[r.hex_id]) return;
          var uid = String(r.user_id);
          assignZone(r.hex_id, uid, (r.route_order || 1) - 1);
          if (_assignments[uid]) {
            var z = _assignments[uid].find(function (z) { return z.hex_id === r.hex_id; });
            if (z && r.days) z.days = Array.isArray(r.days) ? r.days : [];
          }
          dbAssigned[r.hex_id] = true;
        });
        chatSys('📥 ' + rows.length + ' asignaciones cargadas de la BD.');
        renderAll();
      })
      .catch(function (e) { chatSys('⚠ No se pudo leer la BD (' + e.message + '). Verifica que el API esté corriendo.'); });
  }

  function saveToDB() {
    var api = apiUrl();
    var btn = document.getElementById('rb-save-db');
    if (!api) {
      chatSys('⚠ No hay API configurado (⚙). Configura la URL del servidor en ⚙ para guardar.');
      return;
    }
    var payload = { assigned_by: 'mapa-staging', week: _currentWeek, assignments: [] };
    Object.keys(_assignments).forEach(function (uid) {
      _assignments[uid].forEach(function (z, i) {
        payload.assignments.push({
          hex_id: z.hex_id, hex_code: z.hex_code || (ZONE_BY_ID[z.hex_id] || {}).hex_code || '',
          user_id: parseInt(uid, 10), route_order: i + 1, notes: z.zona || '',
          week: _currentWeek,
          days: z.days && z.days.length ? z.days : (z.day_of_week ? [z.day_of_week] : []),
        });
      });
    });
    btn.textContent = '⏳ Guardando…';
    fetch(api + '/api/assignments', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        btn.textContent = '✅ Guardado (' + (d.saved || payload.assignments.length) + ')';
        payload.assignments.forEach(function (a) { dbAssigned[a.hex_id] = true; });
        renderAll();
        setTimeout(function () { btn.textContent = '💾 Guardar asignación en DB'; }, 2500);
      })
      .catch(function (e) {
        btn.textContent = '❌ Error al guardar';
        chatSys('⚠ Error al guardar en BD (' + e.message + '). Verifica que el servidor esté corriendo.');
        setTimeout(function () { btn.textContent = '💾 Guardar asignación en DB'; }, 3500);
      });
  }

  // ── AI Chat ───────────────────────────────────────────────────────
  function chatSys(text) {
    var box = document.getElementById('rb-chat-msgs');
    if (!box) return;
    var d = document.createElement('div');
    d.className = 'rb-msg sys'; d.textContent = text;
    box.appendChild(d); box.scrollTop = box.scrollHeight;
  }

  function chatMsg(role, text, actions) {
    var box = document.getElementById('rb-chat-msgs');
    var d = document.createElement('div');
    d.className = 'rb-msg ' + role;
    d.textContent = (role === 'user' ? '🧑 ' : '🤖 ') + text;
    box.appendChild(d);
    (actions || []).forEach(function (a) {
      if (a.action !== 'assign') return;
      var hexId = a.hex_id ||
        (ZONES.find(function (z) { return z.hex_code === a.hex_code; }) || {}).hex_id ||
        gridIdByCode(a.hex_code);             // hexes sin señal de la malla completa
      if (!hexId || !ensureZone(hexId)) return;
      var b = document.createElement('button');
      b.className = 'rb-action-btn';
      var uid = String(a.user_id || a.hunter);
      b.textContent = '✅ Asignar ' + (a.hex_code || ZONE_BY_ID[hexId].hex_code || hexId) + ' → ' + _displayName(uid);
      b.onclick = function () {
        assignZone(hexId, uid, a.route_order ? a.route_order - 1 : undefined);
        if (openLanes.indexOf(uid) < 0) { openLanes.push(uid); renderLanes(); }
        b.textContent = '✔ Asignado'; b.disabled = true;
      };
      box.appendChild(b);
    });
    box.scrollTop = box.scrollHeight;
  }

  function buildChatContext() {
    var asg = {};
    Object.keys(_assignments).forEach(function (h) {
      asg[h] = _assignments[h].map(function (z, i) {
        return { orden: i + 1, hex_code: z.hex_code, hex_id: z.hex_id, zona: z.zona, gap: z.gap };
      });
    });
    return {
      semana: _currentWeek,
      hunters: Object.values(_hunterById).map(function (h) { return {id: h.id, nombre: h.nombre}; }),
      asignaciones: asg,
      zonas: ZONES.slice(0, 150).map(function (z) {
        return {
          hex_id: z.hex_id, hex_code: z.hex_code, tier: z.tier, rank: z.rank,
          score: z.combined_score, gap: z.gap, demanda_dia: z.demanda_dia,
          usuarios: z.usuarios, lat: z.lat, lng: z.lng,
          asignada_a: assignedHunterOf(z.hex_id),
        };
      }),
    };
  }

  function sendChat() {
    var input = document.getElementById('rb-chat-input');
    var msg = input.value.trim();
    if (!msg) return;
    input.value = '';
    chatMsg('user', msg);
    var api = apiUrl();
    if (!api) { chatSys('⚠ Configura la URL del API server con ⚙ para usar el chat.'); return; }
    chatHistory.push({ role: 'user', content: msg });
    chatSys('… pensando');
    fetch(api + '/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, history: chatHistory.slice(0, -1), context: buildChatContext() }),
    })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        var box = document.getElementById('rb-chat-msgs');
        var last = box.lastChild;
        if (last && last.textContent === '… pensando') box.removeChild(last);
        chatHistory.push({ role: 'assistant', content: d.reply || '' });
        if (chatHistory.length > 20) chatHistory = chatHistory.slice(-20);
        chatMsg('ai', d.reply || '(sin respuesta)', d.actions || []);
      })
      .catch(function (e) { chatSys('⚠ Error del chat: ' + e.message); });
  }

  // ── Toggle global (reemplaza toggleAssignPanel del botón 🗺) ──────
  var rbOpen = false;
  window.toggleRouteBuilder = function () {
    rbOpen = !rbOpen;
    document.getElementById('rb-left').classList.toggle('open', rbOpen);
    document.getElementById('rb-right').classList.toggle('open', rbOpen);
    document.getElementById('rb-chat').classList.toggle('open', rbOpen);
    var tb = document.getElementById('assign-tool-btn');
    if (tb) tb.classList.toggle('active', rbOpen);
    if (rbOpen) {
      // Asegurar capa Zonas Hunter + malla visibles para poder seleccionar
      if (window.THE_MAP && typeof window.toggleLayer === 'function') {
        var lyCb = document.getElementById('ly_hunter');
        if (lyCb && !lyCb.checked) { lyCb.checked = true; }
        window.toggleLayer('hunter', true);
      }
      // Inicializar semana activa (solo la primera vez que se abre)
      if (!_currentWeek) {
        _currentWeek = typeof getISOWeek === 'function' ? getISOWeek() : isoWeekOf(new Date());
      }
      var wk = document.getElementById('rb-week');
      if (wk) wk.textContent = weekLabel(_currentWeek);
      loadHunters();
      renderAll();
      loadFromDB();
    } else {
      clearHighlight();
    }
  };

  // ── Click en el mapa → asignar cualquier hex (con o sin señal) ────
  var _rbPopup = null;

  // Negocios activos en el hex + anillo-1 (para hexes fuera de HUNTER_DATA)
  function bizNearbyOf(cell) {
    if (!window.h3 || !window.h3.gridDisk || !window.BIZ_DATA) return null;
    var ring = {};
    window.h3.gridDisk(cell, 1).forEach(function (c) { ring[c] = true; });
    var n = 0;
    BIZ_DATA.features.forEach(function (f) {
      var c = f.geometry.coordinates;
      if (ring[window.h3.latLngToCell(c[1], c[0], 8)]) n++;
    });
    return n;
  }

  function indicatorsHTML(cell, z) {
    var f = HUNTER_DATA.features.find(function (x) { return x.properties.hex_id === cell; });
    var row = function (label, val) {
      return '<div style="display:flex;justify-content:space-between;gap:10px">' +
        '<span style="color:#94a3b8">' + label + '</span><span style="font-weight:600">' + val + '</span></div>';
    };
    var s = '<div style="font-size:10.5px;margin:5px 0;padding:5px 7px;background:rgba(148,163,184,.12);' +
            'border-radius:6px;line-height:1.55">';
    if (f) {
      var p = f.properties;
      var covPct = Math.min(100, Math.round(((p.neg_cercanos || 0) / 3) * 100));
      var cov = (p.neg_cercanos || 0) + '/3 opciones (' + covPct + '%)';
      s += row('Score', Math.round((p.combined_score || 0) * 100) + '/100') +
           row('👮 Sesiones PA', p.usuarios + (p.sin_compras ? ' (' + p.sin_compras + ' sin comprar)' : '')) +
           ((p.users_other || 0) > 0 ? row('👮 Otras orgs', (p.users_other || 0) + ' usuarios') : '') +
           row('📈 Conversión', (p.tasa_conv_pct || 0) + '%') +
           row('🏪 Oferta', p.neg_activos + ' en hex · ' + (p.neg_cercanos || 0) + ' cerca (~1km)') +
           row('Cobertura', cov) +
           (p.gap > 0 ? row('🎯 Faltantes', p.gap + ' negocio(s) (meta: 3)') : row('✅ Cubierta', '≥3 opciones cerca'));
    } else {
      var nb = bizNearbyOf(cell);
      s += row('👤 Sesiones', '0 — sin señal') +
           row('🏪 Oferta cercana', nb === null ? '—' : nb + '/3 negocios');
    }
    s += row('📍', z.lat.toFixed(5) + ', ' + z.lng.toFixed(5)) + '</div>';
    return s;
  }

  function showAssignPopup(cell, latlng) {
    var z = ensureZone(cell);
    if (!z || !_rbPopup) return;
    var who = assignedHunterOf(cell);
    var html = '<div style="font-family:system-ui;font-size:12px;min-width:210px">' +
      '<b style="font-family:monospace;color:#0f4c81">' + z.hex_code + '</b> ' +
      (z.empty ? '<span style="color:#94a3b8;font-size:10px">sin señal</span>'
               : '<span style="font-size:10px">' + z.zona + '</span>') +
      indicatorsHTML(cell, z);
    if (who) {
      html += '<div style="margin-top:4px">En la ruta de <b>' + _displayName(who) + '</b> ' +
        '<button onclick="window._rbUnassign(\'' + cell + '\')" style="margin-left:6px;font-size:10px;' +
        'padding:2px 8px;border:1px solid #dc2626;background:none;color:#dc2626;border-radius:4px;cursor:pointer">' +
        '✕ Quitar</button></div>';
    } else {
      html += '<div style="display:flex;gap:5px;margin-top:5px">' +
        '<select id="rb-pop-h" style="flex:1;font-size:11px;padding:3px">' +
        Object.keys(_hunterById).map(function (uid) {
          return '<option value="' + uid + '">' + _hunterById[uid].nombre + '</option>';
        }).join('') +
        '</select>' +
        '<button onclick="window._rbAssignFromPopup(\'' + cell + '\')" style="font-size:11px;padding:3px 10px;' +
        'background:#14532d;color:#86efac;border:none;border-radius:4px;cursor:pointer;font-weight:700">➕ Asignar</button></div>';
    }
    html += '</div>';
    _rbPopup.setLatLng(latlng).setContent(html).openOn(THE_MAP);
  }

  function setupMapClick() {
    if (!window.THE_MAP || !window.L) return;
    _rbPopup = L.popup({ maxWidth: 240 });
    // Handler único del click del mapa. Se expone como window._rbMapClick para
    // que el handler informativo de v5 delegue aquí (sin importar el orden en
    // que se registren); _lastClick deduplica si ambos llegan a dispararse.
    var _lastClick = 0;
    window._rbMapClick = function (e) {
      if (typeof _assignMode !== 'undefined' && _assignMode) return;
      if (!window.h3 || !window.h3.latLngToCell) return;
      var now = Date.now();
      if (now - _lastClick < 150) return;      // dedup v5 + propio
      _lastClick = now;
      var cell = window.h3.latLngToCell(e.latlng.lat, e.latlng.lng, 8);
      var z = ensureZone(cell);
      if (!z) return;                          // fuera de la malla CDMX+Edomex
      if (rbOpen) {
        showAssignPopup(cell, e.latlng);
      } else {
        // Route Builder cerrado: solo en hexes vacíos (las zonas con señal
        // conservan su popup original de coordenadas/dirección)
        if (!z.empty) return;
        _rbPopup.setLatLng(e.latlng).setContent(
          '<div style="font-family:system-ui;font-size:12px;min-width:210px">' +
          '<b style="font-family:monospace;color:#0f4c81">' + z.hex_code + '</b> ' +
          (z.empty ? '<span style="color:#94a3b8;font-size:10px">sin señal</span>' : '') +
          indicatorsHTML(cell, z) +
          '<button onclick="window._rbOpenAndAssign(\'' + cell + '\',' + e.latlng.lat + ',' + e.latlng.lng + ')" ' +
          'style="margin-top:5px;font-size:11px;padding:4px 10px;background:#0f4c81;color:#fff;border:none;' +
          'border-radius:5px;cursor:pointer;font-weight:600">🗺 Asignar a una ruta</button></div>'
        ).openOn(THE_MAP);
      }
    };
    THE_MAP.on('click', window._rbMapClick);
    window._rbAssignFromPopup = function (hexId) {
      var sel = document.getElementById('rb-pop-h');
      var uid = sel ? sel.value : null;
      if (!uid) return;
      if (openLanes.indexOf(uid) < 0) openLanes.push(uid);
      assignZone(hexId, uid);
      THE_MAP.closePopup();
    };
    window._rbUnassign = function (hexId) {
      unassignZone(hexId);
      THE_MAP.closePopup();
    };
    // Asignación rápida desde el tooltip de hover (no requiere Route Builder abierto)
    window._rbAssignZone = function (hexId, hunter) {
      if (!hunter) return;
      // hunter puede llegar como nombre (tooltip v5) o como user_id — normalizar a uid
      var uid = String(hunter);
      if (!_hunterById[uid]) {
        var found = Object.keys(_hunterById).find(function (id) {
          return _hunterById[id].nombre === hunter;
        });
        if (found) uid = found;
      }
      if (openLanes.indexOf(uid) < 0) openLanes.push(uid);
      assignZone(hexId, uid);
      THE_MAP.closeTooltip();
    };
    window._rbUnassignZone = function (hexId) {
      unassignZone(hexId);
      THE_MAP.closeTooltip();
    };
    // Consulta quién tiene asignada una zona (usado por el tooltip interactivo)
    window._rbGetAssignment = function (hexId) {
      return assignedHunterOf(hexId) || null;
    };
    window._rbOpenAndAssign = function (hexId, lat, lng) {
      if (!rbOpen) window.toggleRouteBuilder();
      showAssignPopup(hexId, L.latLng(lat, lng));
    };
  }

  // ── Drag directo de hex → Hunter Lane (Pointer Events) ───────────
  function setupMapDrag() {
    var ghost = document.createElement('div');
    ghost.id = 'rb-map-drag-badge';
    ghost.style.pointerEvents = 'none';  // ghost no bloquea elementFromPoint
    document.body.appendChild(ghost);

    // dragState: { hexId, zone, startX, startY, active, ready, holdTimer }
    // ready = long-press confirmado (350ms sin moverse); active = drag en movimiento
    var dragState = null;
    var TIER_COLOR = { A:'#f87171', B:'#fb923c', C:'#fbbf24', D:'#4ade80', S:'#94a3b8' };
    var HOLD_MS = 350;   // ms de hold para entrar en modo drag
    var MOVE_CANCEL = 6; // px de movimiento antes del hold que cancela el drag

    function startGhost(z, x, y) {
      var tc = TIER_COLOR[z.tier] || '#94a3b8';
      ghost.innerHTML =
        '<span style="color:' + tc + ';font-weight:800">' + z.tier + '</span>' +
        '<span style="color:#7dd3fc;font-family:monospace"> ' + (z.hex_code || z.hex_id.slice(-6)) + '</span>';
      ghost.style.display = 'flex';
      ghost.style.left = (x + 14) + 'px';
      ghost.style.top  = (y - 22) + 'px';
    }

    function clearDrag() {
      if (dragState) {
        clearTimeout(dragState.holdTimer);
        if (dragState.active && window.THE_MAP) THE_MAP.dragging.enable();
      }
      ghost.style.display = 'none';
      document.querySelectorAll('.rb-lane-zones.rb-drop-over').forEach(function (el) {
        el.classList.remove('rb-drop-over');
      });
      dragState = null;
    }

    function onPointerDown(e) {
      if (!rbOpen || e.button !== 0) return;
      if (!window.h3 || !window.THE_MAP) return;
      var mc = THE_MAP.getContainer();
      var rect = mc.getBoundingClientRect();
      var pt = L.point(e.clientX - rect.left, e.clientY - rect.top);
      var latlng = THE_MAP.containerPointToLatLng(pt);
      var cell = window.h3.latLngToCell(latlng.lat, latlng.lng, 8);
      var z = ZONE_BY_ID[cell] || ensureZone(cell);
      if (!z) return;
      var cmdDrag = e.metaKey || e.ctrlKey;  // ⌘ Mac / Ctrl Win
      dragState = { hexId: cell, zone: z, startX: e.clientX, startY: e.clientY,
                    active: false, ready: cmdDrag, holdTimer: null };
      if (!cmdDrag) {
        // Sin modificador: confirmar intención después de HOLD_MS sin moverse
        dragState.holdTimer = setTimeout(function () {
          if (dragState) dragState.ready = true;
        }, HOLD_MS);
      }
    }

    function onPointerMove(e) {
      if (!dragState) return;
      var dx = e.clientX - dragState.startX, dy = e.clientY - dragState.startY;
      // Movimiento antes del hold → es pan, dejar a Leaflet
      if (!dragState.ready) {
        if (Math.sqrt(dx * dx + dy * dy) > MOVE_CANCEL) {
          clearTimeout(dragState.holdTimer);
          dragState = null;
        }
        return;
      }
      // Long press confirmado: activar drag al mover más de 8px
      if (!dragState.active) {
        if (Math.sqrt(dx * dx + dy * dy) < 8) return;
        dragState.active = true;
        THE_MAP.dragging.disable();
        startGhost(dragState.zone, e.clientX, e.clientY);
      }
      ghost.style.left = (e.clientX + 14) + 'px';
      ghost.style.top  = (e.clientY - 22) + 'px';
      var under = document.elementFromPoint(e.clientX, e.clientY);
      document.querySelectorAll('.rb-lane-zones').forEach(function (el) {
        el.classList.toggle('rb-drop-over', el === under || el.contains(under));
      });
    }

    function onPointerUp(e) {
      if (!dragState) return;
      var state = dragState;
      clearDrag();
      if (!state.active) return;
      var under = document.elementFromPoint(e.clientX, e.clientY);
      document.querySelectorAll('.rb-lane-zones').forEach(function (el) {
        if (el === under || el.contains(under)) {
          assignZone(state.hexId, el.getAttribute('data-hunter'));
        }
      });
    }

    var _t = setInterval(function () {
      if (!window.THE_MAP) return;
      clearInterval(_t);
      var mc = THE_MAP.getContainer();
      mc.addEventListener('pointerdown', onPointerDown);
      document.addEventListener('pointermove', onPointerMove);
      document.addEventListener('pointerup', onPointerUp);
      document.addEventListener('pointercancel', clearDrag);
    }, 300);
  }

  // ── Init ──────────────────────────────────────────────────────────
  function init() {
    if (!window.HUNTER_DATA || !window.L) { setTimeout(init, 400); return; }
    buildZones();
    buildUI();
    setupMapClick();
    setupMapDrag();
    // El botón 🗺 existente ahora abre el Route Builder; el panel clásico
    // queda accesible como window.toggleAssignPanelLegacy().
    if (typeof window.toggleAssignPanel === 'function') {
      window.toggleAssignPanelLegacy = window.toggleAssignPanel;
    }
    window.toggleAssignPanel = window.toggleRouteBuilder;
    console.log('[route_builder] listo — ' + ZONES.length + ' zonas, ' +
                (window.HUNTERS_LIST || []).length + ' hunters, API: ' + (apiUrl() || '(no configurado)'));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
