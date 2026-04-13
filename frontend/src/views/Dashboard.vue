<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import L from "leaflet";
import {
  getTrip,
  updateTrip,
  searchPlaces,
  addPlace,
  deletePlace,
  updatePlace,
  getFeasibility,
  getNextRecommendation,
  checkinPlace,
  connectTripStream,
  getTrajectory,
} from "../api.js";

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

// Start/end point icons
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
const orangeIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-orange.png",
  ...markerIconOpts,
});

// Feasibility color icons for places
const feasGreenIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-green.png",
  ...markerIconOpts,
});
const feasYellowIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-yellow.png",
  ...markerIconOpts,
});
const feasRedIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png",
  ...markerIconOpts,
});
const feasGrayIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-grey.png",
  ...markerIconOpts,
});
const feasVioletIcon = new L.Icon({
  iconUrl:
    "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-violet.png",
  ...markerIconOpts,
});

const feasIconMap = {
  green: feasGreenIcon,
  yellow: feasYellowIcon,
  red: feasRedIcon,
  gray: feasGrayIcon,
  unknown: feasVioletIcon,
};

const route = useRoute();
const router = useRouter();
const tripId = route.params.id;

const trip = ref(null);
const places = ref([]);
const searchQuery = ref("");
const searchResults = ref([]);
const searching = ref(false);
const loadError = ref("");
const feasibility = ref(new Map());
const userLat = ref(null);
const userLon = ref(null);

const settingPosition = ref(false);
const checkinLoading = ref(false);
const showArrivePicker = ref(false);
const nextRecs = ref(null); // { recommendations: [], message: '' }
const nextLoading = ref(false);
const modeChanging = ref(false);
const nextSkipIndex = ref(0); // which recommendation is "primary"
const alerts = ref([]); // urgency alert banners
const toasts = ref([]); // toast notifications
const isOffline = ref(!navigator.onLine);
const feasLoading = ref(true); // loading skeleton state

// Trajectory state
const trajectorySegments = ref([]);

// Pending arrival: place the user tapped "Go" on in What Next?
const pendingArrivalPlace = ref(null); // { id, name, lat, lon }

// Click-to-add state
const addingByClick = ref(false);
const showClickAddModal = ref(false);
const clickAddLat = ref(null);
const clickAddLon = ref(null);
const clickAddName = ref("");
const clickAddPriority = ref("want");
const clickAddDuration = ref(30);
const clickAddCategory = ref("");

let map = null;
let eventSource = null;
let markersLayer = null;
let searchMarkersLayer = null;
let userPositionMarker = null;
let trajectoryLayerGroup = null;

// Computed stats
const visitedCount = computed(
  () =>
    places.value.filter((p) => p.status === "done" || p.status === "visiting")
      .length,
);
const remainingCount = computed(
  () => places.value.filter((p) => !p.status || p.status === "pending").length,
);
const reachableCount = computed(() => {
  return places.value.filter((p) => {
    if (p.status && p.status !== "pending") return false;
    const f = feasibility.value.get(p.id);
    return !f || f.color !== "gray";
  }).length;
});

const remainingMinutes = computed(() => {
  if (!trip.value) return 0;
  const now = new Date();
  const [eh, em] = (trip.value.end_time || "18:00").split(":").map(Number);
  const end = new Date(now);
  end.setHours(eh, em, 0, 0);
  const diff = Math.max(0, Math.floor((end - now) / 60000));
  return diff;
});

// Sectioned place lists
const visitingPlace = computed(
  () => places.value.find((p) => p.status === "visiting") || null,
);
const pendingPlaces = computed(() =>
  places.value.filter((p) => !p.status || p.status === "pending"),
);
const donePlaces = computed(() =>
  places.value.filter((p) => p.status === "done"),
);
const skippedPlaces = computed(() =>
  places.value.filter((p) => p.status === "skipped"),
);

const tripEnded = computed(() => {
  if (!trip.value) return false;
  const now = new Date();
  const [eh, em] = (trip.value.end_time || "18:00").split(":").map(Number);
  const end = new Date(now);
  end.setHours(eh, em, 0, 0);
  return now >= end;
});

const allPlacesDone = computed(() => {
  if (!trip.value || places.value.length === 0) return false;
  return places.value.every(
    (p) => p.status === "done" || p.status === "skipped",
  );
});

const isOpenTrip = computed(() => {
  if (!trip.value) return false;
  return (
    Math.abs(trip.value.start_lat - trip.value.end_lat) > 0.0001 ||
    Math.abs(trip.value.start_lon - trip.value.end_lon) > 0.0001
  );
});

const tripSummary = computed(() => {
  const total = places.value.length;
  const visited = places.value.filter((p) => p.status === "done").length;
  const skipped = places.value.filter((p) => p.status === "skipped").length;
  return { total, visited, skipped };
});

const timeUsedPercent = computed(() => {
  if (!trip.value) return 0;
  const [sh, sm] = (trip.value.start_time || "09:00").split(":").map(Number);
  const [eh, em] = (trip.value.end_time || "18:00").split(":").map(Number);
  const totalMin = eh * 60 + em - (sh * 60 + sm);
  if (totalMin <= 0) return 0;
  const now = new Date();
  const elapsed = now.getHours() * 60 + now.getMinutes() - (sh * 60 + sm);
  return Math.min(100, Math.max(0, (elapsed / totalMin) * 100));
});

watch(
  () => trip.value?.city,
  (city) => {
    document.title = city ? `PathFinder — ${city}` : "PathFinder";
  },
);

