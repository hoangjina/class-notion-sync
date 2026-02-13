#!/usr/bin/env python3
"""
EDB School → Notion 완전 자동 동기화
GitHub Actions에서 매주 자동 실행됩니다.
"""

import json
import sys
import os
import base64
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

# ============================================================
# 설정
# ============================================================
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "")

CLASSES = {
    "초급": {"collection_id": "Import66", "sort_field": "text", "limit": 100},
    "낮초": {"collection_id": "Import66", "sort_field": "text", "limit": 100},
    "낮중": {"collection_id": "Import824", "sort_field": "text", "limit": 100},
    "낮중2": {"collection_id": "Live939au0g9z2bq2w955b", "sort_field": "text", "limit": 100},
    "직장중급반": {"collection_id": "Live939au0gi89as22c955b", "sort_field": "newField1", "limit": 100},
    "직장초급반": {"collection_id": "Live939au0gi89as22c9z6b", "sort_field": "newField", "limit": 100},
    "기타": {"collection_id": "Yellow2939an3loog35nxfva335o", "sort_field": "title", "limit": 12},
}

APP_ID = "4db06b74-0d81-4c9b-9407-b1c5f5791eca"
BASE_URL = "https://www.edbschool.co.kr/_api/cloud-data/v2/items/query"


# ============================================================
# 1. Playwright로 토큰 자동 획득
# ============================================================
def get_token():
    """사이트 방문해서 Wix API 토큰을 자동으로 가져옵니다."""
    print("🌐 edbschool.co.kr 접속해서 토큰 가져오는 중...")
    token = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/142.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def on_request(request):
            nonlocal token
            if "cloud-data" in request.url and "authorization" in request.headers:
                token = request.headers["authorization"]

        page.on("request", on_request)

        try:
            # 메인 페이지 방문 → Wix가 데이터 로드하면서 토큰 발급
            page.goto("https://www.edbschool.co.kr", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(5000)

            # 토큰을 아직 못 잡았으면, 시간표 관련 페이지로 이동 시도
            if not token:
                # 네비게이션 링크를 클릭해서 데이터 로딩 트리거
                links = page.query_selector_all("a")
                for link in links:
                    href = link.get_attribute("href") or ""
                    text = link.inner_text() or ""
                    if any(kw in text for kw in ["시간표", "수업", "강의", "초급", "중급"]):
                        try:
                            link.click()
                            page.wait_for_timeout(5000)
                            break
                        except Exception:
                            continue

            # 그래도 없으면 좀 더 기다림
            if not token:
                page.wait_for_timeout(10000)

        except Exception as e:
            print(f"   ⚠️ 페이지 로딩 중 경고: {e}")
        finally:
            browser.close()

    if token:
        print("   ✅ 토큰 획득 성공!")
    else:
        print("   ❌ 토큰 획득 실패")
        sys.exit(1)

    return token


# ============================================================
# 2. API에서 데이터 가져오기
# ============================================================
def build_query(collection_id, sort_field, limit):
    query = {
        "dataCollectionId": collection_id,
        "query": {
            "filter": {},
            "sort": [{"fieldName": sort_field, "order": "ASC"}],
            "paging": {"offset": 0, "limit": limit},
            "fields": [],
        },
        "referencedItemOptions": [],
        "returnTotalCount": True,
        "environment": "LIVE",
        "appId": APP_ID,
    }
    return base64.b64encode(json.dumps(query).encode()).decode()


def fetch_class(name, conf, token):
    headers = {
        "x-wix-brand": "wix",
        "authorization": token,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    }
    url = f"{BASE_URL}?.r={build_query(conf['collection_id'], conf['sort_field'], conf['limit'])}"

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        items = r.json().get("dataItems", [])
        print(f"   ✅ {name}: {len(items)}개 항목")
        return items
    except requests.exceptions.HTTPError as e:
        print(f"   ❌ {name}: HTTP {e.response.status_code}")
        return []
    except Exception as e:
        print(f"   ❌ {name}: {e}")
        return []


def fetch_all(token):
    print("\n📚 학원 데이터 가져오는 중...")
    result = {}
    for name, conf in CLASSES.items():
        result[name] = fetch_class(name, conf, token)
        time.sleep(0.5)  # 서버 부담 줄이기
    return result


# ============================================================
# 3. 노션 동기화
# ============================================================
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def notion_get_all_pages():
    """기존 노션 페이지 전부 가져오기"""
    pages = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            r = requests.post(
                f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
                headers=NOTION_HEADERS, json=body, timeout=15
            )
            data = r.json()
            pages.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        except Exception:
            break
    return pages


def notion_archive_pages(pages):
    """기존 페이지들 아카이브"""
    for page in pages:
        try:
            requests.patch(
                f"https://api.notion.com/v1/pages/{page['id']}",
                headers=NOTION_HEADERS,
                json={"archived": True},
                timeout=10,
            )
        except Exception:
            pass
        time.sleep(0.1)


def notion_create_page(item, class_name):
    """새 페이지 생성"""
    data = item.get("data", item)

    title = str(
        data.get("title", "")
        or data.get("text", "")
        or data.get("name", "")
        or data.get("_id", "제목 없음")
    )[:2000]

    # 상세 정보 조합
    skip = {"_id", "_owner", "_createdDate", "_updatedDate", "title", "text", "name"}
    details = []
    for k, v in data.items():
        if k not in skip and v:
            details.append(f"{k}: {v}")
    detail_text = "\n".join(details)[:2000]

    properties = {
        "이름": {"title": [{"text": {"content": title}}]},
        "반": {"select": {"name": class_name}},
        "동기화일시": {"date": {"start": datetime.now().isoformat()}},
    }
    if detail_text:
        properties["상세"] = {"rich_text": [{"text": {"content": detail_text}}]}

    try:
        r = requests.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS,
            json={"parent": {"database_id": NOTION_DB_ID}, "properties": properties},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def sync_to_notion(all_data):
    if not NOTION_TOKEN or not NOTION_DB_ID:
        print("\n⚠️ NOTION_TOKEN 또는 NOTION_DB_ID가 설정되지 않았습니다.")
        return

    print("\n📝 노션 동기화 중...")

    # 기존 데이터 정리
    existing = notion_get_all_pages()
    if existing:
        print(f"   🗑️ 기존 {len(existing)}개 페이지 아카이브 중...")
        notion_archive_pages(existing)

    # 새 데이터 추가
    created = 0
    for class_name, items in all_data.items():
        for item in items:
            if notion_create_page(item, class_name):
                created += 1
            time.sleep(0.15)  # 노션 API 속도 제한 방지

    print(f"   ✅ {created}개 항목 노션에 추가 완료!")


# ============================================================
# 4. 로컬 백업 (GitHub Actions에서 커밋용)
# ============================================================
def save_backup(all_data):
    os.makedirs("data", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"data/edb_data_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    # 최신 데이터는 항상 latest.json으로도 저장
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"\n💾 백업 저장: {path}")


# ============================================================
# 메인
# ============================================================
def main():
    print("=" * 50)
    print("🏫 EDB School → Notion 자동 동기화")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    token = get_token()
    all_data = fetch_all(token)

    total = sum(len(v) for v in all_data.values())
    if total == 0:
        print("\n❌ 데이터를 가져오지 못했습니다.")
        sys.exit(1)

    save_backup(all_data)
    sync_to_notion(all_data)

    print("\n" + "=" * 50)
    print(f"✨ 완료! 총 {total}개 항목 처리")
    print("=" * 50)


if __name__ == "__main__":
    main()
