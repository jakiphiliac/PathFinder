<script setup>
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import L from "leaflet";
import { getTrip, getTrajectory } from "../api.js";

// Fix Leaflet default marker icon paths for bundled builds
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
const blueIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-blue.png",
  ...markerIconOpts,
});
const grayIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-grey.png",
  ...markerIconOpts,
});
const redIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png",
  ...markerIconOpts,
});

const route = useRoute();
const router = useRouter();
const tripId = route.params.id;

const trip = ref(null);
const segments = ref([]);
const loading = ref(true);
const error = ref("");

let map = null;

// Stats
const visitedPlaces = computed(() =>
  (trip.value?.places || []).filter((p) => p.status === "done"),
);
const skippedPlaces = computed(() =>
  (trip.value?.places || []).filter((p) => p.status === "skipped"),
);
const notReachedPlaces = computed(() =>
  (trip.value?.places || []).filter((p) => p.status === "pending"),
);

const totalDistanceKm = computed(() => {
  const total = segments.value.reduce(
    (sum, s) => sum + (s.distance_meters || 0),
    0,
  );
  return (total / 1000).toFixed(2);
});

const totalTravelMinutes = computed(() => {
  const total = segments.value.reduce(
    (sum, s) => sum + (s.duration_seconds || 0),
    0,
  );
  return Math.round(total / 60);
});

const tripDurationMinutes = computed(() => {
  if (!trip.value?.completed_at || !trip.value?.created_at) return null;
  const start = new Date(trip.value.created_at);
  const end = new Date(trip.value.completed_at);
  return Math.round((end - start) / 60000);
});

function timeAtPlace(place) {
  if (!place.arrived_at || !place.departed_at) return null;
  const arrived = new Date(place.arrived_at);
  const departed = new Date(place.departed_at);
  return Math.round((departed - arrived) / 60000);
}

function formatMinutes(min) {
  if (min === null || min === undefined) return "—";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m > 0 ? `${h}h ${m}min` : `${h}h`;
}

function shortName(name) {
  return name.split(",")[0].trim();
}

// Polyline decoder (same as Dashboard)
function decodePolyline(encoded) {
  const points = [];
  let index = 0,
    lat = 0,
    lng = 0;
  while (index < encoded.length) {
    let b,
      shift = 0,
      result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lat += result & 1 ? ~(result >> 1) : result >> 1;
    shift = 0;
    result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lng += result & 1 ? ~(result >> 1) : result >> 1;
    points.push([lat / 1e5, lng / 1e5]);
  }
  return points;
}

function initMap(tripData, segs) {
  if (map) return;
  map = L.map("summary-map").setView(
    [tripData.start_lat, tripData.start_lon],
    13,
  );
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(map);

  // Draw trajectory
  for (const seg of segs) {
    let latlngs;
    if (seg.geometry) {
      latlngs = decodePolyline(seg.geometry);
    } else {
      latlngs = [
        [seg.from_lat, seg.from_lon],
        [seg.to_lat, seg.to_lon],
      ];
    }
    L.polyline(latlngs, {
      color: "#6366f1",
      weight: 3,
      opacity: 0.6,
      lineCap: "round",
      lineJoin: "round",
    }).addTo(map);
  }

  // Start marker
  L.marker([tripData.start_lat, tripData.start_lon], { icon: greenIcon })
    .bindPopup("Start")
    .addTo(map);

  // End destination marker (only for open trips where end differs from start)
  const isOpenTrip =
    Math.abs(tripData.start_lat - tripData.end_lat) > 0.0001 ||
    Math.abs(tripData.start_lon - tripData.end_lon) > 0.0001;
  if (isOpenTrip) {
    L.marker([tripData.end_lat, tripData.end_lon], { icon: redIcon })
      .bindPopup("Final destination")
      .addTo(map);
  }

  // Place markers
  for (const p of tripData.places || []) {
    const icon = p.status === "done" ? blueIcon : grayIcon;
    L.marker([p.lat, p.lon], { icon })
      .bindPopup(shortName(p.name))
      .addTo(map);
  }

  // Fit map to all points (include end destination for open trips)
  const allPoints = [
    [tripData.start_lat, tripData.start_lon],
    ...(isOpenTrip ? [[tripData.end_lat, tripData.end_lon]] : []),
    ...(tripData.places || []).map((p) => [p.lat, p.lon]),
    ...segs.flatMap((s) => [
      [s.from_lat, s.from_lon],
      [s.to_lat, s.to_lon],
    ]),
  ];
  if (allPoints.length > 1) {
    map.fitBounds(L.latLngBounds(allPoints), { padding: [30, 30] });
  }
}

onMounted(async () => {
  try {
    const [tripData, trajData] = await Promise.all([
      getTrip(tripId),
      getTrajectory(tripId),
    ]);
    trip.value = tripData;
    segments.value = trajData.segments || [];
    loading.value = false;

    // Init map after DOM updates
    setTimeout(() => initMap(tripData, segments.value), 50);
  } catch (e) {
    error.value = "Failed to load trip summary.";
    loading.value = false;
  }
});
</script>

