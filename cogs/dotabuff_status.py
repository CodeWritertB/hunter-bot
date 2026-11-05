"""Conservative, cached checks for visible Dotabuff match statistics."""
import asyncio
import re
import time
import aiohttp

_cache = {}
_gate = asyncio.Semaphore(3)


async def public_stats(session, account):
    cached = _cache.get(account)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    result = False
    ttl = 300
    try:
        async with _gate:
            async with session.get(f"https://www.dotabuff.com/players/{account}",
                                   allow_redirects=False) as response:
                if response.status == 200:
                    page = (await response.content.read(2000000)).decode("utf-8", errors="replace")
                    text = re.sub("<[^>]*>", " ", page)
                    result = bool(re.search(r'href=["\']/matches/\d+', page)) and bool(re.search(r"Win Rate|Winrate|Процент побед", text, re.I))
                    ttl = 3600
    except (aiohttp.ClientError, TimeoutError):
        pass
    if len(_cache) > 2048:
        _cache.clear()
    _cache[account] = (time.monotonic() + ttl, result)
    return result


async def statuses(players):
    accounts = {int(p.get("account_id", p.get("steam32")) or 0) for p in players}
    accounts = sorted(a for a in accounts if 0 < a < 4294967295)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
        values = await asyncio.gather(*(public_stats(session, account) for account in accounts))
    return dict(zip(accounts, values))