function getMarkerIcon(place) {
  const f = feasibility.value.get(place.id);
  if (!f) return new L.Icon.Default();
  return feasIconMap[f.color] || new L.Icon.Default();
}

function feasColorCss(placeId) {
  const f = feasibility.value.get(placeId);
  if (!f) return "#8b5cf6"; // violet for unknown
  const map = {
    green: "#22c55e",
    yellow: "#eab308",
    red: "#ef4444",
    gray: "#9ca3af",
    unknown: "#8b5cf6",
  };
  return map[f.color] || "#8b5cf6";
}

function feasReason(placeId) {
  const f = feasibility.value.get(placeId);
  return f ? f.reason || "" : "";
}

async function loadTrip() {
  try {
    const data = await getTrip(tripId);
    trip.value = data;
    places.value = data.places || [];
    updateMapMarkers();
  } catch (e) {
    loadError.value = `Failed to load trip: ${e.message}`;
  }
}

async function loadFeasibility() {
  if (!trip.value) return;
  feasLoading.value = true;
  const lat = userLat.value ?? trip.value.start_lat;
  const lon = userLon.value ?? trip.value.start_lon;
  try {
    const data = await getFeasibility(tripId, lat, lon);
    const m = new Map();
    if (data && Array.isArray(data.places)) {
      for (const item of data.places) {
        m.set(item.place_id, item);
      }
    }
    feasibility.value = m;
    updateMapMarkers();
  } catch (e) {
    if (isOffline.value) return; // don't toast when offline
    showToast("Could not refresh feasibility data");
  } finally {
    feasLoading.value = false;
  }
}

