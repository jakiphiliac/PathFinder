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

document.addEventListener("DOMContentLoaded", () => {
  const mapEl = document.getElementById("map");
  if (!mapEl) return;

  // Initial view: London (will recenter once destination results arrive)
  const londonLatLng = [51.5074, -0.1278];
  const map = L.map("map").setView(londonLatLng, 12);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  const markers = [];

  function clearMarkers() {
    while (markers.length) {
      const m = markers.pop();
      map.removeLayer(m);
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
      if (solveBtn) {
        solveBtn.disabled = true;
      }
      setStatus(`Geocoding ${places.length} place(s)...`);

      try {
        const resp = await fetch("/api/geocode", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ destination, places }),
        });

        if (!resp.ok) {
          setStatus(`Geocoding failed: HTTP ${resp.status}`);
          return;
        }

        const data = await resp.json();
        const results = Array.isArray(data.results) ? data.results : [];

        let successCount = 0;
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
          successCount += 1;
        });

        if (bounds.length > 0) {
          map.fitBounds(bounds, { padding: [40, 40] });
        } else {
          // No markers were added; keep initial view.
        }

        if (successCount === 0) {
          setStatus("No places could be geocoded. Please refine your input.");
        } else if (successCount === places.length) {
          setStatus(`Geocoded all ${successCount} place(s).`);
        } else {
          setStatus(
            `Geocoded ${successCount}/${places.length} place(s). Some were not found.`
          );
        }
      } catch (err) {
        console.error(err);
        setStatus("Geocoding failed. Please try again.");
      } finally {
        if (solveBtn) {
          solveBtn.disabled = false;
        }
      }
    });
  }
});

