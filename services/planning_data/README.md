# Kakao Mobility planning-data adapter

TravelSync의 기존 `planning-data-1.0` 경계에 자동차 경로를 연결한다.

- Kakao Mobility Multi-Destination Directions 사용
- 한 출발지당 최대 30개 목적지 배치 호출
- `priority=TIME`
- `avoid=["toll"]`: 통행료 없는 자동차 경로만 사용
- `duration_minutes`, `distance_m`를 OR-Tools edge로 변환
- private-car MVP이므로 `group_fare=0`, `walk_minutes=0`
- 운영시간/체류시간/비용은 TravelSync PostgreSQL의 기존 값을 사용
- NULL/UNKNOWN 비용을 무료로 바꾸지 않는다

필수 환경변수:
- `DATABASE_URL`
- `KAKAO_REST_API_KEY`

선택:
- `PLANNING_DATA_TOKEN`
- `KAKAO_ROUTE_RADIUS` (default 5000, max 10000)
- `KAKAO_ROUTE_CONCURRENCY` (default 8)

## Repository setup

On the `drive` branch, Compose connects the API to `http://planning-data:8004`.
Run `./configure_kakao_key.ps1` from PowerShell to save a replacement key in
the ignored root `.env`, then run `docker compose up --build`.
Check `http://localhost:8004/health` for `kakao_configured: true`.
Use `transport_mode=drive`; walking and transit requests are explicitly rejected.
For processes outside Docker, configure the API's `PLANNING_DATA_URL` as
`http://localhost:8004` and provide the adapter's local `DATABASE_URL`.

The default Compose snapshot timeout is 180 seconds for batched route calls;
an existing `.env` value overrides it. At 60 POIs, each day can require 121
provider requests. Kakao quotas/billing and latency apply. Routes reflect the
fetch-time road situation, not a forecast for the requested trip date.
The `avoid=["toll"]` option and zero route fare implement the supplied private-car
policy; fuel, parking, rental and taxi costs are not included.
Unknown POI cost/hours and access/event evidence remain unknown and may cause
E2 exclusion. Existing CP-SAT and independent E3 publication gates still apply.

Offline adapter checks (with service requirements, pytest and the optimizer extra installed):
`python -m pytest tests/test_kakao_planning_data.py`

Provider reference: https://developers.kakaomobility.com/guide/navi-api/destinations
