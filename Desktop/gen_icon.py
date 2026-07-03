"""Generate robot.ico - two ocean-blue robot eyes on a dark background."""
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (11, 15, 23, 255))
d = ImageDraw.Draw(img)
EYE = (16, 110, 170, 255)
HI = (120, 185, 225, 255)


def eye(cx):
    w, h = 48, 84
    d.rounded_rectangle([cx - w // 2, 128 - h // 2, cx + w // 2, 128 + h // 2],
                        radius=22, fill=EYE)


eye(94)
eye(162)
d.ellipse([82, 92, 100, 118], fill=HI)
d.ellipse([150, 92, 168, 118], fill=HI)
img.save("robot.ico", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
print("wrote robot.ico")
