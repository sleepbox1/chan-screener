# -*- coding: utf-8 -*-
"""
本地预览服务器
==============

浏览器不允许 file:// 页面读取本地 JSON（CORS 限制），所以本地看效果
必须起一个 HTTP 服务。这个脚本就是干这个的，零依赖。

    python serve.py            # 默认 8000 端口，自动打开浏览器
    python serve.py -p 9000    # 指定端口
    python serve.py --no-open  # 不自动开浏览器
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import threading
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # 开发阶段禁用缓存，改完刷新即可见
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):  # noqa: A003
        # 只打印 4xx / 5xx，安静一点
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)


def main() -> None:
    ap = argparse.ArgumentParser(description="本地预览服务器")
    ap.add_argument("-p", "--port", type=int, default=8000)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    socketserver.TCPServer.allow_reuse_address = True
    handler = functools.partial(Handler, directory=ROOT)

    port = args.port
    for _ in range(20):
        try:
            httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            port += 1
    else:
        print("[err] 找不到可用端口")
        return

    url = f"http://127.0.0.1:{port}/index.html"
    print("=" * 60)
    print(" 缠论多级别买点选股 · 本地预览")
    print("=" * 60)
    print(f" 地址：{url}")
    print(f" 根目录：{ROOT}")
    print(" Ctrl+C 退出")
    print("=" * 60)

    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[退出]")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