function initMap() {
  map = L.map("map").setView([47.4979, 19.0402], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  markersLayer = L.layerGroup().addTo(map);
  searchMarkersLayer = L.layerGroup().addTo(map);
  trajectoryLayerGroup = L.layerGroup().addTo(map);

  map.on("click", async (e) => {
    const { lat, lng } = e.latlng;

    if (settingPosition.value) {
      userLat.value = lat;
      userLon.value = lng;
      updateUserPositionMarker(lat, lng);
      settingPosition.value = false;
      map.getContainer().style.cursor = "";
      loadFeasibility();
      return;
    }

    if (addingByClick.value) {
      addingByClick.value = false;
      map.getContainer().style.cursor = "";
      clickAddLat.value = lat;
      clickAddLon.value = lng;
      // Prefill name via reverse-geocode (best-effort, don't block the modal)
      clickAddName.value = "";
      showClickAddModal.value = true;
      try {
        const res = await fetch(
          `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lng}&format=json`,
          { headers: { "User-Agent": "PathFinder/2.0" } },
        );
        if (res.ok) {
          const data = await res.json();
          clickAddName.value =
            data.name ||
            data.address?.tourism ||
            data.address?.amenity ||
            data.address?.road ||
            "";
        }
      } catch {
        // Prefill failed — user can type a name manually
      }
    }
  });
}

function updateUserPositionMarker(lat, lon) {
  if (userPositionMarker) {
    userPositionMarker.setLatLng([lat, lon]);
  } else if (map) {
    userPositionMarker = L.circleMarker([lat, lon], {
      radius: 10,
      color: "#3b82f6",
      fillColor: "#3b82f6",
      fillOpacity: 0.5,
      weight: 2,
      className: "user-position-pulse",
    }).addTo(map);
    userPositionMarker.bindPopup("Your position");
  }
}

function updateMapMarkers() {
  if (!map || !trip.value) return;
  markersLayer.clearLayers();

  const t = trip.value;
  L.marker([t.start_lat, t.start_lon], { icon: greenIcon })
    .bindPopup("Start")
    .addTo(markersLayer);
  L.marker([t.end_lat, t.end_lon], { icon: redIcon })
    .bindPopup("End")
    .addTo(markersLayer);

  for (const p of places.value) {
    L.marker([p.lat, p.lon], { icon: getMarkerIcon(p) })
      .bindPopup(p.name)
      .addTo(markersLayer);
  }

  map.setView([t.start_lat, t.start_lon], 13);
}

function updateSearchMarkers() {
  if (!map) return;
  searchMarkersLayer.clearLayers();
  for (const r of searchResults.value) {
    L.marker([r.lat, r.lon], { icon: orangeIcon })
      .bindPopup(r.name)
      .addTo(searchMarkersLayer);
  }
}

async function doSearch() {
  if (!searchQuery.value.trim() || !trip.value) return;
  searching.value = true;
  try {
    const results = await searchPlaces(
      searchQuery.value,
      trip.value.start_lat,
      trip.value.start_lon,
    );
    searchResults.value = Array.isArray(results) ? results : [];
    updateSearchMarkers();
  } catch (e) {
    searchResults.value = [];
  } finally {
    searching.value = false;
  }
}

async function handleAddPlace(result) {
  try {
    await addPlace(tripId, {
      name: result.name,
      lat: result.lat,
      lon: result.lon,
      category: result.category || null,
      opening_hours: result.opening_hours || null,
    });
    searchResults.value = [];
    searchQuery.value = "";
    updateSearchMarkers();
    await loadTrip();
    await loadFeasibility();
  } catch (e) {
    showToast(`Failed to add place: ${e.message}`);
  }
}

async function handleDeletePlace(placeId) {
  try {
    await deletePlace(tripId, placeId);
    await loadTrip();
    await loadFeasibility();
  } catch (e) {
    showToast(`Failed to delete place: ${e.message}`);
  }
}

async function handlePriorityChange(place, newPriority) {
  try {
    await updatePlace(tripId, place.id, { priority: newPriority });
    place.priority = newPriority;
  } catch (e) {
    showToast(`Failed to update priority: ${e.message}`);
  }
}

async function handleDurationChange(place, newDuration) {
  try {
    await updatePlace(tripId, place.id, {
      estimated_duration_min: parseInt(newDuration) || 30,
    });
    place.estimated_duration_min = parseInt(newDuration) || 30;
  } catch (e) {
    showToast(`Failed to update duration: ${e.message}`);
  }
}

async function askWhatNext() {
  if (!trip.value) return;
  nextLoading.value = true;
  nextSkipIndex.value = 0;
  try {
    const lat = userLat.value ?? trip.value.start_lat;
    const lon = userLon.value ?? trip.value.start_lon;
    nextRecs.value = await getNextRecommendation(tripId, lat, lon);
    // Highlight top recommendation on map
    highlightRecommendation();
  } catch (e) {
    showToast(`What Next? failed: ${e.message}`);
    nextRecs.value = null;
  } finally {
    nextLoading.value = false;
  }
}

function skipRecommendation() {
  if (!nextRecs.value) return;
  const recs = nextRecs.value.recommendations || [];
  if (nextSkipIndex.value < recs.length - 1) {
    nextSkipIndex.value++;
    highlightRecommendation();
  } else {
    // No more alternatives
    nextRecs.value = null;
  }
}

const TRANSPORT_MODE_MAP = {
  foot: "walking",
  car: "driving",
  bicycle: "bicycling",
};

function getLastPosition() {
  if (trajectorySegments.value.length > 0) {
    const last = trajectorySegments.value[trajectorySegments.value.length - 1];
    return { lat: last.to_lat, lon: last.to_lon };
  }
  if (userLat.value != null) return { lat: userLat.value, lon: userLon.value };
  if (trip.value)
    return { lat: trip.value.start_lat, lon: trip.value.start_lon };
  return null;
}

function openGoogleMaps(destLat, destLon) {
  const origin = getLastPosition();
  if (!origin) return;
  const mode = TRANSPORT_MODE_MAP[trip.value?.transport_mode] || "walking";
  window.open(
    `https://www.google.com/maps/dir/?api=1&origin=${origin.lat},${origin.lon}&destination=${destLat},${destLon}&travelmode=${mode}`,
    "_blank",
  );
}

function navigateToPlace(rec) {
  const place = places.value.find((p) => p.id === rec.place_id);
  if (!place) return;
  pendingArrivalPlace.value = {
    id: place.id,
    name: place.name,
    lat: place.lat,
    lon: place.lon,
  };
  openGoogleMaps(place.lat, place.lon);
  dismissNextCard();
}

function navigateToFinalDestination() {
  if (!trip.value) return;
  openGoogleMaps(trip.value.end_lat, trip.value.end_lon);
}

let highlightMarker = null;
function highlightRecommendation() {
  if (highlightMarker && map) {
    map.removeLayer(highlightMarker);
    highlightMarker = null;
  }
  if (!nextRecs.value || !map) return;
  const recs = nextRecs.value.recommendations || [];
  const rec = recs[nextSkipIndex.value];
  if (!rec) return;
  const place = places.value.find((p) => p.id === rec.place_id);
  if (!place) return;
  highlightMarker = L.circleMarker([place.lat, place.lon], {
    radius: 18,
    color: "#f59e0b",
    fillColor: "#f59e0b",
    fillOpacity: 0.3,
    weight: 3,
    className: "rec-pulse",
  }).addTo(map);
  map.setView([place.lat, place.lon], 15);
}

async function handleCheckin(placeId, action) {
  try {
    checkinLoading.value = true;
    const result = await checkinPlace(tripId, placeId, action);
    showArrivePicker.value = false;

    // Draw trajectory segment on arrival
    if (action === "arrived" && result.trajectory_segment) {
      trajectorySegments.value.push(result.trajectory_segment);
      drawTrajectorySegment(result.trajectory_segment);
    }
    if (action === "arrived") {
      pendingArrivalPlace.value = null;
    }

    await loadTrip();
    await loadFeasibility();

    // Dismiss stale recs when all places are done
    if (pendingPlaces.value.length === 0) {
      dismissNextCard();
    }
  } catch (e) {
    showToast(`Check-in failed: ${e.message}`);
  } finally {
    checkinLoading.value = false;
  }
}

function sortedByDistance(placeList) {
  if (userLat.value == null || userLon.value == null) return placeList;
  return [...placeList].sort((a, b) => {
    const dA = (a.lat - userLat.value) ** 2 + (a.lon - userLon.value) ** 2;
    const dB = (b.lat - userLat.value) ** 2 + (b.lon - userLon.value) ** 2;
    return dA - dB;
  });
}

function placeName(name) {
  if (!name) return name;
  const comma = name.indexOf(",");
  return comma === -1 ? name : name.slice(0, comma).trim();
}

function dismissNextCard() {
  nextRecs.value = null;
  if (highlightMarker && map) {
    map.removeLayer(highlightMarker);
    highlightMarker = null;
  }
}

function handleVisibilityChange() {
  if (document.visibilityState === "visible" && trip.value) {
    loadFeasibility();
  }
}

function connectStream() {
  if (eventSource) eventSource.close();
  const lat = userLat.value ?? trip.value?.start_lat;
  const lon = userLon.value ?? trip.value?.start_lon;
  eventSource = connectTripStream(tripId, lat, lon, {
    onFeasibilityUpdate(data) {
      const m = new Map();
      if (data && Array.isArray(data.places)) {
        for (const item of data.places) {
          m.set(item.place_id, item);
        }
      }
      feasibility.value = m;
      updateMapMarkers();
    },
    onUrgencyAlert(alert) {
      const id = Date.now() + Math.random();
      alerts.value.push({ id, ...alert });
      // Keep max 5 visible
      if (alerts.value.length > 5) alerts.value.shift();
      // Auto-dismiss after 30s
      setTimeout(() => dismissAlert(id), 30000);
    },
    onError() {
      // EventSource will auto-reconnect
    },
  });
}

function dismissAlert(id) {
  alerts.value = alerts.value.filter((a) => a.id !== id);
}

function showToast(message, type = "error") {
  const id = Date.now() + Math.random();
  toasts.value.push({ id, message, type });
  if (toasts.value.length > 5) toasts.value.shift();
  setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id);
  }, 5000);
}

