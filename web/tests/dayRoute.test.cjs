const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const ts = require("typescript");

const source = readFileSync(path.join(__dirname, "../src/dayRoute.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
const exportsObject = {};
new Function("exports", compiled.outputText)(exportsObject);
const { dayRoute, position } = exportsObject;
const point = (label, latitude = 35.1, longitude = 129.1) => ({ label, latitude, longitude });
const item = (id, lat = 35.2, lng = 129.2) => ({ poi_id: id, start: "10:00", end: "11:00", travel_minutes: 12,
  place: lat == null ? null : { name: id, lat, lng } });
const day = () => ({ date: "2026-11-30", start: "09:00", end_arrival: "17:45", start_label: "Original start", end_label: "Original return",
  return_travel_minutes: 25, map_endpoints: { start: point("Saved start"), end: point("Saved return", 35.3, 129.3) },
  items: [item("Museum"), item("Cafe", 35.25, 129.25)] });

test("preserves saved endpoints, visit order, original indices and return travel", () => {
  const original = day(), before = JSON.stringify(original), route = dayRoute(original);
  assert.deepEqual(route.nodes.map(n => [n.kind, n.label, n.number, n.itemIndex]), [
    ["start", "Saved start", undefined, undefined], ["visit", "Museum", 1, 0], ["visit", "Cafe", 2, 1], ["end", "Saved return", undefined, undefined]]);
  assert.equal(route.nodes.at(-1).time, "17:45");
  assert.equal(route.travelMinutes, 49);
  assert.equal(route.segments.length, 3);
  assert.equal(JSON.stringify(original), before);
});

test("missing coordinates neither fabricate zero coordinates nor connect across a missing stop", () => {
  const data = day();
  data.items.splice(1, 0, item("Unknown", null));
  const route = dayRoute(data);
  assert.equal(route.missingVisits, 1);
  assert.equal(route.nodes[2].position, null);
  assert.equal(route.nodes[3].number, 3);
  assert.deepEqual(route.segments, [ [[35.1, 129.1], [35.2, 129.2]], [[35.25, 129.25], [35.3, 129.3]] ]);
});

test("legacy endpoints stay missing instead of using current settings or a default city", () => {
  const data = day();
  delete data.map_endpoints;
  const route = dayRoute(data);
  assert.equal(route.missingEndpoints, 2);
  assert.equal(route.nodes[0].label, "Original start");
  assert.equal(route.nodes[0].position, null);
  assert.equal(route.nodes.at(-1).position, null);
  assert.equal(route.segments.length, 1);
});

test("invalid map positions are omitted while real zero coordinates remain valid", () => {
  for (const [lat, lng] of [[null, null], [undefined, 129], ["35.1", "129.1"], [NaN, 129], [35, Infinity], [90, 129], [-86, 129], [35, 181]]) {
    assert.equal(position(lat, lng), null);
  }
  assert.deepEqual(position(0, 0), [0, 0]);
});

test("changing day or plan rebuilds positions and times without retaining previous stops", () => {
  const first = day(), next = day();
  next.date = "2026-12-01";
  next.items = [item("Another day", 35.8, 129.8)];
  next.map_endpoints.end = point("New return", 35.9, 129.9);
  const route = dayRoute(next);
  assert.equal(route.nodes.length, 3);
  assert.equal(route.nodes[1].label, "Another day");
  assert.deepEqual(route.nodes.at(-1).position, [35.9, 129.9]);
  assert.equal(dayRoute(first).nodes.length, 4);
});

test("empty days and coincident places retain real visit identity and order", () => {
  const data = day();
  data.items = [];
  assert.equal(dayRoute(data).travelMinutes, 25);
  assert.equal(dayRoute(data).nodes.length, 2);
  data.items = [item("First"), item("Second")];
  assert.deepEqual(dayRoute(data).nodes.filter(n => n.kind === "visit").map(n => n.number), [1, 2]);
});
