import io
import re
import urllib.request
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
from pathlib import Path

FONT = Path(__file__).resolve().parent.parent / "assets" / "NotoSans-Regular.ttf"


@lru_cache(maxsize=128)
def remote_image(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return Image.open(io.BytesIO(response.read(5000000))).convert("RGB")
    except Exception:
        return None


def render_cs_card(embed, guild, match_data=None, players_info=None):
    data = match_data or {}
    maps = data.get("voting", {}).get("map", {}).get("pick", [])
    map_name = data.get("_stats_map") or (maps[0] if maps else "")
    background = None
    if re.fullmatch(r"[a-z0-9_]+", map_name):
        background = remote_image("https://raw.githubusercontent.com/MurkyYT/cs2-map-icons/main/images/thumbs/" + map_name + "_png.png")
    image = Image.new("RGB", (1440, 1160), "#15191e")
    if background:
        image = ImageEnhance.Brightness(ImageOps.fit(background, image.size)).enhance(0.28)
    draw = ImageDraw.Draw(image)
    def clean(value):
        def member(match):
            user = guild.get_member(int(match.group(1))) if guild else None
            return user.display_name if user else "Участник"
        value = re.sub(r"<@!?(\d+)>", member, str(value))
        return re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", value).replace("**","")
    def text(value, xy, size=22, color="#e7e8e9", width=1200, center=False):
        value = clean(value)
        face = ImageFont.truetype(str(FONT), size)
        while value and draw.textlength(value,font=face)>width:
            value=value[:-1]
        x,y=xy
        if center:
            x-=draw.textlength(value,font=face)/2
        draw.text((x,y),value,font=face,fill=color)
    teams=data.get("teams",{})
    result=data.get("results",{})
    score=result.get("score",{})
    status=data.get("status","")
    status_text={"FINISHED":"МАТЧ ЗАВЕРШЁН","CANCELLED":"МАТЧ ОТМЕНЁН","ONGOING":"МАТЧ ИДЁТ","READY":"МАТЧ ГОТОВ","CONFIGURING":"ПОДГОТОВКА"}.get(status,"ОЖИДАНИЕ МАТЧА")
    if data.get("source")=="valve":
        status_text="НОВЫЙ МАТЧ VALVE"
    text("HUNTER BOT / CS2",(56,35),20,"#dfbd86")
    text(("VALVE" if data.get("source")=="valve" else "FACEIT")+"  ·  "+(map_name.removeprefix("de_").upper() or "КАРТА УТОЧНЯЕТСЯ"),(720,82),25,"#f3ddba",center=True)
    text(status_text,(720,126),23,"#ffd184",center=True)
    text("VS",(720,198),56,"#f4dfbd",center=True)
    duration = data.get("_duration")
    if not duration:
        try:
            seconds = max(0, int(float(data["finished_at"])) - int(float(data["started_at"])))
            duration = f"{seconds // 60} мин {seconds % 60:02d} сек"
        except (KeyError, TypeError, ValueError, OverflowError):
            duration = None
    if status == "FINISHED":
        text("ДЛИТЕЛЬНОСТЬ МАТЧА",(720,276),13,"#c6bda9",300,True)
        text(duration or "Нет данных",(720,300),23,"#fff0cf",320,True)
    elif duration:
        text(duration,(720,285),22,"#d7d4c9",320,True)
    ordered=list(teams.items())[:2]
    # CT/T are only displayed if the source explicitly identifies the team's side.
    for side,(faction,team) in enumerate(ordered):
        center=300 if side==0 else 1140
        accent="#8fbef5" if side==0 else "#f4bf6b"
        current_side=str(team.get("side","")).lower()
        title={"ct":"СПЕЦНАЗ","counter-terrorist":"СПЕЦНАЗ","t":"ТЕРРОРИСТЫ","terrorist":"ТЕРРОРИСТЫ"}.get(current_side,team.get("name","Команда "+str(side+1)))
        text(title,(center,132),32,accent,430,True)
        text(score.get(faction,"—"),(center,184),88,"#ffe0a2" if result.get("winner")==faction else "#fff6e6",430,True)
        if result.get("winner")==faction:
            draw.rounded_rectangle((center-125,313,center+125,360),12,fill="#ac6b26",outline="#ffd28c",width=2)
            text("ПОБЕДА",(center,318),25,"#fff0ca",240,True)
        x=56+side*670
        for index,player in enumerate(team.get("roster",[])[:5]):
            y=402+index*135
            draw.rounded_rectangle((x,y,x+642,y+123),14,fill="#171d23")
            info=next((entry for entry in players_info or [] if entry.get("faceit_id")==player.get("player_id")),{})
            server=bool(info.get("linked"))
            faceit=bool(player.get("player_id"))
            border="#67df92" if server else "#ed9e45" if faceit else "#596169"
            avatar=remote_image(player["_steam_avatar"]) if player.get("_steam_avatar") else None
            box=(x+15,y+17,x+95,y+97)
            mask=Image.new("L",(80,80))
            ImageDraw.Draw(mask).rounded_rectangle((0,0,79,79),10,fill=255)
            if avatar:
                image.paste(ImageOps.fit(avatar,(80,80)),box[:2],mask)
            else:
                draw.rounded_rectangle(box,10,fill="#303943")
            draw.rounded_rectangle((x+13,y+15,x+97,y+99),12,outline=border,width=2)
            if server and faceit:
                draw.rounded_rectangle((x+9,y+11,x+101,y+103),15,outline="#ed9e45",width=2)
            text(info.get("mention") or player.get("nickname","Игрок"),(x+115,y+14),22,border,250)
            draw.rounded_rectangle((x+115,y+57,x+355,y+95),8,fill="#292b2b")
            text("ELO",(x+128,y+66),12,"#c2b59d",40)
            text(info.get("elo","—"),(x+167,y+61),20,"#f3cf94",82)
            text("УР.",(x+253,y+66),12,"#c2b59d",32)
            text(info.get("level","—"),(x+290,y+61),20,"#f3cf94",55)
            draw.line((x+373,y+17,x+373,y+104),fill="#41433d",width=1)
            stats=player.get("_match_stats",{})
            for number,(caption,key,color) in enumerate((
                ("УБИЙСТВА","Kills","#c8edcd"),
                ("СМЕРТИ","Deaths","#ecb9ac"),
                ("ПОМОЩИ","Assists","#e5d6ad"),
            )):
                center=x+420+number*84
                text(caption,(center,y+19),10,"#aeb7bb",80,True)
                text(stats.get(key,"—"),(center,y+39),29,color,78,True)
            if stats:
                adr=stats.get("ADR","—")
                hs=str(stats.get("Headshots %","—")).rstrip("%")
                text(f"Урон/раунд {adr} · HS {hs}%",(x+394,y+88),12,"#b8c2ca",235)
            else:
                kd=info.get("kd","—")
                hs=str(info.get("hs","—")).rstrip("%")
                text(f"Профиль K/D {kd} · HS {hs}%",(x+394,y+88),12,"#b8c2ca",235)
    if not teams:
        text(embed.title,(56,420),28,width=1300)
        for index,field in enumerate(embed.fields[:4]):
            text(field.name+" "+field.value,(56,490+index*55),20,width=1300)
    text("Hunter Bot",(56,1110),18,"#b1b2a9")
    output=io.BytesIO()
    image.save(output,format="PNG",optimize=True)
    output.seek(0)
    return output
