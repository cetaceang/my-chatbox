/* eslint-env browser */
/* globals getCookie, storeConversationId, refreshConversationList, renderMessageContent, sendWebSocketRequest, getChatSettings, initWebSocket */

// =========================================================================
// 图片上传通用处理函数 - 使用WebSocket统一逻辑
// =========================================================================
function handleImageUpload(file, { tempId, generationId }) {
    const messageContainer = document.querySelector('#message-container');
    const messageInput = document.querySelector('#message-input');
    const sendButton = document.querySelector('#send-button');
    const uploadFileBtn = document.querySelector('#upload-file-btn');
    const modelSelect = document.querySelector('#model-select');

    const modelId = modelSelect.value;
    const message = messageInput.value.trim(); // 直接从输入框获取消息
    
    console.log(`[ImageHandler] 用户输入的消息: "${message}"`);
    console.log(`[ImageHandler] 消息长度: ${message.length}`);

    // 使用MessageFactory创建标准的用户消息
    // 如果用户没有输入文本，显示默认提示
    const displayMessage = message || '[图片上传]';
    const userMessageDiv = window.MessageFactory.createUserMessage(tempId, displayMessage);
    
    console.log(`[ImageHandler] 创建用户消息，显示内容: "${displayMessage}"`);

    // 创建并添加图片预览
    const imagePreviewDiv = document.createElement('div');
    imagePreviewDiv.className = 'image-upload-preview mt-2';
    imagePreviewDiv.innerHTML = `
        <div class="text-center">
            <img src="${URL.createObjectURL(file)}" class="img-fluid" style="max-height: 150px; max-width: 150px;" alt="${file.name || 'Pasted Image'}">
        </div>
        <div class="mt-2 text-muted small">
            <i class="bi bi-info-circle"></i> 
            提示：如果AI无法"看到"图片内容，可能是因为当前API不支持图片功能。
            您可以尝试使用API原生的图片上传界面。
        </div>
    `;
    userMessageDiv.querySelector('p').after(imagePreviewDiv);
    
    if (messageContainer) {
        messageContainer.appendChild(userMessageDiv);
        userMessageDiv.scrollIntoView();
    }

    // 清空输入框，因为消息已经显示
    messageInput.value = '';

    // 通知状态管理器开始生成（与纯文本发送保持一致）
    if (window.ChatStateManager) {
        // 使用统一的ID（现在tempId和generationId是同一个值）
        window.ChatStateManager.startGeneration(generationId, tempId);
        console.log(`[ImageHandler] 通知状态管理器开始生成: ${generationId}`);
    }

    // 创建AI加载指示器（与纯文本发送保持一致）
    const loadingDiv = window.MessageFactory.createLoadingIndicator(`ai-response-loading-${generationId}`);
    userMessageDiv.after(loadingDiv);
    loadingDiv.scrollIntoView();
    console.log(`[ImageHandler] 创建AI加载指示器: ai-response-loading-${generationId}`);

    // 将文件转换为Base64并通过WebSocket发送
    const reader = new FileReader();
    reader.onload = function(e) {
        const base64Data = e.target.result.split(',')[1]; // 移除data:image/...;base64,前缀
        
        // 构建WebSocket消息 - 使用与纯文本发送相同的逻辑
        const websocketMessage = {
            type: 'image_upload',
            message: message,
            model_id: modelId,
            generation_id: generationId,
            temp_id: generationId, // 关键修复：使用相同的ID
            file_data: base64Data,
            file_name: file.name || 'pasted_image.png',
            file_type: file.type || 'image/png',
            is_streaming: getChatSettings().isStreaming
        };

        console.log(`[ImageHandler] 通过WebSocket发送图片上传消息`);
        
        // 使用 sendWebSocketRequest 统一发送，它内置了HTTP回退逻辑
        sendWebSocketRequest('image_upload', {
            ...websocketMessage,
            file: file // 将原始文件对象传递给回退逻辑
        });
    };

    reader.onerror = function(error) {
        console.error('[ImageHandler] 读取文件失败:', error);
        
        // 显示错误信息
        const errorDiv = document.createElement('div');
        errorDiv.className = 'alert alert-danger mt-2';
        errorDiv.innerHTML = '<strong>文件读取失败:</strong> 无法读取选择的图片文件。';
        userMessageDiv.after(errorDiv);
        
        // 移除加载指示器
        if (loadingDiv) {
            loadingDiv.remove();
        }
        
        // 重置状态管理器
        if (window.ChatStateManager) {
            window.ChatStateManager.completeGeneration(generationId);
        }
    };

    // 开始读取文件
    reader.readAsDataURL(file);
}

window.handleImageUpload = handleImageUpload;
