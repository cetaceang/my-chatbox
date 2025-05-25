#!/usr/bin/env python3
"""
启动Django项目的简化脚本
适用于Windows和Linux环境
"""
import os
import sys
import subprocess
import platform
import django
import time

def check_redis():
    """检查Redis服务是否运行"""
    print("\n===== 检查Redis服务 =====")
    system = platform.system()
    
    if system == "Linux":
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "redis-server"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
            if "active" not in result.stdout:
                print("Redis服务未运行，尝试启动...")
                subprocess.call(["sudo", "systemctl", "start", "redis-server"])
                print("Redis服务已启动")
            else:
                print("Redis服务正在运行")
        except Exception as e:
            print(f"检查Redis服务时出错: {e}")
            print("请确保Redis服务已安装并运行")
            print("可以使用以下命令安装和启动Redis:")
            print("sudo apt install redis-server")
            print("sudo systemctl start redis-server")
    else:
        print("在Windows环境下，请确保Redis服务已手动启动")

def check_urls():
    """检查所有已注册的URL"""
    print("\n===== 检查已注册的URL =====")
    
    # 使用Django的命令行工具显示URL
    subprocess.call([sys.executable, "manage.py", "show_urls"])

def check_database():
    """检查数据库中的表"""
    print("\n===== 检查数据库 =====")
    subprocess.call([sys.executable, "manage.py", "inspectdb"])

def run_migrations():
    """运行数据库迁移"""
    print("\n===== 数据库迁移 =====")
    
    # 先创建迁移文件
    print("1. 创建迁移文件...")
    result = subprocess.call([sys.executable, "manage.py", "makemigrations"])
    
    if result != 0:
        print("警告: 创建迁移文件可能出现问题")
    else:
        print("✓ 迁移文件创建成功")
    
    # 等待一下确保文件写入完成
    time.sleep(1)
    
    # 再执行迁移
    print("\n2. 应用迁移到数据库...")
    result = subprocess.call([sys.executable, "manage.py", "migrate"])
    
    if result != 0:
        print("错误: 数据库迁移失败")
        return False
    else:
        print("✓ 数据库迁移成功")
        return True

def main():
    print("===== 启动Django聊天应用 =====")
    
    # 检测操作系统
    system = platform.system()
    print(f"检测到操作系统: {system}")
    
    # 设置环境变量
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    
    # 确保Django已初始化
    try:
        django.setup()
        print("✓ Django环境已成功初始化")
    except Exception as e:
        print(f"✗ Django初始化失败: {e}")
        return
    
    # 检查Redis服务
    check_redis()
    
    # 运行迁移
    if not run_migrations():
        print("由于迁移失败，程序将退出")
        return
    
    # 检查URL配置
    check_urls()
    
    # 启用调试模式
    print("\n启用Django调试模式...")
    os.environ["DEBUG"] = "True"
    
    # 检查是否安装了daphne
    try:
        import daphne
        print("找到daphne，使用daphne启动服务器...")
    except ImportError:
        print("未找到daphne，尝试安装...")
        subprocess.call([sys.executable, "-m", "pip", "install", "daphne"])
    
    # 在所有环境下都使用Daphne ASGI服务器
    print(f"\n在{system}环境下启动Daphne ASGI服务器...")
    
    # 启动服务器
    print("启动服务器...")
    host = "0.0.0.0"
    port = 3000
    print(f"服务器将在 http://{host}:{port} 上运行")
    print("按Ctrl+C停止服务器")
    
    # 使用subprocess运行daphne，这样在不同环境下都能正常工作
    if system == "Linux":
        subprocess.call([
            "daphne",
            "-b", host,
            "-p", str(port),
            "config.asgi:application"
        ])
    else:
        # Windows环境
        subprocess.call([
            sys.executable, "-m", "daphne",
            "-b", host,
            "-p", str(port),
            "config.asgi:application"
        ])

if __name__ == "__main__":
    main()