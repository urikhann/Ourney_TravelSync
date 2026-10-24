"""Busan API collection and rule-based preprocessing.

Run: ourney-travelsync collect --output data/generated
Python: from ourney_travelsync.ingestion.travelsync_pipeline_rule_v2 import run
Rules are project assumptions, not provider-guaranteed preference labels.
"""
import argparse
import hashlib
import tempfile
import json
import math
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import requests

from ..data_schema import BASE, EXTRA, FEATURES
TYPES = {'12': '관광지', '14': '문화시설', '15': '축제/공연/행사',
         '25': '여행코스', '28': '레포츠', '32': '숙박', '38': '쇼핑', '39': '음식점'}
DISTRICTS = ['중구', '서구', '동구', '영도구', '부산진구', '동래구', '남구',
             '북구', '해운대구', '사하구', '금정구', '강서구', '연제구', '수영구', '사상구', '기장군']
TERMS = ['맛집', '카페', '자연 명소', '역사 명소', '미술관', '쇼핑', '체험',
         '테마파크', '공원', '축제', '박물관', '해수욕장', '전통시장', '문화유적',
         '갤러리', '공연장', '전망대', '산책', '레포츠', '온천']
TOUR_BASE = 'https://apis.data.go.kr/B551011/KorService2/'
KAKAO_URL = 'https://dapi.kakao.com/v2/local/search/keyword.json'

# 기존 코드의 명확한 소분류를 유지한다. 불명확한 코드에 취향을 강제하지 않는다.
# A02060700/A02061100에는 일괄 art+history를 부여하지 않는다.
# 시장의 food 일괄 부여도 제거하고 쇼핑만 부여한다.
CAT3_RULES = {
    'A01010400': ['nature', 'relax'], 'A01010700': ['nature', 'relax'],
    'A01011600': ['nature', 'relax'], 'A01011800': ['nature', 'relax'],
    'A02010100': ['history'], 'A02010200': ['history'], 'A02010700': ['history'],
    'A02010800': ['history'], 'A02010900': ['history'],
    'A02020600': ['activity', 'entertainment'], 'A02020700': ['relax'],
    'A02030100': ['activity'], 'A02040800': ['art'],
    'A02050300': ['entertainment', 'relax'], 'A03010200': ['activity'],
    'A03021700': ['activity', 'relax'], 'A03022700': ['activity', 'nature', 'relax'],
    'A04010100': ['shopping'], 'A04010200': ['shopping'],
    'A04010600': ['shopping'], 'A04010900': ['shopping'],
    'A05020100': ['food'], 'A05020200': ['food'], 'A05020300': ['food'],
    'A05020400': ['food'], 'A05020900': ['cafe'],
    'A02070200': ['festival'], 'A02081300': ['festival'],
}


def secret(name):
    """환경변수를 먼저 읽고, 없으면 Colab Secret을 읽는다. 값을 출력하지 않는다."""
    value = os.getenv(name, '')
    if not value:
        try:
            from google.colab import userdata
            value = userdata.get(name)
        except Exception:
            value = ''
    return (value or '').strip()


class Client:
    """HTTP/JSON 오류를 안전한 코드로 기록하고 일시적 오류만 최대 3회 재시도한다."""
    def __init__(self):
        self.session = requests.Session()
        self.errors = []
        self.fatal_error = None

    def get(self, url, params, source, context, headers=None):
        for attempt in range(3):
            code = 'UNKNOWN'
            retry = True
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=(10, 30))
                code = f'HTTP_{r.status_code}'
                if r.status_code == 200:
                    data = r.json()
                    if source == 'TOUR_API':
                        response = data.get('response', {})
                        result = str(response.get('header', {}).get('resultCode', ''))
                        if result not in ('0000', '00', '0'):
                            code = 'TOUR_RESULT_' + re.sub(r'[^0-9]', '', result)[:4]
                            retry = result in ('01', '05', '23')
                        elif 'body' not in response:
                            code = 'INVALID_SCHEMA'
                        else:
                            return data
                    elif isinstance(data.get('documents'), list) and isinstance(data.get('meta'), dict):
                        return data
                    else:
                        code = 'INVALID_SCHEMA'
                else:
                    retry = r.status_code in (408, 500, 502, 503, 504)
                    if r.status_code in (401, 403, 429):
                        self.fatal_error = code
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                # 예외 문자열/URL/본문은 키를 포함할 수 있으므로 출력하지 않는다.
                code = 'NETWORK_OR_JSON_ERROR'
            if not retry or attempt == 2:
                break
            time.sleep(2 ** attempt)
        entry = {'source': source, 'context': context, 'error': code}
        self.errors.append(entry)
        print('요청 실패:', source, context, code)
        return None


def tour_items(data):
    """TourAPI의 빈 items 및 단일 item 응답을 모두 목록으로 변환한다."""
    body = data['response']['body']
    value = (body.get('items') or {})
    items = value.get('item', []) if isinstance(value, dict) else []
    if isinstance(items, dict):
        items = [items]
    return items if isinstance(items, list) else [], body


