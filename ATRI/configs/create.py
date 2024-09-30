from pathlib import Path
from ipaddress import IPv4Address

from .data_source import Console, C

console = Console(C())


def init_config(conf_path: Path, default_conf_path: Path):
    console.print("\n[b]アトリは、高性能ですから！[/b]\n", style="#00bee6")

    console.warn("文档地址: https://lokyoh.github.io/ATRI-LK-docs")
    console.info("这是你第一次启动本项目, 请根据提示填写信息")
    console.info("如中途填写错, 你可以直接退出, 再次启动以重新填写")
    console.info("如需使用默认值, Enter 跳过方可\n")

    console.info("[b]Bot 主体设置[/b]\n", style="white")
    host = console.input(
        "Bot 监听的主机名 (IP). 如有控制台相关需求, 建议: [green]0.0.0.0[/green] (默认: [green]127.0.0.1[/green])",
        "127.0.0.1",
        IPv4Address,
        "输入不正确 示例: 127.0.0.1",
    )
    port = console.input(
        "Bot 对外开放的端口 (Port). 范围建议: [green]10000-60000[/green] (默认: [green]20000[/green])",
        "20000",
        int,
        "输入不正确 示例: 20000",
    )
    superusers = console.input(
        "超级用户 (qq号), 即 Bot 的[b]主人[/b]. 可填多个, 用英文逗号 (,) 隔开 (默认: [green]1145141919[/green])",
        "1145141919810",
        str,
        "输入不正确 示例: 1145141919",
    )
    access_token = console.input(
        "协议端通信密钥, 此项留空[b]将无法进入控制台[/b]. 无长度限制 示例: [green]21^sASDA!@3l67GJlk7sd!14#[/green]",
        str(),
        str,
        "输入不正确 示例: 21^sASDA!@3l67GJlk7sd!14# (请尽可能复杂, 无长度限制)",
    )
    proxy = console.input(
        "是否有代理. 格式参考: http(s)://127.0.0.1:8100 (如无请 Enter 以跳过)",
        str(),
        str,
        "输入不正确 示例: http://127.0.0.1:8100",
    )
    browser = console.input(
        "浏览器内核 (默认: [green]chromium[/green], 可选: [green]firefox[/green])",
        "chromium",
        str,
    )
    download_host = console.input(
        "下载 playwright 的代理地址 (如无请 Enter 以跳过)",
        str(),
        str,
    )
    proxy_host = console.input(
        "浏览器自定代理地址 (如无请 Enter 以跳过)",
        str(),
        str,
    )
    browser_channel = console.input(
        "浏览器实现途径，手动填写可以直接使用系统自带浏览器而不用重新下载 chromium (系统无浏览器请 Enter 以跳过, 可选: [green]chrome, chrome-beta, chrome-dev, chrome-canary, msedge, msedge-beta, msedge-dev, msedge-canary[/green])",
        str(),
        str,
    )

    console.success("[white]至此, 所需基本配置已填写完毕[white]")

    raw_conf = default_conf_path.read_text("utf-8")
    raw_conf = raw_conf.replace("{host}", str(host))
    raw_conf = raw_conf.replace("{port}", str(port))
    raw_conf = raw_conf.replace("{superusers}", str(superusers.split(",")))
    raw_conf = raw_conf.replace("{access_token}", access_token)
    raw_conf = raw_conf.replace("{proxy}", proxy)

    raw_conf = raw_conf.replace("{browser}", browser)
    raw_conf = raw_conf.replace("{download_host}", download_host)
    raw_conf = raw_conf.replace("{proxy_host}", proxy_host)
    raw_conf = raw_conf.replace("{browser_channel}", browser_channel)

    with open(conf_path, "w", encoding="utf-8") as w:
        w.write(raw_conf)