function copyTripUrl() {
  navigator.clipboard
    .writeText(window.location.href)
    .then(() => showToast("Trip URL copied!", "success"))
    .catch(() => showToast("Failed to copy URL"));
}

function handleOnline() {
  isOffline.value = false;
  showToast("Back online", "success");
  if (trip.value) {
    loadFeasibility();
    connectStream();
  }
}

function handleOffline() {
  isOffline.value = true;
}

function reconnectStream() {
  if (trip.value) connectStream();
}

function toggleSetPosition() {
  addingByClick.value = false;
  settingPosition.value = !settingPosition.value;
  if (map) {
    map.getContainer().style.cursor = settingPosition.value ? "crosshair" : "";
  }
}

function toggleAddByClick() {
  settingPosition.value = false;
  addingByClick.value = !addingByClick.value;
  if (map) {
    map.getContainer().style.cursor = addingByClick.value ? "crosshair" : "";
  }
}

function cancelClickAdd() {
  showClickAddModal.value = false;
  clickAddName.value = "";
  clickAddPriority.value = "want";
  clickAddDuration.value = 30;
  clickAddCategory.value = "";
}

async function confirmClickAdd() {
  const name = clickAddName.value.trim();
  if (!name) return;
  try {
    await addPlace(tripId, {
      name,
      lat: clickAddLat.value,
      lon: clickAddLon.value,
      category: clickAddCategory.value.trim() || null,
      estimated_duration_min: clickAddDuration.value,
      priority: clickAddPriority.value,
    });
    cancelClickAdd();
    await loadTrip();
    await loadFeasibility();
  } catch (e) {
    showToast(`Failed to add place: ${e.message}`);
  }
}

// --- Trajectory ---

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

function drawTrajectorySegment(segment) {
  if (!map || !trajectoryLayerGroup) return;
  let latlngs;
  if (segment.geometry) {
    latlngs = decodePolyline(segment.geometry);
  } else {
    latlngs = [
      [segment.from_lat, segment.from_lon],
      [segment.to_lat, segment.to_lon],
    ];
  }
  L.polyline(latlngs, {
    color: "#6366f1",
    weight: 3,
    opacity: 0.5,
    lineCap: "round",
    lineJoin: "round",
  }).addTo(trajectoryLayerGroup);
}

async function loadTrajectory() {
  if (!tripId) return;
  try {
    const data = await getTrajectory(tripId);
    trajectorySegments.value = data.segments || [];
    if (trajectoryLayerGroup) trajectoryLayerGroup.clearLayers();
    for (const seg of trajectorySegments.value) {
      drawTrajectorySegment(seg);
    }
  } catch (e) {
    console.warn("Failed to load trajectory:", e);
  }
}

async function changeTransportMode(newMode) {
  if (!trip.value || newMode === trip.value.transport_mode) return;
  modeChanging.value = true;
  try {
    const updated = await updateTrip(tripId, { transport_mode: newMode });
    trip.value.transport_mode = updated.transport_mode;
    await loadFeasibility();
    connectStream();
    if (nextRecs.value) await askWhatNext();
  } catch (e) {
    showToast(`Failed to switch mode: ${e.message}`);
  } finally {
    modeChanging.value = false;
  }
}

onMounted(async () => {
  initMap();
  await loadTrip();
  await loadTrajectory();

  // Request geolocation
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        userLat.value = pos.coords.latitude;
        userLon.value = pos.coords.longitude;
        updateUserPositionMarker(pos.coords.latitude, pos.coords.longitude);
        loadFeasibility();
        connectStream();
      },
      () => {
        // Denied or error — use trip start as fallback
        loadFeasibility();
        connectStream();
      },
      { timeout: 5000 },
    );
  } else {
    loadFeasibility();
    connectStream();
  }

  document.addEventListener("visibilitychange", handleVisibilityChange);
  window.addEventListener("online", handleOnline);
  window.addEventListener("offline", handleOffline);
});

onUnmounted(() => {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  if (map) {
    map.remove();
    map = null;
    userPositionMarker = null;
  }
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  window.removeEventListener("online", handleOnline);
  window.removeEventListener("offline", handleOffline);
});
</script>

