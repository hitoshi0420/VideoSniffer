"""VideoSniffer — 网站视频嗅探下载工具
一款基于 Python 的视频地址嗅探与下载工具，支持 HTTP/HTTPS 代理嗅探、
m3u8 流解析下载、多线程分块下载、断点续传等功能。
"""

import sys
import os

# 确保项目根目录在 sys.path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


def main():
    from gui.main_window import run
    run()


if __name__ == "__main__":
    main()
