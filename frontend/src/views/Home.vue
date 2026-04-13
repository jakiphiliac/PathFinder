<script setup>
import { ref, onMounted, onUnmounted, watch } from "vue";
import { useRouter } from "vue-router";
import { createTrip, getTrip, geocode } from "../api.js";
import L from "leaflet";

// Fix Leaflet default marker icon paths
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: new URL(
    "leaflet/dist/images/marker-icon-2x.png",
    import.meta.url,
  ).href,
  iconUrl: new URL("leaflet/dist/images/marker-icon.png", import.meta.url).href,
  shadowUrl: new URL("leaflet/dist/images/marker-shadow.png", import.meta.url)
    .href,
});

const markerIconOpts = {
  shadowUrl:
    "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
};
const greenIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-green.png",
  ...markerIconOpts,
});
const redIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png",
  ...markerIconOpts,
});

const router = useRouter();

function roundedNow() {
  const d = new Date();
  const mins = Math.ceil(d.getMinutes() / 5) * 5;
  d.setMinutes(mins, 0, 0);
  return d.toTimeString().slice(0, 5);
}

const city = ref("");
const date = ref(new Date().toISOString().slice(0, 10));
const startTime = ref(roundedNow());
const endTime = ref("18:00");
const transport = ref("foot");
const startLat = ref("");
const startLon = ref("");
const endLat = ref("");
const endLon = ref("");
const sameAsStart = ref(true);
const loading = ref(false);
const error = ref("");

// Geocode search state
const startSearch = ref("");
const endSearch = ref("");
const startResults = ref([]);
const endResults = ref([]);
const startAddress = ref("");
const endAddress = ref("");
const mapMode = ref("none"); // 'none', 'start', or 'end'

let map = null;
let startMarker = null;
let endMarker = null;
let debounceTimer = null;

function initMap() {
  map = L.map("home-map").setView([47.4979, 19.0402], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  map.on("click", (e) => {
    if (mapMode.value === "none") return;
    const { lat, lng } = e.latlng;
    if (mapMode.value === "start") {
      setStartPosition(lat, lng);
      startAddress.value = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    } else if (mapMode.value === "end") {
      setEndPosition(lat, lng);
      endAddress.value = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    }
    // Deactivate pick mode after placing
    mapMode.value = "none";
    map.getContainer().style.cursor = "";
  });
}

function pickOnMap(mode) {
  mapMode.value = mode;
  if (map) map.getContainer().style.cursor = "crosshair";
}

function setStartPosition(lat, lon) {
  startLat.value = lat.toFixed(6);
  startLon.value = lon.toFixed(6);
  if (startMarker) {
    startMarker.setLatLng([lat, lon]);
  } else {
    startMarker = L.marker([lat, lon], {
      icon: greenIcon,
      draggable: true,
    }).addTo(map);
    startMarker.bindPopup("Start").openPopup();
    startMarker.on("dragend", () => {
      const pos = startMarker.getLatLng();
      startLat.value = pos.lat.toFixed(6);
      startLon.value = pos.lng.toFixed(6);
      startAddress.value = `${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)}`;
    });
  }
  if (sameAsStart.value) {
    setEndPosition(lat, lon);
  }
}

function setEndPosition(lat, lon) {
  endLat.value = lat.toFixed(6);
  endLon.value = lon.toFixed(6);
  if (sameAsStart.value) {
    if (endMarker) {
      endMarker.remove();
      endMarker = null;
    }
    return;
  }
  if (endMarker) {
    endMarker.setLatLng([lat, lon]);
  } else {
    endMarker = L.marker([lat, lon], { icon: redIcon, draggable: true }).addTo(
      map,
    );
    endMarker.bindPopup("End");
    endMarker.on("dragend", () => {
      const pos = endMarker.getLatLng();
      endLat.value = pos.lat.toFixed(6);
      endLon.value = pos.lng.toFixed(6);
      endAddress.value = `${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)}`;
    });
  }
}

watch(sameAsStart, (val) => {
  if (val) {
    if (endMarker) {
      endMarker.remove();
      endMarker = null;
    }
    endLat.value = startLat.value;
    endLon.value = startLon.value;
  }
});

function debounceGeocode(query, resultRef) {
  clearTimeout(debounceTimer);
  if (!query || query.length < 2) {
    resultRef.value = [];
    return;
  }
  debounceTimer = setTimeout(async () => {
    try {
      resultRef.value = await geocode(query);
    } catch {
      resultRef.value = [];
    }
  }, 300);
}

watch(startSearch, (val) => debounceGeocode(val, startResults));
watch(endSearch, (val) => debounceGeocode(val, endResults));

function selectStartResult(r) {
  startSearch.value = "";
  startResults.value = [];
  startAddress.value = r.name;
  setStartPosition(r.lat, r.lon);
  map.setView([r.lat, r.lon], 15);
}

function selectEndResult(r) {
  endSearch.value = "";
  endResults.value = [];
  endAddress.value = r.name;
  setEndPosition(r.lat, r.lon);
  map.setView([r.lat, r.lon], 15);
}

function useMyLocation() {
  if (!navigator.geolocation) {
    error.value = "Geolocation not supported";
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      setStartPosition(pos.coords.latitude, pos.coords.longitude);
      startAddress.value = "Current location";
      map.setView([pos.coords.latitude, pos.coords.longitude], 15);
    },
    (err) => {
      error.value = `Location error: ${err.message}`;
    },
  );
}

