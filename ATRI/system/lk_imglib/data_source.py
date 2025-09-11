import os
import random

from ATRI import IMG_DIR
from ATRI.exceptions import BaseBotException
from ATRI.system.lkapi.bot import PLUGIN_DIR

IMAGELIB_DIR = PLUGIN_DIR / "imagelib"


class ImageLibException(BaseBotException):
    prompt = "图库错误"


class ImageManager:
    """
    图库管理器。
    """

    def __init__(self, path=IMAGELIB_DIR):
        """
        图库管理器。
        :param path: 默认值为全局图库位置
        """
        path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.imagelib = {}
        (path / '默认').mkdir(parents=True, exist_ok=True)
        files = os.listdir(path)
        for file in files:
            if (path / file).is_dir():
                self.imagelib[file] = path / file
        if path == IMAGELIB_DIR:
            self.imagelib['sbg'] = IMG_DIR / "sbg"

    def _save_image(self, libname, img_name, img_data):
        img_path = self.imagelib[libname] / f'{img_name}.jpg'
        with open(img_path, "wb") as f:
            f.write(img_data)

    def _get_random_image(self, libname):
        files = os.listdir(self.imagelib[libname])
        if not files:
            return None
        file = random.choice(files)
        img_url = self.imagelib[libname] / file
        with open(img_url, "rb") as f:
            return f.read()

    def _get_image(self, libname, img_name):
        img_path = self.imagelib[libname] / f'{img_name}.jpg'
        if img_path.exists():
            with open(img_path, "rb") as f:
                return f.read()
        else:
            return None

    def get_image_num(self, libname: str):
        """
        获取图库内图片数量。
        :param libname: 图库名
        :return: 图库内图片数量
        """
        num = 0
        files = os.listdir(self.imagelib[libname])
        for file in files:
            if (self.imagelib[libname] / file).is_file():
                num += 1
        return num

    def get_image_from(self, libname: str = None, img_name: str = None):
        """
        获取一张图片。
        :param libname: 当指定图库名时，从指定图库中获取图片
        :param img_name: 当指定图库名与图片名时，获取指定图库内指定图片
        :return: 图片数据bytes，没有指定图片则为None
        """
        if libname is None:
            return self.get_random_image()
        if libname in self.imagelib:
            if img_name is None:
                return self._get_random_image(libname)
            return self._get_image(libname, img_name)
        else:
            return None

    def get_random_image(self):
        """
        获取一张随机图片。
        :return: 随机图片数据。
        """
        weights = []
        for libname in self.imagelib:
            weights.append(self.get_image_num(libname))
        try:
            libname = random.choices(list(self.imagelib.keys()), weights=weights)[0]
        except ValueError:
            return None
        return self.get_image_from(libname)

    def new_lib(self, libname: str):
        """
        新建图库。
        :param libname: 图库名
        """
        if libname == 'sbg':
            return
        libname.replace(' ', '').replace('/', '').replace('\\', '').replace('.', '')
        try:
            (self.path / libname).mkdir(parents=True, exist_ok=True)
            self.imagelib[libname] = self.path / libname
        except:
            return

    def save_image(self, img_name: str, img_data: bytes, libname: str = '默认'):
        """
        向图库内保存图片。
        :param img_name: 图片名
        :param img_data: 图片数据
        :param libname: 图库名，默认值为'默认'
        """
        if libname == 'sbg':
            return
        if libname is None:
            libname = '默认'
        if libname not in self.imagelib:
            self.new_lib(libname)
        self._save_image(libname, img_name, img_data)

    def get_lib_info(self):
        """
        获取图库数据。
        :return: {'图库名' str: 图片数量 int}
        """
        info = {}
        for libname in self.imagelib:
            info[libname] = self.get_image_num(libname)
        return info


image_manager = ImageManager()


class GroupImageManager(ImageManager):
    """
    群聊图库管理器。
    """

    def __init__(self, group_id: str):
        """
        群聊图库管理器。
        :param group_id: 群聊id
        """
        super().__init__(PLUGIN_DIR / 'group_imagelib' / group_id)

    def get_all_random_image(self):
        """
        从全局图库与群聊图库内获取随机图片。
        :return: 随机图片数据
        """
        paths = []
        for path in image_manager.imagelib.values():
            files = os.listdir(path)
            for file in files:
                if (path / file).is_file():
                    paths.append(path / file)
        for path in self.imagelib.values():
            files = os.listdir(path)
            for file in files:
                if (path / file).is_file():
                    paths.append(path / file)
        path = random.choice(paths)
        with open(path, "rb") as f:
            return f.read()
