import sqlite3
import os

# Подключение к БД с поддержкой многопоточности
conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "bot.db"), check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL")      # Параллельные чтения без блокировок
conn.execute("PRAGMA synchronous=NORMAL")    # Баланс между скоростью и надёжностью
conn.execute("PRAGMA cache_size=-8000")      # Кэш 8MB
cursor = conn.cursor()

# --- Создание таблиц ---
cursor.executescript("""
    -- Настройки серверов (все каналы и роли в одной таблице)
    CREATE TABLE IF NOT EXISTS servers (
        guild_id             INTEGER PRIMARY KEY,
        welcome_channel_id   INTEGER,  -- канал приветствий
        music_channel_id     INTEGER,  -- канал музыкального плеера
        lobby_channel_id     INTEGER,  -- лобби для создания комнат
        log_channel_id       INTEGER,  -- канал логов сервера
        admin_log_channel_id INTEGER,  -- канал админских логов
        verify_channel_id    INTEGER,  -- канал верификации
        guest_role_id        INTEGER,  -- роль гостя
        member_role_id       INTEGER   -- роль участника после верификации
    );

    -- Временные голосовые каналы (приватные комнаты)
    CREATE TABLE IF NOT EXISTS temp_channels (
        channel_id INTEGER PRIMARY KEY,
        guild_id   INTEGER NOT NULL,
        owner_id   INTEGER NOT NULL
    );

    -- Сохранённые настройки комнаты по владельцу
    CREATE TABLE IF NOT EXISTS room_settings (
        owner_id   INTEGER PRIMARY KEY,
        name       TEXT,
        user_limit INTEGER DEFAULT 0,
        locked     INTEGER DEFAULT 0,
        hidden     INTEGER DEFAULT 0
    );

    -- XP и уровни участников
    CREATE TABLE IF NOT EXISTS xp (
        guild_id INTEGER NOT NULL,
        user_id  INTEGER NOT NULL,
        xp       INTEGER DEFAULT 0,
        level    INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    );

    -- Роли за достижение уровня
    CREATE TABLE IF NOT EXISTS xp_roles (
        guild_id INTEGER NOT NULL,
        level    INTEGER NOT NULL,
        role_id  INTEGER NOT NULL,
        PRIMARY KEY (guild_id, level)
    );

    -- Стрики ежедневной активности в войсе
    CREATE TABLE IF NOT EXISTS voice_streaks (
        guild_id  INTEGER NOT NULL,
        user_id   INTEGER NOT NULL,
        streak    INTEGER DEFAULT 0,
        last_date TEXT,
        PRIMARY KEY (guild_id, user_id)
    );

    -- Статистика участников
    CREATE TABLE IF NOT EXISTS members (
        guild_id       INTEGER NOT NULL,
        user_id        INTEGER NOT NULL,
        username       TEXT,
        display_name   TEXT,
        joined_at      TEXT,
        messages_count INTEGER DEFAULT 0,
        voice_minutes  INTEGER DEFAULT 0,
        last_seen      TEXT,
        PRIMARY KEY (guild_id, user_id)
    );

    -- Общая статистика сервера
    CREATE TABLE IF NOT EXISTS server_stats (
        guild_id         INTEGER PRIMARY KEY,
        total_messages   INTEGER DEFAULT 0,
        total_voice_min  INTEGER DEFAULT 0,
        peak_online      INTEGER DEFAULT 0,
        peak_online_date TEXT
    );

    -- Привязка Steam аккаунтов
    CREATE TABLE IF NOT EXISTS steam_links (
        guild_id    INTEGER NOT NULL,
        user_id     INTEGER NOT NULL,
        steam_id    TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_steam_guild ON steam_links(guild_id);
    CREATE INDEX IF NOT EXISTS idx_streaks_guild  ON voice_streaks(guild_id);
    CREATE INDEX IF NOT EXISTS idx_temp_guild     ON temp_channels(guild_id);
    CREATE INDEX IF NOT EXISTS idx_members_guild  ON members(guild_id);
""")
conn.commit()

# Добавляем новые колонки если их нет (миграция на месте)
try:
    cursor.execute("ALTER TABLE servers ADD COLUMN dota_channel_id INTEGER")
    conn.commit()
