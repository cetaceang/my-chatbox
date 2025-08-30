/* eslint-env browser */
/* globals handleImageUpload, deleteMessage, editConversationTitle, deleteConversation, regenerateResponse, getCookie, escapeHtml, rebuildMessageElement, completeMessageEdit, saveMessageToServer, sendStopGenerationRequest, displayTerminationMessage, handleGenerationRequest */

function initializeEventListeners() {
    const messageInput = document.querySelector('#message-input');
    const sendButton = document.querySelector('#send-button');
    const uploadFileBtn = document.querySelector('#upload-file-btn');
    const fileUploadInput = document.querySelector('#file-upload');
    const clearConversationBtn = document.querySelector('#clear-conversation-btn');
    const modelSelect = document.querySelector('#model-select');
    const messageContainer = document.querySelector('#message-container');
    const conversationList = document.querySelector('#conversation-list');

    // --- Load saved model preference ---
    if (modelSelect) {
        const savedModelId = localStorage.getItem('selectedModelId');
        if (savedModelId) {
            // Check if the saved ID exists as an option
            const optionExists = modelSelect.querySelector(`option[value="${savedModelId}"]`);
            if (optionExists) {
                modelSelect.value = savedModelId;
                console.log(`Loaded saved model preference: ${savedModelId}`);
            } else {
                console.warn(`Saved model ID "${savedModelId}" not found in dropdown. Using default.`);
                // Optionally remove the invalid saved ID
                // localStorage.removeItem('selectedModelId');
            }
        }
    }
    // --- End Load saved model preference ---

    // --- Save model preference on change ---
    if (modelSelect) {
        modelSelect.addEventListener('change', function() {
            const selectedId = this.value;
            localStorage.setItem('selectedModelId', selectedId);
            window.ChatStateManager.setModelId(selectedId); // Explicitly call window.ChatStateManager.setModelId
            console.log(`Saved model preference: ${selectedId}`);
        });
    }
    // --- End Save model preference on change ---

    // 文件上传按钮点击事件
    if (uploadFileBtn && fileUploadInput) {
        uploadFileBtn.onclick = function() {
            fileUploadInput.click(); // 触发隐藏的文件选择器
        };
    }

    // 文件选择处理
    if (fileUploadInput && modelSelect) {
        fileUploadInput.onchange = function() {
            if (fileUploadInput.files.length > 0) {
                const file = fileUploadInput.files[0];
                
                // 检查文件类型是否为图片
                const fileType = file.type;
                if (!fileType.startsWith('image/')) {
                    alert('目前仅支持图片格式的文件上传');
                    fileUploadInput.value = '';
                    return;
                }
                
                // 检查文件大小
                const maxSize = 5 * 1024 * 1024; // 5MB
                if (file.size > maxSize) {
                    alert('图片大小不能超过5MB');
                    fileUploadInput.value = '';
                    return;
                }
                
                // 显示已选择图片的预览
                const previewContainer = document.createElement('div');
                previewContainer.id = 'image-preview-container';
                previewContainer.className = 'mt-2 mb-2 border rounded p-2 d-flex align-items-center';
                previewContainer.innerHTML = `
                    <img src="${URL.createObjectURL(file)}" class="img-thumbnail me-2" style="max-height: 50px; max-width: 50px;" alt="${file.name}">
                    <div class="flex-grow-1">
                        <div class="small text-truncate">${file.name}</div>
                        <div class="small text-muted">${(file.size / 1024).toFixed(2)} KB</div>
                    </div>
                    <button type="button" class="btn btn-sm btn-outline-danger" id="remove-image-btn">
                        <i class="bi bi-x"></i>
                    </button>
                `;
                
                // 将预览添加到输入框上方
                const inputGroup = messageInput.closest('.input-group');
                inputGroup.parentNode.insertBefore(previewContainer, inputGroup);
                
                // 添加移除图片按钮事件
                document.getElementById('remove-image-btn').addEventListener('click', function() {
                    previewContainer.remove();
                    fileUploadInput.value = '';
                });
                
                // 修改发送按钮行为，使其调用新的通用处理函数
                const originalSendBtnClick = sendButton.onclick;
                sendButton.onclick = function() {
                    const message = messageInput.value.trim() || "请描述这张图片";
                    
                    // 调用通用处理函数
                    handleImageUpload(file, message);
                    
                    // 清理UI
                    previewContainer.remove();
                    fileUploadInput.value = '';
                    messageInput.value = '';
                    
                    // 恢复原始发送按钮功能
                    sendButton.onclick = originalSendBtnClick;
                };
            }
        };
    }

    // --- 添加粘贴图片功能 ---
    if (messageInput) {
        messageInput.addEventListener('paste', function(e) {
            // 检查剪贴板是否包含图片
            const items = (e.clipboardData || e.originalEvent.clipboardData).items;
            let imageItem = null;
            
            for (let i = 0; i < items.length; i++) {
                if (items[i].type.indexOf('image') !== -1) {
                    imageItem = items[i];
                    break;
                }
            }
            
            // 如果找到图片
            if (imageItem) {
                e.preventDefault(); // 阻止默认粘贴行为
                
                const file = imageItem.getAsFile();
                console.log('粘贴的图片:', file.name, file.type, file.size);
                
                // 检查文件大小
                const maxSize = 5 * 1024 * 1024; // 5MB
                if (file.size > maxSize) {
                    alert('图片大小不能超过5MB');
                    return;
                }
                
                // 显示已选择图片的预览
                const previewContainer = document.createElement('div');
                previewContainer.id = 'image-preview-container';
                previewContainer.className = 'mt-2 mb-2 border rounded p-2 d-flex align-items-center';
                previewContainer.innerHTML = `
                    <img src="${URL.createObjectURL(file)}" class="img-thumbnail me-2" style="max-height: 50px; max-width: 50px;" alt="粘贴的图片">
                    <div class="flex-grow-1">
                        <div class="small text-truncate">粘贴的图片</div>
                        <div class="small text-muted">${(file.size / 1024).toFixed(2)} KB</div>
                    </div>
                    <button type="button" class="btn btn-sm btn-outline-danger" id="remove-image-btn">
                        <i class="bi bi-x"></i>
                    </button>
                `;
                
                // 将预览添加到输入框上方
                const inputGroup = messageInput.closest('.input-group');
                inputGroup.parentNode.insertBefore(previewContainer, inputGroup);
                
                // 添加移除图片按钮事件
                document.getElementById('remove-image-btn').addEventListener('click', function() {
                    previewContainer.remove();
                });
                
                // 修改发送按钮行为，使其调用新的通用处理函数
                const originalSendBtnClick = sendButton.onclick;
                sendButton.onclick = function() {
                    const message = messageInput.value.trim() || "请描述这张图片";
                    
                    // 调用通用处理函数
                    handleImageUpload(file, message);
                    
                    // 清理UI
                    previewContainer.remove();
                    messageInput.value = '';
                    
                    // 恢复原始发送按钮功能
                    sendButton.onclick = originalSendBtnClick;
                };
            }
        });
    }

    // Send / Stop Button Click Logic
    if (sendButton && messageInput && modelSelect) {
        sendButton.onclick = function() {
            // Check if the button is in 'stop' mode
            if (sendButton.classList.contains('stop-mode')) {
                // --- Stop Generation Logic ---
                console.log("[Stop Click] Stop button clicked.");

                // --- State Check using StateManager ---
                if (sendButton.disabled || window.ChatStateManager.getState('isStopping')) { // Explicit call
                    console.log(`[Stop Click] Ignoring click. Button Disabled: ${sendButton.disabled}, StateManager isStopping: ${window.ChatStateManager.getState('isStopping')}`); // Explicit call
                    return; // Ignore if already disabled or termination already requested/confirmed
                }
                // --- End State Check ---

                // --- CORRECTED STOP LOGIC ---
                // 1. Get the ID *before* changing the state.
                const generationIdToStop = window.ChatStateManager.getGenerationId();
                const userMessageIdForTermination = generationIdToStop; // The active ID is the temp ID of the user message.

                // 2. Now, optimistically update the UI.
                window.ChatStateManager.requestStopOptimistic();
                console.log("[Stop Click] Called ChatStateManager.requestStopOptimistic() for immediate UI reset.");

                // 3. Display the termination message immediately.
                displayTerminationMessage(userMessageIdForTermination);

                // 4. Send the actual stop request to the backend with the saved ID.
                sendStopGenerationRequest(generationIdToStop);

                // --- Mark the corresponding user message as having a stop requested (Keep this logic) ---
                // Use the determined userMessageIdForTermination to find the correct user message
                let userMessageToMark = null;
                if (userMessageIdForTermination) {
                    userMessageToMark = document.querySelector(`.alert.alert-primary[data-message-id="${userMessageIdForTermination}"], .alert.alert-primary[data-temp-id="${userMessageIdForTermination}"]`);
                }

                if (userMessageToMark) {
                    userMessageToMark.setAttribute('data-stop-requested', 'true');
                    console.log(`[Stop Click] Set data-stop-requested="true" for user message ${userMessageIdForTermination}.`);
                } else {
                    console.warn(`[Stop Click] Could not find user message element for ID ${userMessageIdForTermination} to mark stop requested.`);
                    // Attempt to mark the last user message if ID failed
                    const userMessages = document.querySelectorAll('.alert.alert-primary');
                     if (userMessages.length > 0) {
                         const lastUserMsg = userMessages[userMessages.length - 1];
                         lastUserMsg.setAttribute('data-stop-requested', 'true');
                         const fallbackId = lastUserMsg.getAttribute('data-message-id') || lastUserMsg.getAttribute('data-temp-id');
                         console.log(`[Stop Click] Marked last user message (${fallbackId}) as stop requested (fallback).`);
                     }
                }
                // --- End marking user message ---

                // --- Immediately reset any active regenerate button ---
                // This makes the UI responsive immediately upon clicking Stop.
                const activeRegenBtn = document.querySelector('.regenerate-btn[data-regenerating="true"]');
                if (activeRegenBtn) {
                    console.log("[Stop Click] Found active regenerate button. Resetting its state immediately.");
                    activeRegenBtn.removeAttribute('data-regenerating');
                    activeRegenBtn.classList.remove('btn-processing');
                    activeRegenBtn.disabled = false;
                } else {
                     console.log("[Stop Click] No active regenerate button found to reset immediately.");
                }
                // --- End immediate reset logic ---
            } else {
                // --- Send Message Logic ---
                const message = messageInput.value.trim();
                const modelId = modelSelect.value;

                if (message !== '' && !window.ChatStateManager.isBusy()) {
                    const generationId = window.generateUUID(); // The single, unique ID for this entire operation
                    handleGenerationRequest({
                        generationId: generationId,
                        message: message,
                        modelId: modelId,
                        isRegenerate: false
                    });
                }
            }
        };
    } else {
         console.warn("Send button, message input, or model select not found.");
    }

    // Message Input Keypress (Enter to send)
    if (messageInput) {
        messageInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault(); // Prevent newline
                sendButton?.click(); // Trigger send button click
            }
        });
    }

    // Event Delegation for Message Container Actions (Delete, Edit, Regenerate)
    if (clearConversationBtn) {
        clearConversationBtn.addEventListener('click', function() {
            if (!window.conversationId) {
                alert("没有活动的会话可清除。");
                return;
            }

            if (confirm("您确定要清除此会话中的所有消息吗？此操作不可撤销。")) {
                fetch(`/chat/api/conversations/${window.conversationId}/clear/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken')
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        const messageContainer = document.querySelector('#message-container');
                        if (messageContainer) {
                            messageContainer.innerHTML = ''; // 清空前端消息
                        }
                        alert("会话消息已成功清除。");
                    } else {
                        alert(`清除失败: ${data.message}`);
                    }
                })
                .catch(error => {
                    console.error('清除会话时出错:', error);
                    alert('清除会话时发生网络错误。');
                });
            }
        });
    }

    // Event Delegation for Message Container Actions (Delete, Edit, Regenerate)
    if (messageContainer) {
        messageContainer.addEventListener('click', function(e) {
            const target = e.target;
            const messageDiv = target.closest('.alert');
            if (!messageDiv) return; // Click wasn't inside a message alert

            const messageId = messageDiv.getAttribute('data-message-id') || messageDiv.getAttribute('data-temp-id'); // Get real or temp ID

            // Delete Button
            if (target.closest('.delete-message-btn')) {
                e.stopPropagation(); // Prevent other listeners if needed
                deleteMessage(messageId, messageDiv); // Use function from api_handler
            }

            // Edit Button
            else if (target.closest('.edit-message-btn')) {
                e.stopPropagation();
                
                // 获取消息ID和相关信息
                const messageId = messageDiv.getAttribute('data-message-id') || messageDiv.getAttribute('data-temp-id');
                if (!messageId) {
                    console.error("无法找到消息ID");
                    return;
                }
                
                // 从状态管理器获取消息内容
                const messageState = window.ChatState.getMessage(messageId);
                let originalContent = '';
                
                if (messageState) {
                    originalContent = messageState.content;
                } else {
                    // 回退到DOM属性
                    const renderTarget = messageDiv.querySelector('p > .render-target');
                    if (!renderTarget) return;
                    originalContent = renderTarget.getAttribute('data-original-content') || '';
                }
                
                // 保存编辑状态信息
                const messageInfo = {
                    id: messageId,
                    content: originalContent,
                    isUser: messageDiv.classList.contains('alert-primary')
                };
                
                // 替换为编辑界面
                messageDiv.innerHTML = `
                    <div class="mb-3">
                        <textarea class="form-control edit-textarea" rows="4">${escapeHtml(originalContent)}</textarea>
                    </div>
                    <div class="d-flex justify-content-end">
                        <button class="btn btn-sm btn-secondary cancel-edit-btn me-2">取消</button>
                        <button class="btn btn-sm btn-primary save-edit-btn">保存</button>
                    </div>
                `;
                
                // 保存恢复信息
                messageDiv.setAttribute('data-edit-info', JSON.stringify(messageInfo));
                messageDiv.querySelector('.edit-textarea').focus();
            }

            // Cancel Edit Button
            else if (target.closest('.cancel-edit-btn')) {
                e.stopPropagation();
                
                try {
                    // 获取编辑前的信息
                    const editInfo = JSON.parse(messageDiv.getAttribute('data-edit-info') || '{}');
                    if (!editInfo.id || !editInfo.content) {
                        throw new Error('Missing edit info');
                    }
                    
                    // 从状态管理器获取最新状态，如果存在
                    const messageState = window.ChatState.getMessage(editInfo.id);
                    
                    if (messageState) {
                        // 使用状态管理器重建元素
                        window.MessageFactory.updateDOMForMessage(editInfo.id);
                    } else {
                        // 回退到原始重建函数
                        rebuildMessageElement(messageDiv, editInfo.id, editInfo.content, editInfo.isUser);
                    }
                    
                    messageDiv.removeAttribute('data-edit-info');
                } catch (err) {
                    console.error('Error cancelling edit:', err);
                    // 如果恢复失败，刷新页面
                    window.location.reload();
                }
            }

            // Save Edit Button
            else if (target.closest('.save-edit-btn')) {
                e.stopPropagation();
                
                try {
                    // 获取编辑信息和新内容
                    const editInfo = JSON.parse(messageDiv.getAttribute('data-edit-info') || '{}');
                    if (!editInfo.id) {
                        throw new Error('Missing message ID in edit info');
                    }
                    
                    const newContent = messageDiv.querySelector('.edit-textarea').value;
                    
                    // 检查内容是否有变化
                    if (newContent === editInfo.content) {
                        console.log("内容未变化，恢复原始显示");
                        // 直接恢复显示，不触发更新
                        const messageState = window.ChatState.getMessage(editInfo.id);
                        
                        if (messageState) {
                            window.MessageFactory.updateDOMForMessage(editInfo.id);
                        } else {
                            rebuildMessageElement(messageDiv, editInfo.id, editInfo.content, editInfo.isUser);
                        }
                        
                        messageDiv.removeAttribute('data-edit-info');
                        return;
                    }
                    
                    // 使用状态管理器更新消息
                    if (window.ChatState.getMessage(editInfo.id)) {
                        console.log("使用乐观UI更新");

                        // 1. 立即更新UI（乐观更新）
                        window.ChatState.updateMessage(editInfo.id, newContent);
                        
                        // 2. 在后台保存到服务器
                        saveMessageToServer(editInfo.id, newContent, editInfo.isUser)
                            .then(success => {
                                if (success) {
                                    console.log("后台保存成功");
                                    // 3. 如果是用户消息，并且保存成功，则触发重新生成
                                    if (editInfo.isUser) {
                                        // 使用setTimeout将重新生成推迟到下一个事件循环
                                        // 这给了浏览器足够的时间来渲染UI的乐观更新，避免渲染冲突
                                        setTimeout(() => {
                                            regenerateResponse(editInfo.id);
                                        }, 0);
                                    }
                                } else {
                                    // 如果保存失败，可以选择回滚UI或通知用户
                                    console.error("后台保存失败，正在回滚UI");
                                    alert("保存失败，请重试");
                                    window.ChatState.updateMessage(editInfo.id, editInfo.content); // 恢复到原始内容
                                }
                            });
                    } else {
                        console.log("使用传统方式更新消息");
                        // 回退到传统编辑功能
                        completeMessageEdit(editInfo.id, newContent, messageDiv, editInfo.isUser);
                    }
                    
                    messageDiv.removeAttribute('data-edit-info');
                } catch (err) {
                    console.error('Error saving edit:', err);
                    alert('保存编辑时出错，请刷新页面后重试');
                }
            }

            // Regenerate Button
            else if (target.closest('.regenerate-btn')) {
                e.stopPropagation();
                const userMessageDiv = messageDiv.classList.contains('alert-primary')
                    ? messageDiv
                    : messageDiv.previousElementSibling?.closest('.alert.alert-primary');

                if (userMessageDiv) {
                    const userId = userMessageDiv.getAttribute('data-message-id') || userMessageDiv.getAttribute('data-temp-id');
                    regenerateResponse(userId);
                }
            }
        });
    }

    // Event Delegation for Conversation List Actions (Delete, Edit Title)
    if (conversationList) {
        conversationList.addEventListener('click', function(e) {
            const target = e.target;
            const conversationLink = target.closest('a.list-group-item');
            if (!conversationLink) return; // Click not on a conversation item link

            // Extract conversation ID from the link's href using regex for robustness
            const href = conversationLink.getAttribute('href');
            let convId = null;
            if (href) {
                const match = href.match(/conversation_id=(\d+)/);
                if (match && match[1]) {
                    convId = match[1];
                }
            }

            if (!convId) {
                 console.error("Could not extract conversation ID from link:", href);
                 return; // Stop if ID couldn't be extracted
            }

            // Delete Conversation Button
            const deleteBtn = target.closest('.delete-conversation-btn'); // Get the button element
            if (deleteBtn) { // Check if the click was on the delete button or its icon
                e.preventDefault(); // Prevent navigation
                e.stopPropagation(); // Stop event bubbling

                deleteBtn.disabled = true;

                const clickTimestamp = Date.now();
                console.log(`[${clickTimestamp}] Delete button handler triggered for conversation ID: ${convId}`);
                console.log(`[${clickTimestamp}] Calling deleteConversation(${convId})...`);

                deleteConversation(convId); // Use function from api_handler
            }

            // Edit Conversation Title Button/Icon
            else if (target.closest('.edit-conversation-btn')) {
                e.preventDefault(); // Prevent navigation
                e.stopPropagation(); // Stop event bubbling
                console.log("Edit button clicked for conversation ID:", convId); // Add log
                editConversationTitle(convId); // Use function from api_handler
            }
        });
    }
}

window.initializeEventListeners = initializeEventListeners;
