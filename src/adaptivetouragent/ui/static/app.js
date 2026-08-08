// AdaptTour frontend logic.
// Vanilla JS, no build step. Speaks to the FastAPI backend.

const PRESETS = {
  solo: {
    user_id: "ui_solo", name: "Solo", family_size: 1, require_kid_friendly: false, notes: "",
    party: { min: 1, max: 1, default: 1, locked: true },
    category_weights: { museum: 0.3, heritage: 0.25, arts: 0.15, food: 0.15, neighbourhood: 0.1, viewpoint: 0.05 },
  },
  couple: {
    user_id: "ui_couple", name: "Couple", family_size: 2, require_kid_friendly: false, notes: "",
    party: { min: 2, max: 2, default: 2, locked: true },
    category_weights: { food: 0.25, neighbourhood: 0.2, viewpoint: 0.2, arts: 0.15, park: 0.1, heritage: 0.1 },
  },
  friends: {
    user_id: "ui_friends", name: "Friends", family_size: 2, require_kid_friendly: false,
    notes: "Group of friends: flexible plans, food + neighbourhood heavy.",
    party: { min: 2, max: 12, default: 2, locked: false },
    category_weights: { food: 0.25, neighbourhood: 0.2, arts: 0.15, viewpoint: 0.15, heritage: 0.15, park: 0.1 },
  },
  family: {
    user_id: "ui_family", name: "Family", family_size: 3, require_kid_friendly: true,
    notes: "Family with children: kid-friendly stops only.",
    party: { min: 3, max: 12, default: 3, locked: false },
    category_weights: { park: 0.3, zoo: 0.25, theme_park: 0.2, museum: 0.1, food: 0.1, viewpoint: 0.05 },
  },
};

const CATEGORY_ICONS = {
  park: "🌳", zoo: "🦁", theme_park: "🎢", museum: "🏛", viewpoint: "🌆",
  heritage: "🏯", neighbourhood: "🏘", food: "🍜", arts: "🎭",
  accommodation: "🛏",
};

// Categories a traveller can veto or boost (matches the catalogue's POI tags).
const CATEGORIES = ["park", "zoo", "theme_park", "museum", "viewpoint", "heritage", "neighbourhood", "food", "arts"];

function categoryOptions(blankLabel) {
  const blank = blankLabel ? `<option value="">${blankLabel}</option>` : "";
  return blank + CATEGORIES.map((c) => `<option value="${c}">${c}</option>`).join("");
}

let memberSeq = 0;
function addMemberRow() {
  memberSeq += 1;
  const row = document.createElement("div");
  row.className = "member-row";
  row.innerHTML =
    `<input class="member-name" type="text" value="Traveller ${memberSeq}" aria-label="Traveller name" />` +
    `<div class="member-prefs">` +
      `<label>veto <select class="member-veto">${categoryOptions("none")}</select></label>` +
      `<label>boost <select class="member-boost">${categoryOptions("none")}</select></label>` +
      `<button type="button" class="ghost-small member-remove" aria-label="Remove traveller">✕</button>` +
    `</div>`;
  document.getElementById("group-members").appendChild(row);
  row.querySelector(".member-remove").addEventListener("click", () => row.remove());
}

// Each row contributes a group member only if it casts a veto or boost; an
// empty ballot would just vote with the group's overall taste anyway.
function collectGroupMembers() {
  const members = [];
  for (const row of document.querySelectorAll("#group-members .member-row")) {
    const veto = row.querySelector(".member-veto").value;
    const boost = row.querySelector(".member-boost").value;
    if (!veto && !boost) continue;
    members.push({
      name: row.querySelector(".member-name").value.trim() || "Traveller",
      category_weights: {},
      veto_categories: veto ? [veto] : [],
      boost_categories: boost ? [boost] : [],
    });
  }
  return members;
}

function syncGroupPanel() {
  const preset = document.getElementById("profile-preset").value;
  const isGroup = preset === "friends" || preset === "family";
  document.getElementById("group-panel").hidden = !isGroup;
}

const MODE_BADGE = {
  walk: { icon: "🚶", label: "Walk" },
  cycle: { icon: "🚲", label: "Cycle" },
  transit: { icon: "🚇", label: "Transit" },
  rideshare: { icon: "🚕", label: "Rideshare" },
  drive: { icon: "🚗", label: "Drive" },
};

