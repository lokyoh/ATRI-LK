import json
import random
from typing import Optional

from ATRI.dir import RES_DIR
from ATRI.message import img_msg_from_path


class FaceManager:
    """表情管理器，负责读取表情分类描述和获取随机表情"""

    def __init__(self):
        self.memes_data = {}
        self.memes_dir = RES_DIR / "data" / "lkchat" / "memes"
        self.data_file = self.memes_dir / "memes_data.json"
        self._load_data()

    def _load_data(self):
        """加载表情数据"""
        if self.data_file.exists():
            with open(self.data_file, "r", encoding="utf-8") as f:
                self.memes_data = json.load(f)

    @classmethod
    def get_face(cls, meme: str) -> Optional:
        """
        根据 meme 类型获取随机表情图片消息
        
        :param meme: 表情类型（如 "amused", "distress" 等）
        :return: MessageSegment 对象，如果表情不存在则返回 None
        """
        manager = cls()
        # 检查 meme 类型是否存在
        if meme not in manager.memes_data:
            return None
        # 构建表情文件夹路径
        face_folder = manager.memes_dir / meme
        # 检查文件夹是否存在
        if not face_folder.exists() or not face_folder.is_dir():
            return None
        # 获取文件夹中的所有图片文件
        image_files = []
        for ext in ['*.png', '*.jpg', '*.jpeg', '*.gif']:
            image_files.extend(face_folder.glob(ext))
        # 如果没有图片，返回 None
        if not image_files:
            return None
        # 随机选择一个图片
        selected_image = random.choice(image_files)
        # 生成 MessageSegment 对象并返回
        return img_msg_from_path(selected_image)

    @classmethod
    def get_meme_description(cls, meme: str) -> Optional[str]:
        """
        获取 meme 类型的描述信息
        
        :param meme: 表情类型
        :return: 描述信息，如果不存在则返回 None
        """
        manager = cls()
        return manager.memes_data.get(meme)

    @classmethod
    def get_all_memes(cls) -> list[str]:
        """
        获取所有可用的 meme 类型列表
        
        :return: meme 类型列表
        """
        manager = cls()
        return list(manager.memes_data.keys())

    @classmethod
    def has_meme(cls, meme: str) -> bool:
        """
        检查指定的 meme 类型是否存在
        
        :param meme: 表情类型
        :return: 如果存在返回 True，否则返回 False
        """
        manager = cls()
        return meme in manager.memes_data
