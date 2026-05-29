import asyncio
import json
import os
import re
import subprocess
import sys
from typing import Callable, Optional

import httpx
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig


def detect_sif_auth_state(url: str = "", text: str = "", status_code: Optional[int] = None) -> str:
    """
    识别 SIF 页面状态：
    - login_required: 登录失效/需要重新登录
    - challenge: 验证码/风控拦截
    - ok: 未发现明显异常
    """
    u = (url or "").lower()
    t = (text or "").lower()

    hard_login_signals = [
        "session expired",
        "unauthorized",
        "请先登录",
        "在别的浏览器登录",
        "账号异常登录提醒",
        "为了保证您的账号安全，我们已将您的账号从本浏览器退出",
        "我知道了重新登录",
        "手机号",
        "手机号码",
        "密码",
    ]
    logged_in_markers = [
        "查销量",
        "反查流量",
        "广告透视仪",
        "流量时光机",
        "会员购买",
        "到期",
    ]
    challenge_signals = [
        "captcha",
        "robot",
        "verify",
        "verification",
        "验证码",
        "人机验证",
        "安全验证",
    ]

    if status_code in (401, 403):
        return "login_required"
    if "/login" in u or "/signin" in u:
        return "login_required"
    if any(k in t for k in challenge_signals):
        return "challenge"
    if any(k in t for k in logged_in_markers):
        return "ok"
    if any(k in t for k in hard_login_signals):
        return "login_required"
    return "ok"


def try_refresh_sif_profile(asin: str, log_progress: Callable[[str, str], None], base_dir: str) -> dict:
    """触发外部登录脚本修补 SIF Profile。"""
    log_progress(asin, "❌ SIF 登录已失效或需要登录，准备进入自动登录流程...")
    try:
        login_script = os.path.join(base_dir, "sif_login.py")
        log_progress(asin, "🔑 运行 sif_login.py 修补 Profile...")
        login_proc = subprocess.run([sys.executable, login_script], capture_output=True, text=True)
        if login_proc.returncode == 0:
            log_progress(asin, "✅ SIF 自动登录修补成功！")
            return {"data": [], "error": "SIF Session Refreshed, please retry"}
        log_progress(asin, f"❌ SIF 自动登录失败: {login_proc.stderr}")
        return {"data": [], "error": "SIF Login Failed"}
    except Exception as e:
        log_progress(asin, f"❌ 自动登录尝试异常: {str(e)}")
        return {"data": [], "error": f"SIF Login Exception: {str(e)}"}


async def probe_sif_session_after_timeout(
    sif_url: str,
    asin: str,
    browser_cfg: BrowserConfig,
    log_progress: Callable[[str, str], None],
) -> str:
    """
    当主抓取超时时，做一次轻量探测来判断是否为登录失效或风控拦截。
    返回: login_required | challenge | unknown
    """
    try:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            probe = await asyncio.wait_for(
                crawler.arun(
                    url=sif_url,
                    config=CrawlerRunConfig(
                        cache_mode=CacheMode.BYPASS,
                        css_selector="body",
                        wait_for="css:body",
                        wait_until="domcontentloaded",
                        process_iframes=False,
                        remove_overlay_elements=True,
                        page_timeout=15000,
                    ),
                ),
                timeout=25,
            )

            merged_text = " ".join(
                filter(
                    None,
                    [
                        getattr(probe, "error_message", ""),
                        probe.markdown or "",
                        probe.cleaned_html or "",
                    ],
                )
            )
            state = detect_sif_auth_state(
                url=probe.url or sif_url,
                text=merged_text,
                status_code=probe.status_code,
            )
            log_progress(asin, f"🧪 超时后登录态探测结果: {state}")
            if state in ("login_required", "challenge"):
                return state
    except Exception as e:
        log_progress(asin, f"⚠️ 超时后探测失败: {str(e)}")
    return "unknown"


def _extract_sif_top3_from_html(html_text: str) -> list[dict]:
    if not html_text:
        return []

    match = re.search(
        r'<div[^>]+id=["\']__SIF_TOP3__["\'][^>]*>(.*?)</div>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []

    payload = match.group(1).strip()
    if not payload:
        return []

    try:
        data = json.loads(payload)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    normalized = []
    for item in data[:3]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "keyword": str(item.get("keyword") or "").strip(),
                "organic_rank": str(item.get("organic_rank") or "-").strip() or "-",
                "ad_rank": str(item.get("ad_rank") or "-").strip() or "-",
            }
        )
    return [item for item in normalized if item["keyword"]]


def _get_session_page(crawler: AsyncWebCrawler, session_id: str):
    try:
        strategy = getattr(crawler, "crawler_strategy", None)
        manager = getattr(strategy, "browser_manager", None)
        sessions = getattr(manager, "sessions", {}) if manager else {}
        session = sessions.get(session_id)
        if not session:
            return None
        return session[1]
    except Exception:
        return None


