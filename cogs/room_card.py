import asyncio
import io
from pathlib import Path
import disnake
from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT = Path(__file__).resolve().parent.parent / "assets" / "NotoSans-Regular.ttf"


def render_room(vc, owner, icon_bytes):
    image=Image.new("RGB",(640,620),"#101b1a")
    draw=ImageDraw.Draw(image)
    from cogs.emoji_text import draw_emoji_text
    def text(value, xy, size=30, color="#e4e8df", width=590):
        draw_emoji_text(image, value, xy, FONT, size, color, width)
    draw.rectangle((0,0,640,6),fill="#6bcfa0")
    if icon_bytes:
        try:
            avatar=ImageOps.fit(Image.open(io.BytesIO(icon_bytes)).convert("RGB"),(72,72))
            mask=Image.new("L",(72,72))
            ImageDraw.Draw(mask).ellipse((0,0,71,71),fill=255)
            image.paste(avatar,(24,24),mask)
        except (OSError,ValueError):
            pass
    text(vc.guild.name,(110,23),22,"#9dc4b0",505)
    text(vc.name,(24,109),40,"#fff0d2")
    text("ВЛАДЕЛЕЦ",(24,173),20,"#9cbaa7")
    text(owner.display_name,(24,203),30,"#80e5af")
    permissions=vc.overwrites_for(vc.guild.default_role)
    def access(value):
        return "Запрещено" if value is False else "Разрешено" if value is True else "По ролям"
    fields=[("ВИДИМОСТЬ","Скрыта" if permissions.view_channel is False else "Видна"),
            ("ЛИМИТ",str(vc.user_limit) if vc.user_limit else "Без лимита"),
            ("ВИДЕО / ДЕМОНСТРАЦИЯ",access(permissions.stream)),
            ("ГОЛОС",access(permissions.speak))]
    for i,(title,value) in enumerate(fields):
        x=24+(i%2)*304
        y=264+(i//2)*104
        draw.rounded_rectangle((x,y,x+288,y+92),12,fill="#1b2c29")
        text(title,(x+14,y+9),16,"#9cbaa7",260)
        text(value,(x+14,y+36),30,width=260)
    banned=[target.display_name for target,ow in vc.overwrites.items()
            if isinstance(target,disnake.Member) and ow.connect is False]
    text("БЛОКИРОВКИ · "+str(len(banned)),(24,486),20,"#9cbaa7")
    text(", ".join(banned) if banned else "Нет заблокированных",(24,518),25)
    text("Hunter Bot",(24,580),20,"#8aa395")
    buffer=io.BytesIO()
    image.save(buffer,format="PNG")
    buffer.seek(0)
    return buffer


async def room_payload(vc, owner):
    icon_bytes=None
    if vc.guild.icon:
        try:
            icon_bytes=await vc.guild.icon.with_size(128).read()
        except disnake.HTTPException:
            pass
    buffer=await asyncio.to_thread(render_room,vc,owner,icon_bytes)
    embed=disnake.Embed(color=disnake.Color.green())
    embed.set_image(url="attachment://room.png")
    return embed,disnake.File(buffer,filename="room.png")


async def update_room(message, vc, owner, **kwargs):
    embed,file=await room_payload(vc,owner)
    await message.edit(embed=embed,attachments=[],file=file,**kwargs)
