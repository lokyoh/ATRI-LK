import os
from io import BytesIO
from PIL import Image, ImageFont, ImageDraw

from ATRI import FONT_DIR, IMG_DIR


class IMGEditor:
    font_yz = os.path.join(FONT_DIR, 'yz.ttf')

    def __init__(self, image: Image):
        self.img: Image = image

    def resize(self, target_width, target_height) -> "IMGEditor":
        width, height = self.img.size
        scale = max(target_width / width, target_height / height)
        new_width = int(width * scale)
        new_height = int(height * scale)
        resized_image = self.img.resize((new_width, new_height), Image.LANCZOS)
        left = (new_width - target_width) / 2
        top = (new_height - target_height) / 2
        right = (new_width + target_width) / 2
        bottom = (new_height + target_height) / 2
        self.img = resized_image.crop((left, top, right, bottom))
        return self

    def add_rectangle(self, x, y, rect_width, rect_height, opacity, corner_radius) -> "IMGEditor":
        rectangle = Image.new("RGBA", (rect_width, rect_height), (255, 255, 255, 0))
        ImageDraw.Draw(rectangle).rounded_rectangle((0, 0, rect_width, rect_height), corner_radius,
                                                    fill=(255, 255, 255, opacity))
        self.img.paste(rectangle, (x, y), rectangle)
        return self

    def add_text(self, x, y, text, font_size, color='black', font_path: str = font_yz) -> "IMGEditor":
        draw = ImageDraw.Draw(self.img)
        font = ImageFont.truetype(font_path, font_size)
        draw.text((x, y), text, fill=color, font=font)
        return self

    def add_right_text(self, x, y, text, font_size, color='black', font_path: str = font_yz) -> "IMGEditor":
        draw = ImageDraw.Draw(self.img)
        font = ImageFont.truetype(font_path, font_size)
        length = int(draw.textlength(text, font=font))
        x = x - length
        draw.text((x, y), text, fill=color, font=font)
        return self

    def add_middle_text(self, x, y, text, font_size, color='black', font_path: str = font_yz) -> "IMGEditor":
        draw = ImageDraw.Draw(self.img)
        font = ImageFont.truetype(font_path, font_size)
        length = int(draw.textlength(text, font=font))
        x = x - length / 2
        draw.text((x, y), text, fill=color, font=font)
        return self

    def add_border(self, x: int, y: int, w: int, h: int) -> "IMGEditor":
        """中心固定\nx, y:中心点位置\nw, h:宽高"""
        img_url = os.path.join(IMG_DIR, 'border.png')
        border_image = Image.open(img_url).convert("RGBA")
        border_image = border_image.resize((w, h), Image.LANCZOS)
        x = x - w / 2
        y = y - h / 2
        self.img.paste(border_image, (x, y), border_image)
        return self

    def add_circular_image(self, circular_img_path, x: int, y: int, diameter: int) -> "IMGEditor":
        """中心固定\nx, y:中心点位置\ndiameter:直径"""
        circular_img = Image.open(circular_img_path).convert("RGBA")
        circular_img = circular_img.resize((diameter, diameter), Image.LANCZOS)
        mask = Image.new("L", (diameter, diameter), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, diameter, diameter), fill=255)
        circular_img.putalpha(mask)
        x = x - diameter / 2
        y = y - diameter / 2
        self.img.paste(circular_img, (x, y), circular_img)
        return self

    def to_bytes(self) -> bytes:
        """获取图像的bytes形式"""
        bytes_io = BytesIO()
        self.img.save(bytes_io, format='JPEG')
        return bytes_io.getvalue()

    def get_image(self) -> Image:
        """获取Image对象"""
        return self.img

    def save_rgb(self, save_path):
        """以.jpg形式保存图片"""
        self.img.convert("RGB").save(save_path)


def get_image_bytes(image_path) -> bytes:
    """从图片文件获取bytes"""
    with open(image_path, "rb") as image_file:
        return image_file.read()
