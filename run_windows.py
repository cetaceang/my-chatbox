#!/usr/bin/env python
"""
Windows环境下启动Django项目的简化脚本
"""
import os
import sys
import subprocess
import django

def main():
    print("===== Windows环境下启动Django聊天应用 =====")
    
    # 设置环境变量
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    
    # 确保Django已初始化
    try:
        django.setup()
        print("Django环境已成功初始化")
    except Exception as e:
        print(f"Django初始化失败: {e}")
        return
    
    # 运行迁移
    print("\n运行数据库迁移...")
    subprocess.call([sys.executable, "manage.py", "migrate"])
    
    # 启动服务器
    print("\n启动Django开发服务器...")
    print("注意：此方法不支持WebSocket，仅用于测试基本功能")
    print("如需完整功能，请使用setup_and_run.py脚本")
    
    # 使用Django开发服务器
    subprocess.call([
        sys.executable, "manage.py", "runserver", "0.0.0.0:3000"
    ])

if __name__ == "__main__":
    main() 