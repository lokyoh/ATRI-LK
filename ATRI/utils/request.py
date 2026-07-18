import httpx

from ATRI import conf
from ATRI.log import log

timeout = conf.BotConfig.request_timeout
if timeout:
    timeout = httpx.Timeout(timeout)

if not conf.BotConfig.proxy:
    proxy = dict()
else:
    proxy = {"all://": conf.BotConfig.proxy}


class RequestClient:
    def __init__(self, time_out: float | None, verify: bool = False):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(time_out), verify=verify)

    async def get(self, url: str, **kwargs):
        log.debug(f"GET {url} by {proxy if proxy else 'No proxy'} | MORE: \n {kwargs}")
        return await self.client.get(url, **kwargs)

    async def post(self, url: str, **kwargs):
        log.debug(f"POST {url} by {proxy if proxy else 'No proxy'} | MORE: \n {kwargs}")
        return await self.client.post(url, **kwargs)

    async def delete(self, url: str, **kwargs):
        log.debug(
            f"DELETE {url} by {proxy if proxy else 'No proxy'} | MORE: \n {kwargs}"
        )
        return await self.client.delete(url, **kwargs)


async def get(url: str, verify: bool = False, **kwargs):
    log.debug(f"GET {url} by {proxy if proxy else 'No proxy'} | MORE: \n {kwargs}")
    async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:  # type: ignore
        return await client.get(url, **kwargs)


async def post(url: str, verify: bool = False, **kwargs):
    log.debug(f"POST {url} by {proxy if proxy else 'No proxy'} | MORE: \n {kwargs}")
    async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:  # type: ignore
        return await client.post(url, **kwargs)


async def delete(url: str, verify: bool = False, **kwargs):
    log.debug(f"DELETE {url} by {proxy if proxy else 'No proxy'} | MORE: \n {kwargs}")
    async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:  # type: ignore
        return await client.delete(url, **kwargs)
