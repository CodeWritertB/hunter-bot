"""Read-only Lavalink v4 readiness test; does not alter the current player."""
import os
import aiohttp


async def probe():
    url = os.getenv("LAVALINK_URL", "").rstrip("/")
    password = os.getenv("LAVALINK_PASSWORD", "")
    if not url or not password:
        return "Укажи LAVALINK_URL и LAVALINK_PASSWORD в окружении BotHost, затем перезапусти бота."
    if not url.startswith(("https://", "http://")):
        return "LAVALINK_URL должен начинаться с https:// или http://."
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers={"Authorization": password}) as session:
            async with session.get(url + "/v4/info", allow_redirects=False) as response:
                if response.status != 200:
                    return f"Lavalink /v4/info: HTTP {response.status}. Проверь адрес и пароль."
                info = await response.json()
            version = info.get("version", {}).get("semver", "неизвестна")
            plugin = any("youtube" in str(item.get("name", "")).lower() for item in info.get("plugins", []))
            async with session.get(url + "/v4/loadtracks", params={"identifier": "ytsearch:deadmau5 Kaskade I Remember"}, allow_redirects=False) as response:
                if response.status != 200:
                    return f"Lavalink {version}; YouTube plugin: {plugin}; поиск: HTTP {response.status}."
                result = await response.json()
            kind = result.get("loadType", "unknown")
            return (f"Lavalink {version} доступен.\nYouTube plugin: {'найден' if plugin else 'не обнаружен'}.\n"
                    f"Загрузка метаданных: {kind}.\n"
                    "Это проверка подключения и поиска. Доступность аудиопотока и передачу в Discord необходимо проверить отдельно.")
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return "Не удалось проверить Lavalink: соединение, TLS, таймаут или неверный формат ответа."