// Per-city brand names for each mode; the wire-level value stays canonical
// (walk/cycle/transit/rideshare/drive), but the display uses locally familiar
// labels (MRT in Singapore, Tube in London, etc.). Falls back to MODE_BADGE.
const CITY_MODE_LABEL = {
  "Singapore": { walk: "Walk", cycle: "Anywheel", transit: "MRT",     rideshare: "Grab",  drive: "Taxi" },
  "Melbourne": { walk: "Walk", cycle: "Lime",     transit: "Tram",    rideshare: "Uber",  drive: "Taxi" },
  "London":    { walk: "Walk", cycle: "Lime",     transit: "Tube",    rideshare: "Uber",  drive: "Cab"  },
  "New York":  { walk: "Walk", cycle: "Citi Bike", transit: "Subway", rideshare: "Uber",  drive: "Taxi" },
  "Paris":     { walk: "Walk", cycle: "Vélib",    transit: "Métro",   rideshare: "Uber",  drive: "Taxi" },
};

function modeLabel(mode, city) {
  const cityLabels = CITY_MODE_LABEL[city];
  if (cityLabels && cityLabels[mode]) return cityLabels[mode];
  return (MODE_BADGE[mode] && MODE_BADGE[mode].label) || mode || "Leg";
}

// Map default centre per city (lat, lon, zoom). Used when the user switches
// city before generating a plan so the map doesn't sit on Singapore.
const CITY_CENTERS = {
  "Singapore": [1.3000, 103.8500, 12],
  "Melbourne": [-37.8136, 144.9631, 12],
  "London": [51.5074, -0.1278, 12],
  "New York": [40.7580, -73.9855, 12],
  "Paris": [48.8566, 2.3522, 12],
};

let map = null;
let routeLayer = null;
let markerLayer = null;
let currentSession = null;
let eventSource = null;
let lastDays = [];          // current PlanResponse.days
let lastIsMultiDay = false;
let activeDayIndex = 0;     // which day's visits are shown in the rail + map
let planStartIso = null;    // ISO 8601 of the active day's start_time, used as the
                            // baseline advance_to_iso when no POI is marked visited
let visitedUpToIndex = -1;  // high-water mark: index of last visited POI (-1 = none)

// ---------- Theming ----------
function initTheme() {
  const stored = localStorage.getItem("atau-theme");
  if (stored) document.documentElement.dataset.theme = stored;
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("atau-theme", next);
  });
}

// ---------- Profile preset constraints ----------
function applyPresetConstraints() {
  const presetName = document.getElementById("profile-preset").value;
  const preset = PRESETS[presetName];
  if (!preset) return;
  const input = document.getElementById("family-size");
  const cfg = preset.party || { min: 1, max: 12, default: preset.family_size || 1, locked: false };
  input.min = cfg.min;
  input.max = cfg.max;
  // Snap value into the allowed range; for locked presets, force the default.
  const current = parseInt(input.value, 10) || cfg.default;
  if (cfg.locked) {
    input.value = cfg.default;
  } else {
    input.value = Math.max(cfg.min, Math.min(cfg.max, current));
  }
  // Locked presets disable the input so the user can't override (e.g. solo=1).
  input.disabled = cfg.locked;
  syncGroupPanel();
}

function initPresetSelect() {
  document.getElementById("profile-preset").addEventListener("change", applyPresetConstraints);
  applyPresetConstraints();
}