async def _extract_sif_top3_via_page(crawler: AsyncWebCrawler, session_id: str) -> list[dict]:
    page = _get_session_page(crawler, session_id)
    if not page:
        return []

    try:
        return await page.evaluate(
            """
            async () => {
              const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
              const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const readRows = () => {
                const rows = Array.from(
                  document.querySelectorAll('.table_wrap.section_block.keyword_list_table_wrap .reverse_table_container tbody tr.el-table__row')
                ).slice(0, 3);

                return rows.map((row) => {
                  const cells = row.querySelectorAll('td');
                  const keywordCell = cells[1] || null;
                  const organicCell = cells[6] || null;
                  const spCell = cells[8] || null;

                  const keywordNode = keywordCell
                    ? (keywordCell.querySelector('.correct_direction.inline_block')
                      || keywordCell.querySelector('.icon_copy')
                      || keywordCell.querySelector('.edit_word'))
                    : null;
                  const organicNode = organicCell
                    ? (organicCell.querySelector('.rank_page_pos')
                      || organicCell.querySelector('.rank_num')
                      || organicCell)
                    : null;
                  const spNode = spCell
                    ? (spCell.querySelector('.rank_page_pos')
                      || spCell.querySelector('.rank_num')
                      || spCell)
                    : null;

                  return {
                    keyword: clean(keywordNode && keywordNode.textContent),
                    organic_rank: clean(organicNode && organicNode.textContent),
                    ad_rank: clean(spNode && spNode.textContent),
                  };
                });
              };

              let stableRows = [];
              for (let i = 0; i < 40; i++) {
                const rows = readRows();
                const ready = rows.length >= 3 && rows.every(item =>
                  item.keyword &&
                  item.organic_rank &&
                  item.organic_rank !== '-' &&
                  item.organic_rank.includes('第') &&
                  item.ad_rank
                );
                if (ready) {
                  stableRows = rows;
                  break;
                }
                await wait(500);
              }

              const rows = stableRows.length ? stableRows : readRows();

              return rows.map((row) => {
                return {
                  keyword: row.keyword || '',
                  organic_rank: row.organic_rank || '-',
                  ad_rank: row.ad_rank || '-',
                };
              }).filter(item => item.keyword);
            }
            """
        )
    except Exception:
        return []