<template>
  <div class="dashboard">
    <!-- Toast notifications -->
    <div class="toast-container">
      <div
        v-for="t in toasts"
        :key="t.id"
        :class="['toast', t.type]"
        @click="toasts = toasts.filter((x) => x.id !== t.id)"
      >
        {{ t.message }}
      </div>
    </div>

    <div class="map-container">
      <div id="map"></div>
    </div>

    <div class="sidebar">
      <!-- Offline indicator -->
      <div v-if="isOffline" class="offline-banner">
        You are offline — showing last known data
      </div>

      <!-- Urgency alert banners -->
      <div
        v-for="a in alerts"
        :key="a.id"
        :class="['alert-banner', a.severity]"
        @click="dismissAlert(a.id)"
      >
        <strong>{{ a.place_name }}:</strong> {{ a.message }}
        <span class="alert-dismiss">&times;</span>
      </div>

      <div v-if="loadError" class="error">{{ loadError }}</div>

      <!-- Trip Ended state -->
      <div v-if="trip && tripEnded" class="trip-ended-banner">
        <h3>Trip ended</h3>
        <p>
          You visited {{ tripSummary.visited }} of
          {{ tripSummary.total }} places
          <span v-if="tripSummary.skipped">
            ({{ tripSummary.skipped }} skipped)
          </span>
        </p>
        <button class="btn btn-primary" @click="router.push('/')">
          Back to Home
        </button>
      </div>

      <!-- All Places Done summary -->
      <div v-if="trip && allPlacesDone && !tripEnded" class="all-done-banner">
        <h3>All done!</h3>
        <p>
          You visited {{ tripSummary.visited }} of
          {{ tripSummary.total }} places!
          <span v-if="tripSummary.skipped">
            ({{ tripSummary.skipped }} skipped)
          </span>
        </p>
        <button class="btn btn-secondary" @click="router.push('/')">
          Back to Home
        </button>
      </div>

      <div v-if="trip" class="trip-header">
        <h2>
          {{ trip.city }}
          <button
            class="btn btn-small btn-copy"
            @click="copyTripUrl"
            title="Copy trip URL"
          >
            Share
          </button>
        </h2>
        <p>
          {{ trip.date }} &middot; {{ trip.start_time }}&ndash;{{
            trip.end_time
          }}
          &middot;
          <select
            class="mode-select"
            :value="trip.transport_mode"
            :disabled="modeChanging"
            @change="changeTransportMode($event.target.value)"
          >
            <option value="foot">Walking</option>
            <option value="car">Driving</option>
            <option value="bicycle">Cycling</option>
          </select>
          <span v-if="modeChanging" class="mode-loading">updating...</span>
        </p>
      </div>

      <div v-if="trip" class="time-budget">
        <div class="budget-bar">
          <div
            class="budget-fill"
            :style="{ width: timeUsedPercent + '%' }"
          ></div>
        </div>
        <span>{{ remainingMinutes }} min remaining</span>
      </div>

      <div v-if="trip" class="stats-line">
        Visited: {{ visitedCount }} &middot; Remaining:
        {{ remainingCount }} &middot; Reachable: {{ reachableCount }}
        <button
          :class="['btn', 'btn-small', settingPosition ? 'btn-active' : '']"
          @click="toggleSetPosition"
          title="Click this, then click anywhere on the map to set your current position for feasibility calculations"
        >
          {{ settingPosition ? "Click map to pin..." : "Pin my location" }}
        </button>
        <button
          v-if="!allPlacesDone"
          :class="['btn', 'btn-small', addingByClick ? 'btn-active' : '']"
          @click="toggleAddByClick"
          title="Click this, then click anywhere on the map to add a destination at that location"
        >
          {{ addingByClick ? "Click map to place..." : "+ Add by clicking" }}
        </button>
        <button
          class="btn btn-small btn-refresh"
          @click="
            () => {
              loadFeasibility();
              reconnectStream();
            }
          "
        >
          Refresh
        </button>
      </div>

      <!-- Head to final destination / back to start -->
      <div v-if="trip && allPlacesDone" class="final-destination-banner">
        <p v-if="isOpenTrip">
          All places visited! Head to your final destination.
        </p>
        <p v-else>All places visited! Head back to your starting point.</p>
        <button class="btn btn-primary" @click="navigateToFinalDestination">
          {{ isOpenTrip ? "Go to Final Destination" : "Head Back to Start" }}
        </button>
        <button class="btn btn-secondary" @click="router.push('/')">
          Back to Home
        </button>
      </div>

      <!-- What Next? — hidden when all places are done -->
      <div v-if="!allPlacesDone" class="next-section">
        <button
          class="btn btn-next"
          @click="askWhatNext"
          :disabled="nextLoading || !pendingPlaces.length"
          :title="
            pendingPlaces.length ? '' : 'Add places to enable recommendations'
          "
        >
          {{
            nextLoading
              ? "Thinking..."
              : pendingPlaces.length
                ? "What Next?"
                : "No places"
          }}
        </button>
        <p v-if="!pendingPlaces.length" class="next-helper">
          No pending places — add some to get recommendations.
        </p>

        <div v-if="nextRecs" class="next-card">
          <button class="next-dismiss" @click="dismissNextCard">&times;</button>

          <template
            v-if="nextRecs.recommendations && nextRecs.recommendations.length"
          >
            <div class="next-primary">
              <div class="next-arrow">&rarr;</div>
              <div class="next-details">
                <strong>{{
                  nextRecs.recommendations[nextSkipIndex]?.place_name
                }}</strong>
                <span class="next-travel"
                  >{{
                    nextRecs.recommendations[nextSkipIndex]?.travel_minutes
                  }}
                  min {{ trip?.transport_mode || "walk" }}</span
                >
                <span class="next-reason">{{
                  nextRecs.recommendations[nextSkipIndex]?.reason
                }}</span>
              </div>
              <div class="next-actions">
                <button
                  class="btn btn-primary btn-small"
                  @click="
                    navigateToPlace(nextRecs.recommendations[nextSkipIndex])
                  "
                >
                  Go
                </button>
                <button class="btn btn-small" @click="skipRecommendation">
                  Skip
                </button>
              </div>
            </div>

            <div v-if="nextRecs.recommendations.length > 1" class="next-alts">
              <span class="next-alts-label">Also good:</span>
              <div
                v-for="(alt, i) in nextRecs.recommendations"
                :key="alt.place_id"
              >
                <span v-if="i !== nextSkipIndex" class="next-alt-item">
                  {{ alt.place_name }} &mdash; {{ alt.travel_minutes }} min,
                  {{ alt.reason }}
                </span>
              </div>
            </div>
          </template>

          <div v-else class="next-empty">
            {{
              nextRecs.message || "No reachable places. Head to your endpoint."
            }}
          </div>
        </div>
      </div>

      <div v-if="!allPlacesDone" class="search-section">
        <h3 class="section-title">Add a stop</h3>
        <form class="search-bar" @submit.prevent="doSearch">
          <input
            v-model="searchQuery"
            type="text"
            placeholder="Search for a place to add..."
          />
          <button type="submit" class="btn btn-primary" :disabled="searching">
            {{ searching ? "..." : "Search" }}
          </button>
        </form>

        <ul v-if="searchResults.length" class="search-results">
          <li
            v-for="(r, i) in searchResults"
            :key="i"
            class="search-result-item"
          >
            <div>
              <strong>{{ r.name }}</strong>
              <span v-if="r.category" class="category">{{ r.category }}</span>
            </div>
            <button class="btn btn-small" @click="handleAddPlace(r)">
              Add
            </button>
          </li>
        </ul>
      </div>

      <!-- Check-in actions — hidden when all places are done -->
      <div v-if="trip && !allPlacesDone" class="checkin-actions">
        <!-- Pending arrival: user tapped Go on a recommendation -->
        <div
          v-if="pendingArrivalPlace && !visitingPlace"
          class="pending-arrival-card"
        >
          <p class="picker-label">Did you arrive at:</p>
          <strong>{{ placeName(pendingArrivalPlace.name) }}</strong>
          <div class="pending-arrival-actions">
            <button
              class="btn btn-primary"
              :disabled="checkinLoading"
              @click="handleCheckin(pendingArrivalPlace.id, 'arrived')"
            >
              Yes, I arrived
            </button>
            <button
              class="btn btn-small"
              @click="
                pendingArrivalPlace = null;
                showArrivePicker = true;
              "
            >
              I went somewhere else
            </button>
          </div>
        </div>

        <button
          v-if="!visitingPlace && !pendingArrivalPlace"
          class="btn btn-checkin btn-arrive"
          :disabled="checkinLoading || !pendingPlaces.length"
          @click="showArrivePicker = !showArrivePicker"
        >
          {{ showArrivePicker ? "Cancel" : "I arrived somewhere" }}
        </button>
        <button
          v-if="visitingPlace"
          class="btn btn-checkin btn-done"
          :disabled="checkinLoading"
          @click="handleCheckin(visitingPlace.id, 'done')"
        >
          Done visiting {{ placeName(visitingPlace.name) }}
        </button>

        <!-- Arrive picker -->
        <div v-if="showArrivePicker" class="arrive-picker">
          <p class="picker-label">Where did you arrive?</p>
          <ul class="picker-list">
            <li
              v-for="p in sortedByDistance(pendingPlaces)"
              :key="p.id"
              class="picker-item"
              @click="handleCheckin(p.id, 'arrived')"
            >
              <span class="feas-dot" :style="{ color: feasColorCss(p.id) }"
                >&#9679;</span
              >
              {{ placeName(p.name) }}
            </li>
          </ul>
        </div>
      </div>

      <!-- Now Visiting -->
      <div v-if="visitingPlace" class="place-section">
        <h3 class="section-title section-visiting">Now Visiting</h3>
        <div class="place-item place-visiting">
          <div class="place-info">
            <span class="feas-dot" style="color: #3b82f6">&#9679;</span>
            <strong>{{ placeName(visitingPlace.name) }}</strong>
            <span v-if="visitingPlace.category" class="category">{{
              visitingPlace.category
            }}</span>
            <span class="visiting-since"
              >Since
              {{
                new Date(visitingPlace.arrived_at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })
              }}</span
            >
          </div>
        </div>
      </div>

      <!-- Remaining (pending) -->
      <div class="place-section">
        <h3 class="section-title">Remaining ({{ pendingPlaces.length }})</h3>
        <!-- Loading skeleton -->
        <div v-if="feasLoading && pendingPlaces.length" class="skeleton-list">
          <div
            v-for="p in pendingPlaces"
            :key="'sk-' + p.id"
            class="skeleton-item"
          >
            <div class="skeleton-dot"></div>
            <div class="skeleton-text"></div>
          </div>
        </div>
        <p
          v-if="
            !pendingPlaces.length && !donePlaces.length && !skippedPlaces.length
          "
          class="empty"
        >
          No places added yet. Search above to find and add places.
        </p>
        <ul class="place-list">
          <li v-for="p in pendingPlaces" :key="p.id" class="place-item">
            <div class="place-info">
              <span class="feas-dot" :style="{ color: feasColorCss(p.id) }"
                >&#9679;</span
              >
              <strong>{{ placeName(p.name) }}</strong>
              <span v-if="p.category" class="category">{{ p.category }}</span>
              <span v-if="p.opening_hours" class="hours">{{
                p.opening_hours
              }}</span>
              <span v-if="feasReason(p.id)" class="feas-reason">{{
                feasReason(p.id)
              }}</span>
            </div>
            <div class="place-controls">
              <select
                :value="p.priority || 'want'"
                @change="handlePriorityChange(p, $event.target.value)"
              >
                <option value="must">Must</option>
                <option value="want">Want</option>
                <option value="if_time">If time</option>
              </select>
              <input
                type="number"
                :value="p.estimated_duration_min || 30"
                min="5"
                step="5"
                class="duration-input"
                title="Duration (min)"
                @blur="handleDurationChange(p, $event.target.value)"
              />
              <span class="duration-label">min</span>
              <button
                class="btn btn-small btn-skip"
                @click="handleCheckin(p.id, 'skipped')"
                :disabled="checkinLoading"
              >
                Skip
              </button>
              <button
                class="btn btn-danger btn-small"
                @click="handleDeletePlace(p.id)"
              >
                Remove
              </button>
            </div>
          </li>
        </ul>
      </div>

      <!-- Completed -->
      <div v-if="donePlaces.length" class="place-section">
        <h3 class="section-title section-done">
          Completed ({{ donePlaces.length }})
        </h3>
        <ul class="place-list">
          <li v-for="p in donePlaces" :key="p.id" class="place-item place-done">
            <div class="place-info">
              <span class="feas-dot" style="color: #22c55e">&#10003;</span>
              <strong>{{ placeName(p.name) }}</strong>
              <span v-if="p.category" class="category">{{ p.category }}</span>
            </div>
          </li>
        </ul>
      </div>

      <!-- Skipped -->
      <div v-if="skippedPlaces.length" class="place-section">
        <h3 class="section-title section-skipped">
          Skipped ({{ skippedPlaces.length }})
        </h3>
        <ul class="place-list">
          <li
            v-for="p in skippedPlaces"
            :key="p.id"
            class="place-item place-skipped"
          >
            <div class="place-info">
              <span class="feas-dot" style="color: #9ca3af">&#10005;</span>
              <strong>{{ placeName(p.name) }}</strong>
              <span v-if="p.category" class="category">{{ p.category }}</span>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </div>

  <!-- Click-to-add modal -->
  <div
    v-if="showClickAddModal"
    class="modal-overlay"
    @click.self="cancelClickAdd"
  >
    <div class="modal">
      <h3 class="modal-title">Add destination</h3>
      <p class="modal-coords">
        {{ clickAddLat?.toFixed(5) }}, {{ clickAddLon?.toFixed(5) }}
      </p>

      <label class="modal-label">
        Name
        <input
          v-model="clickAddName"
          type="text"
          class="modal-input"
          placeholder="Place name"
          autofocus
          @keyup.enter="confirmClickAdd"
          @keyup.escape="cancelClickAdd"
        />
      </label>

      <label class="modal-label">
        Category <span class="modal-hint">(optional)</span>
        <input
          v-model="clickAddCategory"
          type="text"
          class="modal-input"
          placeholder="e.g. cafe, museum, viewpoint"
        />
      </label>

      <label class="modal-label">
        Priority
        <select v-model="clickAddPriority" class="modal-input">
          <option value="must">Must visit</option>
          <option value="want">Want to visit</option>
          <option value="if_time">If time allows</option>
        </select>
      </label>

      <label class="modal-label">
        Stay duration (min)
        <input
          v-model.number="clickAddDuration"
          type="number"
          min="5"
          step="5"
          class="modal-input modal-input-narrow"
        />
      </label>

      <div class="modal-actions">
        <button
          class="btn btn-primary"
          :disabled="!clickAddName.trim()"
          @click="confirmClickAdd"
        >
          Add
        </button>
        <button class="btn" @click="cancelClickAdd">Cancel</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.map-container {
  flex: 0 0 60%;
  position: relative;
}