// ---------- Default datetimes ----------
function initDateTimeInputs() {
  const now = new Date();
  const todayMorning = new Date(now);
  todayMorning.setHours(9, 0, 0, 0);
  const todayEvening = new Date(now);
  todayEvening.setHours(19, 0, 0, 0);

  const fmt = (d) => {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  document.getElementById("start-datetime").value = fmt(todayMorning);
  document.getElementById("end-datetime").value = fmt(todayEvening);
}

// ---------- Info icons ----------
function initInfoIcons() {
  const popover = document.getElementById("info-popover");
  const show = (target) => {
    const tip = target.dataset.tip || "";
    if (!tip) return;
    popover.textContent = tip;
    popover.hidden = false;
    const r = target.getBoundingClientRect();
    const px = Math.min(window.innerWidth - 300, Math.max(8, r.right + 8));
    const py = Math.max(8, r.top);
    popover.style.left = `${px}px`;
    popover.style.top = `${py}px`;
  };
  const hide = () => { popover.hidden = true; };

  document.body.addEventListener("mouseover", (e) => {
    const t = e.target.closest(".info-icon");
    if (t) show(t);
  });
  document.body.addEventListener("mouseout", (e) => {
    if (e.target.closest(".info-icon")) hide();
  });
  document.body.addEventListener("focusin", (e) => {
    const t = e.target.closest(".info-icon");
    if (t) show(t);
  });
  document.body.addEventListener("focusout", (e) => {
    if (e.target.closest(".info-icon")) hide();
  });
  document.body.addEventListener("click", (e) => {
    const t = e.target.closest(".info-icon");
    if (t) {
      e.preventDefault();
      // Mobile-friendly toggle.
      if (popover.hidden) show(t); else hide();
    } else {
      hide();
    }
  });
}

// ---------- Map ----------
function ensureMap(center, zoom = 12) {
  if (map !== null) {
    map.setView(center, zoom);
    return;
  }
  map = L.map("map", { zoomControl: true }).setView(center, zoom);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(map);
  routeLayer = L.layerGroup().addTo(map);
  markerLayer = L.layerGroup().addTo(map);
}

// Per-mode line styling on the map. Walk/cycle/rideshare/drive get solid
// road-following polylines (OSRM routed). Transit is dashed; OSRM has no
// public-transit profile, so the line follows roads as a visual proxy and
// the dash tells the reader "this is the metro line, not a road".
const MODE_LINE_STYLE = {
  walk:      { color: "#16a34a", weight: 4, opacity: 0.85, dashArray: null },
  cycle:     { color: "#22c55e", weight: 4, opacity: 0.85, dashArray: null },
  transit:   { color: "#2c5cdb", weight: 4, opacity: 0.85, dashArray: "8 6" },
  rideshare: { color: "#f59e0b", weight: 4, opacity: 0.90, dashArray: null },
  drive:     { color: "#ef4444", weight: 4, opacity: 0.90, dashArray: null },
};

function drawVisits(visits) {
  if (markerLayer === null) return;
  markerLayer.clearLayers();
  routeLayer.clearLayers();
  if (!visits.length) return;

  const bounds = [];
  visits.forEach((v, i) => {
    const ll = [v.lat, v.lon];
    bounds.push(ll);
    const icon = L.divIcon({
      html: `<div class="map-pin">${i + 1}</div>`,
      className: "map-pin-wrap",
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
    L.marker(ll, { icon })
      .addTo(markerLayer)
      .bindPopup(`<b>${i + 1}. ${v.name}</b><br/>${v.category}<br/>${v.arrive.slice(11, 16)}-${v.depart.slice(11, 16)}`);

    // Per-leg polyline. Uses the routed geometry the planner attached if
    // present; otherwise falls back to a 2-point straight line.
    const geom = (v.inbound_geometry && v.inbound_geometry.length >= 2)
      ? v.inbound_geometry
      : (i > 0 ? [[visits[i - 1].lat, visits[i - 1].lon], ll] : null);
    if (geom) {
      const style = MODE_LINE_STYLE[v.travel_mode] || MODE_LINE_STYLE.transit;
      const tooltip = `${v.travel_mode || "leg"} · ${(v.travel_distance_km || 0).toFixed(1)} km`;
      L.polyline(geom, style).bindTooltip(tooltip, { sticky: true }).addTo(routeLayer);
      geom.forEach(p => bounds.push(p));
    }
  });
  map.fitBounds(L.latLngBounds(bounds).pad(0.2));
}

// ---------- Sidebar metrics ----------
function updateMetrics(planMeta, sessionMeta) {
  document.getElementById("panel-summary").hidden = false;
  document.getElementById("m-stops").textContent = String(planMeta.n_visits ?? 0);
  document.getElementById("m-llm-cost").textContent = "$" + (sessionMeta.llm_cost ?? 0).toFixed(4);
  document.getElementById("m-trip-cost").textContent = "$" + (planMeta.total_cost_usd ?? 0).toFixed(2);
  document.getElementById("m-co2").textContent = (planMeta.total_co2e_kg ?? 0).toFixed(2) + " kg";
  document.getElementById("m-replans").textContent = String(sessionMeta.n_replans ?? 0);
}

// ---------- Day tabs + visit list ----------
function renderDayTabs() {
  const tabsEl = document.getElementById("day-tabs");
  if (!lastIsMultiDay || lastDays.length <= 1) {
    tabsEl.hidden = true;
    tabsEl.innerHTML = "";
    return;
  }
  tabsEl.hidden = false;
  tabsEl.innerHTML = "";
  lastDays.forEach((d, idx) => {
    const btn = document.createElement("button");
    btn.className = "day-tab" + (idx === activeDayIndex ? " active" : "");
    btn.textContent = `Day ${idx + 1} · ${d.date.slice(5)} · ${d.visits.length}`;
    btn.addEventListener("click", () => {
      activeDayIndex = idx;
      renderActiveDay();
    });
    tabsEl.appendChild(btn);
  });
}

function renderVisitList(visits) {
  const list = document.getElementById("visit-list");
  list.innerHTML = "";
  const city = document.getElementById("city").value;

  // Banner above the list when at least one POI is marked visited.
  // Lets the user clear the visited mark in one click.
  if (visitedUpToIndex >= 0 && visits[visitedUpToIndex]) {
    const banner = document.createElement("li");
    banner.className = "visited-banner";
    const lastName = visits[visitedUpToIndex].name;
    banner.innerHTML =
      `Visited through <strong>${escapeHtml(lastName)}</strong>. ` +
      `New triggers only replan the stops below.` +
      `<button id="clear-visited" type="button">clear</button>`;
    list.appendChild(banner);
  }

  visits.forEach((v, i) => {
    const isVisited = i <= visitedUpToIndex;
    const isNextUp = i === visitedUpToIndex + 1;
    // Insert a transport-leg row between consecutive POIs. Carries the
    // mode, city-aware name, distance, start→end times, fare, and CO₂.
    // Skipped for the first visit (no inbound leg in the demo's typical
    // no-accommodation flow).
    if (i > 0 && v.travel_mode) {
      const prev = visits[i - 1];
      const modeInfo = MODE_BADGE[v.travel_mode] || { icon: "↪", label: v.travel_mode };
      const label = modeLabel(v.travel_mode, city);
      const start = prev.depart.slice(11, 16);
      const end = v.arrive.slice(11, 16);
      const startDt = new Date(prev.depart);
      const endDt = new Date(v.arrive);
      const durMin = Math.max(0, Math.round((endDt - startDt) / 60000));
      const dist = (v.travel_distance_km || 0).toFixed(1);
      const fareBit = (v.travel_cost_usd > 0)
        ? ` · $${v.travel_cost_usd.toFixed(2)}` : "";
      const co2Bit = (v.travel_co2e_kg > 0)
        ? ` · ${v.travel_co2e_kg.toFixed(2)} kg CO₂` : "";
      const leg = document.createElement("li");
      // A leg is "past" when its destination POI is visited.
      const pastClass = isVisited ? " past" : "";
      leg.className = `transport-leg mode-${v.travel_mode}${pastClass}`;
      leg.innerHTML = `
        <span class="leg-icon" aria-hidden="true">${modeInfo.icon}</span>
        <div class="leg-body">
          <div class="leg-mode">${escapeHtml(label)} · ${dist} km</div>
          <div class="leg-times">${start} → ${end} (${durMin} min)${fareBit}${co2Bit}</div>
        </div>
      `;
      list.appendChild(leg);
    }

    const li = document.createElement("li");
    const stateClass =
      (isVisited ? " visited" : "") + (isNextUp ? " next-up" : "");
    li.className = `visit-card${stateClass}`;
    li.dataset.idx = i;
    const icon = CATEGORY_ICONS[v.category] ?? "📍";
    const time = `${v.arrive.slice(11, 16)} to ${v.depart.slice(11, 16)}`;
    const fee = (v.entry_fee_usd > 0)
      ? `<span title="entry fee × party">$${v.entry_fee_usd.toFixed(2)}</span>` : "";
    const alts = (v.alternatives_considered && v.alternatives_considered.length)
      ? `<div><strong>Alternatives considered:</strong> ${escapeHtml(v.alternatives_considered.slice(0, 3).join(", "))}</div>`
      : "";
    const scoresTip = (v.reasoning_scores || "").replace(/"/g, "&quot;");
    // "Why this stop" and the alternatives list live inside the click-to-
    // expand region so the default card stays compact. The numeric trace
    // sits behind an info icon next to the "Why" label.
    const reasoning = v.reasoning_text
      ? `<div class="visit-why"><strong>Why this stop:</strong> ${escapeHtml(v.reasoning_text)}` +
        (scoresTip ? ` <button class="info-icon" type="button" data-tip="${scoresTip}">ⓘ</button>` : "") +
        `</div>`
      : "";
    const expandableHint = (reasoning || alts)
      ? `<span class="expand-chevron" aria-hidden="true">▾</span>` : "";
    // Round check button; clicking marks this POI + every earlier one as
    // visited (high-water-mark). Clicking the already-marked POI rolls
    // the mark back by one. Tooltip explains the behaviour.
    const checkTitle = isVisited
      ? "Visited, click to unmark"
      : "Mark this stop (and all earlier ones) as visited";
    const check =
      `<button class="visit-check" type="button" data-action="mark-visited" ` +
      `data-idx="${i}" title="${checkTitle}" aria-label="${checkTitle}">` +
      `${isVisited ? "✓" : ""}</button>`;
    li.innerHTML = `
      <div class="visit-row">
        <span class="visit-time">${time}</span>
        <span aria-hidden="true">${icon}</span>
        ${check}
        ${expandableHint}
      </div>
      <div class="visit-name">${i + 1}. ${escapeHtml(v.name)}</div>
      <div class="visit-meta">
        <span>${escapeHtml(v.category)}</span>
        ${fee}
        <button class="book-btn" type="button" data-action="book"
          data-poi="${v.poi_id}" title="Book this stop (sandboxed)">Book</button>
        <button class="remove-stop-btn" type="button" data-action="remove-stop"
          data-poi="${v.poi_id}" title="Remove this stop and re-route around it">✕</button>
      </div>
      <div class="visit-booking" id="booking-${v.poi_id}"></div>
      <div class="visit-detail">${reasoning}${alts}</div>
    `;
    // Click anywhere on the card except an info icon or the visit-check
    // toggles the expanded state (revealing "Why this stop" + alternatives).
    li.addEventListener("click", (e) => {
      if (e.target.closest(".info-icon")) return;
      if (e.target.closest(".visit-check")) return;
      if (e.target.closest(".book-btn")) return;
      if (e.target.closest(".remove-stop-btn")) return;
      li.classList.toggle("expanded");
    });
    list.appendChild(li);
  });
  document.getElementById("detail-rail").hidden = false;
  document.querySelector(".layout").classList.add("with-detail");
}

// Toggle the visited high-water-mark when a check button is clicked.
function onVisitCheckClick(idx) {
  if (idx === visitedUpToIndex) {
    // Re-clicking the topmost visited POI rolls the mark back by one.
    visitedUpToIndex = idx - 1;
  } else {
    visitedUpToIndex = idx;
  }
  renderActiveDay();
}

function clearVisited() {
  visitedUpToIndex = -1;
  renderActiveDay();
}

// `advance_to_iso` for /replan and /chat. When a POI is marked visited,
// the server will lock everything up to (and including) that POI in the
// executed_prefix and only replan the tail. When nothing is marked, we
// send the plan's start time so the entire tail is replanable; never
// the real wall clock (the demo can be tested at any time of day).
function replanClockIso() {
  const visits = lastDays[activeDayIndex] ? lastDays[activeDayIndex].visits : [];
  if (visitedUpToIndex >= 0 && visits[visitedUpToIndex]) {
    return visits[visitedUpToIndex].depart.slice(0, 19);
  }
  return planStartIso || new Date().toISOString().slice(0, 19);
}

function renderActiveDay() {
  const day = lastDays[activeDayIndex];
  if (!day) return;
  drawVisits(day.visits);
  renderVisitList(day.visits);
  renderDayTabs();
}

// ---------- Activity log ----------
function logEntry(tag, html) {
  document.getElementById("panel-log").hidden = false;
  const log = document.getElementById("chat-log");
  const li = document.createElement("li");
  li.className = "entry";
  li.innerHTML = `<span class="tag ${tag}">${tag}</span>${html}`;
  log.prepend(li);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

// ---------- SSE ----------
function openEventStream(sid) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/events/${sid}`);
  eventSource.addEventListener("initial_plan", () => {
    // The /plan response already drove the initial render; the SSE replay
    // is informational only. Avoid double-render flicker.
  });
  eventSource.addEventListener("replan", (e) => {
    const data = JSON.parse(e.data);
    // Replan operates on the active day (day 0 in v1). Update day 0's visits.
    if (lastDays.length) {
      lastDays[0].visits = data.itinerary.visits;
      lastDays[0].total_cost_usd = data.itinerary.total_cost_usd ?? 0;
      lastDays[0].total_co2e_kg = data.itinerary.total_co2e_kg ?? 0;
    }
    if (activeDayIndex === 0) renderActiveDay();
    const totalVisits = lastDays.reduce((s, d) => s + d.visits.length, 0);
    const totalCost = lastDays.reduce((s, d) => s + (d.total_cost_usd ?? 0), 0);
    const totalCo2 = lastDays.reduce((s, d) => s + (d.total_co2e_kg ?? 0), 0);
    updateMetrics(
      { n_visits: totalVisits, total_cost_usd: totalCost, total_co2e_kg: totalCo2 },
      { llm_cost: data.cost_usd, n_replans: parseInt(document.getElementById("m-replans").textContent) + 1 || 1 },
    );
    let src = "";
    if (data.source === "chat") src = `<em>"${escapeHtml(data.user_message)}"</em>: `;
    else if (data.source === "group-veto" || data.source === "remove-stop") src = `<em>${escapeHtml(data.user_message)}</em>: `;
    logEntry(data.source === "undo" ? "info" : "replan", `${src}${escapeHtml(data.diff)}. ${escapeHtml(data.rationale)}`);
  });
  eventSource.addEventListener("trigger", (e) => {
    const data = JSON.parse(e.data);
    logEntry("trigger", `${escapeHtml(data.kind)} (${escapeHtml(data.severity)})`);
  });
}

// ---------- API calls ----------
async function generatePlan() {
  const presetName = document.getElementById("profile-preset").value;
  const preset = PRESETS[presetName];
  const cfg = preset.party || { min: 1, max: 12, locked: false };
  let partySize = parseInt(document.getElementById("family-size").value, 10) || cfg.min;
  partySize = Math.max(cfg.min, Math.min(cfg.max, partySize));
  // Strip the helper `party` field; backend only cares about family_size.
  const { party, ...presetProfile } = preset;
  const profile = { ...presetProfile, family_size: partySize };
  const startVal = document.getElementById("start-datetime").value;
  const endVal = document.getElementById("end-datetime").value;
  const moneyCapStr = document.getElementById("money-cap").value;
  const preferLowCarbon = document.getElementById("prefer-low-carbon").checked;
  const requireWheelchair = document.getElementById("require-wheelchair").checked;
  const requireLowStim = document.getElementById("require-low-stimulation").checked;
  const pace = document.getElementById("pace").value;
  const city = document.getElementById("city").value;

  if (!startVal || !endVal) {
    logEntry("info", "Please fill in both start and end date/time.");
    return;
  }
  // Carry accessibility fields on the profile (per ProfileIn wire schema).
  profile.require_wheelchair = requireWheelchair;
  profile.require_low_stimulation = requireLowStim;
  // Per-member vetoes/boosts for Friends/Family groups.
  const isGroup = presetName === "friends" || presetName === "family";
  profile.group_members = isGroup ? collectGroupMembers() : [];
  const body = {
    profile,
    city,
    start_datetime: startVal,
    end_datetime: endVal,
    prefer_low_carbon: preferLowCarbon,
    pace,
  };
  if (moneyCapStr) body.money_budget_usd = parseFloat(moneyCapStr);

  const center = CITY_CENTERS[city] || [1.30, 103.85, 12];
  ensureMap([center[0], center[1]], center[2]);
  document.getElementById("map-overlay").classList.add("hidden");
  logEntry("info", "Generating plan…");

  const resp = await fetch("/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    logEntry("info", `Plan failed (${resp.status}): ${escapeHtml(err.detail ?? "")}`);
    return;
  }
  const data = await resp.json();
  currentSession = data.session_id;
  lastDays = data.days || [];
  lastIsMultiDay = !!data.is_multi_day;
  activeDayIndex = 0;
  // Reset visited-state and pin the plan's start time for the replan clock.
  visitedUpToIndex = -1;
  planStartIso = (lastDays[0] && lastDays[0].start_time)
    ? lastDays[0].start_time.slice(0, 19)
    : null;
  renderActiveDay();

  const totalVisits = lastDays.reduce((s, d) => s + d.visits.length, 0);
  updateMetrics(
    { n_visits: totalVisits, total_cost_usd: data.total_cost_usd, total_co2e_kg: data.total_co2e_kg },
    { llm_cost: data.cost_usd, n_replans: 0 },
  );
  document.getElementById("panel-triggers").hidden = false;
  // Filter-collapse warning: if hard filters prune the catalogue, say so
  // rather than letting a short plan look like an algorithm failure.
  if (data.catalogue_size && data.candidates_matched < data.catalogue_size) {
    let msg = `${data.candidates_matched} of ${data.catalogue_size} stops match all your filters`;
    if (data.candidates_matched < 5) msg += ", consider relaxing one (wheelchair / sensory / dietary) to see more";
    logEntry("info", `${msg}.`);
  }
  const planSummary = lastIsMultiDay
    ? `Multi-day plan: ${lastDays.length} days, ${totalVisits} stops, $${data.total_cost_usd.toFixed(2)} entry fees, ${data.total_co2e_kg.toFixed(2)} kg CO₂.`
    : `Initial plan: ${totalVisits} stops, $${data.total_cost_usd.toFixed(2)} entry fees, ${data.total_co2e_kg.toFixed(2)} kg CO₂.`;
  logEntry("plan", planSummary);
  openEventStream(currentSession);
}

// Sessions live in server memory and are wiped on a restart (e.g. when the
// HF Space redeploys). When that happens any in-flight `currentSession` is
// dead and every `/replan` or `/chat` call will 404. Self-heal: forget the
// stale session, hide the trigger/chat panel, and tell the user to regenerate
// the plan rather than retrying against a phantom SID.
function handleSessionExpired(verb) {
  currentSession = null;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  document.getElementById("panel-triggers").hidden = true;
  logEntry("info", `${verb} failed: session expired. Click "Generate plan" to start a new session.`);
}

async function injectTrigger(note) {
  if (currentSession === null) return;
  logEntry("trigger", `Injecting: "${escapeHtml(note)}"`);
  const resp = await fetch(`/replan/${currentSession}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note, advance_to_iso: replanClockIso() }),
  });
  if (!resp.ok) {
    if (resp.status === 404) {
      handleSessionExpired("Replan");
      return;
    }
    if (resp.status === 429) {
      logEntry("info", "The demo's LLM budget for this session is used up. Start a new plan to reset.");
      return;
    }
    const err = await resp.json().catch(() => ({}));
    logEntry("info", `Replan failed (${resp.status}): ${escapeHtml(err.detail ?? "")}`);
    return;
  }
  const data = await resp.json().catch(() => ({}));
  if (data.interpretation) logEntry("info", `Understood: ${escapeHtml(data.interpretation)}.`);
  // Metrics are refreshed by the SSE replan event handler.
}

