async function _json(res) {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
  }
  return res.json();
}

export async function createTrip(data) {
  const res = await fetch("/api/trips", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return _json(res);
}

export async function updateTrip(id, data) {
  const res = await fetch(`/api/trips/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return _json(res);
}

export async function getTrip(id) {
  const res = await fetch(`/api/trips/${id}`);
  return _json(res);
}

export async function searchPlaces(query, lat, lon) {
  const res = await fetch(
    `/api/search?q=${encodeURIComponent(query)}&lat=${lat}&lon=${lon}`,
  );
  return _json(res);
}

export async function addPlace(tripId, place) {
  const res = await fetch(`/api/trips/${tripId}/places`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(place),
  });
  return _json(res);
}

export async function deletePlace(tripId, placeId) {
  const res = await fetch(`/api/trips/${tripId}/places/${placeId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
  }
}

export async function updatePlace(tripId, placeId, data) {
  const res = await fetch(`/api/trips/${tripId}/places/${placeId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return _json(res);
}

export async function geocode(query) {
  const res = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`);
  return _json(res);
}

export async function checkinPlace(tripId, placeId, action) {
  const res = await fetch(`/api/trips/${tripId}/checkin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ place_id: placeId, action }),
  });
  return _json(res);
}

export async function getNextRecommendation(tripId, lat, lon) {
  const params = new URLSearchParams();
  if (lat != null && lon != null) {
    params.set("lat", lat);
    params.set("lon", lon);
  }
  const res = await fetch(`/api/trips/${tripId}/next?${params}`);
  return _json(res);
}

export async function getFeasibility(tripId, lat, lon) {
  const params = new URLSearchParams();
  if (lat != null && lon != null) {
    params.set("lat", lat);
    params.set("lon", lon);
  }
  const res = await fetch(`/api/trips/${tripId}/feasibility?${params}`);
  return _json(res);
}

/**
 * Connect to the SSE stream for real-time feasibility updates and urgency alerts.
 * Returns the EventSource instance — caller must call .close() on unmount.
 */
/**
 * Connect to the baseline route optimization SSE stream.
 * Returns the EventSource instance — caller must call .close() on cleanup.
 */
export function connectBaselineStream(
  tripId,
  lat,
  lon,
  { onNNResult, onSwapAccept, onRoadSegment, onDone, onError },
) {
  const params = new URLSearchParams();
  if (lat != null) params.set("lat", lat);
  if (lon != null) params.set("lon", lon);
  const es = new EventSource(
    `/api/trips/${tripId}/baseline/stream?${params}`,
  );

  es.addEventListener("nn_result", (e) => {
    try {
      onNNResult(JSON.parse(e.data));
    } catch (err) {
      console.warn("SSE parse error (nn_result):", err);
    }
  });
  es.addEventListener("swap_accept", (e) => {
    try {
      onSwapAccept(JSON.parse(e.data));
    } catch (err) {
      console.warn("SSE parse error (swap_accept):", err);
    }
  });
  es.addEventListener("road_segment", (e) => {
    try {
      onRoadSegment(JSON.parse(e.data));
    } catch (err) {
      console.warn("SSE parse error (road_segment):", err);
    }
  });
  es.addEventListener("done", (e) => {
    try {
      onDone(JSON.parse(e.data));
    } catch (err) {
      console.warn("SSE parse error (done):", err);
    }
    es.close();
  });
  if (onError) es.onerror = onError;

  return es;
}

export function connectTripStream(
  tripId,
  lat,
  lon,
  { onFeasibilityUpdate, onUrgencyAlert, onError },
) {
  const params = new URLSearchParams();
  if (lat != null) params.set("lat", lat);
  if (lon != null) params.set("lon", lon);
  const es = new EventSource(`/api/trips/${tripId}/stream?${params}`);

  es.addEventListener("feasibility_update", (e) => {
    try {
      onFeasibilityUpdate(JSON.parse(e.data));
    } catch (err) {
      console.warn("SSE parse error (feasibility_update):", err);
    }
  });
  es.addEventListener("urgency_alert", (e) => {
    try {
      onUrgencyAlert(JSON.parse(e.data));
    } catch (err) {
      console.warn("SSE parse error (urgency_alert):", err);
    }
  });
  if (onError) es.onerror = onError;

  return es;
}