#map {
  width: 100%;
  height: 100%;
}

.sidebar {
  flex: 0 0 40%;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  border-left: 1px solid var(--border);
}

.trip-header h2 {
  margin: 0 0 4px;
}

.trip-header p {
  color: var(--text);
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.mode-select {
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg);
  color: var(--text-h);
  font-size: 13px;
  cursor: pointer;
}

.mode-select:disabled {
  opacity: 0.5;
  cursor: wait;
}

.mode-loading {
  font-size: 12px;
  color: var(--text);
  font-style: italic;
}

.time-budget {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  color: var(--text);
}

.budget-bar {
  flex: 1;
  height: 8px;
  background: var(--border);
  border-radius: 4px;
  overflow: hidden;
}

.budget-fill {
  height: 100%;
  background: #3b82f6;
  border-radius: 4px;
  transition: width 0.3s ease;
}

.stats-line {
  font-size: 13px;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn-refresh {
  margin-left: auto;
  font-size: 12px;
}

.search-bar {
  display: flex;
  gap: 8px;
}

.search-bar input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text-h);
  font-size: 15px;
}

.search-results {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.search-result-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 14px;
}

.category {
  display: inline-block;
  margin-left: 8px;
  font-size: 12px;
  color: var(--text);
  background: var(--accent-bg);
  padding: 2px 6px;
  border-radius: 4px;
}

