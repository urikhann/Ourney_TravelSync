"""Read-only POI enrichment triage and bounded official-API coverage pilot.

Writes only to an isolated output directory. Never imports or changes app settings.
History, when available, is read in a READ ONLY transaction; no user inputs are exported.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.hours import ALWAYS_OPEN, _missing, parse_row

DATA = ROOT / "src/ourney_travelsync/data"
PLACES = DATA / "travelsync_poi_metadata_enriched_v3.csv"
HOURS = DATA / "travelsync_poi_operating_info_v1.csv"
TOUR = "https://apis.data.go.kr/B551011/KorService2/"
BUSAN = "https://apis.data.go.kr/6260000/"
AS_OF = date(2026, 11, 17)


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        # These exports are data, never spreadsheet formulas.
        writer.writerows({k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "@")) else v)
                          for k, v in row.items()} for row in rows)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def present(value):
    return not _missing(value)


def text(value):
    value = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def norm(value):
    # Preserve branch identifiers: no removal of parentheses or location suffixes.
    return re.sub(r"[^가-힣a-z0-9]", "", text(value).lower())


def category(row):
    if row.get("cafe") == "1":
        return "cafe"
    if row.get("food") == "1":
        return "restaurant"
    if row.get("festival") == "1":
        return "festival"
    return "attraction"


def district(row):
    for field in ("address", "road_address"):
        match = re.search(r"부산(?:광역시)?\s+(\S+?[구군])\b", row.get(field, ""))
        if match:
            return match.group(1)
    return "UNKNOWN"


def description_usable(row):
    return (present(row.get("description_summary")) and present(row.get("description_source_url"))
            and row.get("detail_verification_status", "") != "CONFLICT")


def read_history(env):
    """Return aggregate selection counts only; never export trip/user IDs or preferences."""
    configured = bool(env.get("DATABASE_URL") or all(env.get(k) for k in
                      ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")))
    if not configured:
        return {}, {"status": "UNAVAILABLE", "reason": "DB_CONFIG_MISSING", "runs": None}
    try:
        import psycopg
        from psycopg.rows import dict_row
        kwargs = {"connect_timeout": 8, "row_factory": dict_row,
                  "options": "-c default_transaction_read_only=on -c statement_timeout=15000"}
        if env.get("DATABASE_URL"):
            dsn = env["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
            conn = psycopg.connect(dsn, **kwargs)
        else:
            conn = psycopg.connect(host=env["DB_HOST"], port=int(env.get("DB_PORT", 5432)),
                dbname=env["DB_NAME"], user=env["DB_USER"], password=env["DB_PASSWORD"],
                sslmode=env.get("DB_SSLMODE", "prefer"), **kwargs)
        counts = Counter()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema=current_schema() AND table_name='recommendation_runs'")
                columns = {r["column_name"] for r in cur.fetchall()}
                if "response" in columns:
                    cur.execute("SELECT id, dataset_version, method, created_at, response FROM recommendation_runs")
                    runs = cur.fetchall()
                    for run in runs:
                        counts.update({p["poi_id"] for p in (run["response"] or {}).get("group", [])})
                elif "run_id" in columns:
                    cur.execute("SELECT r.run_id, r.created_at, p.place_id FROM recommendation_runs r "
                                "LEFT JOIN group_scores x ON x.run_id=r.run_id "
                                "LEFT JOIN poi p ON p.poi_id=x.poi_id AND p.dataset_version=r.dataset_version")
                    runs = cur.fetchall()
                    seen = set()
                    for r in runs:
                        if r["place_id"] and (r["run_id"], r["place_id"]) not in seen:
                            counts[r["place_id"]] += 1
                            seen.add((r["run_id"], r["place_id"]))
                else:
                    return {}, {"status": "UNAVAILABLE", "reason": "UNSUPPORTED_HISTORY_SCHEMA", "runs": None}
        times = [r["created_at"].isoformat() for r in runs if r.get("created_at")]
        metadata = {"status": "AVAILABLE", "runs": len({r.get("id", r.get("run_id")) for r in runs}),
                        "earliest": min(times, default=None), "latest": max(times, default=None),
                        "read_only_transaction": True,
                        "unique_selected_pois": len(counts),
                        "selected_poi_run_pairs": sum(counts.values()),
                        "definition": "distinct stored recommendation runs selecting place in group/group_scores; all methods and versions",
                        "limitation": "Local stored history may include demo/comparison runs; selection frequency is not relevance, satisfaction or verified production traffic."}
        if 'response' in columns:
            metadata['nonempty_runs'] = sum(bool((r['response'] or {}).get('group')) for r in runs)
            metadata['runs_by_dataset_and_method'] = [
                {'dataset_version':version, 'method':method, 'runs':n}
                for (version,method),n in sorted(Counter((r['dataset_version'],r['method']) for r in runs).items())]
        return counts, metadata
    except Exception as exc:
        return {}, {"status": "UNAVAILABLE", "reason": type(exc).__name__, "runs": None}


def make_triage(places, hours, counts, history):
    assert len({r["place_id"] for r in places}) == len(places)
    by_id = {r["place_id"]: r for r in hours}
    assert len(by_id) == len(hours) and set(by_id) == {r["place_id"] for r in places}
    rows = []
    for p in places:
        h = by_id[p["place_id"]]
        parsed = parse_row(h)
        inferred = (parsed.hours == "KNOWN" and _missing(h.get("opening_time"))
                    and _missing(h.get("closing_time"))
                    and any(s in h.get("operating_hours_raw", "") for s in ALWAYS_OPEN))
        expired = (parsed.hours == "SNAPSHOT" and present(h.get("hours_valid_until"))
                   and h["hours_valid_until"] < AS_OF.isoformat())
        reasons = []
        if inferred:
            reasons.append("INFERRED_ALL_DAY")
        if parsed.hours == "KNOWN" and parsed.closed_days != "KNOWN":
            reasons.append("CLOSURE_" + parsed.closed_days)
        if present(h.get("break_times_raw")):
            reasons.append("PRESERVE_BREAKS")
        if present(h.get("last_order_raw")):
            reasons.append("PRESERVE_LAST_ORDER")
        if re.search(r"입장.*(?:마감|전)|매표.*마감", h.get("operating_hours_raw", "")):
            reasons.append("PRESERVE_ADMISSION_CUTOFF")
        cat, described = category(p), description_usable(p)
        missing_regular = parsed.hours != "KNOWN"
        if missing_regular and cat in ("restaurant", "cafe") and described:
            phase, action = 0, "RECOVER_REGULAR_HOURS"
        elif cat == "attraction" and not described:
            phase, action = 1, "ATTRACTION_DESCRIPTION_AND_HOURS" if missing_regular else "ATTRACTION_DESCRIPTION"
        elif missing_regular:
            phase, action = 2, "RECOVER_REGULAR_HOURS"
        elif inferred or parsed.closed_days != "KNOWN":
            phase, action = 3, "REVIEW_EXISTING_RECURRING"
        else:
            phase, action = 4, "VERIFY_EXISTING_RECURRING"
        rows.append({"place_id": p["place_id"], "place_name": p["place_name"], "category": cat,
            "category_raw": p.get("category_name", ""), "district": district(p), "address": p["address"],
            "latitude": p["latitude"], "longitude": p["longitude"],
            "recommendation_run_count": counts.get(p["place_id"], 0) if history["status"] == "AVAILABLE" else "",
            "recommendation_frequency_status": history["status"], "priority_phase": phase,
            "next_action": action, "description_usable_for_scoring": described,
            "description_quality_review": "NOT_ASSESSED" if described else "MISSING_OR_UNUSABLE",
            "parsed_hours": parsed.hours, "missing_regular_hours": missing_regular,
            "snapshot_expired": expired, "hours_scope": h.get("hours_scope", ""),
            "hours_valid_until": h.get("hours_valid_until", ""), "closed_days_status": parsed.closed_days,
            "inferred_all_day": inferred, "review_flags": "|".join(reasons),
            "opening_time_original": h.get("opening_time", ""), "closing_time_original": h.get("closing_time", ""),
            "closed_day_original": h.get("closed_day", ""), "break_times_raw": h.get("break_times_raw", ""),
            "last_order_raw": h.get("last_order_raw", ""),
            "operating_hours_raw": h.get("operating_hours_raw", ""),
            "operating_source_url": h.get("source_url", ""), "checked_at_original": h.get("checked_at", ""),
            "place_url": p.get("place_url", ""), "source": p["source"], "source_place_id": p["source_place_id"],
            "official_homepage_candidates": h.get("official_homepage_candidates", ""),
            "verified_for_planning": False})
    def key(r):
        frequency = r["recommendation_run_count"]
        return (-(frequency or 0), r["priority_phase"], r["district"], r["category"], r["place_id"])
    rows.sort(key=key)
    for i, r in enumerate(rows, 1):
        r["provisional_rank" if history["status"] != "AVAILABLE" else "rank"] = i
    return rows


def balanced(rows, limit):
    """Round-robin district x category coverage; preserve priority within each bucket."""
    buckets = defaultdict(deque)
    for row in rows:
        if row["category"] != "festival":
            buckets[(row["district"], row["category"])].append(row)
    result = []
    while buckets and len(result) < limit:
        for key in sorted(list(buckets)):
            result.append(buckets[key].popleft())
            if not buckets[key]:
                del buckets[key]
            if len(result) == limit:
                break
    return [{"collection_order": i, **r} for i, r in enumerate(result, 1)]


def distance_m(a, b):
    try:
        lat1, lon1, lat2, lon2 = map(float, (a[0], a[1], b[0], b[1]))
    except (ValueError, TypeError):
        return None
    if not all(math.isfinite(v) for v in (lat1, lon1, lat2, lon2)) or not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90):
        return None
    x = math.sin(math.radians(lat2-lat1)/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, x)))


def match_record(p, records, source):
    """Unique exact name plus <=200m, or an existing exact TourAPI source identity.

    Phone/address alone never auto-resolve a branch. Multiple candidates remain ambiguous.
    """
    matches = []
    for record in records:
        if source == "tour":
            rid, names, lat, lon = record.get("contentid"), [record.get("title", "")], record.get("mapy"), record.get("mapx")
        else:
            rid = str(record.get("UC_SEQ", ""))
            names = [record.get(k, "") for k in ("MAIN_TITLE", "TITLE", "PLACE")]
            lat, lon = record.get("LAT"), record.get("LNG")
        if source == "tour" and p["place_id"].startswith("tour:") and str(rid) == p["source_place_id"]:
            return record, "EXACT_SOURCE_ID", distance_m((p["latitude"], p["longitude"]), (lat, lon))
        d = distance_m((p["latitude"], p["longitude"]), (lat, lon))
        if d is not None and d <= 200 and norm(p["place_name"]) in {norm(n) for n in names if n}:
            matches.append((record, "EXACT_NAME_AND_DISTANCE", round(d, 1)))
    if len(matches) == 1:
        return matches[0]
    return None, "AMBIGUOUS" if matches else "NO_CONSERVATIVE_MATCH", None


def classify_schedule(hours, closure=""):
    """Assess evidence, not planning eligibility. Never turn all-day prose into 24h."""
    hours, closure = text(hours), text(closure)
    joined = hours + " " + closure
    ranges = re.findall(r"(?<!\d)([0-2]?\d):([0-5]\d)\s*[~～–—-]\s*([0-2]?\d):([0-5]\d)", hours)
    ranges = [f"{int(a):02}:{b}-{int(c):02}:{d}" for a,b,c,d in ranges
              if int(a) <= 24 and int(c) <= 24 and not (int(a) == 24 and b != "00") and not (int(c) == 24 and d != "00")]
    special = bool(re.search(r"계절|동절기|하절기|변동|유동|행사|예약|일몰|일출|연중.*시기|별도 문의", joined))
    if not hours:
        state = "NO_HOURS_TEXT"
    elif not ranges:
        state = "ALL_DAY_TEXT_REQUIRES_VENUE_CHECK" if re.search(r"상시|24시간|연중무휴", hours) else "UNPARSED_HOURS_TEXT"
    elif special:
        state = "VARIABLE_OR_CONDITIONAL_REQUIRES_REVIEW"
    elif not closure and not re.search(r"매일|연중무휴|정기휴무|휴무|휴관", hours):
        state = "TIME_RANGE_FOUND_CLOSURE_UNRESOLVED"
    else:
        state = "REGULAR_SCHEDULE_CANDIDATE_REVIEW_REQUIRED"
    return {"schedule_evidence_status": state, "time_ranges": "|".join(ranges),
        "break_or_cutoff_present": bool(re.search(r"브레이크|브레이크타임|break|라스트|last.?order|입장.*마감|매표.*마감|입장.*전", joined, re.I)),
        "verified_for_planning": False}


class Collector:
    def __init__(self, out, env):
        self.out, self.env = out, env
        log_path = out / "requests.json"
        self.log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
        self.seq = max((r["request_id"] for r in self.log), default=0)
        self.last_entry = None
        self.secrets = [unquote(v) for k, v in env.items() if v and any(t in k.upper() for t in ("KEY", "PASSWORD", "TOKEN", "SECRET"))]
        (out / "responses").mkdir(exist_ok=True)

    def get(self, endpoint, params, source):
        import requests
        public_params = {k:v for k,v in params.items() if k.lower() != "servicekey"}
        for old in reversed(self.log):
            if old["endpoint"] == endpoint and old["params"] == public_params and old.get("response_file"):
                # Successful responses and explicit authorization failures are evidence caches.
                # Network/schema failures are retried, retaining the failed request in the log.
                if old["status"] == "OK":
                    if digest(self.out / old["response_file"]) != old['response_sha256']:
                        raise ValueError('Cached source response checksum mismatch')
                    payload = json.loads((self.out / old["response_file"]).read_text(encoding="utf-8"))
                    data = payload["response"]["body"] if source.startswith("tour") else next(v for v in payload.values() if isinstance(v, dict))
                    items = (data.get("items") or {}).get("item", []) if source.startswith("tour") else data.get("item", [])
                    self.last_entry = old
                    return [items] if isinstance(items, dict) else items
                if old.get("http_status") in (401, 403):
                    self.last_entry = old
                    return []
        self.seq += 1
        entry = {"request_id": self.seq, "endpoint": endpoint, "source": source,
                 "params": public_params,
                 "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
        try:
            response = requests.get(endpoint, params=params, timeout=(8, 25))
            body = response.text
            for secret in self.secrets:
                body = body.replace(secret, "[REDACTED]")
            entry["http_status"] = response.status_code
            filename = f"{self.seq:04}_{source}.txt"
            (self.out / "responses" / filename).write_text(body, encoding="utf-8")
            entry["response_file"] = "responses/" + filename
            entry["response_sha256"] = digest(self.out / entry["response_file"])
            if response.status_code != 200:
                entry["status"] = "HTTP_ERROR"
                return []
            payload = json.loads(body)
            if source.startswith("tour"):
                head = payload["response"]["header"]
                entry["provider_code"] = str(head.get("resultCode"))
                data = payload["response"].get("body", {})
                items = (data.get("items") or {}).get("item", [])
                if entry["provider_code"] not in ("0000", "00", "0"):
                    entry["status"] = "PROVIDER_ERROR"
                    return []
            else:
                data = next(v for v in payload.values() if isinstance(v, dict))
                entry["provider_code"] = str(data.get("header", {}).get("code"))
                items = data.get("item", [])
                if entry["provider_code"] not in ("00", "0000", "0"):
                    entry["status"] = "PROVIDER_ERROR"
                    return []
            entry["total_count"] = int(data.get("totalCount", 0))
            items = [items] if isinstance(items, dict) else items
            entry["returned_records"] = len(items)
            entry["status"] = "OK"
            return items
        except Exception as exc:
            entry["status"], entry["error_type"] = "REQUEST_OR_SCHEMA_ERROR", type(exc).__name__
            return []
        finally:
            self.last_entry = entry
            self.log.append(entry)
            (self.out / "requests.json").write_text(json.dumps(self.log, ensure_ascii=False, indent=2), encoding="utf-8")

    def listed(self, source, endpoint, key):
        rows, seen = [], set()
        for page in range(1, 31):
            if source == "tour_list":
                params = {"serviceKey": key, "MobileOS": "ETC", "MobileApp": "TravelSyncEnrichmentAudit",
                          "_type": "json", "areaCode": 6, "numOfRows": 100, "pageNo": page, "arrange": "A"}
            else:
                params = {"serviceKey": key, "numOfRows": 100, "pageNo": page, "resultType": "json"}
            batch = self.get(endpoint, params, source)
            if not batch:
                break
            for item in batch:
                rid = str(item.get("contentid") if source == "tour_list" else item.get("UC_SEQ"))
                if rid not in seen:
                    rows.append(item)
                    seen.add(rid)
            if len(rows) >= self.last_entry.get("total_count", 0) or len(batch) < 100:
                break
        return rows


def collect_pilot(out, env, places, triage, queue, limit):
    collector = Collector(out, env)
    previous_evidence = read_csv(out/'enrichment_evidence.csv') if (out/'enrichment_evidence.csv').exists() else []
    for row in previous_evidence:
        for key in ('old_missing_regular_hours', 'old_inferred_all_day', 'description_gap',
                    'break_or_cutoff_present', 'verified_for_planning'):
            row[key] = row[key] == 'True'
    tour_key = unquote(env.get("TOUR_API_KEY") or env.get("TOUR_KEY") or "")
    busan_key = unquote(next((env[k] for k in ("BUSAN_FOOD_API_KEY", "BUSAN_API_KEY", "DATA_GO_KR_API_KEY", "TOUR_API_KEY", "TOUR_KEY") if env.get(k)), ""))
    food = collector.listed("busan_food", BUSAN + "FoodService/getFoodKr", busan_key) if busan_key else []
    attractions = collector.listed("busan_attraction", BUSAN + "AttractionService/getAttractionKr", busan_key) if busan_key else []
    tour = collector.listed("tour_list", TOUR + "areaBasedList2", tour_key) if tour_key else []
    source_rows = {"busan_food": len(food), "busan_attraction": len(attractions), "tour_list": len(tour)}
    pbyid, tbyid = {p["place_id"]:p for p in places}, {r["place_id"]:r for r in triage}
    matches, evidence, tour_matches = [], [], {}
    for p in places:
        cat = category(p)
        name = "busan_food" if cat in ("restaurant", "cafe") else "busan_attraction"
        found, basis, d = match_record(p, food if name == "busan_food" else attractions, "busan")
        matches.append({"place_id":p["place_id"], "source":name, "match_status":basis,
                        "source_id":str(found.get("UC_SEQ", "")) if found else "", "distance_m":d})
        if found:
            raw = found.get("USAGE_DAY_WEEK_AND_TIME", "")
            evidence.append(evidence_row(p, tbyid[p["place_id"]], name, str(found["UC_SEQ"]), basis, raw, "",
                found.get("ITEMCNTNTS", ""), BUSAN + ("FoodService/getFoodKr" if name == "busan_food" else "AttractionService/getAttractionKr"), found.get("HOMEPAGE_URL", "")))
        found, basis, d = match_record(p, tour, "tour")
        matches.append({"place_id":p["place_id"], "source":"tour_list", "match_status":basis,
                        "source_id":found.get("contentid", "") if found else "", "distance_m":d})
        if found:
            tour_matches[p["place_id"]] = (found, basis)
        elif p["place_id"].startswith("tour:") and p.get("category", "").isdigit():
            # Absence from a current list is not proof a pinned source ID is invalid.
            # Test the original identity directly, preserving both observations.
            tour_matches[p["place_id"]] = ({"contentid":p["source_place_id"], "contenttypeid":p["category"]}, "EXACT_EXISTING_SOURCE_ID")
    # Bounded discovery for attractions lacking text, rather than more menu summaries.
    discovery = balanced([r for r in triage if r["category"] == "attraction" and not r["description_usable_for_scoring"]], 24)
    for target in discovery:
        p = pbyid[target["place_id"]]
        params = {"serviceKey":tour_key, "MobileOS":"ETC", "MobileApp":"TravelSyncEnrichmentAudit",
                  "_type":"json", "areaCode":6, "numOfRows":30, "pageNo":1, "keyword":p["place_name"]}
        found_rows = collector.get(TOUR + "searchKeyword2", params, "tour_keyword") if tour_key else []
        found, basis, d = match_record(p, found_rows, "tour")
        matches.append({"place_id":p["place_id"], "source":"tour_keyword", "match_status":basis,
                        "source_id":found.get("contentid", "") if found else "", "distance_m":d})
        if found:
            tour_matches[p["place_id"]] = (found, basis)
    # Prefer the balanced target queue; supplement direct Tour IDs across category/area buckets.
    pilot_ids = [r["place_id"] for r in discovery if r["place_id"] in tour_matches]
    pilot_ids += [r["place_id"] for r in queue if r["place_id"] in tour_matches and r["place_id"] not in pilot_ids]
    extras = balanced([r for r in triage if r["place_id"] in tour_matches and r["place_id"] not in pilot_ids], limit)
    pilot_ids = (pilot_ids + [r["place_id"] for r in extras])[:limit]
    for index, pid in enumerate(pilot_ids, 1):
        p, old = pbyid[pid], tbyid[pid]
        rec, basis = tour_matches[pid]
        cid, ctype = rec["contentid"], rec["contenttypeid"]
        params = {"serviceKey":tour_key, "MobileOS":"ETC", "MobileApp":"TravelSyncEnrichmentAudit",
                  "_type":"json", "contentId":cid, "contentTypeId":ctype}
        intro = collector.get(TOUR + "detailIntro2", params, "tour_intro")
        if not intro:
            continue
        detail = intro[0]
        time_keys = ("opentimefood", "usetime", "usetimeculture", "opentime", "usetimeleports", "playtime")
        close_keys = ("restdatefood", "restdate", "restdateculture", "restdateshopping", "restdateleports")
        raw = " | ".join(text(detail[k]) for k in time_keys if present(detail.get(k)))
        closure = " | ".join(text(detail[k]) for k in close_keys if present(detail.get(k)))
        desc = ""
        if old["category"] == "attraction" and not old["description_usable_for_scoring"]:
            common = collector.get(TOUR + "detailCommon2", {k:v for k,v in params.items() if k != "contentTypeId"}, "tour_common")
            if common:
                desc = common[0].get("overview", "")
        evidence.append(evidence_row(p, old, "tour_intro", cid, basis, raw, closure, desc,
                                    TOUR + "detailIntro2", ""))
        if index % 10 == 0:
            print(f"TourAPI details: {index}/{len(pilot_ids)}", flush=True)
    write_csv(out / "api_matches.csv", matches)
    # Retain useful prior evidence when a new frequency-based pilot selects different POIs.
    combined = {(r['place_id'],r['source'],r['source_id']):r for r in previous_evidence}
    combined.update({(r['place_id'],r['source'],r['source_id']):r for r in evidence})
    evidence = list(combined.values())
    fetched_times = {}
    for log in collector.log:
        if log['status'] != 'OK' or log['source'] not in ('busan_food','busan_attraction','tour_intro'):
            continue
        payload = json.loads((out/log['response_file']).read_text(encoding='utf-8'))
        if log['source'] == 'tour_intro':
            records = (payload['response']['body'].get('items') or {}).get('item', [])
        else:
            records = next(v for v in payload.values() if isinstance(v,dict)).get('item', [])
        records = [records] if isinstance(records,dict) else records
        for record in records:
            source_id = str(record.get('contentid') if log['source']=='tour_intro' else record.get('UC_SEQ'))
            fetched_times[(log['source'],source_id)] = log['fetched_at_utc']
    for row in evidence:
        row['fetched_at_utc'] = fetched_times.get((row['source'],row['source_id']),row['fetched_at_utc'])
    write_csv(out / "enrichment_evidence.csv", evidence)
    return {"source_rows":source_rows, "description_discovery_pois":len(discovery), "pilot_detail_pois":len(pilot_ids),
            "cumulative_detail_pois_with_evidence":len({r['place_id'] for r in evidence if r['source']=='tour_intro'}), "requests":len(collector.log),
            "request_statuses":dict(Counter(r["status"] for r in collector.log))}, evidence


def evidence_row(p, old, source, sid, basis, hours, closure, description, endpoint, homepage):
    assessment = classify_schedule(hours, closure)
    return {"place_id":p["place_id"], "place_name":p["place_name"], "category":old["category"],
        "district":old["district"], "source":source, "source_id":sid, "match_basis":basis,
        "source_endpoint":endpoint, "venue_homepage":homepage,
        "fetched_at_utc":datetime.now(timezone.utc).isoformat(), "old_hours_status":old["parsed_hours"],
        "old_missing_regular_hours":old["missing_regular_hours"], "old_inferred_all_day":old["inferred_all_day"],
        "hours_text":text(hours), "closure_text":text(closure), "description_text":text(description),
        "description_gap":not old["description_usable_for_scoring"], **assessment,
        "review_status":"PENDING_REVIEW", "import_status":"NOT_IMPORTED"}


def finish_report(out, triage, queue, history, pilot, evidence, checksums):
    recurring = [r for r in triage if r["parsed_hours"] == "KNOWN"]
    counts = Counter(r["parsed_hours"] for r in triage)
    gaps = {r["place_id"] for r in triage if r["missing_regular_hours"]}
    with_hours = {r["place_id"] for r in evidence if r["place_id"] in gaps and r["hours_text"]}
    candidates = {r["place_id"] for r in evidence if r["place_id"] in gaps and r["schedule_evidence_status"] == "REGULAR_SCHEDULE_CANDIDATE_REVIEW_REQUIRED"}
    descriptions = {r["place_id"] for r in evidence if r["description_gap"] and r["category"] == "attraction" and r["description_text"]}
    evidence_by_id = defaultdict(list)
    for row in evidence:
        evidence_by_id[row['place_id']].append(row)
    for row in triage:
        ev = evidence_by_id[row['place_id']]
        row['new_api_sources'] = '|'.join(sorted({r['source'] for r in ev}))
        row['new_api_hours_text_found'] = any(r['hours_text'] for r in ev)
        row['new_regular_schedule_candidate'] = any(r['schedule_evidence_status'] == 'REGULAR_SCHEDULE_CANDIDATE_REVIEW_REQUIRED' for r in ev)
        intervals = {r['time_ranges'] for r in ev if r['time_ranges']}
        row['api_time_range_disagreement'] = len(intervals) > 1
        row['venue_followup_reason'] = ('CHECK_API_DISAGREEMENT' if len(intervals) > 1 else
            'VERIFY_SCHEDULE_CANDIDATE' if row['new_regular_schedule_candidate'] else
            'RESOLVE_CLOSURE_OR_VARIABLE_HOURS' if row['new_api_hours_text_found'] else
            'REVIEW_EXISTING_RECURRING' if row['parsed_hours'] == 'KNOWN' else 'FIND_VENUE_SCHEDULE')
    annotated = {r['place_id']:r for r in triage}
    queue = [{**r, **annotated[r['place_id']]} for r in queue]
    followup = [r for r in triage if r['new_api_hours_text_found'] or r['parsed_hours'] == 'KNOWN']
    followup.sort(key=lambda r: (-(r['recommendation_run_count'] or 0),
                                not r['new_regular_schedule_candidate'], not r['inferred_all_day'],
                                r['priority_phase'], r['place_id']))
    followup_queue = balanced(followup, 150)
    write_csv(out / 'targeted_enrichment_all.csv', triage)
    write_csv(out / 'collection_queue_150.csv', queue)
    write_csv(out / 'venue_verification_queue_150.csv', followup_queue)
    write_csv(out / 'recurring_schedule_review.csv', [r for r in triage if r['parsed_hours'] == 'KNOWN'])
    summary = {"as_of":AS_OF.isoformat(), "history":history, "hours_baseline":dict(counts),
        "previously_selected_pois":sum(bool(r['recommendation_run_count']) for r in triage),
        "previously_selected_pois_missing_regular_hours":sum(bool(r['recommendation_run_count']) and r['missing_regular_hours'] for r in triage),
        "collection_queue_previously_selected_pois":sum(bool(r['recommendation_run_count']) for r in queue),
        "expired_snapshots":sum(r["snapshot_expired"] for r in triage),
        "inferred_all_day":sum(r["inferred_all_day"] for r in recurring),
        "recurring_closure_status":dict(Counter(r["closed_days_status"] for r in recurring)),
        "queue_size":len(queue), "queue_districts":dict(Counter(r["district"] for r in queue)),
        "queue_categories":dict(Counter(r["category"] for r in queue)), "pilot":pilot,
        "missing_regular_denominator":len(gaps), "gap_pois_with_new_hours_text":len(with_hours),
        "gap_pois_with_regular_schedule_candidate":len(candidates),
        "attraction_description_gap_pois_with_new_text":len(descriptions),
        "initial_queue_pois_with_new_hours_text":sum(r['new_api_hours_text_found'] for r in queue),
        "initial_queue_gap_pois_with_regular_schedule_candidate":sum(r['missing_regular_hours'] and r['new_regular_schedule_candidate'] for r in queue),
        "venue_verification_queue_size":len(followup_queue),
        "venue_verification_queue_categories":dict(Counter(r['category'] for r in followup_queue)),
        "venue_verification_queue_districts":len({r['district'] for r in followup_queue}),
        "api_time_range_disagreements":sum(r['api_time_range_disagreement'] for r in triage),
        "newly_verified_planning_eligible":0,
        "inputs_sha256":checksums, "inputs_unchanged":all(digest(ROOT/p) == h for p,h in checksums.items())}
    assert summary["inputs_unchanged"]
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Targeted POI enrichment pilot", "", f"As of {AS_OF.isoformat()} (Asia/Seoul).", "",
        "## Recommendation history", "", json.dumps(history, ensure_ascii=False), "",
        "Frequency counts mean distinct stored recommendation runs selecting a POI, not relevance or satisfaction. "
        "When history is unavailable, frequency cells are blank and ranks are provisional. No experimental or synthetic counts are substituted.", "",
        f"The available local history selected {summary['previously_selected_pois']} distinct catalogue POIs. "
        f"{summary['previously_selected_pois_missing_regular_hours']} lack recurring hours under the current parser. "
        f"{summary['collection_queue_previously_selected_pois']} of the 150 collection targets have appeared in stored runs. "
        "Stored local runs may include demos and method comparisons; they are not verified production traffic.", "",
        "## Existing schedule audit", "", f"{len(triage):,} places: {dict(counts)}. "
        f"{summary['expired_snapshots']:,} snapshots expired as of the audit date.", "",
        f"All {len(recurring)} parsed recurring schedules are in recurring_schedule_review.csv. "
        f"{summary['inferred_all_day']} rely on inferred all-day conversion; "
        f"closure status: {summary['recurring_closure_status']}. Breaks and last-order fields are preserved.", "",
        "Description usability means nonmissing text plus a citation and no CONFLICT flag, matching the scorer's basic evidence gate; it is not a human assessment of usefulness.", "",
        "## Collection queue", "", f"{len(queue)} target places, across {len(summary['queue_districts'])} districts: "
        f"{summary['queue_categories']}. This is a collection queue, not a verified planning pool.", "",
        "Ranking uses observed frequency first when available, then action priority, district, category and stable place ID. "
        "Action priority: described restaurants/cafes missing recurring hours; attractions missing useful/cited text; other regular-hours gaps; risky recurring schedules; other recurring schedules. "
        "The 150-place queue round-robins district/category buckets while preserving priority within each bucket. Festivals are excluded from recurring-schedule collection.", "",
        "## Live source yield", "", json.dumps(pilot, ensure_ascii=False), "",
        "Busan Food was accessible. The separate Busan Attraction service returned HTTP 403 / SERVICE_KEY_IS_NOT_REGISTERED_ERROR; its coverage is untested, not zero. "
        "The bounded TourAPI keyword pass for 24 undescribed attractions found no conservative identity matches; this does not prove that no information exists for those places.", "",
        f"Across the {len(gaps):,} existing regular-hours gaps, {len(with_hours)} POIs have newly retrieved API hours text; "
        f"{len(candidates)} have time ranges plus closure/daily information suitable for schedule review. "
        f"{len(descriptions)} attractions missing usable descriptions have newly retrieved text. "
        "These are evidence candidates, not verified or imported schedules. No verified planning-eligible places are claimed.", "",
        "| Source | Matched POIs with hours text | Existing gaps with hours text | Existing gaps with regular schedule candidate |", "|---|---:|---:|---:|"]
    for source in sorted({r['source'] for r in evidence}):
        es = [r for r in evidence if r['source'] == source]
        lines.append(f"| {source} | {sum(bool(r['hours_text']) for r in es)} | "
            f"{sum(r['place_id'] in gaps and bool(r['hours_text']) for r in es)} | "
            f"{sum(r['place_id'] in gaps and r['schedule_evidence_status']=='REGULAR_SCHEDULE_CANDIDATE_REVIEW_REQUIRED' for r in es)} |")
    lines += ["", "Unique-POI totals are deduplicated across sources; source rows overlap. Each TourAPI detail pass is bounded to the configured pilot size; previously collected evidence is retained when the selected pilot changes. These cumulative yields are not an estimate of full-catalogue coverage.", "",
        f"In the current geography/category-balanced 150-place collection queue, {summary['initial_queue_pois_with_new_hours_text']} places received new hours text and "
        f"{summary['initial_queue_gap_pois_with_regular_schedule_candidate']} regular-hours gaps received schedule candidates. "
        "The catalogue-wide totals above also include matches outside that queue.", "",
        f"A separate venue-verification queue now selects {len(followup_queue)} existing POIs with live API hours evidence or an existing recurring schedule, "
        f"across {summary['venue_verification_queue_districts']} districts ({summary['venue_verification_queue_categories']}). "
        f"It prioritizes reviewable schedule candidates and inferred all-day issues within district/category buckets. "
        f"{summary['api_time_range_disagreements']} POIs have differing extracted time ranges across sources; those require venue review. "
        "Neither queue establishes a verified planning pool.", "",
        "## Review and next gate", "",
        "1. Resolve ambiguous and no-match entries in api_matches.csv; never merge a different branch by name alone.",
        "2. Review each evidence candidate against a current venue source, including weekly/holiday closures, validity, split shifts, breaks, last order and admission cutoffs. Preserve original raw API responses and contradictory facts.",
        "3. Old SNAPSHOT_WEEK data remains dated. Generic all-day prose remains unverified. Any variable/conditional schedule requires manual treatment.",
        "4. Build an explicitly versioned reviewed sidecar, import deliberately and verify stored facts. The existing importer does not model all breaks/cutoffs, so do not import those as a single broad opening window.",
        "5. Validate live driving routes and complete plans, then test hours enforcement separately. The current demo enforcement settings remain unchanged.", "",
        "## Files and reproducibility", "",
        "- targeted_enrichment_all.csv: all existing POIs, provisional/observed priority, hours gaps and source fields.",
        "- collection_queue_150.csv: geographically/category-balanced starting queue.",
        "- venue_verification_queue_150.csv: follow-up queue using measured API evidence and existing recurring schedules; all verification remains pending.",
        "- recurring_schedule_review.csv: all existing recurring schedules and review flags.",
        "- enrichment_evidence.csv: matched live source text with provenance, assessment and pending-review/import status.",
        "- api_matches.csv: accepted conservative matches, ambiguities and no matches.",
        "- requests.json and responses/: credential-free request metadata and redacted source responses.",
        "- summary.json: denominators, results and before/after input hashes.", "",
        "- recommendation_frequency.json: read-only database history aggregates, read timestamp and per-place distinct-run counts. No member preferences or user/trip identifiers are exported.", "",
        "Re-run `python scripts/targeted_enrichment.py --collect --tour-limit 80 --out output/targeted_enrichment_20261117`. "
        "Use --docker-db for the existing local Compose PostgreSQL database, or --env-file PATH for a saved connection. "
        "It is read without printing credentials. Use --reuse-evidence instead of --collect to refresh the history-based ranking without provider calls. "
        "Existing successful responses are reused from this run directory; network/schema failures can be retried and retained in the request log. Use a new output directory for a completely fresh source run.", "",
        "Official references: [Busan Food API](https://www.data.go.kr/data/15063472/openapi.do), "
        "[Busan attractions API](https://data.busan.go.kr/bdip/opendata/detail.do?publicdatapk=15063481), "
        "[TourAPI](https://www.data.go.kr/data/15101578/openapi.do).", ""]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "output/targeted_enrichment_20261117")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--docker-db", action="store_true", help="Read the existing local Compose database using its documented defaults and POSTGRES overrides")
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--reuse-evidence", action="store_true", help="Rebuild lists/report from saved evidence without provider requests")
    parser.add_argument("--tour-limit", type=int, default=80)
    args = parser.parse_args()
    if args.collect and args.reuse_evidence:
        parser.error('Choose --collect or --reuse-evidence')
    if not 1 <= args.tour_limit <= 200:
        parser.error('--tour-limit must be between 1 and 200')
    from dotenv import dotenv_values
    env = {**dotenv_values(args.env_file), **os.environ}
    if args.docker_db and not env.get('DATABASE_URL'):
        env = {**env, 'DB_HOST':'127.0.0.1', 'DB_PORT':'5432',
               'DB_NAME':env.get('POSTGRES_DB') or 'travelsync',
               'DB_USER':env.get('POSTGRES_USER') or 'travelsync',
               'DB_PASSWORD':env.get('POSTGRES_PASSWORD') or 'travelsync'}
    out = args.out.resolve()
    if out == DATA.resolve() or DATA.resolve() in out.parents:
        raise ValueError("Output must not be inside packaged data")
    out.mkdir(parents=True, exist_ok=True)
    checksums = {str(p.relative_to(ROOT)):digest(p) for p in (PLACES, HOURS)}
    places, hours = read_csv(PLACES), read_csv(HOURS)
    frequencies, history = read_history(env)
    if history['status'] == 'AVAILABLE':
        history['selected_ids_not_in_catalogue'] = sorted(set(frequencies) - {p['place_id'] for p in places})
        (out/'recommendation_frequency.json').write_text(json.dumps({
            'read_at_utc':datetime.now(timezone.utc).isoformat(), 'history':history,
            'distinct_run_counts':dict(sorted(frequencies.items()))}, ensure_ascii=False, indent=2), encoding='utf-8')
    triage = make_triage(places, hours, frequencies, history)
    queue = balanced(triage, 150)
    write_csv(out / "targeted_enrichment_all.csv", triage)
    write_csv(out / "collection_queue_150.csv", queue)
    write_csv(out / "recurring_schedule_review.csv", [r for r in triage if r["parsed_hours"] == "KNOWN"])
    print(f"Triage: {len(triage)} places; queue: {len(queue)}; recommendation history: {history['status']}", flush=True)
    if args.reuse_evidence:
        pilot = json.loads((out / 'summary.json').read_text(encoding='utf-8'))['pilot']
        evidence = read_csv(out / 'enrichment_evidence.csv')
        for row in evidence:
            for field in ('old_missing_regular_hours', 'old_inferred_all_day', 'description_gap',
                          'break_or_cutoff_present', 'verified_for_planning'):
                row[field] = row[field] == 'True'
    elif args.collect:
        pilot, evidence = collect_pilot(out, env, places, triage, queue, args.tour_limit)
    else:
        pilot, evidence = {'status':'NOT_RUN'}, []
    summary = finish_report(out, triage, queue, history, pilot, evidence, checksums)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
