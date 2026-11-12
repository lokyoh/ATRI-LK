import shutil
import zipfile

import anyio
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ATRI.dir import DATA_DIR, TEMP_DIR
from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.utils import request

router = APIRouter()
WEBUI_PATH = DATA_DIR / "webui"
VERSION_FILE = WEBUI_PATH / "version"
REPO_RELEASE_URL = "https://api.github.com/repos/lokyoh/ATRI-LK-WebUI/releases"
WEBUI_URL = (
    "https://github.com/lokyoh/ATRI-LK-WebUI/releases/latest/download/public.zip"
)
# WebUI 目录下由程序生成的用户数据, 更新时需保留
WEBUI_USER_FILES = {"menu.json", "token.json"}


@router.get("/")
async def index():
    return FileResponse(WEBUI_PATH / "index.html")


@router.get("/favicon.ico")
async def favicon():
    return FileResponse(WEBUI_PATH / "favicon.ico")


# 放在最后: 兜底路由会匹配所有剩余路径, 先注册的具体路由会被它拦截
@router.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    if full_path.startswith(("atri/", "api/", "assets/")):
        raise HTTPException(status_code=404, detail="Not Found")
    if "." in full_path.split("/")[-1]:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(WEBUI_PATH / "index.html")


def check_webui_exists():
    return (WEBUI_PATH / "index.html").exists()


def clean_webui_files():
    """清理 WebUI 旧文件, 保留用户数据(menu.json/token.json)

    旧版本打包出的 assets 文件名带哈希, 不清理会一直堆积无用文件。
    """
    if not WEBUI_PATH.exists():
        return
    for item in WEBUI_PATH.iterdir():
        if item.name in WEBUI_USER_FILES:
            log.debug(f"保留用户数据: {item.name}")
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)
    log.info("已清理WebUI旧文件")


async def webui_download():
    log.info("开始下载WebUI...")
    zip_path = TEMP_DIR / "public.zip"
    unpack_path = TEMP_DIR / "webui_unpack"
    try:
        response = await request.get(WEBUI_URL, follow_redirects=True)
        response.raise_for_status()
        async with await anyio.open_file(zip_path, "wb") as f:
            await f.write(response.content)
        if not zipfile.is_zipfile(zip_path):
            raise RuntimeError("WebUI 压缩包无效")

        # 先解压到临时目录校验, 避免坏包直接覆盖 WEBUI_PATH
        shutil.rmtree(unpack_path, ignore_errors=True)
        log.info("解压WebUI...")
        shutil.unpack_archive(zip_path, unpack_path)
        if not (unpack_path / "index.html").exists():
            raise RuntimeError("WebUI 压缩包缺少 index.html")

        # 校验通过后再清理旧文件, 避免更新失败后连旧版本都没了
        clean_webui_files()
        WEBUI_PATH.mkdir(parents=True, exist_ok=True)
        for item in unpack_path.iterdir():
            target = WEBUI_PATH / item.name
            if target.exists() and item.name in WEBUI_USER_FILES:
                # 用户数据优先: 压缩包内同名文件不覆盖本地已有内容
                log.debug(f"跳过覆盖用户数据: {item.name}")
                continue
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        log.success("WebUI下载完成")
    finally:
        shutil.rmtree(unpack_path, ignore_errors=True)
        zip_path.unlink(missing_ok=True)


async def get_latest_webui_version() -> str:
    try:
        response = await request.get(REPO_RELEASE_URL)
        data = response.json()
        latest_release = data[0]
        return latest_release["tag_name"]
    except Exception as e:
        log.error(f"获取最新 WebUI 版本失败: {str_traceback(e)}")
        return "error"


async def init_public(app: FastAPI):
    try:
        if not check_webui_exists():
            await webui_download()
        else:
            if VERSION_FILE.exists():
                async with await anyio.open_file(
                    VERSION_FILE, "r", encoding="utf-8"
                ) as f:
                    version = (await f.read()).strip()
                    log.info(f"WebUI 版本: {version}")
                    latest_version = await get_latest_webui_version()
                    if latest_version != "error" and version != latest_version:
                        log.info(f"WebUI 需要更新，最新版本: {latest_version}")
                        await webui_download()
            else:
                log.warning("WebUI 版本文件不存在，需要更新。")
                await webui_download()
        folders = [x.name for x in WEBUI_PATH.iterdir() if x.is_dir()]
        for pathname in folders:
            log.debug(f"挂载文件夹: {pathname}")
            app.mount(
                f"/{pathname}",
                StaticFiles(
                    directory=WEBUI_PATH / pathname,
                    check_dir=True,
                ),
                name=f"public_{pathname}",
            )
        app.include_router(router)
    except Exception as e:
        log.error(f"初始化 WebUI资源 失败:{str_traceback(e)}")
