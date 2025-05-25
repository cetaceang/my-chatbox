#!/usr/bin/env python3
"""
安装项目依赖的脚本，适用于Ubuntu和Windows环境
"""
import os
import sys
import subprocess
import platform

def check_system_dependencies():
    """检查Ubuntu系统依赖"""
    if platform.system() != "Linux":
        return True
        
    print("检查系统依赖...")
    
    # 检查是否已安装必要的系统包
    required_packages = [
        "python3-dev",
        "python3-pip",
        "python3-venv",
        "build-essential",
        "libssl-dev",
        "libffi-dev",
        "redis-server"
    ]
    
    try:
        # 检查dpkg是否存在这些包
        for package in required_packages:
            result = subprocess.run(
                ["dpkg", "-l", package], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            if result.returncode != 0:
                print(f"系统缺少依赖: {package}")
                print("您可能需要运行以下命令安装系统依赖:")
                print(f"sudo apt update && sudo apt install -y {' '.join(required_packages)}")
                choice = input("是否现在安装这些依赖? (y/n): ")
                if choice.lower() == 'y':
                    subprocess.call(["sudo", "apt", "update"])
                    subprocess.call(["sudo", "apt", "install", "-y"] + required_packages)
                return True
        return True
    except Exception as e:
        print(f"检查系统依赖时出错: {e}")
        print("如果您使用的是Ubuntu，请确保已安装以下包:")
        print(" ".join(required_packages))
        return True

def main():
    """安装Python依赖包，提供镜像源选择"""
    print("===== 安装项目依赖 =====")
    
    # 检查系统依赖
    if not check_system_dependencies():
        return
    
    # 检查requirements.txt是否存在
    if not os.path.exists("requirements.txt"):
        print("错误: 找不到requirements.txt文件")
        print("请确保requirements.txt文件在当前目录中")
        return
    
    # 询问是否使用镜像源
    use_mirror = input("是否使用阿里云PyPI镜像源? (y/n，默认n): ")
    
    pip_cmd = [
        sys.executable, 
        "-m", 
        "pip", 
        "install", 
        "--upgrade",
        "pip"
    ]
    
    install_cmd = [
        sys.executable, 
        "-m", 
        "pip", 
        "install", 
        "-r", 
        "requirements.txt"
    ]
    
    if use_mirror.lower() == 'y':
        mirror_url = "https://mirrors.aliyun.com/pypi/simple/"
        pip_cmd.extend(["--index-url", mirror_url, "--trusted-host", "mirrors.aliyun.com"])
        install_cmd.extend(["--index-url", mirror_url, "--trusted-host", "mirrors.aliyun.com"])
    
    # 先升级pip
    print("升级pip...")
    subprocess.call(pip_cmd)
    
    # 从requirements.txt安装所有依赖
    print("\n从requirements.txt安装所有依赖...")
    result = subprocess.call(install_cmd)
    
    if result == 0:
        print("\n✓ 所有依赖安装完成！")
        if platform.system() == "Linux":
            print("您现在可以运行 python3 start.py 来启动应用")
        else:
            print("您现在可以运行 python start.py 来启动应用")
    else:
        print("\n✗ 依赖安装过程中出现错误")
        print("请检查上方日志获取详细信息")

if __name__ == "__main__":
    main()