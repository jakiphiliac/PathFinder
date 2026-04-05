/**
 * Leaflet polyline helpers for baseline route visualization.
 *
 * Handles drawing NN routes, 2-opt candidates, and final OSRM road segments.
 */

import L from "leaflet";

// Color constants (extending v1 scheme)
export const ROUTE_COLORS = {
  nn: "#6c757d", // gray — initial NN solution
  twoOptCandidate: "#fd7e14", // orange — evaluating swap
  twoOptAccepted: "#198754", // green — accepted improvement
  road: "#0d6efd", // blue — final OSRM road geometry
};

/**
 * Decode a Google-encoded polyline string into an array of [lat, lng] pairs.
 * (OSRM uses the same encoding format.)
 */
function decodePolyline(encoded) {
  const points = [];
  let index = 0;
  let lat = 0;
  let lng = 0;

  while (index < encoded.length) {
    let b;
    let shift = 0;
    let result = 0;
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

/**
 * Convert [lon, lat] coords (backend format) to [lat, lng] (Leaflet format).
 */
function toLatLngs(coords) {
  return coords.map(([lon, lat]) => [lat, lon]);
}

/**
 * Draw the initial NN route as a gray dashed polyline.
 * @param {L.Map} map
 * @param {Array<[number,number]>} coords - [[lon,lat], ...] from backend
 * @param {L.LayerGroup} layerGroup - group to add the layer to
 * @returns {L.Polyline}
 */
export function drawNNRoute(map, coords, layerGroup) {
  const latLngs = toLatLngs(coords);
  const polyline = L.polyline(latLngs, {
    color: ROUTE_COLORS.nn,
    weight: 4,
    opacity: 0.8,
    dashArray: "8, 8",
  });
  layerGroup.addLayer(polyline);
  return polyline;
}

/**
 * Draw a 2-opt candidate/accepted route.
 * @param {L.Map} map
 * @param {Array<[number,number]>} coords - [[lon,lat], ...]
 * @param {boolean} accepted - true for accepted (green), false for candidate (orange)
 * @param {L.LayerGroup} layerGroup
 * @returns {L.Polyline}
 */
export function draw2optRoute(map, coords, accepted, layerGroup) {
  const latLngs = toLatLngs(coords);
  const color = accepted
    ? ROUTE_COLORS.twoOptAccepted
    : ROUTE_COLORS.twoOptCandidate;
  const polyline = L.polyline(latLngs, {
    color,
    weight: 4,
    opacity: accepted ? 0.9 : 0.6,
    dashArray: accepted ? null : "4, 8",
  });
  layerGroup.addLayer(polyline);
  return polyline;
}

/**
 * Draw a final OSRM road segment (encoded polyline).
 * Falls back to straight line between coords if geometry is empty.
 * @param {L.Map} map
 * @param {string} encodedGeometry - OSRM polyline-encoded string
 * @param {Array<[number,number]>|null} fallbackCoords - [[lon,lat], [lon,lat]] for straight line
 * @param {L.LayerGroup} layerGroup
 * @returns {L.Polyline}
 */
export function drawRoadSegment(
  map,
  encodedGeometry,
  fallbackCoords,
  layerGroup,
) {
  let latLngs;
  if (encodedGeometry) {
    latLngs = decodePolyline(encodedGeometry);
  } else if (fallbackCoords) {
    latLngs = toLatLngs(fallbackCoords);
  } else {
    return null;
  }

  const polyline = L.polyline(latLngs, {
    color: ROUTE_COLORS.road,
    weight: 5,
    opacity: 0.9,
  });
  layerGroup.addLayer(polyline);
  return polyline;
}

/**
 * Clear all route layers from the group.
 * @param {L.LayerGroup} layerGroup
 */
export function clearRouteLayers(layerGroup) {
  if (layerGroup) {
    layerGroup.clearLayers();
  }
}