// --- Trip History (localStorage) ---
const STORAGE_KEY = "pathfinder_trips";
const pastTrips = ref([]);

function getSavedTripIds() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveTripId(id) {
  const ids = getSavedTripIds();
  if (!ids.includes(id)) {
    ids.unshift(id); // newest first
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  }
}

function removeTripId(id) {
  const ids = getSavedTripIds().filter((x) => x !== id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  pastTrips.value = pastTrips.value.filter((t) => t.id !== id);
}

async function loadPastTrips() {
  const ids = getSavedTripIds();
  const results = [];
  for (const id of ids) {
    try {
      const data = await getTrip(id);
      const visited = (data.places || []).filter(
        (p) => p.status === "done",
      ).length;
      const total = (data.places || []).length;
      results.push({
        id: data.id,
        city: data.city,
        date: data.date,
        start_time: data.start_time,
        end_time: data.end_time,
        transport_mode: data.transport_mode,
        visited,
        total,
      });
    } catch {
      // Trip may have been deleted — remove stale ID
      removeTripId(id);
    }
  }
  pastTrips.value = results;
}

async function submit() {
  error.value = "";
  if (!city.value || !endTime.value || !startLat.value || !startLon.value) {
    error.value = "Please fill in city, end time, and start location";
    return;
  }
  const eLat = sameAsStart.value ? startLat.value : endLat.value;
  const eLon = sameAsStart.value ? startLon.value : endLon.value;
  if (!eLat || !eLon) {
    error.value = "Please provide end location";
    return;
  }

  // Validate end_time vs start_time
  const effectiveStart = startTime.value || roundedNow();
  if (endTime.value <= effectiveStart) {
    error.value = `Arrive-by time (${endTime.value}) has already passed — it must be after ${effectiveStart}`;
    return;
  }

  loading.value = true;
  try {
    const payload = {
      city: city.value,
      end_time: endTime.value,
      transport_mode: transport.value,
      start_lat: parseFloat(startLat.value),
      start_lon: parseFloat(startLon.value),
      end_lat: parseFloat(eLat),
      end_lon: parseFloat(eLon),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
    if (date.value) payload.date = date.value;
    if (startTime.value) payload.start_time = startTime.value;
    const trip = await createTrip(payload);
    saveTripId(trip.id);
    router.push(`/trip/${trip.id}`);
  } catch (e) {
    error.value = `Failed to create trip: ${e.message}`;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  initMap();
  useMyLocation();
  loadPastTrips();
});
onUnmounted(() => {
  clearTimeout(debounceTimer);
  if (map) {
    map.remove();
    map = null;
  }
});
</script>

<template>
  <div class="home-layout">
    <div class="form-panel">
      <h1>PathFinder</h1>
      <p class="subtitle">Your reactive journey companion</p>

      <form class="trip-form" @submit.prevent="submit">
        <div class="form-group">
          <label for="city">City</label>
          <input
            id="city"
            v-model="city"
            type="text"
            placeholder="e.g. Budapest"
            required
          />
        </div>

        <!-- End time is the primary required field -->
        <div class="form-group">
          <label for="end-time"
            >Arrive by <span class="required-hint">(required)</span></label
          >
          <input id="end-time" v-model="endTime" type="time" required />
        </div>

        <!-- Date and start time are optional — pre-filled with "now" -->
        <details class="optional-times">
          <summary>Advanced: change date or departure time</summary>
          <div class="form-row optional-row">
            <div class="form-group">
              <label for="date">Date</label>
              <input id="date" v-model="date" type="date" />
            </div>
            <div class="form-group">
              <label for="start-time">Depart at</label>
              <input id="start-time" v-model="startTime" type="time" />
            </div>
          </div>
        </details>

        <div class="form-group">
          <label for="transport">Transport mode</label>
          <select id="transport" v-model="transport">
            <option value="foot">Walking</option>
            <option value="car">Driving</option>
            <option value="bicycle">Cycling</option>
          </select>
        </div>

        <fieldset>
          <legend>Start location</legend>
          <div class="form-group">
            <input
              v-model="startSearch"
              type="text"
              placeholder="Search address or place..."
              autocomplete="off"
              @input="debounceGeocode(startSearch, startResults)"
            />
            <ul v-if="startResults.length" class="autocomplete-list">
              <li
                v-for="(r, i) in startResults"
                :key="i"
                @click="selectStartResult(r)"
              >
                {{ r.name }}
              </li>
            </ul>
          </div>
          <div class="location-actions">
            <button
              type="button"
              class="btn btn-small"
              :class="{ 'btn-active': mapMode === 'start' }"
              @click="pickOnMap('start')"
            >
              {{ mapMode === "start" ? "Click the map..." : "Pick on map" }}
            </button>
            <a class="geo-link" href="#" @click.prevent="useMyLocation"
              >Use my location</a
            >
          </div>
          <div v-if="startAddress" class="location-set">
            <p class="selected-address">{{ startAddress }}</p>
            <p v-if="startLat && startLon" class="coords-display">
              {{ Number(startLat).toFixed(5) }},
              {{ Number(startLon).toFixed(5) }}
            </p>
          </div>
        </fieldset>

        <!-- Trip type toggle -->
        <div class="trip-type-toggle">
          <button
            type="button"
            :class="['trip-type-btn', sameAsStart ? 'active' : '']"
            @click="sameAsStart = true"
          >
            ⟳ Closed trip
            <span class="trip-type-hint">Return to start</span>
          </button>
          <button
            type="button"
            :class="['trip-type-btn', !sameAsStart ? 'active' : '']"
            @click="sameAsStart = false"
          >
            → Open trip
            <span class="trip-type-hint">Different endpoint</span>
          </button>
        </div>

        <!-- End location — only for open trip -->
        <fieldset v-if="!sameAsStart">
          <legend>Final destination</legend>
          <div class="form-group">
            <input
              v-model="endSearch"
              type="text"
              placeholder="Search address or place..."
              autocomplete="off"
              @input="debounceGeocode(endSearch, endResults)"
            />
            <ul v-if="endResults.length" class="autocomplete-list">
              <li
                v-for="(r, i) in endResults"
                :key="i"
                @click="selectEndResult(r)"
              >
                {{ r.name }}
              </li>
            </ul>
          </div>
          <div class="location-actions">
            <button
              type="button"
              class="btn btn-small"
              :class="{ 'btn-active': mapMode === 'end' }"
              @click="pickOnMap('end')"
            >
              {{ mapMode === "end" ? "Click the map..." : "Pick on map" }}
            </button>
          </div>
          <div v-if="endAddress" class="location-set">
            <p class="selected-address">{{ endAddress }}</p>
            <p v-if="endLat && endLon" class="coords-display">
              {{ Number(endLat).toFixed(5) }}, {{ Number(endLon).toFixed(5) }}
            </p>
          </div>
          <p v-if="!endLat && !endLon" class="end-hint">
            Search or pick on map to set your final destination.
          </p>
        </fieldset>

        <p v-if="error" class="error">{{ error }}</p>

        <!-- Create Trip: always visible for closed trip; visible for open trip only when end is set -->
        <button
          v-if="sameAsStart || (endLat && endLon)"
          type="submit"
          class="btn btn-primary"
          :disabled="loading"
        >
          {{ loading ? "Creating..." : "Create Trip" }}
        </button>
        <p v-else class="end-required-hint">
          Set a final destination to create the trip.
        </p>
      </form>

      <!-- Trip History -->
      <div v-if="pastTrips.length" class="trip-history">
        <h2 class="history-title">Your Trips</h2>
        <div class="trip-cards">
          <div
            v-for="t in pastTrips"
            :key="t.id"
            class="trip-card"
            @click="router.push(`/trip/${t.id}`)"
          >
            <div class="trip-card-header">
              <strong>{{ t.city }}</strong>
              <span class="trip-card-date">{{ t.date }}</span>
            </div>
            <div class="trip-card-details">
              <span>{{ t.start_time }}–{{ t.end_time }}</span>
              <span class="trip-card-mode">{{ t.transport_mode }}</span>
            </div>
            <div class="trip-card-stats">
              {{ t.visited }}/{{ t.total }} places visited
            </div>
            <button
              class="trip-card-remove"
              title="Remove from history"
              @click.stop="removeTripId(t.id)"
            >
              &times;
            </button>
          </div>
        </div>
      </div>
    </div>

    <div class="map-panel">
      <div id="home-map"></div>
    </div>
  </div>
</template>

<style scoped>
.home-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.form-panel {
  flex: 0 0 420px;
  overflow-y: auto;
  padding: 30px 24px;
  display: flex;
  flex-direction: column;
}

.map-panel {
  flex: 1;
  position: relative;
}

#home-map {
  width: 100%;
  height: 100%;
}

h1 {
  text-align: center;
  margin-bottom: 4px;
}

.subtitle {
  text-align: center;
  margin-bottom: 24px;
  color: var(--text);
}

.trip-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  position: relative;
}

