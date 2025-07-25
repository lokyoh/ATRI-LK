from fastapi import HTTPException
from fastapi.params import Depends

from ATRI import conf, __version__, __sub_version__
from ATRI.utils import gen_random_str
from ATRI.service import ServiceTools, service_list

access_token = conf.BotConfig.access_token
temp_token = gen_random_str(16)


def verify_token(token: str = ""):
    check_token(token)
    global temp_token
    temp_token = gen_random_str(16)
    return {"tempToken": temp_token}


def check_token(token: str = ""):
    if not token:
        raise HTTPException(status_code=400, detail="Token required")
    if token != temp_token:
        raise HTTPException(status_code=400, detail="Token error")


def get_atri_info(_=Depends(check_token)):
    return {
        "version": f"{__version__} {__sub_version__}",
        "nickName": conf.BotConfig.nickname
    }


def get_service_list(_=Depends(check_token)):
    services = []
    for service in service_list:
        _service = ServiceTools(service)
        info = _service.load_service().model_dump()
        settings = _service.load_service_config().model_dump()
        services.append({
            "info": info,
            "settings": settings
        })
    return services