except Exception:
    pass  # Колонка уже существует

try:
    cursor.execute("ALTER TABLE servers ADD COLUMN cs_channel_id INTEGER")
    conn.commit()
except Exception:
    pass

try:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dota_players (
            guild_id     INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            rank_tier    INTEGER DEFAULT 0,
            matches_week INTEGER DEFAULT 0,
            wins_week    INTEGER DEFAULT 0,
            week_start   TEXT,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    conn.commit()
except Exception:
    pass


# --- Вспомогательные функции ---

def _upsert_server(guild_id: int, **kwargs):
    """Универсальный upsert для таблицы servers."""
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" * len(kwargs))
    updates = ", ".join(f"{k}=excluded.{k}" for k in kwargs)
    cursor.execute(
        f"INSERT INTO servers (guild_id, {cols}) VALUES (?, {placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *kwargs.values())
    )
    conn.commit()


def _get_server(guild_id: int, col: str):
    """Получить одно поле из таблицы servers."""
    row = cursor.execute(f"SELECT {col} FROM servers WHERE guild_id = ?", (guild_id,)).fetchone()
    return row[0] if row else None


# --- Канал приветствий ---

def get_channel(guild_id: int):
    return _get_server(guild_id, "welcome_channel_id")

def set_channel(guild_id: int, channel_id: int):
    _upsert_server(guild_id, welcome_channel_id=channel_id)


# --- Музыкальный канал ---

def get_music_channel(guild_id: int):
    return _get_server(guild_id, "music_channel_id")

def set_music_channel(guild_id: int, channel_id: int):
    _upsert_server(guild_id, music_channel_id=channel_id)


# --- Лобби для голосовых комнат ---

def get_lobby(guild_id: int):
    return _get_server(guild_id, "lobby_channel_id")

def set_lobby(guild_id: int, channel_id: int):
    _upsert_server(guild_id, lobby_channel_id=channel_id)


# --- Каналы логов ---

def get_log_channel(guild_id: int):
    return _get_server(guild_id, "log_channel_id")

def set_log_channel(guild_id: int, channel_id: int):
    _upsert_server(guild_id, log_channel_id=channel_id)

def get_admin_log_channel(guild_id: int):
    return _get_server(guild_id, "admin_log_channel_id")

def set_admin_log_channel(guild_id: int, channel_id: int):
    _upsert_server(guild_id, admin_log_channel_id=channel_id)


# --- Верификация ---

def get_verify_settings(guild_id: int):
    """Возвращает (channel_id, guest_role_id, member_role_id) или None."""
    return cursor.execute(
        "SELECT verify_channel_id, guest_role_id, member_role_id FROM servers WHERE guild_id = ?", (guild_id,)
    ).fetchone()

def set_verify_settings(guild_id: int, channel_id: int, guest_role_id: int, member_role_id: int):
    _upsert_server(guild_id, verify_channel_id=channel_id, guest_role_id=guest_role_id, member_role_id=member_role_id)


# --- Временные голосовые каналы ---

def add_temp_channel(channel_id: int, guild_id: int, owner_id: int):
    cursor.execute(
        "INSERT OR REPLACE INTO temp_channels (channel_id, guild_id, owner_id) VALUES (?, ?, ?)",
        (channel_id, guild_id, owner_id)
    )
    conn.commit()

def get_temp_channel(channel_id: int):
    """Возвращает (guild_id, owner_id) или None."""
    return cursor.execute(
        "SELECT guild_id, owner_id FROM temp_channels WHERE channel_id = ?", (channel_id,)
    ).fetchone()

def update_temp_channel_owner(channel_id: int, owner_id: int):
    cursor.execute("UPDATE temp_channels SET owner_id = ? WHERE channel_id = ?", (owner_id, channel_id))
    conn.commit()

def remove_temp_channel(channel_id: int):
    cursor.execute("DELETE FROM temp_channels WHERE channel_id = ?", (channel_id,))
    conn.commit()

def is_temp_channel(channel_id: int) -> bool:
    """Проверяет является ли канал временной комнатой."""
    return cursor.execute(
        "SELECT 1 FROM temp_channels WHERE channel_id = ?", (channel_id,)
    ).fetchone() is not None


# --- Настройки комнаты ---

def save_room_settings(owner_id: int, name: str, user_limit: int, locked: bool, hidden: bool):
    """Сохраняет последние настройки комнаты для повторного использования."""
    cursor.execute(
        "INSERT INTO room_settings (owner_id, name, user_limit, locked, hidden) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(owner_id) DO UPDATE SET name=excluded.name, user_limit=excluded.user_limit, "
        "locked=excluded.locked, hidden=excluded.hidden",
        (owner_id, name, user_limit, int(locked), int(hidden))
    )
    conn.commit()

def get_room_settings(owner_id: int):
    """Возвращает (name, user_limit, locked, hidden) или None."""
    return cursor.execute(
        "SELECT name, user_limit, locked, hidden FROM room_settings WHERE owner_id = ?", (owner_id,)
    ).fetchone()


# --- XP система ---

def xp_for_level(level: int) -> int:
    """Количество XP необходимое для достижения следующего уровня."""
    return 100 * level

def get_xp(guild_id: int, user_id: int):
    """Возвращает (xp, level) участника."""
    row = cursor.execute(
        "SELECT xp, level FROM xp WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    ).fetchone()
    return row if row else (0, 0)

def add_xp(guild_id: int, user_id: int, amount: int) -> tuple[int, int, bool]:
    """Добавляет XP участнику. Возвращает (xp, level, leveled_up)."""
    xp, level = get_xp(guild_id, user_id)
    xp += amount
    new_level = level
    leveled_up = False
    # Проверяем повышение уровня
    while xp >= xp_for_level(new_level + 1):
        xp -= xp_for_level(new_level + 1)
        new_level += 1
        leveled_up = True
    cursor.execute(
        "INSERT INTO xp (guild_id, user_id, xp, level) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp, level=excluded.level",
        (guild_id, user_id, xp, new_level)
    )
    conn.commit()
    return xp, new_level, leveled_up

def get_leaderboard(guild_id: int, limit: int = 10):
    """Топ участников по уровню и XP."""
    return cursor.execute(
        "SELECT user_id, xp, level FROM xp WHERE guild_id = ? ORDER BY level DESC, xp DESC LIMIT ?",
        (guild_id, limit)
    ).fetchall()

def set_xp_role(guild_id: int, level: int, role_id: int):
    """Привязывает роль к уровню."""
    cursor.execute(
        "INSERT INTO xp_roles (guild_id, level, role_id) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id",
        (guild_id, level, role_id)
    )
    conn.commit()

def get_xp_role(guild_id: int, level: int):
    """Возвращает role_id для указанного уровня или None."""
    row = cursor.execute(
        "SELECT role_id FROM xp_roles WHERE guild_id = ? AND level = ?", (guild_id, level)
    ).fetchone()
    return row[0] if row else None


# --- Стрики войса ---

def update_streak(guild_id: int, user_id: int, streak: int, date_str: str):
    """Обновляет стрик участника."""
    cursor.execute(
        "INSERT INTO voice_streaks (guild_id, user_id, streak, last_date) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET streak=excluded.streak, last_date=excluded.last_date",
        (guild_id, user_id, streak, date_str)
    )
    conn.commit()

def get_all_streaks(guild_id: int):
    """Возвращает все стрики сервера: [(user_id, streak, last_date)]."""
    return cursor.execute(
        "SELECT user_id, streak, last_date FROM voice_streaks WHERE guild_id = ?", (guild_id,)
    ).fetchall()

def get_streak(guild_id: int, user_id: int):
    """Возвращает (streak, last_date) участника или (0, None)."""
    row = cursor.execute(
        "SELECT streak, last_date FROM voice_streaks WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    ).fetchone()
    return row if row else (0, None)


# --- Статистика участников ---

def upsert_member(guild_id: int, user_id: int, username: str, display_name: str, joined_at: str, last_seen: str):
    """Добавляет или обновляет запись участника."""
    cursor.execute(
        "INSERT INTO members (guild_id, user_id, username, display_name, joined_at, last_seen) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET username=excluded.username, "
        "display_name=excluded.display_name, last_seen=excluded.last_seen",
        (guild_id, user_id, username, display_name, joined_at, last_seen)
    )
    conn.commit()

def bulk_upsert_members(rows: list):
    """Батчевое обновление участников — используется при on_ready."""
    cursor.executemany(
        "INSERT INTO members (guild_id, user_id, username, display_name, joined_at, last_seen) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET username=excluded.username, "
        "display_name=excluded.display_name, last_seen=excluded.last_seen",
        rows
    )
    conn.commit()

def increment_messages(guild_id: int, user_id: int, last_seen: str):
    """Увеличивает счётчик сообщений участника на 1."""
    cursor.execute(
        "INSERT INTO members (guild_id, user_id, messages_count, last_seen) VALUES (?, ?, 1, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET messages_count=messages_count+1, last_seen=excluded.last_seen",
        (guild_id, user_id, last_seen)
    )
    conn.commit()

def add_voice_minutes(guild_id: int, user_id: int, minutes: int, last_seen: str):
    """Добавляет минуты проведённые в войсе."""
    cursor.execute(
        "INSERT INTO members (guild_id, user_id, voice_minutes, last_seen) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET voice_minutes=voice_minutes+excluded.voice_minutes, last_seen=excluded.last_seen",
        (guild_id, user_id, minutes, last_seen)
    )
    conn.commit()

def get_member_stats(guild_id: int, user_id: int):
    """Возвращает (username, display_name, joined_at, messages_count, voice_minutes, last_seen)."""
    return cursor.execute(
        "SELECT username, display_name, joined_at, messages_count, voice_minutes, last_seen "
        "FROM members WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    ).fetchone()


# --- Статистика сервера ---

def increment_server_messages(guild_id: int):
    """Увеличивает общий счётчик сообщений сервера."""
    cursor.execute(
        "INSERT INTO server_stats (guild_id, total_messages) VALUES (?, 1) "
        "ON CONFLICT(guild_id) DO UPDATE SET total_messages=total_messages+1",
        (guild_id,)
    )
    conn.commit()

def add_server_voice_minutes(guild_id: int, minutes: int):
    """Добавляет минуты к общему времени в войсе на сервере."""
    cursor.execute(
        "INSERT INTO server_stats (guild_id, total_voice_min) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET total_voice_min=total_voice_min+excluded.total_voice_min",
        (guild_id, minutes)
    )
    conn.commit()

def update_peak_online(guild_id: int, online: int, date_str: str):
    """Обновляет пик онлайна если текущее значение больше сохранённого."""
    cursor.execute(
        "INSERT INTO server_stats (guild_id, peak_online, peak_online_date) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET "
        "peak_online=CASE WHEN excluded.peak_online > peak_online THEN excluded.peak_online ELSE peak_online END, "
        "peak_online_date=CASE WHEN excluded.peak_online > peak_online THEN excluded.peak_online_date ELSE peak_online_date END",
        (guild_id, online, date_str)
    )
    conn.commit()

def get_server_stats(guild_id: int):
    """Возвращает (total_messages, total_voice_min, peak_online, peak_online_date)."""
    return cursor.execute(
        "SELECT total_messages, total_voice_min, peak_online, peak_online_date FROM server_stats WHERE guild_id = ?",
        (guild_id,)
    ).fetchone()

def get_top_members(guild_id: int, limit: int = 5):
    """Топ участников по количеству сообщений."""
    return cursor.execute(
        "SELECT user_id, messages_count, voice_minutes FROM members "
        "WHERE guild_id = ? ORDER BY messages_count DESC LIMIT ?",
        (guild_id, limit)
    ).fetchall()


# --- Steam ---

def link_steam(guild_id: int, user_id: int, steam_id: str):
    """Привязывает Steam ID к Discord пользователю."""
    cursor.execute(
        "INSERT INTO steam_links (guild_id, user_id, steam_id) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET steam_id=excluded.steam_id",
        (guild_id, user_id, steam_id)
    )
    conn.commit()

def get_steam_id(guild_id: int, user_id: int) -> str | None:
    """Возвращает Steam ID привязанный к Discord пользователю."""
    row = cursor.execute(
        "SELECT steam_id FROM steam_links WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    ).fetchone()
    return row[0] if row else None

def unlink_steam(guild_id: int, user_id: int):
    """Удаляет привязку Steam аккаунта."""
    cursor.execute("DELETE FROM steam_links WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    conn.commit()

def get_all_steam_links(guild_id: int) -> list:
    """Возвращает все привязки Steam для сервера: [(user_id, steam_id)]."""
    return cursor.execute(
        "SELECT user_id, steam_id FROM steam_links WHERE guild_id = ?", (guild_id,)
    ).fetchall()

# --- CS2 / Faceit ---

def link_faceit(guild_id: int, user_id: int, faceit_id: str, steam_id: str):
    """Привязывает Faceit + Steam аккаунт для CS2."""
    cursor.execute(
        "INSERT INTO steam_links (guild_id, user_id, steam_id) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET steam_id=excluded.steam_id",
        (guild_id, user_id, steam_id)
    )
    # Faceit ID храним отдельно через upsert_server-подобный механизм
    cursor.execute(
        "INSERT INTO members (guild_id, user_id, username) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET username=excluded.username",
        (guild_id, user_id, f"faceit:{faceit_id}")
    )
    conn.commit()

def get_faceit_id(guild_id: int, user_id: int) -> str | None:
    """Возвращает Faceit ID участника."""
    row = cursor.execute(
        "SELECT username FROM members WHERE guild_id = ? AND user_id = ? AND username LIKE 'faceit:%'",
        (guild_id, user_id)
    ).fetchone()
    return row[0].replace("faceit:", "") if row else None

def get_all_cs_links(guild_id: int) -> list:
    """Возвращает [(user_id, steam_id, faceit_id)] для всех привязанных CS игроков."""
    rows = cursor.execute(
        "SELECT s.user_id, s.steam_id, m.username "
        "FROM steam_links s "
        "LEFT JOIN members m ON s.guild_id = m.guild_id AND s.user_id = m.user_id AND m.username LIKE 'faceit:%' "
        "WHERE s.guild_id = ?",
        (guild_id,)
    ).fetchall()
    return [(r[0], r[1], r[2].replace("faceit:", "") if r[2] else None) for r in rows]

# --- Dota player tracking ---

def get_dota_player(guild_id: int, user_id: int):
    """Возвращает (rank_tier, matches_week, wins_week, week_start)."""
    return cursor.execute(
        "SELECT rank_tier, matches_week, wins_week, week_start FROM dota_players WHERE guild_id=? AND user_id=?",
        (guild_id, user_id)
    ).fetchone()

def update_dota_rank(guild_id: int, user_id: int, rank_tier: int):
    cursor.execute(
        "INSERT INTO dota_players (guild_id, user_id, rank_tier) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET rank_tier=excluded.rank_tier",
        (guild_id, user_id, rank_tier)
    )
    conn.commit()

def add_dota_match(guild_id: int, user_id: int, won: bool, week_start: str):
    """Добавляет матч в недельную статистику, сбрасывает если новая неделя."""
    row = get_dota_player(guild_id, user_id)
    if row and row[3] == week_start:
        cursor.execute(
            "UPDATE dota_players SET matches_week=matches_week+1, wins_week=wins_week+? WHERE guild_id=? AND user_id=?",
            (int(won), guild_id, user_id)
        )
    else:
        cursor.execute(
            "INSERT INTO dota_players (guild_id, user_id, matches_week, wins_week, week_start) VALUES (?,?,1,?,?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET matches_week=1, wins_week=excluded.wins_week, week_start=excluded.week_start",
            (guild_id, user_id, int(won), week_start)
        )
    conn.commit()

def get_guild_week_stats(guild_id: int) -> list:
    """Возвращает [(user_id, matches_week, wins_week)] для всех игроков гильдии."""
    return cursor.execute(
        "SELECT user_id, matches_week, wins_week FROM dota_players WHERE guild_id=? ORDER BY matches_week DESC",
        (guild_id,)
    ).fetchall()