.form-row {
  display: flex;
  gap: 12px;
  align-items: flex-end;
}

fieldset {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

legend {
  color: var(--text-h);
  font-weight: 500;
  padding: 0 8px;
}

label {
  font-size: 14px;
  color: var(--text);
}

.required-hint {
  font-size: 11px;
  color: var(--text);
  font-weight: normal;
}

.optional-times {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0;
}

.optional-times summary {
  padding: 8px 12px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  user-select: none;
}

.optional-row {
  padding: 0 12px 12px;
}

.trip-type-toggle {
  display: flex;
  gap: 8px;
}

.trip-type-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 10px 8px;
  border: 2px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--text-h);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition:
    border-color 0.15s,
    background 0.15s;
}

.trip-type-btn.active {
  border-color: var(--accent);
  background: var(--accent-bg);
  color: var(--accent);
}

.trip-type-hint {
  font-size: 11px;
  font-weight: normal;
  color: var(--text);
}

.trip-type-btn.active .trip-type-hint {
  color: var(--accent);
  opacity: 0.8;
}

.end-hint {
  font-size: 12px;
  color: var(--text);
  margin: 0;
  font-style: italic;
}

.end-required-hint {
  font-size: 13px;
  color: var(--text);
  text-align: center;
  margin: 0;
}

