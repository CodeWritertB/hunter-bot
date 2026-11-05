import asyncio
import io
from pathlib import Path
import disnake
from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT = Path(__file__).resolve().parent.parent / "assets" / "NotoSans-Regular.ttf"


def render_room(vc, owner, icon_bytes):
    image=Image.new("RGB",(1100,650),"#101b1a")
    draw=ImageDraw.Draw(image)
    def text(value, xy, size=24, color="#e4e8df", width=950):
        face=ImageFont.truetype(str(FONT),size)
        value=str(value)
        while value and draw.textlength(value,font=face)>width:
            value=value[:-1]
        draw.text(xy,value,font=face,fill=color)
    draw.rectangle((0,0,1100,6),fill="#6bcfa0")
    if icon_bytes:
        try:
            avatar=ImageOps.fit(Image.open(io.BytesIO(icon_bytes)).convert("RGB"),(90,90))
            mask=Image.new("L",(90,90))
            ImageDraw.Draw(mask).ellipse((0,0,89,89),fill=255)
            image.paste(avatar,(42,35),mask)
        except (OSError,ValueError):
            pass
    text(vc.guild.name,(155,38),19,"#9dc4b0",850)
    text(vc.name,(155,72),32,"#fff0d2",850)
    text("ВЛАДЕЛЕЦ",(44,155),15,"#9cbaa7")
    text(owner.display_name,(44,180),29,"#80e5af")
    permissions=vc.overwrites_for(vc.guild.default_role)
    fields=[("Видимость","Скрыта" if permissions.view_channel is False else "Видна"),
            ("Места",str(vc.user_limit) if vc.user_limit else "Без лимита"),
            ("Видео и демонстрация","Запрещены" if permissions.stream is False else "По правам сервера"),
            ("Голос","Запрещён" if permissions.speak is False else "По правам сервера")]
    for i,(title,value) in enumerate(fields):
        x=44+(i%2)*525
        y=245+(i//2)*105
        draw.rounded_rectangle((x,y,x+490,y+88),12,fill="#1b2c29")
        text(title,(x+18,y+12),17,"#9cbaa7",450)
        text(value,(x+18,y+41),24,width=450)
    banned=[target.display_name for target,ow in vc.overwrites.items()
            if isinstance(target,disnake.Member) and ow.connect is False]
    text("ЗАБЛОКИРОВАНЫ В КОМНАТЕ",(44,475),15,"#9cbaa7")
    text(", ".join(banned) if banned else "Нет заблокированных участников",(44,507),21,width=1000)
    text("Hunter Bot · Управление кнопками ниже",(44,596),16,"#8aa395")
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