<template>
  <div class="summary-page">
    <div v-if="loading" class="summary-loading">Loading trip summary…</div>
    <div v-else-if="error" class="summary-error">{{ error }}</div>

    <template v-else-if="trip">
      <!-- Header -->
      <div class="summary-header">
        <div class="summary-title-row">
          <h1 class="summary-city">{{ trip.city }}</h1>
          <span class="summary-date">{{ trip.date }}</span>
        </div>
        <p class="summary-subtitle">
          {{ trip.start_time }} – {{ trip.end_time }} ·
          {{ trip.transport_mode }}
          <span v-if="tripDurationMinutes">
            · {{ formatMinutes(tripDurationMinutes) }} total
          </span>
        </p>
      </div>

      <!-- Stats row -->
      <div class="summary-stats">
        <div class="stat-chip">
          <span class="stat-value">{{ visitedPlaces.length }}</span>
          <span class="stat-label">visited</span>
        </div>
        <div v-if="skippedPlaces.length" class="stat-chip">
          <span class="stat-value">{{ skippedPlaces.length }}</span>
          <span class="stat-label">skipped</span>
        </div>
        <div v-if="Number(totalDistanceKm) > 0" class="stat-chip">
          <span class="stat-value">{{ totalDistanceKm }} km</span>
          <span class="stat-label">traveled</span>
        </div>
        <div v-if="totalTravelMinutes > 0" class="stat-chip">
          <span class="stat-value">{{ formatMinutes(totalTravelMinutes) }}</span>
          <span class="stat-label">in transit</span>
        </div>
      </div>

      <!-- Map -->
      <div id="summary-map" class="summary-map"></div>

      <!-- Places list -->
      <div class="summary-places">
        <h2 class="places-heading">Your stops</h2>

        <ul class="places-list">
          <!-- Visited -->
          <li
            v-for="p in visitedPlaces"
            :key="p.id"
            class="place-row place-done"
          >
            <span class="place-status-icon">✓</span>
            <span class="place-name">{{ shortName(p.name) }}</span>
            <span v-if="p.category" class="place-category">{{
              p.category
            }}</span>
            <span class="place-time">{{ formatMinutes(timeAtPlace(p)) }}</span>
          </li>

          <!-- Skipped -->
          <li
            v-for="p in skippedPlaces"
            :key="p.id"
            class="place-row place-skipped"
          >
            <span class="place-status-icon">✕</span>
            <span class="place-name">{{ shortName(p.name) }}</span>
            <span v-if="p.category" class="place-category">{{
              p.category
            }}</span>
            <span class="place-time place-skipped-label">skipped</span>
          </li>

          <!-- Not reached -->
          <li
            v-for="p in notReachedPlaces"
            :key="p.id"
            class="place-row place-pending"
          >
            <span class="place-status-icon">·</span>
            <span class="place-name">{{ shortName(p.name) }}</span>
            <span v-if="p.category" class="place-category">{{
              p.category
            }}</span>
            <span class="place-time place-pending-label">not reached</span>
          </li>
        </ul>
      </div>

      <!-- Footer actions -->
      <div class="summary-footer">
        <button class="btn btn-primary" @click="router.push('/')">
          Back to Home
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.summary-page {
  max-width: 700px;
  margin: 0 auto;
  padding: 1.5rem 1rem 3rem;
  font-family: inherit;
}

.summary-loading,
.summary-error {
  text-align: center;
  padding: 3rem;
  color: var(--color-text-muted, #888);
}

.summary-header {
  margin-bottom: 1.25rem;
}

.summary-title-row {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}

.summary-city {
  font-size: 1.75rem;
  font-weight: 700;
  margin: 0;
}

.summary-date {
  font-size: 0.95rem;
  color: var(--color-text-muted, #888);
}

.summary-subtitle {
  margin: 0.25rem 0 0;
  font-size: 0.9rem;
  color: var(--color-text-muted, #888);
}

/* Stats */
.summary-stats {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin-bottom: 1.25rem;
}

.stat-chip {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: var(--color-surface, #1e1e2e);
  border: 1px solid var(--color-border, #333);
  border-radius: 0.75rem;
  padding: 0.5rem 1rem;
  min-width: 70px;
}

.stat-value {
  font-size: 1.2rem;
  font-weight: 700;
}

.stat-label {
  font-size: 0.75rem;
  color: var(--color-text-muted, #888);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* Map */
.summary-map {
  width: 100%;
  height: 320px;
  border-radius: 0.75rem;
  margin-bottom: 1.5rem;
  border: 1px solid var(--color-border, #333);
}

/* Places list */
.places-heading {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 0.75rem;
  color: var(--color-text-muted, #aaa);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.places-list {
  list-style: none;
  padding: 0;
  margin: 0 0 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.place-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 0.75rem;
  border-radius: 0.5rem;
  background: var(--color-surface, #1e1e2e);
  border: 1px solid var(--color-border, #2a2a3e);
}

.place-status-icon {
  font-size: 1rem;
  width: 1.2rem;
  text-align: center;
  flex-shrink: 0;
}

.place-done .place-status-icon {
  color: #4ade80;
}

.place-skipped .place-status-icon {
  color: #f87171;
}

.place-pending .place-status-icon {
  color: #888;
}

.place-name {
  flex: 1;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.place-category {
  font-size: 0.75rem;
  background: var(--color-tag-bg, #2a2a3e);
  color: var(--color-text-muted, #aaa);
  border-radius: 0.3rem;
  padding: 0.1rem 0.4rem;
  flex-shrink: 0;
}

.place-time {
  font-size: 0.85rem;
  color: var(--color-text-muted, #aaa);
  flex-shrink: 0;
}

.place-skipped-label {
  color: #f87171;
}

.place-pending-label {
  color: #666;
}

/* Footer */
.summary-footer {
  display: flex;
  justify-content: center;
}
</style>
