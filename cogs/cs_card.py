import io
import re
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

FONT = Path(__file__).resolve().parent.parent / "assets" / "NotoSans-Regular.ttf"


def render_cs_card(embed, guild, match_data=None, players_info=None):
    data = match_data or {}
    image = Image.new("RGB", (1440, 1060))
    draw = ImageDraw.Draw(image)
    for y in range(1060):
        draw.line((0, y, 1440, y), fill=(20 + int(15 * (1-y/1060)), 23 + int(8*y/1060), 29))
    def text(value, xy, size=22, color="#e7e8e9", width=1200):
        def mention(match):
            member = guild.get_member(int(match.group(1))) if guild else None
            return member.display_name if member else "Участник"
        value = re.sub(r"<@!?(\d+)>", mention, str(value))
        value = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", value).replace("**", "")
        face = ImageFont.truetype(str(FONT), size)
        while value and draw.textlength(value, font=face) > width:
            value = value[:-1]
        draw.text(xy, value, font=face, fill=color)
    draw.rectangle((0,0,1440,7),fill="#f7a928")
    text("HUNTER BOT  /  COUNTER-STRIKE 2", (56,38),20,"#d5ae6b")
    text("CS2", (52,83),80,"#faf4e6")
    finished = data.get("status") == "FINISHED"
    text("НОВЫЙ МАТЧ VALVE" if data.get("source") == "valve" else "МАТЧ ЗАВЕРШЁН" if finished else "МАТЧ ИДЁТ", (900,118),27,"#f2bd68",480)
    result = data.get("results", {})
    score = result.get("score", {})
    text(str(score.get("faction1", "—")), (475,193),80,"#edb258")
    text("VS", (675,225),36,"#bfc7d1")
    text(str(score.get("faction2", "—")), (855,193),80,"#86b3e4")
    maps = data.get("voting", {}).get("map", {}).get("pick", [])
    text(("VALVE  ·  История матчей" if data.get("source") == "valve" else "FACEIT  ·  " + (", ".join(maps) if maps else "Карта уточняется")), (56,315),23)
    teams = data.get("teams", {})
    for side, faction in enumerate(("faction1","faction2")):
        x = 56 + side*670
        team = teams.get(faction, {})
        accent = "#f4b85d" if side==0 else "#8cb9ed"
        text(team.get("name", "Команда " + str(side+1)),(x,375),28,accent,620)
        if result.get("winner")==faction:
            text("ПОБЕДА",(x+460,384),18,"#f7d38a",160)
        roster = team.get("roster", [])
        for index, player in enumerate(roster[:5]):
            y=438+index*105
            draw.rounded_rectangle((x,y,x+642,y+94),12,fill="#242a32")
            info=next((p for p in players_info or [] if p.get("faceit_id")==player.get("player_id")),{})
            text(info.get("mention") or player.get("nickname","Игрок"),(x+20,y+12),23,width=600)
            stats=player.get("_match_stats",{})
            if stats:
                line="У/С/П  " + "/".join(str(stats.get(key,"—")) for key in ("Kills","Deaths","Assists"))
                line+="  ·  Урон/раунд " + str(stats.get("ADR","—")) + "  ·  HS " + str(stats.get("Headshots %","—"))+"%"
            else:
                line="Профиль · ELO "+str(info.get("elo","—"))+" · K/D "+str(info.get("kd","—"))+" · HS "+str(info.get("hs","—"))+"%"
            text(line,(x+20,y+53),16,"#b8c1cc",600)
    if not teams:
        text(embed.title,(56,440),28,width=1300)
        for index,field in enumerate(embed.fields[:4]):
            text(field.name+"  "+field.value,(56,510+index*55),20,width=1300)
    text("Hunter Bot",(56,1008),18,"#84909d")
    output=io.BytesIO()
    image.save(output,format="PNG")
    output.seek(0)
    return output
