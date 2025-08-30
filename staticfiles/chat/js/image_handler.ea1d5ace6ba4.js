/* eslint-env browser */
/* globals getCookie, storeConversationId, refreshConversationList, renderMessageContent */

// =========================================================================
// 图片上传通用处理函数
// =========================================================================
function handleImageUpload(file, message) {
    const messageContainer = document.querySelector('#message-container');
    const messageInput = document.querySelector('#message-input');
    const sendButton = document.querySelector('#send-button');
    const uploadFileBtn = document.querySelector('#upload-file-btn');
    const modelSelect = document.querySelector('#model-select');
    const fileUploadToast = document.querySelector('#file-upload-toast');

    const modelId = modelSelect.value;

    // 创建FormData对象
    const formData = new FormData();
    formData.append('file', file);
    formData.append('model_id', modelId);
    formData.append('message', message);
    
    if (window.conversationId) {
        formData.append('conversation_id', window.conversationId);
    }

    // 禁用输入控件
    if (messageInput) messageInput.disabled = true;
    if (sendButton) sendButton.disabled = true;
    if (uploadFileBtn) uploadFileBtn.disabled = true;

    // 使用MessageFactory创建标准的用户消息
    const tempId = window.generateUUID();
    const userMessageDiv = window.MessageFactory.createUserMessage(tempId, message);

    // 创建并添加图片预览和加载指示器
    const imagePreviewDiv = document.createElement('div');
    imagePreviewDiv.className = 'image-upload-preview mt-2';
    imagePreviewDiv.innerHTML = `
        <div class="text-center">
            <img src="${URL.createObjectURL(file)}" class="img-fluid" style="max-height: 150px; max-width: 150px;" alt="${file.name || 'Pasted Image'}">
        </div>
        <div class="text-center mt-2 loading-indicator">
            <div class="spinner-border spinner-border-sm" role="status">
                <span class="visually-hidden">加载中...</span>
            </div>
            <span class="ms-2">正在处理，请稍候...</span>
        </div>
    `;
    userMessageDiv.querySelector('p').after(imagePreviewDiv);
    
    if (messageContainer) {
        messageContainer.appendChild(userMessageDiv);
        userMessageDiv.scrollIntoView();
    }

    // 发送文件和消息到服务器
    fetch('/chat/api/upload_file/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        // 重新启用输入控件
        if (messageInput) messageInput.disabled = false;
        if (sendButton) sendButton.disabled = false;
        if (uploadFileBtn) uploadFileBtn.disabled = false;
        
        console.log("文件上传API响应:", data);
        
        if (data.success) {
            // 关键修复：用从后端获取的真实ID更新消息元素的ID
            if (data.user_message_id) {
                userMessageDiv.setAttribute('data-message-id', data.user_message_id);
                console.log(`用户消息框的ID已从 ${tempId} 更新为 ${data.user_message_id}`);
            }

            // 移除加载指示器并添加提示信息
            const previewDiv = userMessageDiv.querySelector('.image-upload-preview');
            if (previewDiv) {
                const loadingIndicator = previewDiv.querySelector('.loading-indicator');
                if (loadingIndicator) {
                    loadingIndicator.remove();
                }
                const hintDiv = document.createElement('div');
                hintDiv.className = 'mt-2 text-muted small';
                hintDiv.innerHTML = `
                    <i class="bi bi-info-circle"></i> 
                    提示：如果AI无法"看到"图片内容，可能是因为当前API不支持图片功能。
                    您可以尝试使用API原生的图片上传界面。
                `;
                previewDiv.appendChild(hintDiv);
            }
            
            // 如果创建了新对话，更新对话ID
            if (data.new_conversation_id) {
                window.conversationId = data.new_conversation_id;
                storeConversationId(window.conversationId);
                const newUrl = `/chat/?conversation_id=${window.conversationId}`;
                window.history.pushState({ path: newUrl }, '', newUrl);
                refreshConversationList();
            }
            
            // 显示AI响应
            if (data.ai_response) {
                const aiTempId = window.generateUUID();
                const aiMessageDiv = window.MessageFactory.createAIMessage(aiTempId, data.ai_response);
                if (messageContainer) {
                    messageContainer.appendChild(aiMessageDiv);
                    renderMessageContent(aiMessageDiv);
                    aiMessageDiv.scrollIntoView();
                }
            }
        } else {
            // 显示上传失败消息
            const previewDiv = userMessageDiv.querySelector('.image-upload-preview');
            if (previewDiv) {
                const loadingIndicator = previewDiv.querySelector('.loading-indicator');
                if (loadingIndicator) {
                    loadingIndicator.innerHTML = `<p class="text-danger mt-2">图片上传失败: ${data.message || '该模型不支持图片上传'}</p>`;
                }
            }
            
            const toastBody = fileUploadToast.querySelector('.toast-body');
            if (toastBody) {
                toastBody.textContent = data.message || '该模型不支持图片上传';
            }
            const toast = new bootstrap.Toast(fileUploadToast);
            toast.show();
        }
    })
    .catch(error => {
        console.error('上传文件时出错:', error);
        if (messageInput) messageInput.disabled = false;
        if (sendButton) sendButton.disabled = false;
        if (uploadFileBtn) uploadFileBtn.disabled = false;
        
        const previewDiv = userMessageDiv.querySelector('.image-upload-preview');
        if (previewDiv) {
            const loadingIndicator = previewDiv.querySelector('.loading-indicator');
            if (loadingIndicator) {
                loadingIndicator.innerHTML = `<p class="text-danger mt-2">图片上传失败: ${error.message}</p>`;
            }
        }
    });
}

window.handleImageUpload = handleImageUpload;
