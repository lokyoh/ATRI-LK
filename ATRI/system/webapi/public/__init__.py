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


@router.get("/")
async def index():
    return FileResponse(WEBUI_PATH / "index.html")


@router.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    if full_path.startswith(("atri/", "api/", "assets/")):
        raise HTTPException(status_code=404, detail="Not Found")
    if "." in full_path.split("/")[-1]:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(WEBUI_PATH / "index.html")


@router.get("/favicon.ico")
async def favicon():
    return FileResponse(WEBUI_PATH / "favicon.ico")


def check_webui_exists():
    return (WEBUI_PATH / "index.html").exists()


async def webui_download():
    log.info("开始下载WebUI...")
    response = await request.get(
        "https://github.com/lokyoh/ATRI-LK-WebUI/releases/latest/download/public.zip",
        follow_redirects=True,
    )
    zip_path = TEMP_DIR / "public.zip"
    async with await anyio.open_file(zip_path, "wb") as f:
        await f.write(response.content)
    import shutil

    log.info("解压WebUI...")
    shutil.unpack_archive(zip_path, WEBUI_PATH)
    log.success("WebUI下载完成")


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
