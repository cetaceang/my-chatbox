#!/usr/bin/env python
"""
显示Django项目中所有已注册的URL
"""
import os
import sys
import django

# 设置Django环境
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.urls import get_resolver
from django.core.management import call_command

def main():
    print("===== Django项目中所有已注册的URL =====")
    
    # 使用Django的命令行工具显示URL
    call_command('show_urls')

if __name__ == "__main__":
    main() 