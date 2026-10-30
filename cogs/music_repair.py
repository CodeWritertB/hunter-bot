import asyncio
import json
import logging
import re
import os
import shutil
import sys
import time
from pathlib import Path
from db.storage import DATA_DIR

log = logging.getLogger("cogs.music_repair")

PACKAGES = DATA_DIR / "music-runtime"
lock = asyncio.Lock()
last_attempt = 0.0


def runtime_path():
    node = shutil.which("node")
    if node:
        return node
    if PACKAGES.exists():
        return next((str(p) for p in PACKAGES.rglob("node.exe" if os.name == "nt" else "node") if p.is_file() and (os.name == "nt" or os.access(p, os.X_OK))), None)


def ffmpeg_path():
    configured = shutil.which(os.getenv("FFMPEG_PATH", "ffmpeg"))
    if configured:
        return configured
    binaries = DATA_DIR / "ffmpeg-compat-060" / "imageio_ffmpeg" / "binaries"
    return next((str(p) for p in binaries.glob("ffmpeg*") if p.is_file() and (p.suffix == ".exe" if os.name == "nt" else os.access(p, os.X_OK))), None)


def check_ffmpeg():
    import subprocess
    path = ffmpeg_path()
    if not path:
        return False
    try:
        result = subprocess.run([path, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.2", "-c:a", "libopus", "-f", "opus", "pipe:1"], capture_output=True, timeout=15)
        return result.returncode == 0 and result.stdout.startswith(b"OggS")
    except (OSError, subprocess.TimeoutExpired):
        return False


async def repair():
    global last_attempt
    if lock.locked():
        return "Обновление уже выполняется. Подожди завершения."
    async with lock:
        if time.monotonic() - last_attempt < 120:
            return "Повторное обновление доступно через две минуты."
        last_attempt = time.monotonic()
        PACKAGES.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", "--upgrade", "--target", str(PACKAGES),
                "--no-cache-dir", "--disable-pip-version-check", "yt-dlp[default]", "nodejs-wheel", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(process.wait(), 240)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return "Обновление превысило время ожидания. Проверь доступ хостинга к PyPI."
        if process.returncode:
            return "Установка не удалась. Проверь доступ к PyPI, свободное место и поддержку платформы."
        # Separate destination avoids selecting a stale binary from an earlier installation.
        compat = DATA_DIR / "ffmpeg-compat-060"
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", "--upgrade", "--only-binary=:all:", "--no-deps", "--target", str(compat), "--no-cache-dir", "imageio-ffmpeg==0.6.0", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(process.wait(), 180)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return "Не удалось установить другую сборку FFmpeg: время ожидания истекло."
        if process.returncode:
            return "Не удалось установить совместимую сборку FFmpeg для платформы хостинга."
        node = runtime_path()
        ffmpeg = await asyncio.to_thread(check_ffmpeg)
        return ("Компоненты YouTube обновлены в постоянном хранилище.\n"
                + ("✅ JavaScript runtime найден." if node else "❌ JavaScript runtime не найден.")
                + ("\n✅ FFmpeg прошёл реальное кодирование звука в Opus." if ffmpeg else "\n❌ FFmpeg не прошёл тест кодирования: проблема сборки или среды хостинга.")
                + "\nПопробуй добавить трек заново. Если YouTube требует вход или блокирует IP, обновление не заменяет авторизацию.")


def extract(query, flat, limit, cookies):
    import subprocess
    node = runtime_path()
    args = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download", "--no-warnings", "--socket-timeout", "20", "--retries", "2", "--playlist-end", str(limit)]
    if flat:
        args += ["--flat-playlist"]
    else:
        args += ["--no-playlist", "-f", "bestaudio/best"]
    if node:
        args += ["--js-runtimes", "node:" + node]
    if cookies:
        args += ["--cookies", cookies]
    args += ["--", query]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PACKAGES) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", env=env, timeout=90)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("YouTube request timed out") from error
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return json.loads(result.stdout)


def cookie_path():
    configured = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if configured:
        return configured
    setting = DATA_DIR / "youtube-cookie-path.txt"
    return setting.read_text(encoding="utf-8").strip() if setting.is_file() else None


def configure_cookies(raw):
    raw = raw.strip()
    if not raw:
        return "Путь не изменён."
    path = Path(raw).expanduser().resolve()
    allowed = Path("/app/shared").resolve() if os.name != "nt" else DATA_DIR.resolve()
    if not path.is_relative_to(allowed):
        raise ValueError("Файл должен находиться в /app/shared (локально — в каталоге данных бота).")
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Файл не найден или превышает 2 МБ.")
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        raise ValueError("Нужен файл cookies в Netscape-формате, а не пароль или код входа.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o600)
    (DATA_DIR / "youtube-cookie-path.txt").write_text(str(path), encoding="utf-8")
    os.environ["YOUTUBE_COOKIES_FILE"] = str(path)
    return "Файл cookies подключён. Повтори добавление трека; авторизация не гарантирует снятие ограничений YouTube."


def save_cookie_text(raw):
    import tempfile
    text = raw.strip()
    if not text.startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        raise ValueError("Требуется Netscape-формат")
    valid = []
    for line in text.splitlines():
        if not line.strip() or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            raise ValueError("Неверный формат строки cookies")
        domain = fields[0].removeprefix("#HttpOnly_").lstrip(".").lower()
        if domain == "youtube.com" or domain.endswith(".youtube.com"):
            valid.append(line)
    if not valid:
        raise ValueError("Нет cookies YouTube")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "youtube-cookies.txt"
    fd, temporary = tempfile.mkstemp(prefix="cookies-", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("# Netscape HTTP Cookie File\n" + "\n".join(valid) + "\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    (DATA_DIR / "youtube-cookie-path.txt").write_text(str(path), encoding="utf-8")
    os.environ["YOUTUBE_COOKIES_FILE"] = str(path)
    return "Cookies YouTube сохранены. Попробуй добавить трек. Это не гарантирует снятие ограничений IP."


def download_audio(uri):
    """Download with yt-dlp; FFmpeg never opens the remote HTTPS stream."""
    import subprocess
    import tempfile
    work = tempfile.TemporaryDirectory(prefix="hunter-audio-")
    env = os.environ.copy()
    if (PACKAGES / "yt_dlp" / "__main__.py").is_file():
        env["PYTHONPATH"] = str(PACKAGES) + os.pathsep + env.get("PYTHONPATH", "")
    args = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-progress", "--quiet", "--no-warnings",
            "--socket-timeout", "20", "--retries", "2", "--fragment-retries", "2", "--max-filesize", "150M",
            "--concurrent-fragments", "1", "-f", "bestaudio/best", "-o", str(Path(work.name) / "audio.%(ext)s")]
    node = runtime_path()
    if node:
        args += ["--js-runtimes", "node:" + node]
    cookies = cookie_path()
    if cookies:
        args += ["--cookies", cookies]
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        args += ["--ffmpeg-location", ffmpeg]
    args += ["--", uri]
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", env=env, timeout=180)
        if result.returncode:
            detail = result.stderr[-8000:]
            safe = re.sub(r"https?://[^\s]+", "[URL]", detail)
            safe = "\n".join(line for line in safe.splitlines() if not re.search(r"cookie|authorization|SAPISID|SID=", line, re.I))
            log.error("yt-dlp download failed: exit=%s; %s", result.returncode, safe[-4000:])
            lower = detail.lower()
            flags = {
                "login_required": "sign in" in lower or "not a bot" in lower,
                "cookies_rotated": "rotated" in lower,
                "cookies_invalid": "cookies are no longer valid" in lower or "cookies have expired" in lower,
                "http_403": "403" in lower,
                "http_429": "429" in lower,
                "po_token": "po token" in lower or "po_token" in lower,
                "js_challenge": "challenge" in lower or "javascript" in lower or "n challenge" in lower,
                "format_unavailable": "requested format" in lower,
            }
            cookie_status = {"configured": bool(cookies), "exists": False, "youtube_entries": 0, "expired_entries": 0}
            if cookies:
                try:
                    path = Path(cookies)
                    cookie_status["exists"] = path.is_file()
                    for line in path.read_text(encoding="utf-8-sig").splitlines():
                        if line.startswith("#") and not line.startswith("#HttpOnly_"):
                            continue
                        fields = line.split("\t")
                        if len(fields) == 7 and fields[0].removeprefix("#HttpOnly_").lstrip(".").endswith("youtube.com"):
                            cookie_status["youtube_entries"] += 1
                            if fields[4].isdigit() and 0 < int(fields[4]) < time.time():
                                cookie_status["expired_entries"] += 1
                except (OSError, UnicodeError):
                    cookie_status["unreadable"] = True
            log.error("YouTube diagnostic: flags=%s; cookie_status=%s; stderr_present=%s", flags, cookie_status, bool(detail.strip()))
            if "403" in lower:
                reason = "YouTube вернул HTTP 403 при скачивании аудио."
            elif "sign in" in lower or "not a bot" in lower:
                reason = "YouTube требует авторизацию или подтверждение, что запрос выполняет человек."
            elif "requested format" in lower:
                reason = "YouTube не предоставил доступный аудиоформат."
            elif "ffmpeg" in lower:
                reason = "yt-dlp не смог обработать аудио через FFmpeg."
            else:
                reason = f"Не удалось скачать аудио (код yt-dlp: {result.returncode})."
            raise RuntimeError(reason + " Подробности в журнале: yt-dlp download failed.")
        files = [f for f in Path(work.name).glob("audio.*") if f.is_file() and f.suffix not in {".part", ".ytdl", ".temp"}]
        if len(files) != 1 or files[0].stat().st_size == 0:
            log.error("yt-dlp download failed: no complete audio; files=%s", [(item.suffix, item.stat().st_size) for item in Path(work.name).iterdir() if item.is_file()])
            raise RuntimeError("Аудио не загружено: файл отсутствует, пуст или превышает лимит 150 МБ.")
        return str(files[0]), work
    except subprocess.TimeoutExpired as error:
        work.cleanup()
        raise RuntimeError("Скачивание превысило 180 секунд. Попробуй более короткий трек.") from error
    except Exception:
        work.cleanup()
        raise