async function bookVisit(poiId, btn) {
  if (currentSession === null) {
    logEntry("info", "Generate a plan first, then book a stop.");
    return;
  }
  const slot = document.getElementById(`booking-${poiId}`);
  if (btn) btn.disabled = true;
  const resp = await fetch(`/book/${currentSession}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ poi_id: poiId }),
  });
  if (!resp.ok) {
    if (btn) btn.disabled = false;
    if (resp.status === 404) {
      handleSessionExpired("Booking");
      return;
    }
    const err = await resp.json().catch(() => ({}));
    logEntry("info", `Booking failed (${resp.status}): ${escapeHtml(err.detail ?? "")}`);
    return;
  }
  const b = await resp.json();
  if (slot) {
    const ok = b.status === "confirmed";
    slot.innerHTML = ok
      ? `<span class="booking-ok">✓ ${escapeHtml(b.confirmation_code)} · $${b.amount_usd.toFixed(2)}</span>`
      : `<span class="booking-fail">booking ${escapeHtml(b.status)}</span>`;
  }
  if (btn) btn.textContent = b.status === "confirmed" ? "Booked" : "Book";
  logEntry("plan", `Booked ${escapeHtml(b.target_name)}: ${escapeHtml(b.confirmation_code || b.status)} ($${b.amount_usd.toFixed(2)}).`);
}

async function removeStop(poiId) {
  if (currentSession === null) return;
  logEntry("trigger", "Removing a stop and re-routing…");
  const resp = await fetch(`/remove-stop/${currentSession}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ poi_id: poiId, advance_to_iso: replanClockIso() }),
  });
  if (!resp.ok) {
    if (resp.status === 404) {
      handleSessionExpired("Remove");
      return;
    }
    const err = await resp.json().catch(() => ({}));
    logEntry("info", `Remove failed (${resp.status}): ${escapeHtml(err.detail ?? "")}`);
  }
  // The re-routed plan arrives over the SSE stream.
}

