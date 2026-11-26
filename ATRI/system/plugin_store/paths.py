import os
import shutil
import stat
import time
from pathlib import Path

from ATRI.dir import RES_DIR
from ATRI.exceptions import PluginError

from . import state


def safe_rmtree(path: Path) -> bool:
    """在 Windows 上更稳健地删除目录，处理只读文件和文件占用问题。

    返回是否真的删掉了：调用方（克隆前、卸载后）要据此判断能不能继续，
    吞掉失败会让随后的 ``git clone`` 撞上「目录非空」。
    """
    if not path.exists():
        return True

    def _remove_readonly(func, path, excinfo):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)
        except Exception:
            pass

    for _ in range(3):
        try:
            shutil.rmtree(path, onerror=_remove_readonly)
            if not path.exists():
                return True
        except Exception:
            pass
        time.sleep(0.2)
    try:
        for item in list(path.iterdir()):
            try:
                if item.is_dir():
                    safe_rmtree(item)
                else:
                    try:
                        os.chmod(item, stat.S_IWRITE | stat.S_IREAD)
                        item.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
        path.rmdir()
    except Exception:
        pass
    return not path.exists()


def download_target(raw_path: str, allowed_root: Path | None = None) -> Path:
    """把索引给的相对路径钉在允许的落点内。

    ``path`` 来自远端响应，属于外部输入；``../../config.yml`` 这种值会被
    ``Path(".") / path`` 原样拼出去，写到工作目录之外。

    允许 ``plugins/`` 与 ``res/`` 两个根：前者是插件本体，后者是插件随包
    携带的资源（``res`` 字段），两者都可能在索引里登记。``allowed_root``
    给出时再收窄一层——下载不能写到别的插件的目录里去。
    """
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise PluginError(f"插件文件路径非法：{raw_path!r}")
    target = (Path(".") / text).resolve()
    for root in (state.PLUGINS_DIR, RES_DIR):
        try:
            target.relative_to(root.resolve())
        except ValueError:
            continue
        if allowed_root is not None:
            try:
                target.relative_to(allowed_root.resolve())
            except ValueError as e:
                raise PluginError(f"插件文件路径越界：{raw_path!r}") from e
        return target
    raise PluginError(f"插件文件路径越界：{raw_path!r}")
