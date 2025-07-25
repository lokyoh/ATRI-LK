from fastapi import FastAPI, Request
from fastapi.params import Depends
from fastapi.middleware.cors import CORSMiddleware

from ATRI import asgi
from ATRI.log import log

from .data_source import (
    verify_token,
    get_service_list,
    get_atri_info
)

app: FastAPI = asgi()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    log.info(f"{request.method}[{response.status_code}] {request.url}")
    return response


BASEURL = '/api/atri'


@app.get(f"{BASEURL}/login")
async def login(_temp_token: dict = Depends(verify_token)):
    return _temp_token


@app.get(f"{BASEURL}/services")
async def services(_service_list: dict = Depends(get_service_list)):
    return _service_list


@app.get(f'{BASEURL}/info')
async def info(_atri_info: dict = Depends(get_atri_info)):
    return _atri_info
