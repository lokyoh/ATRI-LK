import base64
import os
from io import BytesIO
from PIL import Image, ImageFont, ImageDraw

from ATRI import FONT_DIR, IMG_DIR


class IMGEditor:
    font_yz = os.path.join(FONT_DIR, 'yz.ttf')

    def __init__(self, image: bytes | Image.Image):
        if type(image) is bytes:
            self.img: Image = Image.open(BytesIO(image))
        else:
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

    def add_auto_text(self, x, y, text, font_size, color='black', font_path: str = font_yz, max_y: int = None,
                      max_width: int = None, line_spacing: int = None, vertical_align: str = 'top') -> "IMGEditor":
        """
        添加带自动换行功能的文本

        参数:
        x, y: 文本起始坐标
        text: 要绘制的文本
        font_size: 字体大小
        color: 文字颜色
        font_path: 字体路径
        max_y: 最大y坐标，文本将在y和max_y之间垂直居中
        max_width: 最大行宽（像素），为None时不换行
        line_spacing: 行间距，为None时默认为字体大小的1.2倍
        """
        draw = ImageDraw.Draw(self.img)
        font = ImageFont.truetype(font_path, font_size)
        # 计算行间距
        if line_spacing is None:
            line_spacing = int(font_size * 1.2)
        # 处理文本换行
        if max_width is None:
            lines = [text]
            total_height = font_size
        else:
            lines = self._wrap_text_by_chars(text, font, max_width, font_size)
            total_height = len(lines) * line_spacing
        # 计算垂直位置
        if max_y is not None and max_y > y:
            available_height = max_y - y
            if vertical_align == 'top':
                # 顶部对齐，保持原y坐标
                current_y = y
            elif vertical_align == 'center':
                # 垂直居中
                current_y = y + (available_height - total_height) // 2
            elif vertical_align == 'bottom':
                # 底部对齐
                current_y = max_y - total_height
            else:
                current_y = y
        else:
            current_y = y
        # 绘制多行文本
        for line in lines:
            draw.text((x, current_y), line, fill=color, font=font)
            current_y += line_spacing
        return self

    @staticmethod
    def _wrap_text_by_chars(text, font, max_width, font_size):
        """按字符换行（适合中文）"""
        lines = []
        current_line = []
        current_width = 0
        for char in text:
            if char == '\n':
                if current_line:
                    lines.append(''.join(current_line))
                    current_line = []
                    current_width = 0
                continue
            try:
                char_width = font.getbbox(char)[2] - font.getbbox(char)[0]
            except:
                char_width = font_size
            if current_width + char_width > max_width and current_line:
                lines.append(''.join(current_line))
                current_line = [char]
                current_width = char_width
            else:
                current_line.append(char)
                current_width += char_width
        if current_line:
            lines.append(''.join(current_line))
        return lines

    def to_bytes(self) -> bytes:
        """获取图像的bytes形式"""
        bytes_io = BytesIO()
        self.img.save(bytes_io, format='JPEG')
        return bytes_io.getvalue()

    def to_base64(self) -> str:
        bytes_io = BytesIO()
        self.img.save(bytes_io, format='JPEG')
        buffer = bytes_io.getvalue()
        base64_encoded = base64.b64encode(buffer).decode('utf-8')
        return f'base64://{base64_encoded}'

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
