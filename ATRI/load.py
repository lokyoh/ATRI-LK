import re
from pathlib import Path

import nonebot

from ATRI import driver
from ATRI.log import log
from ATRI.utils.package_manager import PackageManager

package_requirements = {}
"""包依赖关系。{'package': ['module']}"""


def parse_requirement_line(line: str) -> tuple[str, str] | None:
    """
    解析 requirements.txt 中的一行
    
    Args:
        line: requirements.txt 中的一行
        
    Returns:
        (包名，版本号) 元组，如果解析失败则返回 None
    """
    line = line.strip()
    # 跳过注释和空行
    if not line or line.startswith('#'):
        return None
    # 跳过以 - 开头的选项（如 -r, -e 等）
    if line.startswith('-'):
        return None
    # 匹配包名和版本（支持 ==, >=, <=, ~=, != 等操作符）
    match = re.match(r'^([a-zA-Z0-9_-]+(?:\[[a-zA-Z0-9_-]+\])?)([=<>!~]+.*)?$', line)
    if match:
        package_name = match.group(1)
        # 移除可选的 extras，例如 package[extra] -> package
        base_name = re.sub(r'\[.*\]', '', package_name)
        version = match.group(2) or 'latest'
        return base_name.lower(), version
    return None


def check_package_installed(package_name: str, installed_packages: list[dict]) -> bool:
    """
    检查包是否已安装
    
    Args:
        package_name: 包名
        installed_packages: 已安装包列表
        
    Returns:
        是否已安装
    """
    package_name = package_name.lower()
    for pkg in installed_packages:
        if pkg['name'].lower() == package_name:
            return True
    return False


def load_plugins():
    """
    加载插件并处理依赖
    """
    log.debug("开始加载插件并检查依赖...")
    # 初始化包管理器
    pm = PackageManager()
    # 获取已安装的包列表
    installed_packages = pm.list_installed()
    plugins_dir = Path("plugins")
    if not plugins_dir.exists():
        log.warning(f"插件目录不存在：{plugins_dir}")
        return
    # 扫描 plugins 目录下的所有文件夹
    for item in plugins_dir.iterdir():
        # 只处理文件夹且不以 _ 开头
        if not item.is_dir() or item.name.startswith('_'):
            continue
        requirements_file = item / "requirements.txt"
        # 检查是否存在 requirements.txt
        if requirements_file.exists():
            log.debug(f"检测到插件 {item.name} 有依赖文件：{requirements_file}")
            # 读取并解析 requirements.txt
            try:
                with open(requirements_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                missing_packages = []
                plugin_packages = []
                for line in lines:
                    parsed = parse_requirement_line(line)
                    if parsed:
                        package_name, version = parsed
                        plugin_packages.append(package_name)
                        # 检查是否已安装
                        if not check_package_installed(package_name, installed_packages):
                            missing_packages.append(package_name)
                            log.info(f"插件 {item.name} 缺少依赖：{package_name}")
                # 如果有缺失的依赖，进行安装
                if missing_packages:
                    log.info(f"开始为插件 {item.name} 安装依赖：{missing_packages}")
                    success, message = pm.install(missing_packages)
                    if success:
                        log.success(f"插件 {item.name} 依赖安装成功")
                        # 更新已安装包列表
                        installed_packages = pm.list_installed()
                    else:
                        log.error(f"插件 {item.name} 依赖安装失败：{message}")
                else:
                    log.debug(f"插件 {item.name} 的所有依赖已安装")
                # 将依赖关系加入 package_requirements
                # 格式：{'package': ['plugin1', 'plugin2', ...]} 表示每个包被哪些插件依赖
                for package_name in plugin_packages:
                    if package_name not in package_requirements:
                        package_requirements[package_name] = []
                    package_requirements[package_name].append(item.name)
                    
            except Exception as e:
                log.error(f"处理插件 {item.name} 的依赖时出错：{e}")
    nonebot.load_plugins("plugins")
    nonebot.load_plugins("plugins/rss")

def load_system():
    from ATRI.event.register import register_triggers
    register_triggers()
    nonebot.load_plugins("ATRI/system")

def load_atri():
    load_system()
    load_plugins()
    from ATRI.service import driver_startup
    driver().on_startup(driver_startup)
