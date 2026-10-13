from ATRI.system.lkbot.config import config as config
from ATRI.system.lkbot.data.user import lk_db
from ATRI.system.lkbot.util import PLUGIN_DIR as PLUGIN_DIR
from ATRI.system.lkbot.util import PLUGIN_VERSION as PLUGIN_VERSION
from ATRI.system.lkbot.util import lk_util
from ATRI.system.lkbot.util import on_startup as on_startup

from . import checker as checker
from . import events as events
from . import message_queue as message_queue
from . import setting as setting

db = lk_db
util = lk_util