async function undoChange() {
  if (currentSession === null) return;
  const resp = await fetch(`/undo/${currentSession}`, { method: "POST" });
  if (!resp.ok) {
    if (resp.status === 404) {
      handleSessionExpired("Undo");
      return;
    }
    if (resp.status === 409) {
      logEntry("info", "Nothing to undo yet.");
      return;
    }
    const err = await resp.json().catch(() => ({}));
    logEntry("info", `Undo failed (${resp.status}): ${escapeHtml(err.detail ?? "")}`);
  }
  // The restored plan arrives over the SSE stream.
}

async function castVeto(category) {
  if (currentSession === null) {
    logEntry("info", "Generate a plan first, then a group member can veto a category.");
    return;
  }
  logEntry("trigger", `Group veto: no more ${escapeHtml(category)} stops`);
  const resp = await fetch(`/group-veto/${currentSession}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category, advance_to_iso: replanClockIso() }),
  });
  if (!resp.ok) {
    if (resp.status === 404) {
      handleSessionExpired("Veto");
      return;
    }
    const err = await resp.json().catch(() => ({}));
    logEntry("info", `Veto failed (${resp.status}): ${escapeHtml(err.detail ?? "")}`);
    return;
  }
  // The replan (and its diff/rationale) arrives over the SSE stream.
}

async function sendChat(text) {
  if (currentSession === null || !text.trim()) return;
  logEntry("info", `You: ${escapeHtml(text)}`);
  const resp = await fetch(`/chat/${currentSession}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, advance_to_iso: replanClockIso() }),
  });
  if (!resp.ok) {
    if (resp.status === 404) {
      handleSessionExpired("Chat");
      return;
    }
    if (resp.status === 429) {
      logEntry("info", "The demo's LLM budget for this session is used up. Start a new plan to reset.");
      return;
    }
    const err = await resp.json().catch(() => ({}));
    logEntry("info", `Chat failed (${resp.status}): ${escapeHtml(err.detail ?? "")}`);
    return;
  }
  const data = await resp.json().catch(() => ({}));
  if (data.interpretation) logEntry("info", `Understood: ${escapeHtml(data.interpretation)}.`);
  document.getElementById("chat-input").value = "";
}

