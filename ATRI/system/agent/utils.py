from ATRI.log import log as atri_log
from ATRI.utils.request import RequestClient

log = atri_log
request = RequestClient(time_out=300.0, use_log=False)
