"""Persisted, category-aware discovery and least-recently-checked sampling."""
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

HOSTS = {'soluparts.cl', 'www.soluparts.cl'}
GOOD = {'ok', 'slow', 'transient'}


def canonical(url):
    try:
        value = urlsplit(url)
        if value.scheme not in {'http', 'https'} or value.hostname not in HOSTS or value.username:
            return None
        query = [(k,v) for k,v in parse_qsl(value.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in {'fbclid','gclid'}]
        return urlunsplit(('https', 'soluparts.cl', value.path or '/', urlencode(sorted(query)), ''))
    except ValueError:
        return None


def link_kind(url, pagination=False):
    url = canonical(url)
    if not url:
        return None
    value = urlsplit(url)
    path = value.path.lower()
    if any(x in path for x in ('/cart','/account','/login','/auth','/password','contacto','nosotros','preguntas','sucursales')):
        return None
    if 'listbycategory' in path or '/categoria/' in path or '/category/' in path:
        return 'listing'
    if 'search.rails' in path or '/listado/' in path or path.endswith('/product/list.rails'):
        return 'listing'
    if '/product/' in path or '/producto' in path or path.endswith('.html'):
        return 'product'
    if pagination and any(k.lower() in {'page','p','pagina'} for k,v in parse_qsl(value.query)):
        return 'listing'
    return None


def inventory_value(value):
    if isinstance(value, dict) and 'products' in value:
        return value
    products = {u:{'url':u,'group':'legacy','last_checked':None,'attempts':0}
                for item in (value or []) if (u:=canonical(item)) and link_kind(u)=='product'}
    return {'products':products, 'pages':{}, 'cycles':0, 'discovery_limited':False}


def group_for(source):
    value = urlsplit(source)
    params = dict(parse_qsl(value.query))
    category = next((v for k,v in params.items() if k.lower()=='categoryid'), None)
    if category:
        return 'category:' + category
    term = next((v for k,v in params.items() if k.lower()=='w'), None)
    if term:
        return 'search:' + term
    return 'catalog' if '/listado/' in value.path else 'home'


def ingest(inventory, links, source, now, product_limit=50000, page_limit=5000):
    group = group_for(source)
    for item in links:
        if isinstance(item,str):
            item={'url':item}
        url=canonical(item.get('url',''))
        kind=item.get('kind') or link_kind(url or '',item.get('pagination',False))
        if not url or kind not in {'product','listing'}:
            continue
        pool=inventory['products'] if kind=='product' else inventory['pages']
        limit=product_limit if kind=='product' else page_limit
        if url not in pool:
            if len(pool)>=limit:
                inventory['discovery_limited']=True
                continue
            pool[url]={'url':url,'group':group_for(url) if kind=='listing' else group,
                       'discovered_at':now,'last_checked':None,'attempts':0}
        elif kind=='product' and group.startswith('category:'):
            pool[url]['group']=group


def choose_products(inventory, count, cycle):
    """Unvisited before repeats; one item per category per round where possible."""
    selected=[]
    all_items=list(inventory['products'].values())
    for unvisited in (True, False):
        groups=defaultdict(list)
        for item in all_items:
            if (item.get('last_checked') is None)==unvisited:
                groups[item.get('group','unknown')].append(item)
        keys=sorted(groups)
        if not keys:
            continue
        offset=cycle%len(keys)
        keys=keys[offset:]+keys[:offset]
        for key in keys:
            groups[key].sort(key=lambda item:(item.get('last_checked') or '',item.get('discovered_at',''),item['url']))
        while len(selected)<count and any(groups.values()):
            for key in keys:
                if groups[key] and len(selected)<count:
                    selected.append(groups[key].pop(0)['url'])
    return selected


def choose_pages(inventory,count):
    items=sorted(inventory['pages'].values(), key=lambda item:(item.get('last_checked') is not None,
                  0 if 'listbycategory' in item['url'].lower() else 1,
                  item.get('last_checked') or '',item.get('discovered_at',''),item['url']))
    return [item['url'] for item in items[:count]]


def mark(inventory,url,kind,now,status):
    pool=inventory['products'] if kind=='product' else inventory['pages']
    url=canonical(url)
    if url not in pool:
        return
    item=pool[url]
    item.update(last_checked=now,last_status=status,attempts=item.get('attempts',0)+1)
    if status in GOOD:
        item['last_success']=now


def coverage(inventory):
    products=list(inventory['products'].values())
    return {'products_discovered':len(products),
            'unique_products_attempted':sum(x.get('attempts',0)>0 for x in products),
            'unique_products_successfully_loaded':sum(bool(x.get('last_success')) for x in products),
            'products_not_yet_checked':sum(not x.get('attempts',0) for x in products),
            'category_groups_discovered':len({x.get('group') for x in products if str(x.get('group')).startswith('category:')}),
            'listing_pages_discovered':len(inventory['pages']),
            'listing_pages_attempted':sum(x.get('attempts',0)>0 for x in inventory['pages'].values()),
            'discovery_limited':inventory.get('discovery_limited',False)}
