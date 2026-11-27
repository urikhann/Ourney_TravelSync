# Interactive itinerary map

The day map gives a visible explanation of a generated plan: where the group
starts, the order of visits, and where it returns. Numbered markers match the
ordered place list. Selecting either reveals the saved visit time and category;
the existing place sheet provides further details.

## Using the feature

1. Open a saved itinerary in **Plan** and select a day.
2. Open **Your day at a glance → View map**. The overview includes the saved
   departure/return times, stop count and travel minutes including the return leg.
3. Select a numbered marker or a place in the list. Open **View place details**
   to inspect it using the plan's saved dataset version.
4. Use **Fit all stops**, zoom controls, keyboard navigation or touch gestures
   to explore. Nearby markers can overlap at a wide zoom; zoom in or use the list.
5. Select another day or plan to see its own saved positions and schedule.

Opening or interacting with a map never casts a vote or changes the itinerary.

## Data boundaries

- POI coordinates come from the displayed plan's pinned place dataset. Start and
  return coordinates come from that plan's own optimization audit configuration,
  not the latest form defaults or a failed generation attempt.
- Dashed connections show visit order. They are not road geometry, navigation
  instructions or newly verified driving routes. Travel times are the existing
  backend itinerary values; no straight-line estimate replaces them.
- Missing/invalid positions are omitted. Numbering and place-detail access remain
  available, and no connecting line skips across a visit with a missing position.
  Older plans can lack endpoint coordinates; no city-center substitute is used.
- The map loads only when opened. Street tiles require internet access; if they
  fail, markers, visit-order connections and the place list remain available.
  A failed map-module load is contained within the map panel, leaving the timeline
  and approval controls available.
  This is a background-tile fallback, not a fully offline application.
- Leaflet 1.9.4 is pinned. OpenStreetMap tiles retain visible attribution and use
  normal browser caching. There is no prefetch or bulk tile download.
  See [Leaflet's reference](https://leafletjs.com/reference-1.9.4.html) and the
  [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

## Verification

`web/tests/dayRoute.test.cjs` checks visit order, endpoints, return travel and
missing coordinates. `api/tests/test_itinerary_map.py` checks saved-plan binding,
public fields and read-only behavior. See [test commands](optimizer_verification.md).