input,
select {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text-h);
  font-size: 15px;
}

input:focus,
select:focus {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.autocomplete-list {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  list-style: none;
  padding: 0;
  margin: 2px 0 0;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  max-height: 200px;
  overflow-y: auto;
  z-index: 1000;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.autocomplete-list li {
  padding: 8px 12px;
  cursor: pointer;
  font-size: 13px;
  border-bottom: 1px solid var(--border);
  color: var(--text-h);
}

.autocomplete-list li:last-child {
  border-bottom: none;
}

.autocomplete-list li:hover {
  background: var(--accent-bg, #f0f0f0);
}

.selected-address {
  font-size: 13px;
  color: var(--text);
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.coords-display {
  font-size: 12px;
  color: var(--text);
  margin: 0;
  font-family: monospace;
}

.geo-link {
  font-size: 12px;
  color: var(--accent, #3b82f6);
}

.location-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.location-set {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.error {
  color: #ef4444;
  font-size: 14px;
}

.trip-history {
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
}

.history-title {
  font-size: 18px;
  margin: 0 0 12px;
  color: var(--text-h);
}

.trip-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.trip-card {
  display: block;
  padding: 12px 14px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s;
}

.trip-card:hover {
  border-color: var(--accent, #3b82f6);
}

.trip-card-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 6px;
}

.trip-card-header strong {
  font-size: 15px;
  color: var(--text-h);
}

.trip-card-date {
  font-size: 12px;
  color: var(--text);
}

.trip-card-details {
  font-size: 13px;
  color: var(--text);
  margin-bottom: 6px;
}

.trip-card-mode {
  text-transform: capitalize;
  margin-left: 6px;
  opacity: 0.7;
}

.trip-card-stats {
  font-size: 12px;
  color: var(--text);
  opacity: 0.7;
}

.trip-card-remove {
  float: right;
  background: none;
  border: none;
  color: var(--text);
  opacity: 0.4;
  cursor: pointer;
  font-size: 13px;
  padding: 2px 6px;
}

.trip-card-remove:hover {
  opacity: 1;
  color: #ef4444;
}

@media (max-width: 768px) {
  .home-layout {
    flex-direction: column;
  }
  .form-panel {
    flex: none;
    max-height: 55vh;
    overflow-y: auto;
  }
  .map-panel {
    flex: 1;
    min-height: 45vh;
  }
  .form-row {
    flex-direction: column;
  }
}
</style>