def collect_tour(client, key):
    """부산 전체 페이지를 수집한다. 실패/반복 페이지는 완료로 표시하지 않는다."""
    rows, seen, count = [], set(), 0
    status = {'complete': False, 'total_count': None, 'received_rows': 0, 'pages': 0}
    if not key:
        status['reason'] = 'MISSING_KEY'
        return rows, status
    page = 1
    while True:
        data = client.get(TOUR_BASE + 'areaBasedList2', {
            'serviceKey': unquote(key), 'MobileOS': 'ETC', 'MobileApp': 'TravelSync',
            '_type': 'json', 'areaCode': 6, 'numOfRows': 100, 'pageNo': page,
            'arrange': 'A'}, 'TOUR_API', f'page={page}')
        if data is None:
            status['reason'] = 'PAGE_FAILED'
            break
        items, body = tour_items(data)
        status['pages'] += 1
        try:
            total = int(body['totalCount'])
            if status['total_count'] is not None and total != status['total_count']:
                status['total_count_changed'] = True
            status['total_count'] = total
        except (KeyError, ValueError, TypeError):
            total = None
        if not items:
            status['complete'] = total is None or count >= total
            status['reason'] = 'EMPTY_PAGE'
            break
        new = 0
        for item in items:
            identifier = str(item.get('contentid', '')).strip()
            if not identifier:
                status['missing_id'] = status.get('missing_id', 0) + 1
                continue
            if identifier not in seen:
                rows.append(item)
                seen.add(identifier)
                new += 1
        count += len(items)
        status['received_rows'] = count
        if new == 0:
            status['reason'] = 'REPEATED_PAGE'
            break
        if total is not None and count >= total:
            status['complete'] = len(seen) == total and not status.get('total_count_changed', False)
            status['reason'] = 'TOTAL_REACHED'
            break
        # totalCount가 없는 경우만 짧은 페이지를 종료 조건으로 사용한다.
        if total is None and len(items) < 100:
            status['complete'] = True
            status['reason'] = 'LAST_SHORT_PAGE'
            break
        page += 1
        time.sleep(0.15)
    return rows, status


def is_busan(item):
    """주소 시작의 행정구역을 확인한다. 상호명/주소 중간의 부산 문자열은 쓰지 않는다."""
    return any(re.match(r'^부산(?:광역시)?\s', str(item.get(k, '') or '').strip())
               for k in ('address_name', 'road_address_name'))


def collect_kakao(client, key, cache_dir=None, refresh=False):
    """시 전체+16개 구·군 검색. API가 노출하는 페이지까지만 수집한다.

    검색 결과는 표본이며 부산 실재 장소의 전수 목록이 아니다.
    is_end, pageable_count를 확인하고 page API 상한 45도 준수한다.
    """
    rows, seen, reports = [], set(), []
    for area in [''] + DISTRICTS:
        for term in TERMS:
            query = ' '.join(x for x in ('부산', area, term) if x)
            report = {'query': query, 'complete': False, 'pages': 0, 'received': 0,
                      'requested_pages': 0, 'cache_used': False}
            query_rows = []
            identity = {'version': 1, 'query': query, 'endpoint': KAKAO_URL,
                        'settings': {'size': 15, 'max_pages': 45}}
            path = cache_path(cache_dir, identity)
            cached = read_cache(path, identity) if not refresh else None
            if cached:
                query_rows = cached['rows']
                report.update(cached['report'])
                report.update(cache_used=True, requested_pages=0)
            for page in range(1, 46):
                if cached:
                    break
                if not key or getattr(client, 'fatal_error', None):
                    report['reason'] = 'AUTH_OR_QUOTA' if key else 'MISSING_KEY'
                    break
                report['requested_pages'] += 1
                data = client.get(KAKAO_URL, {'query': query, 'page': page, 'size': 15},
                                  'KAKAO', f'{query} page={page}',
                                  {'Authorization': 'KakaoAK ' + key})
                if data is None:
                    report['reason'] = 'PAGE_FAILED'
                    break
                docs, meta = data['documents'], data['meta']
                query_rows.extend(docs)
                report.update(pages=page, total_count=meta.get('total_count'),
                              pageable_count=meta.get('pageable_count'))
                report['received'] += len(docs)
                available = meta.get('pageable_count')
                try:
                    available = int(available) if available is not None else None
                except (ValueError, TypeError):
                    report['reason'] = 'INVALID_PAGEABLE_COUNT'
                    break
                if meta.get('is_end') is True or (available is not None and report['received'] >= int(available)):
                    report['complete'] = True
                    report['reason'] = 'EXPOSED_RESULTS_END'
                    break
                if not docs:
                    report['reason'] = 'UNEXPECTED_EMPTY_PAGE'
                    break
                time.sleep(0.1)
            if not report['complete'] and 'reason' not in report:
                report['reason'] = 'PAGE_LIMIT'
            # 완료 즉시 저장한다. 실패 Query는 부분 결과만 보존하고 캐시하지 않는다.
            if report['complete'] and not cached:
                write_cache(path, identity, query_rows, report, client)
            before = len(rows)
            for item in query_rows:
                identifier = str(item.get('id', '')).strip()
                if identifier and is_busan(item) and identifier not in seen:
                    rows.append(dict(item, search_query=query))
                    seen.add(identifier)
            report['added_places'] = len(rows) - before
            reports.append(report)
            if len(reports) % 20 == 0:
                print(f'Kakao 검색 진행: {len(reports)}/340, 부산 고유 장소 {len(rows)}개', flush=True)
            # 인증/쿼터 오류 뒤 수백 건의 무의미한 요청을 이어가지 않는다.
            if client.errors and client.errors[-1]['source'] == 'KAKAO' and client.errors[-1]['error'] in ('HTTP_401', 'HTTP_403', 'HTTP_429'):
                return rows, {'complete': False, 'reason': 'AUTH_OR_QUOTA', 'queries': reports}
    return rows, {'complete': all(x['complete'] for x in reports), 'queries': reports}