async def fetch_sif_data_multilayer(
    asin: str,
    llm_cfg,
    browser_cfg: BrowserConfig,
    *,
    db_cache,
    cache_expiry_sec: int,
    debug_mode: bool,
    log_progress: Callable[[str, str], None],
    base_dir: str,
) -> dict:
    if not debug_mode:
        cached_rankings = db_cache.get(f"sif_{asin}")
        if cached_rankings:
            log_progress(asin, "🔍 SIF 数据已存在缓存中，跳过抓取")
            return {"data": cached_rankings, "error": None}

    sif_url = f"https://www.sif.com/reverse?country=US&asin={asin}&isListingSearch=false&trafficType="
    log_progress(asin, "🔍 准备获取 SIF 数据...")
    _ = llm_cfg
    needs_login_refresh = False
    daemon_base = str(os.getenv("SIF_DAEMON_URL", "") or "").strip().rstrip("/")

    if daemon_base:
        log_progress(asin, f"🔌 优先使用 SIF daemon: {daemon_base}")
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                warmup_resp = await client.post(
                    f"{daemon_base}/daemon/warmup",
                    json={"provider": "sif"},
                )
                warmup_resp.raise_for_status()
                daemon_resp = await client.post(
                    f"{daemon_base}/daemon/sif/query",
                    json={
                        "asin": asin,
                        "capture_network": os.getenv("SIF_DAEMON_CAPTURE_NETWORK", "").strip().lower() in {"1", "true", "yes", "on"},
                        "idle_ms": int(os.getenv("SIF_DAEMON_IDLE_MS", "2500") or 2500),
                    },
                )
                daemon_resp.raise_for_status()
                payload = daemon_resp.json()
        except Exception as e:
            log_progress(asin, f"⚠️ SIF daemon 调用失败，回退本地抓取: {str(e)}")
        else:
            state = str(payload.get("state", "unknown") or "unknown")
            rankings = payload.get("rankings") if isinstance(payload.get("rankings"), list) else []
            if state == "ok" and rankings:
                log_progress(asin, f"✅ SIF daemon 提取成功，结果条数: {len(rankings)}")
                db_cache.set(f"sif_{asin}", rankings, expire=cache_expiry_sec)
                return {"data": rankings, "error": None}
            if state == "login_required":
                log_progress(asin, "⚠️ SIF daemon 检测到登录失效，转自动登录修复")
                return try_refresh_sif_profile(asin, log_progress, base_dir)
            if state == "challenge":
                log_progress(asin, "❌ SIF daemon 检测到验证码/风控拦截")
                return {"data": [], "error": "SIF Challenge/CAPTCHA"}
            log_progress(asin, "⚠️ SIF daemon 未拿到有效结果，回退本地抓取")

    try:
        log_progress(asin, "🔍 正在启动 SIF 浏览器实例...")
        session_id = f"sif-fetch-{asin}"
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            js_scroll = [
                "const wait = (ms) => new Promise(r => setTimeout(r, ms));",
                "for (let i = 0; i < 20; i++) {"
                "  const wrap = document.querySelector('.table_wrap.section_block.keyword_list_table_wrap');"
                "  const rows = wrap ? wrap.querySelectorAll('.el-table__body tbody tr') : [];"
                "  if (rows.length >= 3) break;"
                "  await wait(500);"
                "}",
                "const wrap = document.querySelector('.table_wrap.section_block.keyword_list_table_wrap');"
                "if (wrap) { wrap.scrollIntoView({ block: 'start' }); window.scrollBy(0, -100); }",
                "await wait(2500);",
            ]
            log_progress(asin, "🔍 SIF 页面加载中 (arun)...")
            res = await asyncio.wait_for(
                crawler.arun(
                    url=sif_url,
                    config=CrawlerRunConfig(
                        session_id=session_id,
                        cache_mode=CacheMode.BYPASS,
                        css_selector="body",
                        wait_for="css:body",
                        wait_until="domcontentloaded",
                        js_code=js_scroll,
                        process_iframes=False,
                        remove_overlay_elements=False,
                        page_timeout=40000,
                    ),
                ),
                timeout=55,
            )
            log_progress(asin, f"✨ SIF arun 返回成功: {res.success} | Status: {res.status_code}")

            merged_probe_text = " ".join(
                filter(
                    None,
                    [
                        getattr(res, "error_message", ""),
                        res.markdown or "",
                        res.cleaned_html or "",
                    ],
                )
            )
            auth_state = detect_sif_auth_state(
                url=res.url or "",
                text=merged_probe_text,
                status_code=res.status_code,
            )
            redirect_to_login = auth_state == "login_required"
            blocked_by_challenge = auth_state == "challenge"

            final_url = (res.url or "").lower()
            requested_reverse = "/reverse" in sif_url.lower()
            landed_on_home = final_url.endswith("sif.com/") or final_url.endswith("sif.com")
            left_reverse_page = requested_reverse and final_url and "/reverse" not in final_url
            if auth_state == "ok" and (landed_on_home or left_reverse_page):
                redirect_to_login = True

            if redirect_to_login:
                needs_login_refresh = True
                log_progress(asin, "⚠️ 检测到登录失效，等待关闭当前浏览器后执行自动登录修复...")
            elif blocked_by_challenge:
                log_progress(asin, "❌ SIF 页面触发验证码/风控拦截")
                return {"data": [], "error": "SIF Challenge/CAPTCHA"}
            elif not res.success:
                err = getattr(res, "error_message", "SIF 页面加载失败")
                log_progress(asin, f"❌ SIF 加载失败: {err}")
                return {"data": [], "error": err}
            elif res.status_code and res.status_code >= 400:
                log_progress(asin, f"❌ SIF HTTP 错误: {res.status_code}")
                return {"data": [], "error": f"SIF HTTP {res.status_code}"}
            elif not needs_login_refresh:
                rankings = await _extract_sif_top3_via_page(crawler, session_id)
                log_progress(asin, f"🧪 SIF 页面直取结果条数: {len(rankings)}")
                if rankings:
                    log_progress(asin, "✅ SIF DOM 提取成功，进入缓存")
                    db_cache.set(f"sif_{asin}", rankings, expire=cache_expiry_sec)
                    return {"data": rankings, "error": None}

                page_html = res.cleaned_html or ""
                if "keyword_list_table_wrap" not in page_html and "reverse_table_container" not in page_html:
                    needs_login_refresh = True
                    log_progress(asin, "⚠️ 未检测到 SIF 结果表格，等待关闭当前浏览器后执行自动登录修复...")
                else:
                    err = getattr(res, "error_message", "") or "未找到 SIF 排名或内容为空"
                    log_progress(asin, f"❌ SIF 抓取失败: {err}")
                    return {"data": [], "error": err}
    except asyncio.TimeoutError:
        log_progress(asin, "⏰ SIF 抓取超时，开始登录态复核...")
        timeout_state = await probe_sif_session_after_timeout(sif_url, asin, browser_cfg, log_progress)
        if timeout_state == "login_required":
            return try_refresh_sif_profile(asin, log_progress, base_dir)
        if timeout_state == "challenge":
            return {"data": [], "error": "SIF Challenge/CAPTCHA (after timeout)"}
        log_progress(asin, "⏰ SIF 抓取超时 (55s)")
        return {"data": [], "error": "SIF Timeout"}
    except Exception as e:
        log_progress(asin, f"💥 SIF 抓取异常: {str(e)}")
        return {"data": [], "error": str(e)}

    if needs_login_refresh:
        return try_refresh_sif_profile(asin, log_progress, base_dir)
    return {"data": [], "error": "SIF Unknown State"}
