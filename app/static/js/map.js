function setStatus(message) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = message;
}


function parsePlaces(raw) {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}


/**
 * Read an SSE stream from a fetch Response and call onEvent for each parsed JSON event.
 * onEvent may be async — it is awaited between events so the browser can repaint.
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
            await onEvent(JSON.parse(jsonStr));
          } catch (e) {
            console.warn("SSE parse error:", e, jsonStr);
          }
        }
      }
    }
  }
}


/**
 * Fetch the actual walking geometry from OSRM for a list of coordinates in visit order.
 * Queries each consecutive pair to get the real road-following path.
 *
 * @param {Array<{lat: number, lon: number}>} orderedCoords - Coordinates in route order
 * @param {Function|null} onSegment - Optional callback(segmentLatLngs, segmentIndex) called after each segment is fetched
 * @returns {Promise<Array<[number, number]>>} - Array of [lat, lon] for the full polyline
 */
async function fetchWalkingGeometry(orderedCoords, onSegment = null) {
  const allLatLngs = [];

  for (let i = 0; i < orderedCoords.length - 1; i++) {
    const from = orderedCoords[i];
    const to = orderedCoords[i + 1];
    const url =
      `https://router.project-osrm.org/route/v1/foot/` +
      `${from.lon},${from.lat};${to.lon},${to.lat}` +
      `?overview=full&geometries=geojson`;

    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        console.warn(`OSRM route request failed for segment ${i}: HTTP ${resp.status}`);
        allLatLngs.push([from.lat, from.lon]);
        if (onSegment) await onSegment([...allLatLngs], i);
        continue;
      }
      const data = await resp.json();
      if (data.code === "Ok" && data.routes && data.routes[0]) {
        const coords = data.routes[0].geometry.coordinates; // [lon, lat] pairs
        for (const [lon, lat] of coords) {
          allLatLngs.push([lat, lon]);
        }
      } else {
        allLatLngs.push([from.lat, from.lon]);
      }
    } catch (err) {
      console.warn(`OSRM route request error for segment ${i}:`, err);
      allLatLngs.push([from.lat, from.lon]);
    }

    if (onSegment) await onSegment([...allLatLngs], i);
  }

  // Ensure the last point is included
  if (orderedCoords.length > 0) {
    const last = orderedCoords[orderedCoords.length - 1];
    allLatLngs.push([last.lat, last.lon]);
  }

  return allLatLngs;
}


