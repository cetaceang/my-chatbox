# Ubuntu服务器部署指南

## 准备工作

1. 确保服务器已安装Python 3.8+
   ```bash
   python3 --version
   ```

2. 安装pip和虚拟环境
   ```bash
   sudo apt update
   sudo apt install python3-pip python3-venv -y
   ```

3. 安装Git（如果需要从Git仓库拉取代码）
   ```bash
   sudo apt install git -y
   ```

## 项目部署步骤

### 1. 创建项目目录并设置虚拟环境

```bash
# 创建项目目录
mkdir -p /var/www/my_chatbox
cd /var/www/my_chatbox

# 复制项目文件到此目录
# 或者使用git clone从仓库拉取

# 创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate
```

### 2. 安装依赖

```bash
# 安装依赖
pip install -r requirements.txt
```

### 3. 配置环境变量

创建.env文件：
```bash
touch .env
```

编辑.env文件，添加必要的环境变量：
```
DEBUG=False
SECRET_KEY=your_secure_secret_key
ALLOWED_HOSTS=your_domain.com,www.your_domain.com
```

### 4. 数据库迁移

```bash
python manage.py migrate
```

### 5. 收集静态文件

```bash
python manage.py collectstatic --noinput
```

### 6. 创建超级用户（可选）

```bash
python manage.py createsuperuser
```

### 7. 使用Gunicorn和Daphne部署

#### 安装Supervisor来管理进程

```bash
sudo apt install supervisor -y
```

#### 创建Supervisor配置文件

创建Daphne配置文件：
```bash
sudo nano /etc/supervisor/conf.d/daphne.conf
```

添加以下内容：
```
[program:daphne]
command=/var/www/my_chatbox/venv/bin/daphne -b 0.0.0.0 -p 8001 config.asgi:application
directory=/var/www/my_chatbox
user=www-data
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/daphne.log
```

#### 更新Supervisor

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start daphne
```

### 8. 配置Nginx

安装Nginx：
```bash
sudo apt install nginx -y
```

创建Nginx配置文件：
```bash
sudo nano /etc/nginx/sites-available/my_chatbox
```

添加以下内容：
```
server {
    listen 80;
    server_name your_domain.com www.your_domain.com;

    location /static/ {
        alias /var/www/my_chatbox/staticfiles/;
    }

    location /media/ {
        alias /var/www/my_chatbox/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

启用站点并重启Nginx：
```bash
sudo ln -s /etc/nginx/sites-available/my_chatbox /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 9. 配置SSL（可选但推荐）

使用Certbot安装SSL证书：
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your_domain.com -d www.your_domain.com
```

### 10. 防火墙设置

```bash
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 维护和监控

### 查看日志

```bash
# Daphne日志
sudo tail -f /var/log/daphne.log

# Nginx日志
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### 重启服务

```bash
# 重启Daphne
sudo supervisorctl restart daphne

# 重启Nginx
sudo systemctl restart nginx
```

## 常见问题排查

1. 如果网站无法访问，检查防火墙设置和Nginx配置
2. 如果WebSocket连接失败，确保Nginx配置中的WebSocket代理设置正确
3. 如果静态文件无法加载，检查STATIC_ROOT路径和Nginx中的静态文件配置
4. 如果遇到权限问题，确保文件所有权和权限设置正确
```bash
sudo chown -R www-data:www-data /var/www/my_chatbox
``` 