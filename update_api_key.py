#!/usr/bin/env python
"""
更新AI提供商的API密钥和基础URL
"""
import os
import sys
import django

# 设置Django环境
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from chat.models import AIProvider, AIModel

def main():
    print("===== 更新AI提供商配置 =====")
    
    # 获取所有AI提供商
    providers = AIProvider.objects.all()
    
    if not providers.exists():
        print("数据库中没有AI提供商，请先运行setup_data.py")
        return
    
    # 显示当前配置
    print("\n当前AI提供商配置:")
    for provider in providers:
        print(f"ID: {provider.id}")
        print(f"名称: {provider.name}")
        print(f"基础URL: {provider.base_url}")
        print(f"API密钥: {provider.api_key[:5]}..." if provider.api_key else "API密钥: 未设置")
        print(f"是否启用: {provider.is_active}")
        print("-" * 30)
    
    # 选择要更新的提供商
    provider_id = input("\n请输入要更新的提供商ID (默认为1): ") or "1"
    try:
        provider = AIProvider.objects.get(id=int(provider_id))
    except (AIProvider.DoesNotExist, ValueError):
        print(f"找不到ID为{provider_id}的提供商")
        return
    
    # 更新配置
    print(f"\n正在更新 {provider.name} 的配置...")
    
    # 更新基础URL
    new_base_url = input(f"请输入新的基础URL (当前: {provider.base_url}，直接回车保持不变): ")
    if new_base_url:
        provider.base_url = new_base_url
    
    # 更新API密钥
    new_api_key = input("请输入新的API密钥 (直接回车保持不变): ")
    if new_api_key:
        provider.api_key = new_api_key
    
    # 更新启用状态
    new_is_active = input(f"是否启用 (y/n，当前: {'y' if provider.is_active else 'n'}，直接回车保持不变): ")
    if new_is_active:
        provider.is_active = new_is_active.lower() == 'y'
    
    # 保存更改
    provider.save()
    
    print("\n更新成功！新的配置:")
    print(f"名称: {provider.name}")
    print(f"基础URL: {provider.base_url}")
    print(f"API密钥: {provider.api_key[:5]}..." if provider.api_key else "API密钥: 未设置")
    print(f"是否启用: {provider.is_active}")
    
    # 显示相关模型
    models = AIModel.objects.filter(provider=provider)
    print(f"\n该提供商关联的模型 ({models.count()}):")
    for model in models:
        print(f"- {model.display_name} ({model.model_name})")

if __name__ == "__main__":
    main() 