# Interactive itinerary map

The day map gives a visible explanation of a generated plan: where the group
starts, the order of visits, and where it returns. Numbered markers match the
ordered place list. Selecting either reveals the saved visit time and category;
the existing place sheet provides further details.

This was selected for presentation payoff because the plan already supports
recommendations, constrained scheduling, localized repairs and group voting, but
the Plan screen previously required the audience to reconstruct the geography
from a text timeline. A map makes that result visible without changing those
algorithms or adding another LLM, routing provider or API key.

## Using the feature

1. Open a saved itinerary in **Plan** and select a day.
2. Open **Your day at a glance → View map**. The overview includes the saved
   departure/return times, stop count and travel minutes including the return leg.
3. Select a numbered marker or a place in the list. Open **View place details**
   to inspect it using the plan's saved dataset version.
4. Use **Fit all stops**, zoom controls, keyboard navigation or touch gestures
   to explore. Nearby markers can overlap at a wide zoom; zoom in or use the list.
5. Select another day or plan to see its own saved positions and schedule.

For a short presentation, show the generated plan geographically, inspect one
stop, and switch days. Then use the existing chat proposal and approval flow;
return to the selected plan and reopen its map to show the approved result.
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

- `cd web && npm test`: ordered visits and original indices, saved endpoints,
  inclusion of return travel, missing/invalid positions, no invented shortcuts,
  day changes, empty days and coincident positions.
- `pytest api/tests/test_itinerary_map.py`: saved endpoints remain attached to the
  displayed plan after a newer failed attempt; read-only behavior, unchanged votes
  and replay data, missing legacy endpoints and public-field whitelisting.
- TypeScript/Vite production build and the full API suite validate integration.
- Local headless Edge checks use synthetic UI fixtures for desktop/mobile layouts,
  day changes, marker/list selection, detail callbacks, English/Korean text, actual
  tile loading and blocked-tile fallback. They do not establish live trip/provider
  behavior or physical-device performance. Local preview fixtures are not shipped.
