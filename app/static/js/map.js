function setStatus(message) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = message;
}


function showWarning(message) {
  const container = document.getElementById("warning-container");
  if (!container) return;
  container.innerHTML = `<div class="alert alert-warning alert-dismissible fade show" role="alert">
    <strong>Warning:</strong> ${message}
    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
  </div>`;
}


function clearWarning() {
  const container = document.getElementById("warning-container");
  if (container) container.innerHTML = "";
}


function parsePlaces(raw) {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}


/**
 * Read an SSE stream from a fetch Response and call onEvent for each parsed JSON event.
 */
async function readSSEStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // keep incomplete line in buffer

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("data:")) {
        const jsonStr = trimmed.slice(5).trim();
        if (jsonStr) {
          try {
            onEvent(JSON.parse(jsonStr));
          } catch (e) {
            console.warn("SSE parse error:", e, jsonStr);
          }
        }
      }
    }
  }
}


document.addEventListener("DOMContentLoaded", () => {
  const mapEl = document.getElementById("map");
  if (!mapEl) return;

  const map = L.map("map").setView([48.8566, 2.3522], 12);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  const markers = [];
  let routePolyline = null;
  let geocodedCoords = null;

  function clearMarkers() {
    while (markers.length) {
      map.removeLayer(markers.pop());
    }
  }

  function clearRoute() {
    if (routePolyline) {
      map.removeLayer(routePolyline);
      routePolyline = null;
    }
  }

  /**
   * Draw route as straight lines using route indices (for NN/SA animation phases).
   */
  function drawRoute(routeIndices, coords, color = "#0d6efd") {
    clearRoute();
    const latlngs = routeIndices
      .filter((i) => i < coords.length)
      .map((i) => [coords[i].lat, coords[i].lon]);
    if (latlngs.length < 2) return;
    routePolyline = L.polyline(latlngs, { color, weight: 4, opacity: 0.8 }).addTo(map);
  }

  /**
   * Draw raw [lat, lon] array as a polyline (for walking geometry).
   */
  function drawRawPolyline(latlngs, color = "#0d6efd") {
    clearRoute();
    if (latlngs.length < 2) return;
    routePolyline = L.polyline(latlngs, { color, weight: 4, opacity: 0.85 }).addTo(map);
  }

  /**
   * Fetch walking geometry segment by segment (one OSRM call per leg).
   * onSegment(partialLatLngs, segmentIndex) is called after each leg arrives —
   * the caller can use it to animate the growing green path.
   */
  async function fetchWalkingGeometry(orderedCoords, onSegment = null) {
    const allLatLngs = [];

    for (let i = 0; i < orderedCoords.length - 1; i++) {
      try {
        const resp = await fetch("/api/route-geometry", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ coordinates: [orderedCoords[i], orderedCoords[i + 1]] }),
        });
        const data = await resp.json();

        if (data.error) {
          console.warn(`Segment ${i} geometry error:`, data.error);
        } else if (data.latlngs && data.latlngs.length >= 2) {
          // Skip the first point on subsequent segments to avoid duplication at junctions
          const segLatlngs = allLatLngs.length > 0 ? data.latlngs.slice(1) : data.latlngs;
          allLatLngs.push(...segLatlngs);
          if (onSegment) await onSegment([...allLatLngs], i);
        }
      } catch (e) {
        console.warn(`Walking segment ${i} failed:`, e);
      }
    }

    return allLatLngs;
  }

  /**
   * Animate the final route as road-snapped walking paths.
   * Segments appear in green one by one, then turn blue when all are loaded.
   */
  async function drawWalkingPath(finalRoute, coords, finalCost, saImprovements) {
    const orderedCoords = finalRoute
      .filter((i) => i < coords.length)
      .map((i) => [coords[i].lon, coords[i].lat]);

    const totalSegments = orderedCoords.length - 1;

    const walkingLatLngs = await fetchWalkingGeometry(
      orderedCoords,
      async (partialPath, segIdx) => {
        drawRawPolyline(partialPath, "#198754"); // Green while loading
        setStatus(`Loading walking path... segment ${segIdx + 1} of ${totalSegments}`);
      }
    );

    if (walkingLatLngs.length >= 2) {
      drawRawPolyline(walkingLatLngs, "#0d6efd"); // Blue when complete
    }

    const minutes = (finalCost / 60).toFixed(1);
    const saNote = saImprovements > 0 ? ` (${saImprovements} SA improvements)` : "";
    setStatus(`Route optimized! Total walking time: ${minutes} min${saNote}`);
  }

  /**
   * Two-phase route solving: NN (gray) → SA (orange) → walking geometry (green → blue).
   */
  async function solveRoute(coords, locations, timeWindowsOverride) {
    const coordinates = coords.map((c) => [c.lon, c.lat]);

    clearWarning();
    setStatus("Fetching walking times from OSRM...");

    const resp = await fetch("/api/solve/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ coordinates, locations, time_windows_override: timeWindowsOverride }),
    });

    if (!resp.ok) {
      setStatus(`Route solving failed: HTTP ${resp.status}`);
      return;
    }

    let finalRoute = null;
    let finalCost = null;
    let saImprovements = 0;
    const totalPlaces = coords.length;

    await readSSEStream(resp, (event) => {
      if (event.type === "matrix") {
        setStatus(`Distance matrix ready (${event.size} places). Solving route...`);
      } else if (event.type === "status") {
        setStatus(event.message);
      } else if (event.type === "opening_hours") {
        setStatus("Opening hours loaded. Running Nearest Neighbor...");
      } else if (event.type === "progress") {
        const visited = event.route.length - 1; // don't count start duplicate
        drawRoute(event.route, coords, "#6c757d"); // Gray
        setStatus(`Nearest Neighbor: visited ${visited} of ${totalPlaces} place(s)...`);
      } else if (event.type === "nn_done") {
        drawRoute(event.route, coords, "#6c757d"); // Gray
        const minutes = (event.cost / 60).toFixed(1);
        setStatus(`NN done (${minutes} min). Improving with Simulated Annealing...`);
      } else if (event.type === "sa_progress") {
        saImprovements++;
        drawRoute(event.route, coords, "#fd7e14"); // Orange
        const minutes = (event.cost / 60).toFixed(1);
        setStatus(`SA: improvement #${saImprovements} — ${minutes} min`);
      } else if (event.type === "sa_done") {
        finalRoute = event.route;
        finalCost = event.cost;
        drawRoute(event.route, coords, "#0d6efd"); // Blue straight-line placeholder
      } else if (event.type === "feasibility") {
        if (!event.feasible && event.warning) {
          showWarning(event.warning);
        }
      } else if (event.type === "error") {
        setStatus(`Solve error: ${event.message}`);
      }
    });

    if (finalRoute) {
      await drawWalkingPath(finalRoute, coords, finalCost, saImprovements);
    } else {
      setStatus("Route solving ended without a result.");
    }
  }

  // --- UI element references ---
  const geocodeForm = document.getElementById("geocode-form");
  const destinationInput = document.getElementById("destination-input");
  const placesInput = document.getElementById("places-input");
  const placesTableContainer = document.getElementById("places-table-container");
  const placesTbody = document.getElementById("places-tbody");
  const geocodeBtn = document.getElementById("geocode-btn");
  const solveBtn = document.getElementById("solve-btn");

  // --- Step 1: Geocode ---
  if (geocodeForm) {
    geocodeForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const destination = destinationInput.value.trim();
      const places = parsePlaces(placesInput.value);

      if (!destination) {
        setStatus("Please enter a destination city.");
        return;
      }
      if (places.length === 0) {
        setStatus("Please enter at least one place.");
        return;
      }

      clearMarkers();
      clearRoute();
      geocodedCoords = null;
      if (placesTableContainer) placesTableContainer.style.display = "none";
      if (geocodeBtn) geocodeBtn.disabled = true;
      setStatus(`Geocoding ${places.length} place(s)...`);

      try {
        const resp = await fetch("/api/geocode", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ destination, places }),
        });

        if (!resp.ok) {
          setStatus(`Geocoding failed: HTTP ${resp.status}`);
          return;
        }

        const data = await resp.json();
        const results = Array.isArray(data.results) ? data.results : [];
        const coords = [];
        const bounds = [];

        results.forEach((r) => {
          if (r.error) {
            console.warn("Geocode error:", r.name, r.error);
            return;
          }
          const m = L.marker([r.lat, r.lon])
            .addTo(map)
            .bindPopup(r.display_name || r.name);
          markers.push(m);
          bounds.push([r.lat, r.lon]);
          coords.push({ lat: r.lat, lon: r.lon, name: r.name });
        });

        if (bounds.length > 0) map.fitBounds(bounds, { padding: [40, 40] });

        if (coords.length === 0) {
          setStatus("No places could be geocoded. Please refine your input.");
          return;
        }

        geocodedCoords = coords;

        // Populate per-place time window table
        if (placesTbody && placesTableContainer) {
          placesTbody.innerHTML = "";
          coords.forEach((c, idx) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
              <td class="text-muted">${idx + 1}</td>
              <td>${c.name}</td>
              <td><input type="text" class="form-control form-control-sm tw-open" placeholder="09:00" style="width:80px"></td>
              <td><input type="text" class="form-control form-control-sm tw-close" placeholder="21:00" style="width:80px"></td>
              <td class="text-muted small tw-osm">—</td>
            `;
            placesTbody.appendChild(tr);
          });
          placesTableContainer.style.display = "block";
        }

        const note =
          coords.length < places.length ? ` (${coords.length}/${places.length} found)` : "";
        setStatus(
          `Geocoded ${coords.length} place(s)${note}. Set optional time windows and click Solve Route.`
        );
      } catch (err) {
        console.error(err);
        setStatus("Geocoding failed. Please try again.");
      } finally {
        if (geocodeBtn) geocodeBtn.disabled = false;
      }
    });
  }

  // --- Step 2: Solve ---
  if (solveBtn) {
    solveBtn.addEventListener("click", async () => {
      if (!geocodedCoords || geocodedCoords.length < 2) {
        setStatus("Please geocode at least 2 places first.");
        return;
      }

      // Collect optional time window overrides from table inputs
      const timeWindowsOverride = {};
      if (placesTbody) {
        placesTbody.querySelectorAll("tr").forEach((row, idx) => {
          const opens = row.querySelector(".tw-open")?.value.trim();
          const closes = row.querySelector(".tw-close")?.value.trim();
          if (opens && closes) {
            timeWindowsOverride[String(idx)] = { earliest: opens, latest: closes };
          }
        });
      }

      // Build locations list for Overpass opening-hours lookup
      const locations = geocodedCoords.map((c) => ({ name: c.name, lat: c.lat, lon: c.lon }));

      clearRoute();
      solveBtn.disabled = true;
      try {
        await solveRoute(geocodedCoords, locations, timeWindowsOverride);
      } catch (err) {
        console.error(err);
        setStatus("Something went wrong. Please try again.");
      } finally {
        solveBtn.disabled = false;
      }
    });
  }
});
