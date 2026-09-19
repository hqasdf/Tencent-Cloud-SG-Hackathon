const EARTH_RADIUS_KM = 6371.0088;

export function round(value, digits = 2) {
  const factor = 10 ** digits;
  return Math.round((value + Number.EPSILON) * factor) / factor;
}

function toRadians(degrees) {
  return (degrees * Math.PI) / 180;
}

export function haversineDistanceKm(a, b) {
  const lat1 = toRadians(a.lat);
  const lat2 = toRadians(b.lat);
  const deltaLat = toRadians(b.lat - a.lat);
  const deltaLng = toRadians(b.lng - a.lng);
  const h = Math.sin(deltaLat / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(deltaLng / 2) ** 2;
  return EARTH_RADIUS_KM * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

export function polylineDistanceKm(points) {
  if (!Array.isArray(points) || points.length < 2) return 0;
  return round(points.slice(1).reduce((total, point, index) => {
    return total + haversineDistanceKm(points[index], point);
  }, 0), 3);
}

function routeDistanceKm(route) {
  return Number.isFinite(route.distance_km)
    ? route.distance_km
    : polylineDistanceKm(route.points);
}

function positiveDifference(actual, expected) {
  return Math.max(0, actual - expected);
}

function percentageDifference(actual, expected) {
  if (expected === 0) return actual === 0 ? 0 : 100;
  return (positiveDifference(actual, expected) / expected) * 100;
}

export function calculateRouteMetrics(trip) {
  const expectedDistanceKm = routeDistanceKm(trip.expected_route);
  const actualDistanceKm = routeDistanceKm(trip.actual_route);
  const expectedDurationMinutes = trip.expected_route.duration_minutes;
  const actualDurationMinutes = trip.actual_route.duration_minutes;
  const expectedFare = trip.fare.expected_amount;
  const actualFare = trip.fare.actual_amount;

  return {
    expected_distance_km: round(expectedDistanceKm, 3),
    actual_distance_km: round(actualDistanceKm, 3),
    excess_distance_km: round(positiveDifference(actualDistanceKm, expectedDistanceKm), 3),
    distance_deviation_pct: round(percentageDifference(actualDistanceKm, expectedDistanceKm), 2),
    distance_deviation_pct_exact: percentageDifference(actualDistanceKm, expectedDistanceKm),
    expected_duration_minutes: round(expectedDurationMinutes, 2),
    actual_duration_minutes: round(actualDurationMinutes, 2),
    excess_duration_minutes: round(positiveDifference(actualDurationMinutes, expectedDurationMinutes), 2),
    duration_deviation_pct: round(percentageDifference(actualDurationMinutes, expectedDurationMinutes), 2),
    expected_fare: round(expectedFare, 2),
    actual_fare: round(actualFare, 2),
    fare_difference: round(positiveDifference(actualFare, expectedFare), 2),
    currency: trip.fare.currency
  };
}