document.addEventListener("DOMContentLoaded", () => {
  const mapEl = document.getElementById("map");
  if (!mapEl) return;

  const londonLatLng = [51.5074, -0.1278];
  const map = L.map("map").setView(londonLatLng, 12);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  const markers = [];
  let routePolyline = null;

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
   * Draw a straight-line polyline through route indices (used for progress animation).
   */
  function drawRoute(routeIndices, geocodedCoords, color = "#0d6efd") {
    clearRoute();
    const latlngs = routeIndices
      .filter((i) => i < geocodedCoords.length)
      .map((i) => [geocodedCoords[i].lat, geocodedCoords[i].lon]);

    if (latlngs.length < 2) return;

    routePolyline = L.polyline(latlngs, {
      color: color,
      weight: 4,
      opacity: 0.8,
    }).addTo(map);
  }

  /**
   * Draw a polyline from raw [lat, lon] coordinate array (used for OSRM walking geometry).
   */
  function drawRawPolyline(latlngs, color = "#0d6efd") {
    clearRoute();
    if (latlngs.length < 2) return;

    routePolyline = L.polyline(latlngs, {
      color: color,
      weight: 4,
      opacity: 0.8,
    }).addTo(map);
  }

  /**
   * Small delay so the browser can repaint between progress events.
   */
  function animationDelay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /**
   * Format seconds-from-midnight as "HH:MM".
   */
  function secsToTime(secs) {
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }

  /**
   * Display time window info in the panel and allow user overrides.
   */
  function showTimeWindows(windows, geocodedCoords) {
    const panel = document.getElementById("time-windows-panel");
    const list = document.getElementById("time-windows-list");
    if (!panel || !list) return;

    list.innerHTML = "";
    for (const tw of windows) {
      const name = (geocodedCoords[tw.index] && geocodedCoords[tw.index].name) || `Place ${tw.index}`;
      const earliest = secsToTime(tw.window[0]);
      const latest = secsToTime(tw.window[1]);
      const sourceLabel = tw.overpass_hours
        ? `${tw.overpass_name}: ${tw.overpass_hours}`
        : tw.source;

      const row = document.createElement("div");
      row.className = "row mb-1 align-items-center small";
      row.innerHTML = `
        <div class="col-4 text-truncate" title="${name}">${name}</div>
        <div class="col-3">${earliest} - ${latest}</div>
        <div class="col-5 text-muted text-truncate" title="${sourceLabel}">${sourceLabel}</div>
      `;
      list.appendChild(row);
    }

    panel.style.display = "block";
  }

  /**
   * After geocoding, call /api/solve/stream with the coordinates and
   * progressively draw the route on the map as SSE events arrive.
   */
  async function solveRoute(geocodedCoords) {
    // OSRM expects [lon, lat] order
    const coordinates = geocodedCoords.map((c) => [c.lon, c.lat]);
    const names = geocodedCoords.map((c) => c.name || "");
    const visitDateEl = document.getElementById("visit-date");
    const visitDate = visitDateEl ? visitDateEl.value : "";

    setStatus("Fetching opening hours...");

    const body = { coordinates, names };
    if (visitDate) body.visit_date = visitDate;

    const resp = await fetch("/api/solve/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!resp.ok) {
      setStatus(`Route solving failed: HTTP ${resp.status}`);
      return;
    }

    let finalRoute = null;
    let finalCost = null;
    let saImprovements = 0;
    const totalPlaces = geocodedCoords.length;

    await readSSEStream(resp, async (event) => {
      if (event.type === "status") {
        setStatus(event.message);
      } else if (event.type === "time_windows") {
        showTimeWindows(event.windows, geocodedCoords);
        setStatus("Opening hours loaded. Fetching walking times...");
      } else if (event.type === "matrix") {
        setStatus(`Distance matrix ready (${event.size} places). Solving route...`);
      } else if (event.type === "progress") {
        const visited = event.route.length - 1;
        drawRoute(event.route, geocodedCoords, "#6c757d");
        setStatus(`Nearest Neighbor: visited ${visited} of ${totalPlaces} place(s)...`);
        await animationDelay(300);
      } else if (event.type === "nn_done") {
        drawRoute(event.route, geocodedCoords, "#6c757d");
        const minutes = (event.cost / 60).toFixed(1);
        setStatus(`Nearest Neighbor done (${minutes} min). Improving with Simulated Annealing...`);
        await animationDelay(400);
      } else if (event.type === "sa_progress") {
        saImprovements++;
        drawRoute(event.route, geocodedCoords, "#fd7e14");
        const minutes = (event.cost / 60).toFixed(1);
        setStatus(`SA: improvement #${saImprovements} — ${minutes} min`);
        await animationDelay(100);
      } else if (event.type === "sa_done") {
        finalRoute = event.route;
        finalCost = event.cost;
        drawRoute(event.route, geocodedCoords, "#0d6efd");
        const minutes = (event.cost / 60).toFixed(1);
        setStatus(`Route optimized! ${minutes} min (${saImprovements} SA improvements). Loading walking path...`);
      } else if (event.type === "error") {
        setStatus(`Solve error: ${event.message}`);
      }
    });

    if (!finalRoute) {
      setStatus("Route solving ended without a result.");
      return;
    }

    // Fetch real walking geometry from OSRM, animating segment by segment
    try {
      const orderedCoords = finalRoute
        .filter((i) => i < geocodedCoords.length)
        .map((i) => geocodedCoords[i]);

      const totalSegments = orderedCoords.length - 1;
      const walkingLatLngs = await fetchWalkingGeometry(orderedCoords, async (partialPath, segIdx) => {
        drawRawPolyline(partialPath, "#198754");
        const minutes = (finalCost / 60).toFixed(1);
        setStatus(`Loading walking path... segment ${segIdx + 1} of ${totalSegments} (${minutes} min)`);
      });
      drawRawPolyline(walkingLatLngs, "#0d6efd");
      const minutes = (finalCost / 60).toFixed(1);
      setStatus(`Route found! Total walking time: ${minutes} min`);
    } catch (err) {
      console.warn("Failed to fetch walking geometry, keeping straight lines:", err);
    }
  }

  const form = document.getElementById("places-form");
  const destinationInput = document.getElementById("destination-input");
  const input = document.getElementById("places-input");
  const solveBtn = document.getElementById("solve-btn");

  if (form && input && destinationInput) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const destination = destinationInput.value.trim();
      const places = parsePlaces(input.value);
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
      if (solveBtn) solveBtn.disabled = true;
      setStatus(`Geocoding ${places.length} place(s)...`);

      try {
        // --- Step 1: Geocode ---
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

        const geocodedCoords = [];
        let bounds = [];

        results.forEach((r) => {
          if (r.error) {
            console.warn("Geocode error:", r.name, r.error);
            return;
          }
          const lat = r.lat;
          const lon = r.lon;
          const m = L.marker([lat, lon])
            .addTo(map)
            .bindPopup(r.display_name || r.name);
          markers.push(m);
          bounds.push([lat, lon]);
          geocodedCoords.push({ lat, lon, name: r.name });
        });

        if (bounds.length > 0) {
          map.fitBounds(bounds, { padding: [40, 40] });
        }

        if (geocodedCoords.length === 0) {
          setStatus("No places could be geocoded. Please refine your input.");
          return;
        }

        if (geocodedCoords.length < places.length) {
          setStatus(
            `Geocoded ${geocodedCoords.length}/${places.length} place(s). Solving with found places...`
          );
        } else {
          setStatus(`Geocoded all ${geocodedCoords.length} place(s). Solving route...`);
        }

        // --- Step 2: Solve route ---
        if (geocodedCoords.length >= 2) {
          await solveRoute(geocodedCoords);
        } else {
          setStatus("Need at least 2 places to solve a route.");
        }
      } catch (err) {
        console.error(err);
        setStatus("Something went wrong. Please try again.");
      } finally {
        if (solveBtn) solveBtn.disabled = false;
      }
    });
  }
});
