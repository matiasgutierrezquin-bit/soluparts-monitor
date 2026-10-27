from __future__ import annotations

import asyncio
import hashlib
import gzip
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .inventory import canonical, link_kind, inventory_value, ingest, choose_pages, choose_products, mark, coverage

from .core import UTC, iso
import datetime as dt

BLOCK_SIGNS = ("sorry, you have been blocked", "verify you are human", "checking your browser",
               "attention required! | cloudflare", "cf-chl-", "unusual traffic", "automated queries")


def classify_access(status, title: str, text: str, html: str = "") -> str:
    evidence = (title + "\n" + text + "\n" + html[:18000]).lower()
    if any(value in evidence for value in BLOCK_SIGNS) or (
        "just a moment" in title.lower() and ("cloudflare" in evidence or "/cdn-cgi/" in evidence)
    ):
        return "blocked"
    if status is None:
        return "network_error"
    if status >= 400:
        return "http_error"
    if status < 200 or status >= 300:
        return "inconclusive"
    if len(text.strip()) < 80:
        return "inconclusive"
    return "ok"


def same_shop(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {"soluparts.cl", "www.soluparts.cl"}


async def product_links(page, config) -> list[str]:
    values = await page.locator(config["listing_link_selector"]).evaluate_all(
        "els => els.filter(e => e.getBoundingClientRect().width > 0).map(e => e.href)")
    result = []
    for url in values:
        path = urlparse(url).path.lower()
        if link_kind(url)=='product' and (clean:=canonical(url)) not in result:
            result.append(clean)
    return result


async def save_evidence(page, record: dict, path: Path):
    path.mkdir(parents=True, exist_ok=True)
    try:
        screenshot = await page.screenshot(path=str(path / "page.jpg"), type="jpeg", quality=65,
                                           full_page=False, timeout=10000)
        record["screenshot"] = str(path / "page.jpg")
        record["screenshot_sha256"] = hashlib.sha256(screenshot).hexdigest()
    except Exception:
        record["screenshot_error"] = "No se pudo capturar la página"
    try:
        source = await page.content()
        (path / "page.html.gz").write_bytes(gzip.compress(source.encode("utf-8")))
        record["html_file"] = str(path / "page.html.gz")
        record["html_sha256"] = hashlib.sha256(source.encode()).hexdigest()
    except Exception:
        pass
    (path / "observation.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


async def inspect_page(context, url, kind, config, path):
    page = await context.new_page()
    failures, responses, script_errors = [], [], []
    page.on("requestfailed", lambda request: failures.append({"url": request.url[:1500],
                          "type": request.resource_type, "error": request.failure}))
    page.on("response", lambda response: responses.append({"url": response.url[:1500],
                          "status": response.status, "type": response.request.resource_type})
            if response.status >= 400 else None)
    page.on("pageerror", lambda error: script_errors.append(str(error)[:1500]))
    record = {"url": url, "kind": kind, "at": iso(dt.datetime.now(UTC)), "status": "monitor_error"}
    started = time.monotonic()
    links = []
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=config["navigation_timeout_ms"])
        record["http_status"] = response.status if response else None
        record["dom_ms"] = round((time.monotonic() - started) * 1000)
        record["cf_ray"] = (await response.all_headers()).get("cf-ray", "") if response else ""
        record["title"] = await page.title()
        text = await page.locator("body").inner_text(timeout=5000)
        record["final_url"] = page.url
        status = classify_access(record["http_status"], record["title"], text, await page.content())
        if not same_shop(page.url):
            status = "inconclusive"
            record["reason"] = "Redirección fuera de Soluparts"
        if status == "ok":
            try:
                await page.wait_for_load_state("load", timeout=config["render_timeout_ms"])
            except Exception:
                record["load_event_timeout"] = True
            if kind in {"home", "search", "listing"}:
                deadline = time.monotonic() + config["render_timeout_ms"] / 1000
                while time.monotonic() < deadline:
                    links = await product_links(page, config)
                    if links:
                        break
                    await asyncio.sleep(0.5)
                record["product_links_count"] = len(links)
                if not links:
                    status = "product_error" if config.get("_calibrated", {}).get(kind) else "inconclusive"
                    record["reason"] = "La página responde, pero no aparecen enlaces de productos con los selectores configurados; revisar también el diseño del sitio"
                else:
                    config.setdefault("_calibrated", {})[kind] = True
                anchors = await page.locator('a[href]').evaluate_all("""els => els.map(e => ({url:e.href,
                    pagination:!!e.closest('.pagination, .pagination-block, [aria-label*=Pagin], [aria-label*=pagin]')}))""")
                record['catalog_links'] = [{'url':canonical(x['url']),'kind':link_kind(x['url'],x['pagination'])}
                    for x in anchors if link_kind(x['url'],x['pagination'])]
                record['catalog_links'] += [{'url':url,'kind':'product'} for url in links]
            if kind == "product":
                details = await page.evaluate("""cfg => {
                  const visible = e => !!e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility !== 'hidden';
                  const title = [...document.querySelectorAll(cfg.product_name_selector)].find(visible);
                  const prices = [...document.querySelectorAll(cfg.product_price_selector)].filter(visible).map(e => e.innerText || e.getAttribute('content') || '');
                  const images = [...document.querySelectorAll(cfg.product_image_selector)].filter(visible).slice(0, 8);
                  return {name: title ? title.innerText.trim() : '', prices,
                    images: images.map(i => ({url:i.currentSrc || i.src, complete:i.complete, width:i.naturalWidth}))};
                }""", config)
                # Lazy-loaded main images must get a chance to enter the viewport.
                image = page.locator(config["product_image_selector"])
                if await image.count():
                    try:
                        await image.first.scroll_into_view_if_needed(timeout=3000)
                        await page.wait_for_function("""sel => [...document.querySelectorAll(sel)].some(i => i.complete && i.naturalWidth > 0)""",
                                                     arg=config["product_image_selector"], timeout=5000)
                    except Exception:
                        pass
                    details["images"] = await image.evaluate_all("els => els.filter(e=>e.getBoundingClientRect().width>0).slice(0,8).map(i=>({url:i.currentSrc||i.src,complete:i.complete,width:i.naturalWidth}))")
                record["product"] = details
                missing = []
                if not details["name"]:
                    missing.append("nombre")
                if not any(re.search(r"\d", p) for p in details["prices"]):
                    missing.append("precio")
                if not any(i["complete"] and i["width"] > 0 for i in details["images"]):
                    missing.append("imagen principal")
                if missing:
                    status = "product_error" if config.get("_calibrated", {}).get(kind) else "inconclusive"
                    record["reason"] = "No se pudo validar " + ", ".join(missing) + "; puede ser carga fallida o selector pendiente de ajuste"
                else:
                    config.setdefault("_calibrated", {})[kind] = True
            record["render_ms"] = round((time.monotonic() - started) * 1000)
            if status == "ok" and record["render_ms"] > config["slow_ms"]:
                status = "slow"
                record["reason"] = "Carga completa por sobre el umbral operativo de 10 segundos"
        record["status"] = status
    except Exception as error:
        name, message = type(error).__name__, str(error)
        record["status"] = "network_error" if "Timeout" in name or "net::ERR_" in message else "monitor_error"
        record["reason"] = message[:1800]
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        # Classify a rendered security page even if navigation timed out.
        try:
            title = await page.title()
            body = await page.locator("body").inner_text(timeout=2000)
            if classify_access(None, title, body) == "blocked":
                record.update(status="blocked", title=title)
        except Exception:
            pass
    record["network_failures"] = failures[:100]
    record["http_resource_errors"] = responses[:100]
    record["script_errors"] = script_errors[:25]
    # A broken tracker does not imply a failed purchase flow. Keep it as evidence only.
    if record["status"] != "ok":
        await save_evidence(page, record, path)
    await page.close()
    return record, links


async def inspect_with_retry(context, url, kind, config, path):
    first, links = await inspect_page(context, url, kind, config, path / "attempt1")
    if first["status"] == "ok":
        return first, links
    await asyncio.sleep(config["retry_seconds"])
    second, retry_links = await inspect_page(context, url, kind, config, path / "attempt2")
    second["first_attempt"] = first
    if second["status"] == "ok":
        second["status"] = "transient"
        second["reason"] = "El primer intento presentó una incidencia y el segundo cargó correctamente"
    return second, retry_links or links


async def probe(config, directory: Path, slot: int, known_products):
    from playwright.async_api import async_playwright
    records, discovered = [], inventory_value(known_products)
    discovered['cycles'] = discovered.get('cycles',0)+1
    cycle=discovered['cycles']
    started=time.monotonic()
    seed=config.get('catalog_url')
    if seed:
        ingest(discovered,[{'url':seed,'kind':'listing'}],seed,iso(dt.datetime.now(UTC)))
    search_value=urlsplit(config['search_url'])
    terms=config.get('search_terms',['FILTRO'])
    term=terms[(cycle-1)%len(terms)]
    query=[(k,term if k=='w' else v) for k,v in parse_qsl(search_value.query,keep_blank_values=True)]
    search_url=urlunsplit((search_value.scheme,search_value.netloc,search_value.path,urlencode(query),''))

    def remember(result,links):
        records.append(result)
        ingest(discovered,result.get('catalog_links',[])+[{'url':u,'kind':'product'} for u in links],
               result['url'],result.get('at',iso(dt.datetime.now(UTC))))
        config['_cycle_coverage']=coverage(discovered)

    def budget_ok():
        if time.monotonic()-started < config.get('cycle_budget_seconds',420):
            return True
        records.append({'kind':'coverage','url':config['site'],'at':iso(dt.datetime.now(UTC)),
                        'status':'inconclusive','reason':'Se alcanzó el tiempo máximo del control; las fichas pendientes no se cuentan como revisadas'})
        return False
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = await browser.new_context(locale="es-CL", timezone_id=config["timezone"],
                                            viewport={"width": 1365, "height": 900})
        try:
            for kind, url in (("home", config["site"]), ("search", search_url)):
                result, links = await inspect_with_retry(context, url, kind, config, directory / f"{slot:04d}" / kind)
                remember(result,links)
                # A confirmed block is not a reason to try alternate routes or proxies.
                if result["status"] == "blocked":
                    return records, discovered
            for i,url in enumerate(choose_pages(discovered,config.get('listing_pages_per_cycle',4))):
                if not budget_ok():
                    return records,discovered
                result,links=await inspect_with_retry(context,url,'listing',config,directory/f'{slot:04d}'/f'listing{i+1}')
                remember(result,links)
                mark(discovered,url,'listing',result['at'],result['status'])
                if result['status']=='blocked':
                    return records,discovered
            selection=choose_products(discovered,config['products_per_cycle'],cycle)
            if not selection:
                records.append({"kind": "product_sample", "url": config["site"], "at": iso(dt.datetime.now(UTC)),
                                "status": "inconclusive", "reason": "No se descubrieron fichas; falta validar el catálogo/selectores"})
            for i, url in enumerate(selection):
                if not budget_ok():
                    break
                result, _ = await inspect_with_retry(context, url, "product", config,
                                                     directory / f"{slot:04d}" / f"product{i+1}")
                records.append(result)
                mark(discovered,url,'product',result['at'],result['status'])
                if result["status"] == "blocked":
                    break
        finally:
            await context.close()
            await browser.close()
    config['_cycle_coverage']=coverage(discovered)
    config['_cycle_coverage']['products_checked_this_cycle']=sum(x.get('kind')=='product' for x in records)
    return records, discovered
