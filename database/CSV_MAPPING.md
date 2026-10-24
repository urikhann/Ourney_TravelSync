# CSV to PostgreSQL mapping

The official travelsync_final_places_v2.csv was inspected and strictly validated:
3,570 rows, 29 columns, 3,570 unique external IDs. Canonical location:
src/ourney_travelsync/data/travelsync_final_places_v2.csv.
SHA256: 5fe6fc689b10c03b0a4b1dd6ff99db4cf33e0d2478b20aaf32a6c51929127a15.
It is copied byte-for-byte from the supplied travel.zip.

Columns follow data_schema.BASE + EXTRA + FEATURES; feature order comes from
features.FEATURE_NAMES. Import does not import HTTP/pandas collection code or
reclassify merged labels. The integrated collector has layout/output-path changes
and is not byte-identical to the handoff source. Its code hash identifies the
installed mapping implementation, not proof of the historical CSV generator.

| 파이프라인 출력 컬럼 | PostgreSQL 저장 | 처리 |
|---|---|---|
| place_id | poi.place_id TEXT + poi.poi_id UUID | 원문 보존, UUID는 `uuid5(NAMESPACE_URL, 'travelsync:'+place_id)`로 결정적 생성 |
| place_name | poi.place_name TEXT | 필수, 공백만 있으면 거부 |
| category | poi.category TEXT | TourAPI contenttypeid 또는 Kakao 카테고리 경로 유지 |
| category_name | poi.category_name TEXT | 원문 유지 |
| cat1 | poi.cat1 TEXT | 원문 유지 |
| cat2 | poi.cat2 TEXT | 원문 유지 |
| cat3 | poi.cat3 TEXT | 원문 유지 |
| address | poi.address TEXT | 부산 주소 접두사 검증에 사용 |
| road_address | poi.road_address TEXT | 보완 주소, 부산 판정에 함께 사용 |
| latitude | poi.latitude NUMERIC(10,7) | 유한 숫자·32~39 검증, 7자리 정밀도 저장 |
| longitude | poi.longitude NUMERIC(10,7) | 유한 숫자·124~132 검증, 7자리 정밀도 저장 |
| phone | poi.phone TEXT | 빈 문자열 보존, 명시적 `\N`만 NULL |
| place_url | poi.place_url TEXT | 원문 보존, poi_source에도 연결 |
| image_url | poi.image_url TEXT | 원문 보존 |
| source | poi.source TEXT | TOUR_API / KAKAO / TOUR_API+KAKAO |
| source_place_id | poi.source_place_id + poi_source.source_id | place_id 접두사와 일치 확인 |
| kakao_place_ids | poi.kakao_place_ids + poi_source의 KAKAO 행 | 원문 유지, 세미콜론 구분 ID 검증; v2 현재 병합은 단일 ID |
| feature_rules | poi.feature_rules_raw TEXT + feature_rules JSONB | 원문 보존 및 `{rules:[…]}` 변환, 병합 근거 유지 |
| exclude_reason | poi.exclude_reason TEXT | 최종 후보는 빈 값 또는 NULL만 허용 |
| food | poi.food SMALLINT | 0 또는 1 |
| cafe | poi.cafe SMALLINT | 0 또는 1 |
| nature | poi.nature SMALLINT | 0 또는 1 |
| history | poi.history SMALLINT | 0 또는 1 |
| art | poi.art SMALLINT | 0 또는 1 |
| shopping | poi.shopping SMALLINT | 0 또는 1 |
| activity | poi.activity SMALLINT | 0 또는 1 |
| entertainment | poi.entertainment SMALLINT | 0 또는 1 |
| relax | poi.relax SMALLINT | 0 또는 1 |
| festival | poi.festival SMALLINT | 0 또는 1 |

10개 Feature는 CSV 값을 그대로 보존합니다. 최종 병합 행을 다시 classify하면 출처별 분류/합집합 의미가 달라질 수 있으므로 import에서 재분류하지 않습니다. 원본 API 데이터 재처리가 필요할 때만 기존 prepare()를 사용합니다.

## 추가 저장 필드

- description, operating_hours, closed_days, cost, estimated_stay_time: CSV에 없으므로 NULL. 운영정보 네 필드는 field_status에서 UNKNOWN. 무료가 확인된 경우에만 cost=0, KNOWN. 상태와 값 불일치는 DB 트리거로 거부합니다.
- dataset_version: UUID. 문서의 VARCHAR 버전 식별자를 형식이 보장되는 UUID로 구체화했습니다.
- feature_schema_version: `travel10-v1`; mapping_version: `rule-v2-<소스 SHA256 앞16자리>`. mapping_versions에 전체 SHA256 저장. CSV가 실제로 이 v2 코드로 생성됐는지는 운영자가 확인해야 합니다. CSV만으로 과거 실행 코드 출처를 증명할 수는 없습니다.
- verified_at: NULL. imported_at 성격의 created_at/updated_at을 원천 수집 시각으로 표시하지 않습니다. poi_source.collected_at도 원본 CSV에서 확인 불가하므로 NULL.
- active: 검증된 최종 후보만 true. 운영정보 UNKNOWN이어도 선호 점수 후보로 조회 가능하며, 일정 후보 조건 검증은 별도 단계입니다.
- submission_id: USER 출처에서만 필수. 승인된 제안과 연결.

기존 문서의 `name`을 실제 CSV와 맞춰 `place_name`으로 저장하며 UUID `poi_id`는 유지했습니다. `place_id`는 외부 파이프라인 ID 보존용으로 추가했습니다.

## 빈 문자열과 NULL

CSV 표준에는 SQL NULL 표현이 없습니다. 이 import의 계약은 `""`/빈 셀은 텍스트 빈 문자열로 보존, 문자 `\N`은 NULL입니다. 문자열 `NULL`, `NaN`, `NA`를 자동으로 누락 처리하지 않습니다. 좌표·ID·Feature 등 필수 필드는 빈 셀도 오류입니다. 헤더 없는 파일, 중복 컬럼, 추가/누락 컬럼, 필드 수 불일치는 거부합니다.

## 부산 여부 검증의 한계

CSV import는 기존 파이프라인의 국내 좌표 범위와 부산 주소를 검증합니다. 정밀 행정경계 폴리곤 검증은 포함하지 않습니다. 사용자 제안 승인은 관리자가 실제 주소/좌표 일치 및 부산 여부를 확인했다는 근거가 필요합니다. 해당 검증을 공개 클라이언트의 bool 값만으로 승인하면 안 됩니다.

## 원본과 설계의 차이

상세설계서에는 축제 회차/날짜 확인 후 병합을 기술하지만 첨부 v2 코드는 이름·거리로 판단하며 별도 회차 필드가 없습니다. 이번 DB 전환에서는 이를 구현 완료로 가정하지 않았으며 기존 merge 로직도 수정하지 않았습니다. 회차·유효일 보완은 다음 데이터 품질 작업입니다.
