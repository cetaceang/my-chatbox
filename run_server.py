#!/usr/bin/env python
"""
使用Daphne运行Django项目的脚本
支持WebSocket连接
"""
import os
import sys
import subprocess

def main():
    # 设置环境变量
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    
    # 检查是否安装了daphne
    try:
        import daphne
        print("找到daphne，使用daphne启动服务器...")
    except ImportError:
        print("未找到daphne，尝试安装...")
        subprocess.call([sys.executable, "-m", "pip", "install", "daphne"])
    
    # 确保安装了channels和channels-redis
    try:
        import channels
        print("找到channels...")
    except ImportError:
        print("未找到channels，尝试安装...")
        subprocess.call([sys.executable, "-m", "pip", "install", "channels"])
    
    try:
        import channels_redis
        print("找到channels-redis...")
    except ImportError:
        print("未找到channels-redis，尝试安装...")
        subprocess.call([sys.executable, "-m", "pip", "install", "channels-redis"])
    
    # 运行数据库迁移
    print("运行数据库迁移...")
    subprocess.call([sys.executable, "manage.py", "migrate"])
    
    # 使用daphne启动服务器
    print("启动服务器...")
    host = "0.0.0.0"
    port = 3000
    print(f"服务器将在 http://{host}:{port} 上运行")
    print("按Ctrl+C停止服务器")
    
    # 运行daphne
    subprocess.call([
        sys.executable, "-m", "daphne", 
        "-b", host, 
        "-p", str(port), 
        "config.asgi:application"
    ])

if __name__ == "__main__":
    main() 