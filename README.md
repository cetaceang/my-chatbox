# 我的AI聊天盒子 (My AI ChatBox)

一个基于Django和Channels的AI聊天应用，支持与AI模型进行实时对话，提供流畅的聊天体验，并保护您的隐私。

## 项目背景

我开发这个AI聊天盒子的主要原因：

1. **隐私保护**：市面上的类似网页可能会泄露个人隐私，尽管他们宣称自己的chatbox完全建立在本地
2. **跨设备同步**：比较流行的chatbox AI无法在手机和电脑端进行消息同步
3. **文件处理能力**：有些类似的网页无法上传文件，因此我计划自己加入该功能

## 项目优势

- **多AI模型支持**：支持接入多种AI服务提供商和模型，如GPT系列、Claude等
- **跨设备同步**：通过同步标识符实现手机和电脑之间的对话历史完全同步
- **隐私安全**：完全控制您的数据，不会将对话内容发送给未授权的第三方
- **实时通信**：基于WebSocket技术，AI响应即时无延迟
- **响应式设计**：适配各种设备尺寸，包括移动端和桌面端
- **完整的对话历史**：保存并展示完整的AI对话记录
- **跨平台兼容**：支持Windows和Ubuntu环境部署
- **易于部署**：提供简单的部署脚本，一键启动应用
- **可扩展架构**：基于Django的模块化设计，易于添加新功能

## 快速开始

### 环境要求

- Python 3.8+
- Redis服务器（用于WebSocket通信）
- 操作系统：Windows 10+ 或 Ubuntu 20.04+

### 安装依赖

项目提供了自动安装依赖的脚本，执行以下命令：

```bash
# Windows
python install_dependencies.py

# Ubuntu
python3 install_dependencies.py
```

脚本内容如下：

```python
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
```

### 启动应用

安装依赖后，使用以下命令启动应用：

```bash
# Windows
python start.py

# Ubuntu
python3 start.py
```

启动脚本内容如下：

```python
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
```

启动后，打开浏览器访问：`http://localhost:3000`

## 项目结构

```
my_chatbox/
├── chat/                  # 主要应用目录
│   ├── consumers.py       # WebSocket消费者
│   ├── models.py          # 数据模型
│   ├── templates/         # 应用模板
│   │   └── chat/          # 聊天相关模板
│   │       ├── chat.html  # 主聊天界面
│   │       └── ...
│   ├── urls.py            # URL路由
│   └── views.py           # 视图函数
├── config/                # 项目配置
│   ├── asgi.py            # ASGI配置(WebSocket)
│   ├── settings.py        # 项目设置
│   └── urls.py            # 主URL配置
├── static/                # 静态文件
├── templates/             # 全局模板
├── users/                 # 用户管理应用
├── db.sqlite3             # SQLite数据库
├── install_dependencies.py # 依赖安装脚本
├── requirements.txt       # 项目依赖列表
├── start.py               # 启动脚本
└── manage.py              # Django管理脚本
```

## 主要依赖

项目使用以下主要技术和库：

```
django>=4.2.0           # Web框架
channels>=4.0.0         # WebSocket支持
daphne>=4.0.0           # ASGI服务器
channels-redis>=4.1.0   # Redis通道层
redis>=4.6.0            # Redis客户端
djangorestframework>=3.14.0  # REST API支持
```

## 已实现功能

1. **多AI模型支持**：可以配置和使用多种AI服务提供商和模型
2. **对话历史同步**：通过同步标识符实现跨设备同步
3. **用户认证系统**：支持用户注册、登录和权限管理
4. **实时对话**：基于WebSocket的实时AI对话
5. **对话管理**：创建、编辑、删除对话
6. **消息管理**：编辑、重新生成和删除消息
7. **API调试工具**：内置API测试功能，方便调试

## 未来功能规划

项目计划在未来添加以下功能：

1. **文件上传功能**：支持在与AI聊天中上传和分析文件，包括图片、文档等
2. **离线模式**：支持在无网络环境下使用本地AI模型
3. **上下文管理**：更智能的对话上下文管理，提高AI回复质量
4. **自定义提示词**：允许用户创建和管理自己的提示词模板
5. **AI对话分类**：自动对不同主题的AI对话进行分类
6. **数据导出**：支持导出对话历史为多种格式
7. **隐私设置**：细粒度的隐私控制选项
8. **多语言支持**：国际化和本地化

## 贡献指南

欢迎提交问题和功能请求！如果您想贡献代码，请遵循以下步骤：

1. Fork 仓库
2. 创建您的特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交您的更改 (`git commit -m 'Add some amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 打开一个 Pull Request

## 许可证

本项目采用 MIT 许可证 - 详情请参阅 LICENSE 文件