def cache_path(directory, identity):
    """비밀값 없이 요청 설정을 해시하여 Query 파일을 구분한다."""
    if directory is None:
        return None
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return Path(directory) / (digest + '.json')


def read_cache(path, identity):
    """손상·구버전·형식 불일치 캐시는 재수집한다."""
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        report, rows = value['report'], value['rows']
        if (value['identity'] == identity and report['complete'] is True
                and report['query'] == identity['query']
                and type(report['pages']) is int and 1 <= report['pages'] <= 45
                and type(report['received']) is int and report['received'] == len(rows)
                and isinstance(report['reason'], str)
                and isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write_cache(path, identity, rows, report, client):
    """동일 폴더 임시 파일을 원자 교체하고 저장 실패는 안전한 코드로 기록한다."""
    if path is None:
        return
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({'identity': identity, 'rows': rows, 'report': report}, handle,
                      ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        client.errors.append({'source': 'KAKAO', 'context': report['query'],
                              'error': 'CACHE_WRITE_FAILED'})
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def normalize(rows, source):
    """기존 공통 컬럼을 유지하고 ID 충돌을 막기 위해 Source 접두사를 붙인다."""
    result = []
    for p in rows:
        tour = source == 'TOUR_API'
        sid = str(p.get('contentid' if tour else 'id', ''))
        cat = str(p.get('contenttypeid' if tour else 'category_name', '') or '')
        result.append(dict(
            place_id=('tour:' if tour else 'kakao:') + sid, source_place_id=sid,
            place_name=p.get('title' if tour else 'place_name', ''), category=cat,
            category_name=TYPES.get(cat, '') if tour else cat,
            cat1=p.get('cat1', ''), cat2=p.get('cat2', ''), cat3=p.get('cat3', ''),
            address=' '.join(str(p.get(k, '') or '') for k in ('addr1', 'addr2')).strip() if tour else p.get('address_name', ''),
            road_address='' if tour else p.get('road_address_name', ''),
            latitude=p.get('mapy' if tour else 'y', ''), longitude=p.get('mapx' if tour else 'x', ''),
            phone=p.get('tel' if tour else 'phone', ''), place_url='' if tour else p.get('place_url', ''),
            image_url=p.get('firstimage', '') or p.get('firstimage2', ''), source=source,
            kakao_place_ids='' if tour else sid, feature_rules='', exclude_reason=''))
    df = pd.DataFrame(result, columns=BASE + EXTRA).fillna('')
    for col in ('latitude', 'longitude'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    for f in FEATURES:
        df[f] = 0
    return df


def classify(df):
    """Category를 먼저 적용하고 이름은 허용 Category 내에서만 보조로 사용한다.

    feature_rules에 실제로 적용된 규칙을 기록하여 분류 근거를 추적한다.
    숙박·여행코스는 10개 취향의 직접 대상이 아니므로 미분류로 보존한다.
    """
    df = df.copy()
    for i, r in df.iterrows():
        df.at[i, 'exclude_reason'] = ''
        selected, reasons = set(), []
        def add(fs, rule):
            selected.update(fs)
            reasons.append(rule)
        name, cat = str(r.place_name), str(r.category)
        tour = r.source == 'TOUR_API'
        if tour:
            if cat not in ('25', '32'):
                c1, c2, c3 = str(r.cat1), str(r.cat2), str(r.cat3)
                # 상위 분류와 코드 접두사가 맞는 경우에만 소분류 규칙 적용.
                coherent = (not c1 or c3.startswith(c1)) and (not c2 or c3.startswith(c2))
                if coherent and c3 in CAT3_RULES:
                    add(CAT3_RULES[c3], 'tour:cat3=' + c3)
                if cat == '39' and not selected:
                    if c3 == 'A05020900':
                        add(['cafe'], 'tour:cafe_code')
                    else:
                        add(['food'], 'tour:type=39')
                if cat == '38':
                    add(['shopping'], 'tour:type=38')
                if cat == '28':
                    add(['activity'], 'tour:type=28')
                if cat == '15':
                    add(['festival'], 'tour:type=15(event_bucket)')
                if cat == '12' and c1 == 'A01':
                    add(['nature'], 'tour:cat1=A01')
                if cat == '12' and c2 == 'A0201':
                    add(['history'], 'tour:cat2=A0201')
                if cat in ('12', '14') and c3 == 'A02060500':
                    add(['art'], 'tour:art_museum')
                # 명칭 보정은 관광지/문화시설/레포츠 등의 조건이 맞을 때만.
                for pattern, allowed, fs in [
                    (r'카페|커피', ('39',), ['cafe']),
                    (r'미술관|갤러리|미술전시', ('12', '14'), ['art']),
                    (r'해수욕장|해변|수목원|자연공원', ('12', '28'), ['nature', 'relax']),
                    (r'테마파크|놀이공원', ('12',), ['entertainment']),
                    (r'체험', ('12', '28'), ['activity']),
                    (r'패션거리|전통시장', ('12', '38'), ['shopping']),
                ]:
                    if cat in allowed and re.search(pattern, name, re.I):
                        add(fs, 'tour:name+category:' + pattern)
        else:
            parts = [x.strip() for x in cat.split('>')]
            root = parts[0] if parts else ''
            category = ' '.join(parts[1:])
            # 제조·교육 업종은 관광 Keyword가 있어도 추천하지 않는다.
            experience = ('체험' in name and (
                (root == '교육,학문' and any(x in parts[1:] for x in ('교육단체', '체험학습장')))
                or (root == '스포츠,레저' and '스포츠시설' in parts[1:])
                or (root == '문화,예술' and '미술,공예' in parts[1:])))
            if (root in ('기업', '교통,수송', '부동산')
                    or re.search(r'학원|학습지|교육서비스|제조업|공장|직업소개|연구소|주차장|주차시설|입출구|화장실', cat)
                    or (root == '교육,학문' and not experience)):
                df.at[i, 'exclude_reason'] = '관광 무관 업종'
            elif re.search(r'(?:입구|출구|입출구|주차장|매표소|관리사무소)(?:\s*\d+|\s*[A-Z])?$', name):
                df.at[i, 'exclude_reason'] = '부속시설'
            else:
                if experience:
                    add(['activity'], 'kakao:체험명칭+방문형 category=' + cat)
                    if root == '문화,예술' and '미술,공예' in parts[1:]:
                        add(['art'], 'kakao:체험명칭+문화,예술>미술,공예')
                    if re.search(r'VR|가상현실|게임|놀이', name + ' ' + category, re.I):
                        add(['entertainment'], 'kakao:방문형 체험+VR/가상현실/게임/놀이')
                if root == '음식점':
                    add(['cafe'] if re.search(r'카페|커피|디저트', category) else ['food'], 'kakao:음식점 세부분류')
                allowed = root in ('여행', '문화,예술', '스포츠,레저', '가정,생활')
                # 실제 수집에서 확인된 정확한 경로/노드만 보완한다.
                if root == '가정,생활' and '시장' in parts[1:]:
                    add(['shopping'], 'kakao:가정,생활>시장')
                if root == '문화,예술' and '화랑' in parts[1:]:
                    add(['art'], 'kakao:문화,예술>화랑')
                if root == '이슈' and '이벤트' in parts[1:] and '페스티벌' in parts[1:]:
                    add(['festival'], 'kakao:이슈>이벤트>페스티벌')
                if root == '여행' and any(x in parts[1:] for x in ('숲', '하천', '자연휴양림', '산', '호수', '섬')):
                    add(['nature', 'relax'], 'kakao:여행 자연 세부노드')
                if root == '여행' and '도보여행' in parts[1:]:
                    add(['activity', 'nature'], 'kakao:여행>도보여행')
                if allowed:
                    if root == '여행' and '공원' in parts[1:]:
                        add(['nature', 'relax'], 'kakao:여행>공원')
                    for pattern, fs in [
                        (r'백화점|쇼핑센터|복합쇼핑몰|아울렛|전통시장|재래시장', ['shopping']),
                        (r'미술관|갤러리|전시관|공연장|극장|영화관', ['art']),
                        (r'문화유적|유적지|고궁|성곽|고분|사찰|역사박물관', ['history']),
                        (r'자연명소|해수욕장|해변|수목원|식물원|도시근린공원|자연공원', ['nature', 'relax']),
                        (r'체험|레포츠|수상스포츠|서핑|등산|캠핑', ['activity']),
                        (r'테마파크|놀이공원|유원지|오락시설', ['entertainment']),
                        (r'온천|스파|찜질방', ['relax']),
                        (r'축제|지역행사', ['festival']),
                    ]:
                        if re.search(pattern, category):
                            add(fs, 'kakao:category:' + pattern)
                    # 박물관 전체에 art/history를 붙이지 않는다.
                    if root in ('여행', '문화,예술') and '박물관' in category and re.search(r'역사|민속', name):
                        add(['history'], 'kakao:박물관+역사/민속 명칭')
        # 수동 검증에서 확인된 오분류를 일반화한 보정 규칙.
        # 특정 장소명을 하드코딩하지 않고 '장소 유형' 기준으로만 보정한다.
        descriptor = f'{name} {str(r.category_name)}'.strip()

        # 인증대/간이역/정거장/진입광장 등은 관광 콘텐츠 자체가 아닌 부속시설이다.
        auxiliary = re.search(
            r'(?:(?:중간|시작|종점)\s*)?인증대$|간이역$|정거장$|진입광장$',
            name.strip(), re.I
        )
        if auxiliary:
            selected.clear()
            df.at[i, 'exclude_reason'] = '부속시설'
            reasons.append('common:부속시설 명칭')

        # 숙박/여행코스 및 이미 제외된 행에는 아래 취향 보정을 적용하지 않는다.
        elif not (tour and cat in ('25', '32')) and not df.at[i, 'exclude_reason']:
            # 체육공원은 자연관광보다 운동 목적이 중심인 장소로 처리한다.
            if re.search(r'체육공원', descriptor, re.I):
                selected.discard('nature')
                selected.update(['activity', 'relax'])
                reasons.append('common:체육공원=>activity+relax')

            # 예술마을은 예술/전시 목적을 우선한다.
            if re.search(r'예술마을', descriptor, re.I):
                selected.discard('activity')
                selected.add('art')
                reasons.append('common:예술마을=>art')

            # 영화관은 영상문화 공간이므로 art로 통일한다.
            if re.search(r'영화관', descriptor, re.I):
                selected.discard('entertainment')
                selected.add('art')
                reasons.append('common:영화관=>art')

            # 테마파크·놀이기구는 activity와 분리해 entertainment로 처리한다.
            if re.search(r'테마파크|놀이공원|워터테마파크|놀이기구|회전그네|유원지', descriptor, re.I):
                selected.discard('activity')
                selected.add('entertainment')
                reasons.append('common:테마파크/놀이기구=>entertainment')

            # 캠핑장은 직접 참여 활동 + 체류/휴식 장소로 처리한다.
            if re.search(r'캠핑장', descriptor, re.I):
                selected.discard('nature')
                selected.update(['activity', 'relax'])
                reasons.append('common:캠핑장=>activity+relax')

            # 숲길/둘레길/산책로는 자연 속 이동 활동과 휴식 목적을 함께 가진다.
            if re.search(r'숲길|둘레길|산책로', descriptor, re.I):
                selected.update(['nature', 'activity', 'relax'])
                reasons.append('common:숲길/둘레길/산책로=>nature+activity+relax')

            # 게임·VR 성격이 핵심인 행사/전시는 entertainment도 부여한다.
            if re.search(r'게임|VR|가상현실|e스포츠|이스포츠', descriptor, re.I):
                if cat == '15' or re.search(r'축제|행사|이벤트|전시', descriptor, re.I):
                    selected.add('entertainment')
                    reasons.append('common:게임/VR 행사=>entertainment')

        for f in FEATURES:
            df.at[i, f] = int(f in selected)
        df.at[i, 'feature_rules'] = '; '.join(reasons)
    df[FEATURES] = df[FEATURES].astype('int8')
    return df


def valid_coordinates(r):
    """누락/0/범위 밖 좌표는 거리 비교와 추천에서 제외한다."""
    return (pd.notna(r.latitude) and pd.notna(r.longitude)
            and 32 <= float(r.latitude) <= 39 and 124 <= float(r.longitude) <= 132)


def normal_name(name):
    """끝의 부산 지역 식별 괄호만 제거하고 본관·지점 등 구분 내용은 보존한다."""
    value = unicodedata.normalize('NFKC', str(name)).strip().lower()
    districts = '|'.join(map(re.escape, DISTRICTS))
    region = rf'(?:부산(?:광역시)?(?:\s+(?:{districts}))?|기장(?:군)?)'
    value = re.sub(rf'\(\s*{region}\s*\)\s*$', '', value)
    return re.sub(r'[^0-9a-z가-힣]', '', value)


def distance(a, b):
    """위경도 두 쌍의 Haversine 거리(m)."""
    y1, x1, y2, x2 = map(math.radians, [a.latitude, a.longitude, b.latitude, b.longitude])
    h = math.sin((y2-y1)/2)**2 + math.cos(y1)*math.cos(y2)*math.sin((x2-x1)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, h)))


def merge_sources(tour, kakao):
    """후보: 거리<=150m, 이름 동일 또는 유사도>=0.90(양쪽 이름>=4자).

    자동병합: 이름 동일+100m 이하 또는 유사도>=0.96+50m 이하.
    축제/공연/행사(15)는 이름 완전 동일+100m 이하일 때만 허용한다.
    후보 연결이 양쪽 모두 1개일 때만 자동병합한다(지점·복합시설 보호).
    공간 격자로 주변만 비교하여 전체 쌍 비교를 피한다.
    """
    candidates, grid = [], defaultdict(list)
    krows = {i: r for i, r in kakao.iterrows()}
    for i, r in krows.items():
        if valid_coordinates(r) and not r.exclude_reason:
            grid[(int(r.latitude / .002), int(r.longitude / .002))].append(i)
    for ti, t in tour.iterrows():
        if not valid_coordinates(t) or t.exclude_reason or str(t.category) in ('25', '32'):
            continue  # 여행코스와 숙박은 기존처럼 병합 대상에서 제외한다.
        nt = normal_name(t.place_name)
        if not nt:
            continue
        cell = (int(t.latitude / .002), int(t.longitude / .002))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for ki in grid.get((cell[0]+dy, cell[1]+dx), []):
                    k = krows[ki]
                    nk = normal_name(k.place_name)
                    d = distance(t, k)
                    if not nk or d > 150:
                        continue
                    sim = SequenceMatcher(None, nt, nk).ratio()
                    exact = nt == nk
                    if not exact and (min(len(nt), len(nk)) < 4 or sim < .90):
                        continue
                    # 식당/카페와 관광지 등 명백한 종류 충돌은 자동병합하지 않는다.
                    compatible = True
                    if t.category == '39':
                        compatible = str(k.category).startswith('음식점')
                    elif str(k.category).startswith('음식점'):
                        compatible = False
                    if t.category == '38':
                        compatible = bool(k.shopping)
                    auto = compatible and ((exact and d <= 100) or (sim >= .96 and d <= 50 and min(len(nt), len(nk)) >= 4))
                    if str(t.category) == '15':
                        auto = compatible and exact and d <= 100
                    candidates.append({'tour_index': ti, 'kakao_index': ki,
                        'tour_id': t.place_id, 'kakao_id': k.place_id,
                        'tour_name': t.place_name, 'kakao_name': k.place_name,
                        'distance_m': round(d, 3), 'name_similarity': round(sim, 6),
                        'eligible': auto, 'merged': False})
    tc, kc = defaultdict(int), defaultdict(int)
    for c in candidates:
        tc[c['tour_index']] += 1
        kc[c['kakao_index']] += 1
    merged, used = tour.copy(), set()
    for c in candidates:
        ti, ki = c['tour_index'], c['kakao_index']
        if not c['eligible'] or tc[ti] != 1 or kc[ki] != 1:
            continue
        k = krows[ki]
        for field in ('road_address', 'phone', 'place_url', 'image_url', 'address'):
            if not str(merged.at[ti, field] or '').strip():
                merged.at[ti, field] = k[field]
        for f in FEATURES:
            merged.at[ti, f] = max(int(merged.at[ti, f]), int(k[f]))
        merged.at[ti, 'source'] = 'TOUR_API+KAKAO'
        merged.at[ti, 'kakao_place_ids'] = k.source_place_id
        merged.at[ti, 'feature_rules'] += '; merged:' + k.feature_rules
        used.add(ki)
        c['merged'] = True
    all_places = pd.concat([merged, kakao.drop(index=list(used))], ignore_index=True)
    cols = ['tour_index', 'kakao_index', 'tour_id', 'kakao_id', 'tour_name',
            'kakao_name', 'distance_m', 'name_similarity', 'eligible', 'merged']
    return all_places, pd.DataFrame(candidates, columns=cols)


def prepare(tour_rows, kakao_rows):
    """전처리를 API 호출에서 분리한다. 원본 재처리와 테스트에 같은 함수를 쓴다."""
    tour = classify(normalize(tour_rows, 'TOUR_API'))
    kakao = classify(normalize(kakao_rows, 'KAKAO'))
    all_places, audit = merge_sources(tour, kakao)
    for i, r in all_places.iterrows():
        if not r.exclude_reason and not valid_coordinates(r):
            all_places.at[i, 'exclude_reason'] = '좌표 누락/범위 오류'
        elif not r.exclude_reason and not str(r.place_name).strip():
            all_places.at[i, 'exclude_reason'] = '장소명 누락'
    final = all_places[(all_places[FEATURES].sum(axis=1) > 0) & (all_places.exclude_reason == '')].copy()
    final[FEATURES] = final[FEATURES].astype('int8')
    assert final.place_id.is_unique
    assert (final[FEATURES].sum(axis=1) > 0).all()
    assert final[FEATURES].isin([0, 1]).all().all()
    assert len(all_places) == len(tour) + len(kakao) - int(audit.merged.sum())
    return tour, kakao, all_places, final, audit


def statistics(tour, kakao, all_places, final, audit):
    """수집 수는 Source 내부 ID 중복 제거 후 수이며, 후보 수는 Source 간 쌍 수이다."""
    stats = {}
    for label, df in [('TourAPI', tour), ('Kakao', kakao)]:
        stats[label + '_collected'] = len(df)
        stats[label + '_with_features'] = int((df[FEATURES].sum(axis=1) > 0).sum())
        stats[label + '_zero_features'] = int((df[FEATURES].sum(axis=1) == 0).sum())
    stats.update(zero_features_before_merge=stats['TourAPI_zero_features'] + stats['Kakao_zero_features'],
                 zero_features_after_merge=int((all_places[FEATURES].sum(axis=1) == 0).sum()),
                 duplicate_candidates=len(audit), merged_duplicates=int(audit.merged.sum()),
                 final_recommendation_places=len(final),
                 feature_counts={f: int(final[f].sum()) for f in FEATURES},
                 source_counts={str(k): int(v) for k, v in final.source.value_counts().items()},
                 excluded_by_reason=all_places.exclude_reason[all_places.exclude_reason != ''].value_counts().to_dict())
    return stats


def show_table(df, title):
    """Colab/Jupyter는 실제 표로 표시하고 터미널은 간단한 표를 출력한다."""
    print('\n' + title)
    try:
        from IPython import get_ipython
        from IPython.display import display
        if get_ipython() is not None:
            display(df.head(10))
            return
    except ImportError:
        pass
    print(df.head(10).to_string(index=False))


def evaluate_features(validation_csv, tour, kakao):
    """病합 전 Source/이름 키로 사람 정답을 평가한다. 0 분모 지표는 0이다."""
    metric_cols = ['feature', 'accuracy', 'precision', 'recall', 'F1', 'TP', 'FP', 'FN', 'TN', 'positive_count']
    mismatch_cols = ['place_name', 'source', 'feature', 'expected', 'predicted']
    unmatched_cols = ['place_name', 'source', 'reason']
    empty = (pd.DataFrame(columns=metric_cols), pd.DataFrame(columns=mismatch_cols),
             pd.DataFrame(columns=unmatched_cols))
    summary = {'status': 'skipped', 'reason': 'NO_VALIDATION_CSV',
               'zero_denominator': '0', 'total_rows': 0, 'evaluated_rows': 0,
               'coverage': 0.0, 'unmatched_count': 0, 'ambiguous_count': 0}
    if validation_csv is None:
        return summary, *empty
    try:
        truth = pd.read_csv(validation_csv, dtype=str, keep_default_na=False)
        summary['total_rows'] = len(truth)
        if not set(['place_name', 'source'] + FEATURES).issubset(truth.columns):
            raise ValueError('MISSING_COLUMNS')
        if not truth.source.isin(['TOUR_API', 'KAKAO']).all():
            raise ValueError('INVALID_SOURCE')
        if not truth[FEATURES].isin(['0', '1']).all().all():
            raise ValueError('INVALID_BINARY_VALUE')
        if truth.duplicated(['place_name', 'source']).any():
            raise ValueError('DUPLICATE_TRUTH_KEY')
        if truth.place_name.str.strip().eq('').any():
            raise ValueError('EMPTY_NAME')
    except ValueError as error:
        # 읽기 오류의 원문은 출력하지 않고 정해진 오류 코드만 사용한다.
        reason = str(error)
        allowed = {'MISSING_COLUMNS', 'INVALID_SOURCE', 'INVALID_BINARY_VALUE',
                   'DUPLICATE_TRUTH_KEY', 'EMPTY_NAME'}
        summary.update(status='invalid', reason=reason if reason in allowed else 'CSV_READ_FAILED')
        return summary, *empty
    except (OSError, UnicodeError):
        summary.update(status='invalid', reason='CSV_READ_FAILED')
        return summary, *empty
    automatic = pd.concat([tour, kakao], ignore_index=True)
    pairs, missing, differences = [], [], []
    for _, row in truth.iterrows():
        matches = automatic[(automatic.place_name == row.place_name) & (automatic.source == row.source)]
        if len(matches) != 1:
            reason = 'NOT_FOUND' if len(matches) == 0 else 'AMBIGUOUS'
            missing.append({'place_name': row.place_name, 'source': row.source, 'reason': reason})
            summary['unmatched_count' if len(matches) == 0 else 'ambiguous_count'] += 1
            continue
        pairs.append((row, matches.iloc[0]))
        for f in FEATURES:
            if int(row[f]) != int(matches.iloc[0][f]):
                differences.append({'place_name': row.place_name, 'source': row.source,
                                    'feature': f, 'expected': int(row[f]),
                                    'predicted': int(matches.iloc[0][f])})
    n = len(pairs)
    summary.update(evaluated_rows=n, coverage=n / len(truth) if len(truth) else 0,
                   unmatched=missing)
    if not n:
        summary.update(reason='NO_MATCHED_ROWS')
        return summary, empty[0], empty[1], pd.DataFrame(missing, columns=unmatched_cols)
    def ratio(a, b):
        return a / b if b else 0.0
    values = []
    for f in FEATURES:
        tp = fp = fn = tn = 0
        for expected, predicted in pairs:
            e, p = int(expected[f]), int(predicted[f])
            tp += e == 1 and p == 1
            fp += e == 0 and p == 1
            fn += e == 1 and p == 0
            tn += e == 0 and p == 0
        values.append(dict(feature=f, accuracy=(tp+tn)/n, precision=ratio(tp, tp+fp),
                           recall=ratio(tp, tp+fn), F1=ratio(2*tp, 2*tp+fp+fn),
                           TP=tp, FP=fp, FN=fn, TN=tn, positive_count=tp+fn))
    metrics = pd.DataFrame(values, columns=metric_cols)
    summary.update(status='evaluated', reason='OK', metrics=values,
                   micro_f1=ratio(2*int(metrics.TP.sum()), int((2*metrics.TP+metrics.FP+metrics.FN).sum())),
                   macro_f1=float(metrics.F1.mean()))
    return summary, metrics, pd.DataFrame(differences, columns=mismatch_cols), pd.DataFrame(missing, columns=unmatched_cols)


def run(output='data/generated', cache_dir=None, refresh=False, validation_csv=None):
    """수집·분류·병합·검증·저장을 수행하고 DataFrame을 반환한다.

    일부 요청 실패 시 partial_run/에 저장해 기존 정상 결과를 덮어쓰지 않는다.
    complete는 정해진 검색 작업의 성공이며 부산 모든 장소의 전수성을 뜻하지 않는다.
    """
    from .storage import validate_output_location
    validate_output_location(output)
    if cache_dir is not None:
        validate_output_location(cache_dir)
    client = Client()
    tour_rows, ts = collect_tour(client, secret('TOUR_API_KEY'))
    cache_dir = Path(cache_dir) if cache_dir is not None else Path(output) / 'kakao_cache'
    if client.fatal_error:
        kakao_rows, ks = [], {'complete': False, 'reason': 'AUTH_OR_QUOTA', 'queries': []}
    else:
        kakao_rows, ks = collect_kakao(client, secret('KAKAO_REST_API_KEY'), cache_dir, refresh)
    frames = prepare(tour_rows, kakao_rows)
    tour, kakao, all_places, final, audit = frames
    complete = ts['complete'] and ks['complete']
    root = Path(output) if complete else Path(output) / ('partial_run_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True, exist_ok=True)
    for filename, df in zip(['travelsync_processed_tour.csv', 'travelsync_processed_kakao.csv',
                             'travelsync_all_places.csv', 'travelsync_generated_places.csv'], frames[:4]):
        df.to_csv(root / filename, index=False, encoding='utf-8-sig')
    # API 원문도 보존해 분류 규칙 변경 시 재호출 없이 재처리할 수 있게 한다.
    for filename, rows in [('tour_original.json', tour_rows), ('kakao_original.json', kakao_rows)]:
        (root / filename).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    audit.to_csv(root / 'duplicate_audit.csv', index=False, encoding='utf-8-sig')
    stats = statistics(*frames)
    evaluation, metrics, mismatches, unmatched = evaluate_features(validation_csv, tour, kakao)
    if evaluation['status'] == 'evaluated':
        for filename, frame in [('feature_validation_metrics.csv', metrics),
                                ('feature_validation_mismatches.csv', mismatches),
                                ('feature_validation_unmatched.csv', unmatched)]:
            frame.to_csv(root / filename, index=False, encoding='utf-8-sig')
    template = pd.concat([tour, kakao])[['place_name', 'source']].drop_duplicates().copy()
    for feature in FEATURES:
        template[feature] = ''
    template.to_csv(root / 'feature_validation_template.csv', index=False, encoding='utf-8-sig')
    print('Feature 평가:', json.dumps(evaluation, ensure_ascii=False))
    report = {'executed_at_utc': datetime.now(timezone.utc).isoformat(),
              'complete': complete, 'tour_collection': ts, 'kakao_collection': ks,
              'errors': client.errors, 'statistics': stats, 'feature_validation': evaluation}
    (root / 'run_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('수집 작업 상태:', '완료' if complete else '미완료/부분 결과')
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    show_table(all_places.loc[all_places[FEATURES].sum(axis=1) == 0,
                             ['place_id', 'place_name', 'category', 'cat3', 'source', 'exclude_reason']], 'Feature가 모두 0인 예시')
    show_table(audit.loc[audit.merged], '실제 중복 병합 예시')
    print('저장 폴더:', str(root))
    return {'tour': tour, 'kakao': kakao, 'all_places': all_places, 'final_places': final,
            'audit': audit, 'report': report, 'output': root}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/generated')
    parser.add_argument('--cache-dir')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--validation-csv')
    args = parser.parse_args()
    run(args.output, args.cache_dir, args.refresh, args.validation_csv)
