from ATRI import driver as atri_driver
from ATRI.log import log

from .browser import (
    get_browser as get_browser,
    get_new_page as get_new_page,
    shutdown_browser as shutdown_browser,
)
from .data_source import (
    capture_element as capture_element,
    html_to_pic as html_to_pic,
    md_to_pic as md_to_pic,
    template_to_html as template_to_html,
    template_to_pic as template_to_pic,
    text_to_pic as text_to_pic,
)

driver = atri_driver()


@driver.on_startup
async def init(**kwargs):
    """开启浏览器

    Returns:
        Browser: Browser
    """
    browser = await get_browser(**kwargs)
    log.info("浏览器已开启")
    return browser


@driver.on_shutdown
async def shutdown():
    await shutdown_browser()
    log.info("浏览器已停止")


browser_init = init

__all__ = [
    "browser_init",
    "capture_element",
    "get_new_page",
    "html_to_pic",
    "md_to_pic",
    "template_to_html",
    "template_to_pic",
    "text_to_pic",
]