.hours {
  display: block;
  font-size: 12px;
  color: var(--text);
}

.empty {
  font-size: 14px;
  color: var(--text);
}

.place-list {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.place-item {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.place-info strong {
  color: var(--text-h);
}

.place-info .feas-dot {
  font-size: 14px;
  margin-right: 4px;
}

.feas-reason {
  display: block;
  font-size: 11px;
  color: var(--text);
  font-style: italic;
  margin-top: 2px;
}

.place-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.place-controls select {
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg);
  color: var(--text-h);
  font-size: 13px;
}

.duration-input {
  width: 60px;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg);
  color: var(--text-h);
  font-size: 13px;
}

.duration-label {
  font-size: 12px;
  color: var(--text);
}

.btn-active {
  background: #3b82f6 !important;
  color: #fff !important;
}

.btn-next {
  width: 100%;
  padding: 12px;
  font-size: 16px;
  font-weight: 600;
  background: #f59e0b;
  color: #000;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s;
}
.btn-next:hover {
  background: #d97706;
}
.btn-next:disabled {
  opacity: 0.6;
  cursor: wait;
}

.next-card {
  position: relative;
  border: 2px solid #f59e0b;
  border-radius: 8px;
  padding: 14px;
  background: var(--bg);
}

.next-dismiss {
  position: absolute;
  top: 6px;
  right: 10px;
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
  color: var(--text);
}