// ---------- Wire up ----------
initTheme();
initDateTimeInputs();
initPresetSelect();
initInfoIcons();
// Recentre the map immediately when the user picks a different city, even
// before a plan is generated, so the reviewer sees the right city.
document.getElementById("city").addEventListener("change", (e) => {
  const center = CITY_CENTERS[e.target.value];
  if (center) ensureMap([center[0], center[1]], center[2]);
});
document.getElementById("plan-btn").addEventListener("click", generatePlan);
for (const btn of document.querySelectorAll("#panel-triggers .trigger-row button.trigger")) {
  btn.addEventListener("click", () => injectTrigger(btn.dataset.note));
}
// Group controls: member rows + the live mid-trip veto.
document.getElementById("veto-category").innerHTML = categoryOptions("Veto a category…");
document.getElementById("add-member").addEventListener("click", addMemberRow);
document.getElementById("veto-btn").addEventListener("click", () => {
  const cat = document.getElementById("veto-category").value;
  if (cat) castVeto(cat);
});
document.getElementById("undo-btn").addEventListener("click", undoChange);
document.getElementById("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  sendChat(document.getElementById("chat-input").value);
});
document.getElementById("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    sendChat(e.target.value);
  }
});
document.getElementById("close-detail").addEventListener("click", () => {
  document.getElementById("detail-rail").hidden = true;
  document.querySelector(".layout").classList.remove("with-detail");
});
// Event-delegate the visit-check toggle and the "clear" link in the
// visited banner. Rebound on every render is unnecessary; one listener
// on the detail rail catches every check across re-renders.
document.getElementById("visit-list").addEventListener("click", (e) => {
  const check = e.target.closest('.visit-check[data-action="mark-visited"]');
  if (check) {
    e.stopPropagation();
    onVisitCheckClick(parseInt(check.dataset.idx, 10));
    return;
  }
  const book = e.target.closest('.book-btn[data-action="book"]');
  if (book) {
    e.stopPropagation();
    bookVisit(book.dataset.poi, book);
    return;
  }
  const remove = e.target.closest('.remove-stop-btn[data-action="remove-stop"]');
  if (remove) {
    e.stopPropagation();
    removeStop(remove.dataset.poi);
    return;
  }
  if (e.target.id === "clear-visited") {
    e.stopPropagation();
    clearVisited();
  }
});
