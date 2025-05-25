#!/usr/bin/env python3
"""
Ubuntu服务器部署脚本
用于在Ubuntu服务器上部署Django聊天应用
"""
import os
import sys
import subprocess
import time
import getpass
import random
import string

def print_banner():
    """打印脚本横幅"""
    print("=" * 60)
    print("Django聊天应用 - Ubuntu服务器部署脚本")
    print("=" * 60)
    print("此脚本将帮助您在Ubuntu服务器上部署Django聊天应用")
    print("=" * 60)

def check_python_version():
    """检查Python版本"""
    print("\n[1/10] 检查Python版本...")
    import platform
    version = platform.python_version()
    print(f"当前Python版本: {version}")
    
    major, minor, _ = map(int, version.split('.'))
    if major < 3 or (major == 3 and minor < 8):
        print("错误: 需要Python 3.8或更高版本")
        return False
    print("✓ Python版本检查通过")
    return True

def install_system_dependencies():
    """安装系统依赖"""
    print("\n[2/10] 安装系统依赖...")
    
    try:
        # 更新包列表
        subprocess.run(["sudo", "apt", "update"], check=True)
        
        # 安装必要的系统包
        packages = [
            "python3-pip", 
            "python3-venv", 
            "nginx", 
            "supervisor",
            "build-essential",
            "python3-dev",
            "libpq-dev"  # PostgreSQL开发库（如果使用PostgreSQL）
        ]
        
        print(f"安装系统包: {', '.join(packages)}")
        subprocess.run(["sudo", "apt", "install", "-y"] + packages, check=True)
        print("✓ 系统依赖安装完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"错误: 安装系统依赖失败: {e}")
        return False

def setup_virtualenv():
    """设置虚拟环境"""
    print("\n[3/10] 设置Python虚拟环境...")
    
    try:
        # 创建虚拟环境
        if not os.path.exists("venv"):
            subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
            print("✓ 虚拟环境创建成功")
        else:
            print("✓ 虚拟环境已存在")
        
        # 获取虚拟环境的pip路径
        if os.name == 'nt':  # Windows
            pip_path = os.path.join("venv", "Scripts", "pip")
        else:  # Linux/Mac
            pip_path = os.path.join("venv", "bin", "pip")
        
        # 升级pip
        subprocess.run([pip_path, "install", "--upgrade", "pip"], check=True)
        print("✓ pip已升级到最新版本")
        
        return pip_path
    except subprocess.CalledProcessError as e:
        print(f"错误: 设置虚拟环境失败: {e}")
        return None

def install_python_dependencies(pip_path):
    """安装Python依赖"""
    print("\n[4/10] 安装Python依赖...")
    
    try:
        # 安装requirements.txt中的依赖
        subprocess.run([pip_path, "install", "-r", "requirements.txt"], check=True)
        print("✓ Python依赖安装完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"错误: 安装Python依赖失败: {e}")
        return False

def setup_env_file():
    """设置.env文件"""
    print("\n[5/10] 设置环境变量...")
    
    if os.path.exists(".env"):
        overwrite = input("发现已存在的.env文件。是否覆盖? (y/n): ").lower() == 'y'
        if not overwrite:
            print("跳过.env文件设置")
            return True
    
    # 生成随机SECRET_KEY
    chars = string.ascii_letters + string.digits + string.punctuation
    secret_key = ''.join(random.choice(chars) for _ in range(50))
    
    # 获取域名
    domain = input("请输入您的域名 (如果没有，请输入服务器IP): ")
    
    # 创建.env文件
    with open(".env", "w") as f:
        f.write(f"DEBUG=False\n")
        f.write(f"SECRET_KEY='{secret_key}'\n")
        f.write(f"ALLOWED_HOSTS={domain},localhost,127.0.0.1\n")
    
    print("✓ .env文件已创建")
    return True

def run_migrations():
    """运行数据库迁移"""
    print("\n[6/10] 运行数据库迁移...")
    
    try:
        # 获取虚拟环境中python的路径
        if os.name == 'nt':  # Windows
            python_path = os.path.join("venv", "Scripts", "python")
        else:  # Linux/Mac
            python_path = os.path.join("venv", "bin", "python")
        
        # 运行迁移
        subprocess.run([python_path, "manage.py", "migrate"], check=True)
        print("✓ 数据库迁移完成")
        
        # 创建超级用户
        create_superuser = input("\n是否创建超级用户? (y/n): ").lower() == 'y'
        if create_superuser:
            subprocess.run([python_path, "manage.py", "createsuperuser"])
        
        return True
    except subprocess.CalledProcessError as e:
        print(f"错误: 数据库迁移失败: {e}")
        return False

def collect_static():
    """收集静态文件"""
    print("\n[7/10] 收集静态文件...")
    
    try:
        # 获取虚拟环境中python的路径
        if os.name == 'nt':  # Windows
            python_path = os.path.join("venv", "Scripts", "python")
        else:  # Linux/Mac
            python_path = os.path.join("venv", "bin", "python")
        
        # 收集静态文件
        subprocess.run([python_path, "manage.py", "collectstatic", "--noinput"], check=True)
        print("✓ 静态文件收集完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"错误: 收集静态文件失败: {e}")
        return False

def setup_supervisor():
    """设置Supervisor配置"""
    print("\n[8/10] 配置Supervisor...")
    
    try:
        # 获取当前工作目录的绝对路径
        current_dir = os.path.abspath(os.getcwd())
        
        # 创建Supervisor配置文件
        supervisor_config = f"""[program:daphne]
command={current_dir}/venv/bin/daphne -b 0.0.0.0 -p 8001 config.asgi:application
directory={current_dir}
user={getpass.getuser()}
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/daphne.log
"""
        
        # 写入临时文件
        with open("daphne.conf", "w") as f:
            f.write(supervisor_config)
        
        # 复制到Supervisor配置目录
        subprocess.run(["sudo", "cp", "daphne.conf", "/etc/supervisor/conf.d/"], check=True)
        
        # 重新加载Supervisor配置
        subprocess.run(["sudo", "supervisorctl", "reread"], check=True)
        subprocess.run(["sudo", "supervisorctl", "update"], check=True)
        
        # 删除临时文件
        os.remove("daphne.conf")
        
        print("✓ Supervisor配置完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"错误: 配置Supervisor失败: {e}")
        return False
    except Exception as e:
        print(f"错误: {e}")
        return False

def setup_nginx():
    """设置Nginx配置"""
    print("\n[9/10] 配置Nginx...")
    
    try:
        # 获取当前工作目录的绝对路径
        current_dir = os.path.abspath(os.getcwd())
        
        # 获取域名
        domain = input("请再次输入您的域名 (如果没有，请输入服务器IP): ")
        
        # 创建Nginx配置文件
        nginx_config = f"""server {{
    listen 80;
    server_name {domain};

    location /static/ {{
        alias {current_dir}/staticfiles/;
    }}

    location /media/ {{
        alias {current_dir}/media/;
    }}

    location / {{
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
"""
        
        # 写入临时文件
        with open("my_chatbox", "w") as f:
            f.write(nginx_config)
        
        # 复制到Nginx配置目录
        subprocess.run(["sudo", "cp", "my_chatbox", "/etc/nginx/sites-available/"], check=True)
        
        # 创建符号链接
        try:
            subprocess.run(["sudo", "ln", "-sf", "/etc/nginx/sites-available/my_chatbox", 
                           "/etc/nginx/sites-enabled/"], check=True)
        except subprocess.CalledProcessError:
            print("警告: 创建符号链接失败，可能已存在")
        
        # 测试Nginx配置
        subprocess.run(["sudo", "nginx", "-t"], check=True)
        
        # 重启Nginx
        subprocess.run(["sudo", "systemctl", "restart", "nginx"], check=True)
        
        # 删除临时文件
        os.remove("my_chatbox")
        
        print("✓ Nginx配置完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"错误: 配置Nginx失败: {e}")
        return False
    except Exception as e:
        print(f"错误: {e}")
        return False

def setup_ssl():
    """设置SSL证书"""
    print("\n[10/10] 配置SSL证书...")
    
    try_ssl = input("是否配置SSL证书? (需要域名) (y/n): ").lower() == 'y'
    if not try_ssl:
        print("跳过SSL配置")
        return True
    
    try:
        # 获取域名
        domain = input("请输入您的域名 (不要输入IP地址): ")
        
        # 检查域名格式
        if not domain or domain.count('.') < 1 or domain.startswith('http'):
            print("错误: 无效的域名格式")
            return False
        
        # 安装certbot
        subprocess.run(["sudo", "apt", "install", "certbot", "python3-certbot-nginx", "-y"], check=True)
        
        # 获取证书
        subprocess.run(["sudo", "certbot", "--nginx", "-d", domain], check=True)
        
        print("✓ SSL证书配置完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"错误: 配置SSL证书失败: {e}")
        print("提示: 确保您的域名已正确解析到此服务器IP")
        return False

def main():
    """主函数"""
    print_banner()
    
    # 检查是否是root用户
    if os.geteuid() == 0:
        print("警告: 不建议以root用户运行此脚本")
        continue_as_root = input("是否继续? (y/n): ").lower() == 'y'
        if not continue_as_root:
            print("部署已取消")
            return
    
    # 检查Python版本
    if not check_python_version():
        return
    
    # 安装系统依赖
    if not install_system_dependencies():
        return
    
    # 设置虚拟环境
    pip_path = setup_virtualenv()
    if not pip_path:
        return
    
    # 安装Python依赖
    if not install_python_dependencies(pip_path):
        return
    
    # 设置.env文件
    if not setup_env_file():
        return
    
    # 运行数据库迁移
    if not run_migrations():
        return
    
    # 收集静态文件
    if not collect_static():
        return
    
    # 设置Supervisor
    if not setup_supervisor():
        return
    
    # 设置Nginx
    if not setup_nginx():
        return
    
    # 设置SSL
    setup_ssl()
    
    print("\n" + "=" * 60)
    print("部署完成!")
    print("=" * 60)
    print("您的Django聊天应用已成功部署")
    print("请访问您的域名或服务器IP查看网站")

if __name__ == "__main__":
    main() 