.next-primary {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.next-arrow {
  font-size: 24px;
  color: #f59e0b;
  font-weight: bold;
  padding-top: 2px;
}

.next-details {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.next-details strong {
  color: var(--text-h);
  font-size: 15px;
}
.next-travel {
  font-size: 13px;
  color: var(--text);
}
.next-reason {
  font-size: 12px;
  color: var(--text);
  font-style: italic;
}

.next-actions {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.next-alts {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  font-size: 13px;
}
.next-alts-label {
  font-weight: 600;
  color: var(--text-h);
  display: block;
  margin-bottom: 4px;
}
.next-alt-item {
  display: block;
  color: var(--text);
  padding: 2px 0;
}

.next-empty {
  font-size: 14px;
  color: var(--text);
  text-align: center;
  padding: 8px 0;
}

.rec-pulse {
  animation: pulse-ring 1.5s ease-out infinite;
}
@keyframes pulse-ring {
  0% {
    opacity: 1;
  }
  100% {
    opacity: 0.3;
  }
}

.alert-banner {
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  animation: alert-slide-in 0.3s ease;
}
.alert-banner.warning {
  background: #fef3c7;
  border: 1px solid #f59e0b;
  color: #92400e;
}
.alert-banner.critical {
  background: #fee2e2;
  border: 1px solid #ef4444;
  color: #991b1b;
}
.alert-dismiss {
  margin-left: auto;
  font-size: 16px;
  font-weight: bold;
  opacity: 0.6;
}
@keyframes alert-slide-in {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.error {
  color: #ef4444;
  font-size: 14px;
}

.toast-container {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
}

.toast {
  pointer-events: auto;
  padding: 10px 16px;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  animation: toast-in 0.3s ease;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
}

.toast.error {
  background: #fee2e2;
  color: #991b1b;
  border: 1px solid #ef4444;
}

.toast.success {
  background: #dcfce7;
  color: #166534;
  border: 1px solid #22c55e;
}

@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateX(20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.offline-banner {
  padding: 8px 14px;
  background: #fef3c7;
  border: 1px solid #f59e0b;
  border-radius: 6px;
  color: #92400e;
  font-size: 13px;
  text-align: center;
}

.trip-ended-banner,
.all-done-banner {
  padding: 16px;
  border-radius: 8px;
  text-align: center;
}

.trip-ended-banner {
  background: var(--accent-bg);
  border: 1px solid var(--accent-border);
}

.trip-ended-banner h3 {
  color: var(--accent);
  margin-bottom: 4px;
}

.all-done-banner {
  background: #dcfce7;
  border: 1px solid #22c55e;
}

.all-done-banner h3 {
  color: #166534;
  margin-bottom: 4px;
}

.btn-copy {
  font-size: 12px;
  vertical-align: middle;
  margin-left: 8px;
}

.skeleton-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skeleton-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.skeleton-dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--border);
  animation: skeleton-pulse 1.5s ease-in-out infinite;
}

.skeleton-text {
  flex: 1;
  height: 14px;
  border-radius: 4px;
  background: var(--border);
  animation: skeleton-pulse 1.5s ease-in-out infinite;
}

@keyframes skeleton-pulse {
  0%,
  100% {
    opacity: 0.4;
  }
  50% {
    opacity: 1;
  }
}

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
}

.modal {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 24px;
  width: min(360px, 90vw);
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25);
}

.modal-title {
  margin: 0;
  font-size: 1.1rem;
  color: var(--text-h);
}

.modal-coords {
  margin: 0;
  font-size: 12px;
  color: var(--text);
  font-family: monospace;
}

.modal-label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: var(--text);
}

.modal-hint {
  font-weight: normal;
  opacity: 0.6;
}

.modal-input {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--bg);
  color: var(--text-h);
  font-size: 13px;
}

.modal-input-narrow {
  width: 80px;
}

.modal-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 4px;
}

.pending-arrival-card {
  background: rgba(99, 102, 241, 0.1);
  border: 1px solid rgba(99, 102, 241, 0.4);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pending-arrival-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.final-destination-banner {
  background: rgba(99, 102, 241, 0.15);
  border: 1px solid rgba(99, 102, 241, 0.5);
  border-radius: 8px;
  padding: 12px;
  text-align: center;
}

.final-destination-banner p {
  margin-bottom: 8px;
  font-weight: 500;
}

@media (max-width: 768px) {
  .dashboard {
    flex-direction: column;
  }
  .map-container {
    flex: 0 0 40vh;
  }
  .sidebar {
    flex: 1;
    border-left: none;
    border-top: 1px solid var(--border);
    padding: 12px;
  }
  .trip-header h2 {
    font-size: 1.2rem;
  }
  .place-controls {
    flex-wrap: wrap;
    gap: 6px;
  }
  .search-bar {
    flex-direction: column;
  }
  .next-primary {
    flex-direction: column;
  }
  .next-actions {
    flex-direction: row;
  }
  .stats-line {
    flex-wrap: wrap;
    font-size: 12px;
  }
  .toast-container {
    left: 16px;
    right: 16px;
  }
}
</style>
