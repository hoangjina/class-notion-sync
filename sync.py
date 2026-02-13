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
import re
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
# 1. 토큰 획득 (여러 방법 시도)
# ============================================================
def get_token():
    print("🌐 edbschool.co.kr 접속해서 토큰 가져오는 중...")
    token = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/142.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()

        # 방법 1: 네트워크 요청에서 토큰 가로채기
        def on_request(request):
            nonlocal token
            if token:
                return
            auth = request.headers.get("authorization", "")
            if auth.startswith("wixcode-pub"):
                token = auth
                print(f"   🔑 네트워크 요청에서 토큰 발견!")

        def on_response(response):
            nonlocal token
            if token:
                return
            # Wix 사이트의 초기 데이터에서 토큰 찾기
            if "warmup" in response.url or "siteassets" in response.url:
                try:
                    text = response.text()
                    match = re.search(r'wixcode-pub\.[A-Za-z0-9._-]+', text)
                    if match:
                        token = match.group(0)
                        print(f"   🔑 사이트 데이터에서 토큰 발견!")
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            # 메인 페이지 방문
            print("   📄 메인 페이지 로딩 중...")
            page.goto("https://www.edbschool.co.kr", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(8000)

            if not token:
                # 방법 2: 페이지 JavaScript 컨텍스트에서 토큰 찾기
                print("   🔍 페이지 컨텍스트에서 토큰 검색 중...")
                try:
                    # Wix는 window에 다양한 데이터를 저장함
                    js_token = page.evaluate("""() => {
                        // Wix warmup data에서 찾기
                        const scripts = document.querySelectorAll('script');
                        for (const s of scripts) {
                            const text = s.textContent || '';
                            const match = text.match(/wixcode-pub\\.[A-Za-z0-9._-]+/);
                            if (match) return match[0];
                        }
                        // window 객체에서 찾기
                        try {
                            const warmup = window.__WARMUP_DATA__ || window.warmupData;
                            if (warmup) {
                                const str = JSON.stringify(warmup);
                                const match = str.match(/wixcode-pub\\.[A-Za-z0-9._-]+/);
                                if (match) return match[0];
                            }
                        } catch(e) {}
                        return null;
                    }""")
                    if js_token:
                        token = js_token
                        print(f"   🔑 JavaScript 컨텍스트에서 토큰 발견!")
                except Exception as e:
                    print(f"   ⚠️ JS 컨텍스트 검색 실패: {e}")

            if not token:
                # 방법 3: 사이트의 다른 페이지들 방문해보기
                print("   📄 하위 페이지 탐색 중...")
                try:
                    links = page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('a[href]'))
                            .map(a => ({href: a.href, text: a.textContent.trim()}))
                            .filter(a => a.href.includes('edbschool.co.kr') && a.href !== window.location.href)
                            .slice(0, 10);
                    }""")
                    print(f"   발견된 링크: {len(links)}개")
                    for link in links:
                        if token:
                            break
                        href = link.get("href", "")
                        text = link.get("text", "")
                        if href and href.startswith("http"):
                            print(f"   → {text[:30]} ({href[:60]})")
                            try:
                                page.goto(href, wait_until="networkidle", timeout=30000)
                                page.wait_for_timeout(5000)
                            except Exception:
                                continue
                except Exception as e:
                    print(f"   ⚠️ 하위 페이지 탐색 실패: {e}")

            if not token:
                # 방법 4: 페이지 전체 HTML에서 토큰 찾기
                print("   🔍 HTML 소스에서 토큰 검색 중...")
                try:
                    html = page.content()
                    match = re.search(r'wixcode-pub\.[A-Za-z0-9._-]+', html)
                    if match:
                        token = match.group(0)
                        print(f"   🔑 HTML 소스에서 토큰 발견!")
                except Exception as e:
                    print(f"   ⚠️ HTML 검색 실패: {e}")

            if not token:
                # 방법 5: Wix의 /_api/v1/access-tokens 엔드포인트 직접 호출
                print("   🔍 Wix API에서 토큰 요청 중...")
                try:
                    api_response = page.evaluate("""async () => {
                        try {
                            const r = await fetch('/_api/v1/access-tokens', {
                                method: 'GET',
                                headers: {'Accept': 'application/json'}
                            });
                            return await r.text();
                        } catch(e) {
                            return 'error: ' + e.message;
                        }
                    }""")
                    if api_response and 'wixcode-pub' in str(api_response):
                        match = re.search(r'wixcode-pub\.[A-Za-z0-9._-]+', str(api_response))
                        if match:
                            token = match.group(0)
                            print(f"   🔑 Wix API에서 토큰 발견!")
                    else:
                        print(f"   API 응답: {str(api_response)[:200]}")
                except Exception as e:
                    print(f"   ⚠️ API 요청 실패: {e}")

        except Exception as e:
            print(f"   ❌ 페이지 로딩 실패: {e}")
        finally:
            browser.close()

    if token:
        print(f"   ✅ 토큰 획득 성공! (길이: {len(token)})")
    else:
        print("   ❌ 토큰 획득 실패 — 모든 방법 시도 완료")
        print("   💡 수동 토큰을 MANUAL_TOKEN secret으로 등록해보세요.")
        # 수동 토큰 fallback
        manual = os.environ.get("MANUAL_TOKEN", "")
        if manual:
            token = manual
            print("   ✅ 수동 토큰 사용!")
        else:
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
        time.sleep(0.5)
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
    data = item.get("data", item)
    title = str(
        data.get("title", "")
        or data.get("text", "")
        or data.get("name", "")
        or data.get("_id", "제목 없음")
    )[:2000]
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
    existing = notion_get_all_pages()
    if existing:
        print(f"   🗑️ 기존 {len(existing)}개 페이지 아카이브 중...")
        notion_archive_pages(existing)
    created = 0
    for class_name, items in all_data.items():
        for item in items:
            if notion_create_page(item, class_name):
                created += 1
            time.sleep(0.15)
    print(f"   ✅ {created}개 항목 노션에 추가 완료!")


def save_backup(all_data):
    os.makedirs("data", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"data/edb_data_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 백업 저장: {path}")


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
