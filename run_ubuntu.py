#!/usr/bin/env python3
"""
Ubuntu环境下启动Django项目的脚本
使用Daphne作为ASGI服务器
"""
import os
import sys
import subprocess
import time

def check_dependencies():
    """检查必要的依赖是否已安装"""
    try:
        import django
        import channels
        import daphne
        import requests
        print("所有必要的依赖已安装")
        return True
    except ImportError as e:
        print(f"缺少必要的依赖: {e}")
        print("请先运行 python install_dependencies.py 安装依赖")
        return False

def run_migrations():
    """运行数据库迁移"""
    print("\n运行数据库迁移...")
    subprocess.call(["python3", "manage.py", "migrate"])

def run_server():
    """使用Daphne启动服务器"""
    print("\n启动Daphne ASGI服务器...")
    host = "0.0.0.0"
    port = 3000
    print(f"服务器将在 http://{host}:{port} 上运行")
    print("按Ctrl+C停止服务器")
    
    # 运行daphne
    subprocess.call([
        "daphne",
        "-b", host,
        "-p", str(port),
        "config.asgi:application"
    ])

def main():
    print("===== Ubuntu环境下启动Django聊天应用 =====")
    
    # 设置环境变量
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    
    # 检查依赖
    if not check_dependencies():
        return
    
    # 运行迁移
    run_migrations()
    
    # 启动服务器
    run_server()

if __name__ == "__main__":
    main() 