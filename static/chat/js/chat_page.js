/* eslint-env browser */
/* globals initWebSocket, syncConversationData, getStoredConversationId, storeConversationId, escapeHtml, renderMessageContent, sendWebSocketMessage, getCookie, tempIdMap, getRealMessageId, deleteConversation, editConversationTitle, deleteMessage, saveMessageEdit, regenerateResponse, displaySystemError, createLoadingIndicator, createAIMessageDiv */ // Inform linter about globals

// Global variable for the current conversation ID, initialized from the template
// It's generally better to avoid globals, but necessary here due to split files without a module system
// Ensure this is defined in the HTML template before this script runs.
// let conversationId = null; // This will be set in the HTML template script block

document.addEventListener("DOMContentLoaded", () => {
    console.log("Chat Page Initializing...");

    // Initialize tempIdMap (assuming it's defined in utils.js or globally)
    if (typeof tempIdMap === 'undefined') {
        console.warn("tempIdMap is not defined globally. Initializing locally.");
        window.tempIdMap = {}; // Make it global if not already
    } else {
        tempIdMap = {}; // Reset it on page load
    }

    // Check and update temporary IDs periodically
    // setTimeout(checkForTemporaryIds, 1000); // Moved checkForTemporaryIds to api_handler for now
    // setInterval(checkForTemporaryIds, 30000); // Moved checkForTemporaryIds to api_handler for now

    // --- Conversation ID Handling ---
    const urlParams = new URLSearchParams(window.location.search);
    const conversationIdFromUrl = urlParams.get('conversation_id');
    const storedConversationId = getStoredConversationId();
    // const noNewFlag = urlParams.get('no_new'); // Flag check removed, backend handles loading correct convo after delete
    // 'conversationId' should be set globally by the Django template script block
    const currentConversationIdOnPage = typeof conversationId !== 'undefined' ? conversationId : null;

    console.log("Stored Conversation ID:", storedConversationId);
    console.log("Conversation ID from URL:", conversationIdFromUrl);
    console.log("Conversation ID from Template:", currentConversationIdOnPage);

    // Determine the definitive conversation ID to use, prioritizing template, then URL, then storage as fallback
    let definitiveConversationId = currentConversationIdOnPage; // 1. Trust template first

    if (!definitiveConversationId && conversationIdFromUrl) {
        // 2. If no template ID, use URL ID
        console.log("Using conversation ID from URL:", conversationIdFromUrl);
        definitiveConversationId = conversationIdFromUrl;
    } else if (!definitiveConversationId && storedConversationId) {
        // 3. If no template or URL ID, use stored ID as a fallback
        //    (e.g., navigating back to the page without specifying an ID)
        //    Avoid redirecting here; let backend handle loading the correct page.
        console.log("No ID from template/URL, using stored ID as fallback:", storedConversationId);
        definitiveConversationId = storedConversationId;
    }

    // Ensure the definitive ID is stored if it exists and differs from storage
    if (definitiveConversationId && definitiveConversationId !== storedConversationId) {
        console.log("Updating stored conversation ID to:", definitiveConversationId);
        storeConversationId(definitiveConversationId);
    }

    // Ensure the global 'conversationId' variable matches the definitive one
    if (definitiveConversationId && (typeof conversationId === 'undefined' || conversationId !== definitiveConversationId)) {
         console.warn("Global conversationId mismatch or undefined. Setting to:", definitiveConversationId);
         window.conversationId = definitiveConversationId; // Ensure global scope has the correct ID
    }

    console.log("Using definitive Conversation ID:", definitiveConversationId);

    // Define elements needed for initial setup
    const messageInput = document.querySelector('#message-input');
    const sendButton = document.querySelector('#send-button');
    const uploadFileBtn = document.querySelector('#upload-file-btn');
    const fileUploadInput = document.querySelector('#file-upload');
    const fileUploadToast = document.querySelector('#file-upload-toast');
    // 添加终止生成按钮元素
    // const stopGenerationBtn = document.querySelector('#stop-generation-btn'); // Removed
    // const stopGenerationContainer = document.querySelector('#stop-generation-container'); // Removed

    // 全局变量，用于跟踪当前是否正在生成回复
    window.isGeneratingResponse = false;
    // 全局变量，用于跟踪终止状态
    window.isTerminationInProgress = false; // Initialize flag

    // --- Initial Setup ---
    if (definitiveConversationId) {
        // Existing conversation
        initWebSocket(); // Initialize WebSocket connection (uses global conversationId)
        syncConversationData().catch(error => { // Initial data sync - Re-enabled
            console.error("Error during initial sync:", error);
        });
        // Ensure input/button are enabled for existing conversations
        if (messageInput) messageInput.disabled = false;
        if (sendButton) sendButton.disabled = false;
        if (uploadFileBtn) uploadFileBtn.disabled = false;
    } else {
        // New conversation scenario
        console.log("No conversation ID available. Ready for new conversation.");
        // Ensure input/button are enabled to allow sending the first message
        if (messageInput) messageInput.disabled = false;
        if (sendButton) sendButton.disabled = false;
        if (uploadFileBtn) uploadFileBtn.disabled = false;
        console.log("Skipping WebSocket init and sync - no conversation ID yet.");
    }

    // --- Event Listeners ---
    // Elements already defined above
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
            console.log(`Saved model preference: ${selectedId}`);
        });
    }
    // --- End Save model preference on change ---

    // --- REMOVED Listener for separate stop button ---

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
                
                // 修改发送按钮行为，使其同时发送消息和图片
                const originalSendBtnClick = sendButton.onclick;
                sendButton.onclick = function() {
                    const message = messageInput.value.trim();
                    const modelId = modelSelect.value;
                    
                    // 如果没有消息但有图片，添加默认消息
                    if (!message) {
                        messageInput.value = "请描述这张图片";
                    }
                    
                    // 创建FormData对象
                    const formData = new FormData();
                    formData.append('file', file);
                    formData.append('model_id', modelId);
                    formData.append('message', messageInput.value);
                    
                    if (window.conversationId) {
                        formData.append('conversation_id', window.conversationId);
                    }
                    
                    // 禁用输入控件
                    if (messageInput) messageInput.disabled = true;
                    if (sendButton) sendButton.disabled = true;
                    if (uploadFileBtn) uploadFileBtn.disabled = true;
                    
                    // 显示加载指示器
                    const userMessageDiv = document.createElement('div');
                    userMessageDiv.className = 'alert alert-primary';
                    userMessageDiv.innerHTML = `
                        <div class="d-flex justify-content-between">
                            <span>您</span>
                            <div>
                                <small>${new Date().toLocaleTimeString()}</small>
                            </div>
                        </div>
                        <hr>
                        <p>${escapeHtml(messageInput.value)}</p>
                        <div class="text-center">
                            <img src="${URL.createObjectURL(file)}" class="img-fluid mt-2" style="max-height: 150px; max-width: 150px;" alt="${file.name}">
                        </div>
                        <div class="text-center mt-2">
                            <div class="spinner-border spinner-border-sm" role="status">
                                <span class="visually-hidden">加载中...</span>
                            </div>
                            <span class="ms-2">正在处理，请稍候...</span>
                        </div>
                    `;
                    
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
                        
                        // 清除文件选择和预览
                        fileUploadInput.value = '';
                        previewContainer.remove();
                        
                        // 保存原始消息内容
                        const originalMessage = messageInput.value || '请描述这张图片';
                        
                        // 清空输入框
                        messageInput.value = '';
                        
                        // 恢复原始发送按钮功能
                        sendButton.onclick = originalSendBtnClick;
                        
                        console.log("文件上传API响应:", data);
                        
                        if (data.success) {
                            // 更新上传消息
                            userMessageDiv.innerHTML = `
                                <div class="d-flex justify-content-between">
                                    <span>您</span>
                                    <div>
                                        <small>${new Date().toLocaleTimeString()}</small>
                                    </div>
                                </div>
                                <hr>
                                <p>${escapeHtml(originalMessage)}</p>
                                <div class="text-center">
                                    <img src="${URL.createObjectURL(file)}" class="img-fluid mt-2" style="max-height: 150px; max-width: 150px;" alt="${file.name}">
                                </div>
                                <div class="mt-2 text-muted small">
                                    <i class="bi bi-info-circle"></i> 
                                    提示：如果AI无法"看到"图片内容，可能是因为当前API不支持图片功能。
                                    您可以尝试使用API原生的图片上传界面。
                                </div>
                            `;
                            
                            // 如果创建了新对话，更新对话ID
                            if (data.new_conversation_id) {
                                window.conversationId = data.new_conversation_id;
                                storeConversationId(window.conversationId);
                                // 更新URL但不重新加载页面
                                const newUrl = `/chat/?conversation_id=${window.conversationId}`;
                                window.history.pushState({ path: newUrl }, '', newUrl);
                                // 刷新侧边栏对话列表
                                refreshConversationList();
                            }
                            
                            // 显示AI响应
                            if (data.ai_response) {
                                console.log("收到AI回复:", data.ai_response);
                                
                                const aiMessageDiv = document.createElement('div');
                                aiMessageDiv.className = 'alert alert-secondary';
                                aiMessageDiv.innerHTML = `
                                    <div class="d-flex justify-content-between">
                                        <span>助手</span>
                                        <div>
                                            <small>${new Date().toLocaleTimeString()}</small>
                                        </div>
                                    </div>
                                    <hr>
                                    <p><span class="render-target" data-original-content="${escapeHtml(data.ai_response)}">${escapeHtml(data.ai_response)}</span></p>
                                `;
                                
                                if (messageContainer) {
                                    messageContainer.appendChild(aiMessageDiv);
                                    renderMessageContent(aiMessageDiv);
                                    aiMessageDiv.scrollIntoView();
                                }
                            } else {
                                console.warn("API返回成功但没有AI回复内容");
                            }
                        } else {
                            // 显示上传失败消息
                            userMessageDiv.innerHTML = `
                                <div class="d-flex justify-content-between">
                                    <span>您</span>
                                    <div>
                                        <small>${new Date().toLocaleTimeString()}</small>
                                    </div>
                                </div>
                                <hr>
                                <p>${escapeHtml(originalMessage)}</p>
                                <div class="text-center">
                                    <img src="${URL.createObjectURL(file)}" class="img-fluid mt-2" style="max-height: 150px; max-width: 150px;" alt="${file.name}">
                                </div>
                                <p class="text-danger mt-2">图片上传失败: ${data.message || '该模型不支持图片上传'}</p>
                            `;
                            
                            // 显示Toast提示
                            const toastBody = document.querySelector('#file-upload-toast .toast-body');
                            if (toastBody) {
                                toastBody.textContent = data.message || '该模型不支持图片上传';
                            }
                            const toast = new bootstrap.Toast(fileUploadToast);
                            toast.show();
                        }
                    })
                    .catch(error => {
                        console.error('上传文件时出错:', error);
                        
                        // 重新启用输入控件
                        if (messageInput) messageInput.disabled = false;
                        if (sendButton) sendButton.disabled = false;
                        if (uploadFileBtn) uploadFileBtn.disabled = false;
                        
                        // 清除文件选择和预览
                        fileUploadInput.value = '';
                        previewContainer.remove();
                        
                        // 恢复原始发送按钮功能
                        sendButton.onclick = originalSendBtnClick;
                        
                        // 显示错误消息
                        userMessageDiv.innerHTML = `
                            <div class="d-flex justify-content-between">
                                <span>您</span>
                                <div>
                                    <small>${new Date().toLocaleTimeString()}</small>
                                </div>
                            </div>
                            <hr>
                            <p>${escapeHtml(messageInput.value || '请描述这张图片')}</p>
                            <div class="text-center">
                                <img src="${URL.createObjectURL(file)}" class="img-fluid mt-2" style="max-height: 150px; max-width: 150px;" alt="${file.name}">
                            </div>
                            <p class="text-danger mt-2">图片上传失败: ${error.message}</p>
                        `;
                    });
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
                
                // 修改发送按钮行为，使其同时发送消息和图片
                const originalSendBtnClick = sendButton.onclick;
                sendButton.onclick = function() {
                    const message = messageInput.value.trim();
                    const modelId = modelSelect.value;
                    
                    // 如果没有消息但有图片，添加默认消息
                    if (!message) {
                        messageInput.value = "请描述这张图片";
                    }
                    
                    // 创建FormData对象
                    const formData = new FormData();
                    formData.append('file', file);
                    formData.append('model_id', modelId);
                    formData.append('message', messageInput.value);
                    
                    if (window.conversationId) {
                        formData.append('conversation_id', window.conversationId);
                    }
                    
                    // 禁用输入控件
                    if (messageInput) messageInput.disabled = true;
                    if (sendButton) sendButton.disabled = true;
                    if (uploadFileBtn) uploadFileBtn.disabled = true;
                    
                    // 显示加载指示器
                    const userMessageDiv = document.createElement('div');
                    userMessageDiv.className = 'alert alert-primary';
                    userMessageDiv.innerHTML = `
                        <div class="d-flex justify-content-between">
                            <span>您</span>
                            <div>
                                <small>${new Date().toLocaleTimeString()}</small>
                            </div>
                        </div>
                        <hr>
                        <p>${escapeHtml(messageInput.value)}</p>
                        <div class="text-center">
                            <img src="${URL.createObjectURL(file)}" class="img-fluid mt-2" style="max-height: 150px; max-width: 150px;" alt="粘贴的图片">
                        </div>
                        <div class="text-center mt-2">
                            <div class="spinner-border spinner-border-sm" role="status">
                                <span class="visually-hidden">加载中...</span>
                            </div>
                            <span class="ms-2">正在处理，请稍候...</span>
                        </div>
                    `;
                    
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
                        
                        // 清除文件选择和预览
                        previewContainer.remove();
                        
                        // 保存原始消息内容
                        const originalMessage = messageInput.value || '请描述这张图片';
                        
                        // 清空输入框
                        messageInput.value = '';
                        
                        // 恢复原始发送按钮功能
                        sendButton.onclick = originalSendBtnClick;
                        
                        console.log("文件上传API响应:", data);
                        
                        if (data.success) {
                            // 更新上传消息
                            userMessageDiv.innerHTML = `
                                <div class="d-flex justify-content-between">
                                    <span>您</span>
                                    <div>
                                        <small>${new Date().toLocaleTimeString()}</small>
                                    </div>
                                </div>
                                <hr>
                                <p>${escapeHtml(originalMessage)}</p>
                                <div class="text-center">
                                    <img src="${URL.createObjectURL(file)}" class="img-fluid mt-2" style="max-height: 150px; max-width: 150px;" alt="粘贴的图片">
                                </div>
                                <div class="mt-2 text-muted small">
                                    <i class="bi bi-info-circle"></i> 
                                    提示：如果AI无法"看到"图片内容，可能是因为当前API不支持图片功能。
                                    您可以尝试使用API原生的图片上传界面。
                                </div>
                            `;
                            
                            // 如果创建了新对话，更新对话ID
                            if (data.new_conversation_id) {
                                window.conversationId = data.new_conversation_id;
                                storeConversationId(window.conversationId);
                                // 更新URL但不重新加载页面
                                const newUrl = `/chat/?conversation_id=${window.conversationId}`;
                                window.history.pushState({ path: newUrl }, '', newUrl);
                                // 刷新侧边栏对话列表
                                refreshConversationList();
                            }
                            
                            // 显示AI响应
                            if (data.ai_response) {
                                console.log("收到AI回复:", data.ai_response);
                                
                                const aiMessageDiv = document.createElement('div');
                                aiMessageDiv.className = 'alert alert-secondary';
                                aiMessageDiv.innerHTML = `
                                    <div class="d-flex justify-content-between">
                                        <span>助手</span>
                                        <div>
                                            <small>${new Date().toLocaleTimeString()}</small>
                                        </div>
                                    </div>
                                    <hr>
                                    <p><span class="render-target" data-original-content="${escapeHtml(data.ai_response)}">${escapeHtml(data.ai_response)}</span></p>
                                `;
                                
                                if (messageContainer) {
                                    messageContainer.appendChild(aiMessageDiv);
                                    renderMessageContent(aiMessageDiv);
                                    aiMessageDiv.scrollIntoView();
                                }
                            } else {
                                console.warn("API返回成功但没有AI回复内容");
                            }
                        } else {
                            // 显示上传失败消息
                            userMessageDiv.innerHTML = `
                                <div class="d-flex justify-content-between">
                                    <span>您</span>
                                    <div>
                                        <small>${new Date().toLocaleTimeString()}</small>
                                    </div>
                                </div>
                                <hr>
                                <p>${escapeHtml(originalMessage)}</p>
                                <div class="text-center">
                                    <img src="${URL.createObjectURL(file)}" class="img-fluid mt-2" style="max-height: 150px; max-width: 150px;" alt="粘贴的图片">
                                </div>
                                <p class="text-danger mt-2">图片上传失败: ${data.message || '该模型不支持图片上传'}</p>
                            `;
                            
                            // 显示Toast提示
                            const toastBody = document.querySelector('#file-upload-toast .toast-body');
                            if (toastBody) {
                                toastBody.textContent = data.message || '该模型不支持图片上传';
                            }
                            const toast = new bootstrap.Toast(fileUploadToast);
                            toast.show();
                        }
                    })
                    .catch(error => {
                        console.error('上传文件时出错:', error);
                        
                        // 重新启用输入控件
                        if (messageInput) messageInput.disabled = false;
                        if (sendButton) sendButton.disabled = false;
                        if (uploadFileBtn) uploadFileBtn.disabled = false;
                        
                        // 清除文件选择和预览
                        previewContainer.remove();
                        
                        // 恢复原始发送按钮功能
                        sendButton.onclick = originalSendBtnClick;
                        
                        // 显示错误消息
                        userMessageDiv.innerHTML = `
                            <div class="d-flex justify-content-between">
                                <span>您</span>
                                <div>
                                    <small>${new Date().toLocaleTimeString()}</small>
                                </div>
                            </div>
                            <hr>
                            <p>${escapeHtml(messageInput.value || '请描述这张图片')}</p>
                            <div class="text-center">
                                <img src="${URL.createObjectURL(file)}" class="img-fluid mt-2" style="max-height: 150px; max-width: 150px;" alt="粘贴的图片">
                            </div>
                            <p class="text-danger mt-2">图片上传失败: ${error.message}</p>
                        `;
                    });
                };
            }
        });
    }

    // Send / Stop Button Click Logic
    if (sendButton && messageInput && modelSelect) {
        // let stopButtonClickTimeout = null; // REMOVED Debounce timeout variable

        sendButton.onclick = function() {
            // Check if the button is in 'stop' mode
            if (sendButton.classList.contains('stop-mode')) {
                // --- Stop Generation Logic ---
                console.log("[Stop Click] Stop button clicked.");

                // --- Simplified Debounce: Check flags and disabled state ---
                if (sendButton.disabled || window.isTerminationInProgress) {
                    console.log(`[Stop Click] Ignoring click. Disabled: ${sendButton.disabled}, Termination in progress: ${window.isTerminationInProgress}`);
                    return; // Ignore if already disabled or termination started
                }
                // --- End Simplified Debounce ---

                // Disable button IMMEDIATELY and set termination flag
                sendButton.disabled = true;
                window.isTerminationInProgress = true;
                console.log("[Stop Click] Button disabled, isTerminationInProgress set to true.");

                // --- Get user message ID to pass to termination display ---
                let userMessageIdForTermination = null;
                // Find the *last* loading indicator (most likely the current one)
                const indicators = document.querySelectorAll('[id^="ai-response-loading"]');
                const currentIndicator = indicators.length > 0 ? indicators[indicators.length - 1] : null;

                if (currentIndicator) {
                     const indicatorId = currentIndicator.id;
                     // Try to extract userMessageId from unique ID pattern
                     const match = indicatorId.match(/ai-response-loading-(.+)/);
                     if (match && match[1]) {
                         userMessageIdForTermination = match[1];
                         console.log(`[Stop Click] Found user ID from indicator ID: ${userMessageIdForTermination}`);
                     } else {
                         console.warn(`[Stop Click] Could not parse user ID from indicator ID: ${indicatorId}. Falling back.`);
                         // Fallback: Find preceding user message div relative to the found indicator
                         let associatedUserDiv = currentIndicator.previousElementSibling;
                         while (associatedUserDiv && !associatedUserDiv.classList.contains('alert-primary')) {
                             associatedUserDiv = associatedUserDiv.previousElementSibling;
                         }
                         if (associatedUserDiv) {
                             userMessageIdForTermination = associatedUserDiv.getAttribute('data-message-id') || associatedUserDiv.getAttribute('data-temp-id');
                             console.log(`[Stop Click] Found user ID from preceding element: ${userMessageIdForTermination}`);
                         }
                     }
                }
                if (!userMessageIdForTermination) {
                     console.warn("[Stop Click] Could not determine userMessageId for displayTerminationMessage.");
                     // Attempt to find the *last* user message as a final fallback
                     const userMessages = document.querySelectorAll('.alert.alert-primary');
                     if (userMessages.length > 0) {
                         const lastUserMsg = userMessages[userMessages.length - 1];
                         userMessageIdForTermination = lastUserMsg.getAttribute('data-message-id') || lastUserMsg.getAttribute('data-temp-id');
                         console.log(`[Stop Click] Using last user message ID as fallback: ${userMessageIdForTermination}`);
                     }
                }
                // --- End getting user message ID ---

                // 立即显示终止提示 (Pass the ID)
                displayTerminationMessage(userMessageIdForTermination);

                // 发送终止请求
                sendStopGenerationRequest(); // This function now also sets terminationRequestSent

                // --- Mark the corresponding user message as having a stop requested ---
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
                    activeRegenBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
                } else {
                     console.log("[Stop Click] No active regenerate button found to reset immediately.");
                }
                // --- End immediate reset logic ---

                // 隐藏/恢复按钮状态 (hideStopGenerationButton handles this)
                // This resets the main Send/Stop button and global flags (isGeneratingResponse, isTerminationInProgress)
                hideStopGenerationButton();

            } else {
                // --- Send Message Logic --- (Keep this part unchanged)
                const message = messageInput.value.trim();
                const modelId = modelSelect.value;
                const isNewConversation = !window.conversationId; // Check if it's a new conversation BEFORE sending

                // Allow sending if message is not empty AND (it's an existing conversation OR it's the first message of a new one)
                if (message !== '' && (window.conversationId || isNewConversation)) {
                    console.log("Send button clicked. Message:", message, "Model:", modelId, "Is New:", isNewConversation);

                    // If it's a new conversation, enable input/button temporarily if they were disabled
                    if (isNewConversation) {
                        if (messageInput) messageInput.disabled = false;
                        if (sendButton) sendButton.disabled = false;
                    } // <-- Closing brace for if(isNewConversation)

                    // --- Revised Order ---
                    // 1. Mark generation starting
                    window.isGeneratingResponse = true;
                    // 2. Switch button to STOP mode (handles enabling/disabling based on termination state)
                    showStopGenerationButton();
                    // 3. Disable the input field
                    messageInput.disabled = true;
                    // --- End Revised Order ---
                    // Redundant button disabling/styling removed as showStopGenerationButton handles it

                    // 生成临时ID
                    const tempId = 'temp-' + Date.now();

                // 使用组件工厂创建用户消息
                const userMessageDiv = window.MessageFactory.createUserMessage(tempId, message);
                
                // 添加到消息容器
                if (messageContainer) {
                    messageContainer.appendChild(userMessageDiv);
                    renderMessageContent(userMessageDiv); // 渲染用户消息
                    userMessageDiv.scrollIntoView();
                }

                // 添加AI加载指示器
                const loadingDiv = window.MessageFactory.createLoadingIndicator();
                if (messageContainer) {
                    messageContainer.appendChild(loadingDiv);
                    loadingDiv.scrollIntoView();
                }

                // 尝试通过WebSocket发送
                const sentViaWebSocket = sendWebSocketMessage(message, modelId, tempId);

                if (!sentViaWebSocket) {
                    console.log("WebSocket不可用，回退到HTTP API发送");
                    // 回退到HTTP API
                    fetch('/chat/api/chat/', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': getCookie('csrftoken')
                        },
                        body: JSON.stringify({
                            'message': message,
                            'model_id': modelId,
                            // Send null or omit conversation_id if it's a new conversation
                            'conversation_id': isNewConversation ? null : window.conversationId,
                            'temp_id': tempId
                        })
                    })
                    .then(response => {
                        if (!response.ok) {
                            return response.text().then(text => { throw new Error(`HTTP error ${response.status}: ${text}`); });
                        }
                        return response.json();
                    })
                    .then(data => {
                        // 更新用户消息ID
                        if (data.user_message_id) {
                            tempIdMap[tempId] = data.user_message_id;
                            console.log(`API响应: 更新tempIdMap: ${tempId} -> ${data.user_message_id}`);
                            
                            // 更新DOM元素ID
                            const msgDiv = messageContainer?.querySelector(`.alert[data-temp-id="${tempId}"]`);
                            if (msgDiv) {
                                msgDiv.setAttribute('data-message-id', data.user_message_id);
                                msgDiv.removeAttribute('data-waiting-id');
                                console.log(`API响应: 更新DOM用户消息ID`);
                                
                                // 更新状态管理器中的消息ID
                                const messageState = window.ChatState.getMessage(tempId);
                                if (messageState) {
                                    // 注册新ID的消息状态
                                    window.ChatState.registerMessage(
                                        data.user_message_id, 
                                        messageState.content, 
                                        messageState.isUser, 
                                        msgDiv
                                    );
                                    // 移除临时ID的消息状态
                                    window.ChatState.removeMessage(tempId);
                                    console.log(`状态管理器: ${tempId} -> ${data.user_message_id}`);
                                }
                            }
                        }

                        // 处理新对话创建
                        if (isNewConversation && data.success && data.new_conversation_id) {
                            console.log("通过API创建了新对话。新ID:", data.new_conversation_id);
                            window.conversationId = data.new_conversation_id;
                            storeConversationId(window.conversationId);

                            // 更新URL但不重新加载页面
                            const newUrl = `/chat/?conversation_id=${window.conversationId}`;
                            history.pushState({ conversationId: window.conversationId }, '', newUrl);
                            console.log("URL已更新为:", newUrl);

                            // 刷新侧边栏对话列表
                            refreshConversationList();

                            // 使用新ID初始化WebSocket
                            initWebSocket();
                        }

                        // 移除加载指示器
                        loadingDiv.remove();

                        if (data.success) {
                            // 使用组件工厂创建AI消息
                            const aiMessageDiv = window.MessageFactory.createAIMessage(data.message_id, data.message);
                            if (messageContainer) {
                                messageContainer.appendChild(aiMessageDiv);
                                renderMessageContent(aiMessageDiv);
                                aiMessageDiv.scrollIntoView();
                            }
                            
                            // 隐藏终止按钮，标记生成完成
                            hideStopGenerationButton();
                            window.isGeneratingResponse = false;
                        } else {
                            console.error("API错误:", data.message);
                            // 如果创建新对话失败，重置conversationId
                            if (isNewConversation) {
                                window.conversationId = null;
                            }
                            
                            // 显示错误消息
                            const errorDiv = window.MessageFactory.createErrorMessage(`处理AI回复出错: ${data.message}`);
                            if (messageContainer) {
                                messageContainer.appendChild(errorDiv);
                                errorDiv.scrollIntoView();
                            }
                            
                            // 隐藏终止按钮，标记生成完成
                            hideStopGenerationButton();
                            window.isGeneratingResponse = false;
                        }
                    })
                    .catch(error => {
                        console.error('通过API发送消息时出错:', error);
                        loadingDiv.remove();
                        
                        // 显示错误消息
                        const errorDiv = window.MessageFactory.createErrorMessage(`发送消息失败: ${error.message}`);
                        if (messageContainer) {
                            messageContainer.appendChild(errorDiv);
                            errorDiv.scrollIntoView();
                        }
                        
                        // 隐藏终止按钮，标记生成完成
                        hideStopGenerationButton();
                        window.isGeneratingResponse = false;
                    })
                    .finally(() => {
                        // 无论API成功或失败都重新启用输入
                        messageInput.value = '';
                        messageInput.disabled = false;
                        messageInput.focus();
                        // Button state is handled by hideStopGenerationButton called in .then/.catch
                    });
                } else {
                    // 如果通过WebSocket发送，立即重新启用UI (Input only, button state managed by show/hide)
                    messageInput.value = '';
                    messageInput.disabled = false;
                    // sendButton state is handled by show/hideStopGenerationButton
                    messageInput.focus();
                } // <-- Closing brace for if (!sentViaWebSocket)
            } // <-- **ADDED Missing closing brace for the main message sending logic block: if (message !== '' && (window.conversationId || isNewConversation))**
            else if (message === '') { // Now this else if correctly follows the closed 'if' block
                // 处理空消息情况
                console.log("消息输入为空");
            } else if (!window.conversationId && !isNewConversation) {
                // 这种情况理论上不应该发生
                console.error("状态不一致: 没有对话ID且未标记为新对话");
                alert("出现错误，请刷新页面");
            }
        } // <-- **ADDED Missing closing brace for the 'else' block (Send Message Logic)**
    }; // Closes sendButton.onclick = function()
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
                        console.log("使用状态管理器更新消息");
                        
                        // 保存编辑到服务器并在成功后更新状态
                        saveMessageToServer(editInfo.id, newContent, editInfo.isUser)
                            .then(success => {
                                if (success) {
                                    // 更新状态，触发DOM更新
                                    window.ChatState.updateMessage(editInfo.id, newContent);
                                    
                                    // 如果是用户消息，触发重新生成
                                    if (editInfo.isUser) {
                                        setTimeout(() => {
                                            regenerateResponse(editInfo.id);
                                        }, 100);
                                    }
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
                    : messageDiv.previousElementSibling?.closest('.alert.alert-primary'); // Find preceding user message

                if (userMessageDiv) {
                    const userId = userMessageDiv.getAttribute('data-message-id') || userMessageDiv.getAttribute('data-temp-id');
                    if (userId) {
                         // Add visual feedback
                         const regenBtn = target.closest('.regenerate-btn');
                         regenBtn.classList.add('btn-processing');
                         regenBtn.disabled = true;
                         regenBtn.innerHTML = '<i class="bi bi-arrow-clockwise animate-spin"></i>';
                         // Call regenerate function (will restore button in finally block)
                         regenerateResponse(userId); // Use function from api_handler
                    } else {
                         console.error("Could not find ID for user message to regenerate.");
                    }
                } else {
                     console.error("Could not find corresponding user message for regeneration.");
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
            // Fallback or alternative: Check for a data attribute if href fails
            // if (!convId) {
            //     convId = conversationLink.getAttribute('data-conversation-id');
            // }

            if (!convId) {
                 console.error("Could not extract conversation ID from link:", href);
                 return; // Stop if ID couldn't be extracted
            }

            // Delete Conversation Button
            const deleteBtn = target.closest('.delete-conversation-btn'); // Get the button element
            if (deleteBtn) { // Check if the click was on the delete button or its icon
                e.preventDefault(); // Prevent navigation
                e.stopPropagation(); // Stop event bubbling

                // --- Disable button immediately to prevent double trigger ---
                deleteBtn.disabled = true;
                // Optional: Add visual feedback (e.g., spinner)
                // deleteBtn.innerHTML = '<i class="bi bi-hourglass-split"></i>';

                // --- Add detailed logging ---
                const clickTimestamp = Date.now();
                console.log(`[${clickTimestamp}] Delete button handler triggered for conversation ID: ${convId}`);
                console.log(`[${clickTimestamp}] Calling deleteConversation(${convId})...`);
                // --- End added logging ---

                // Call the delete function. Since success leads to redirect,
                // we don't strictly need to re-enable the button here.
                // If the API call fails AND doesn't redirect, the button remains disabled,
                // which might be acceptable or could be handled inside deleteConversation's catch block if needed.
                deleteConversation(convId); // Use function from api_handler
            }

            // Edit Conversation Title Button/Icon
            else if (target.closest('.edit-conversation-btn')) {
                e.preventDefault(); // Prevent navigation
                e.stopPropagation(); // Stop event bubbling
                console.log("Edit button clicked for conversation ID:", convId); // Add log
                editConversationTitle(convId); // Use function from api_handler
            }
            // Note: If the click was on the link itself (not a button),
            // the default navigation will happen unless prevented earlier.
        });
    }

    // Initial render attempt for any existing messages loaded by the template
    if (messageContainer) {
        console.log("Initial Render: Starting loop for existing messages.");
        const existingTargets = messageContainer.querySelectorAll('.alert .render-target');
        console.log(`Initial Render: Found ${existingTargets.length} potential render targets.`);
        existingTargets.forEach((target, index) => {
            const messageDiv = target.closest('.alert');
            const messageId = messageDiv ? messageDiv.getAttribute('data-message-id') : 'unknown';
            console.log(`Initial Render: Processing target ${index + 1} for message ID ${messageId}. Has 'data-rendered': ${target.hasAttribute('data-rendered')}`);
            if (messageDiv && !target.hasAttribute('data-rendered')) { // Only render if not already marked
                console.log(`Initial Render: Calling renderMessageContent for message ID ${messageId}.`);
                renderMessageContent(messageDiv);
            } else if (!messageDiv) {
                 console.warn(`Initial Render: Target ${index + 1} has no parent .alert element.`);
            }
        });
        console.log("Initial Render: Finished loop.");
        // Initial MathJax typesetting for the whole container
        if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
            console.log("DOMContentLoaded: Initial MathJax typesetting promise call.");
            MathJax.typesetPromise([messageContainer]).catch((err) => console.error("Initial MathJax typesetting failed:", err));
        }
    }

    console.log("Chat Page Initialized.");
});

// --- Conversation List Refresh Function ---
// Fetches the updated conversation list HTML from the server and updates the sidebar.
// Assumes a backend endpoint '/chat/conversation_list/' exists and returns the rendered HTML.
function refreshConversationList() {
    console.log("Refreshing conversation list...");
    const conversationListContainer = document.querySelector('#conversation-list');
    if (!conversationListContainer) {
        console.error("Conversation list container not found.");
        return;
    }

    // Add a temporary loading indicator? (Optional)
    // conversationListContainer.innerHTML = '<div class="text-center p-2"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...</div>';

    fetch('/chat/conversation_list/') // Assumed endpoint - Needs to be created in backend
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error ${response.status}`);
            }
            return response.text(); // Get HTML content
        })
        .then(html => {
            conversationListContainer.innerHTML = html;
            console.log("Conversation list refreshed successfully.");
            // Highlight the current conversation after refresh
            highlightCurrentConversation();
        })
        .catch(error => {
            console.error('Error refreshing conversation list:', error);
            // Optionally display an error message in the container
            conversationListContainer.innerHTML = '<div class="alert alert-warning p-2">无法加载对话列表。</div>';
        });
}

// Helper function to highlight the active conversation in the refreshed list
function highlightCurrentConversation() {
    const currentId = window.conversationId; // Use the global ID
    if (!currentId) return;

    const conversationList = document.querySelector('#conversation-list');
    if (!conversationList) return;

    // Remove active class from any currently active item
    const activeItem = conversationList.querySelector('a.list-group-item.active');
    if (activeItem) {
        activeItem.classList.remove('active');
    }

    // Add active class to the new current item
    // Use attribute selector for robustness against URL changes
    const newItem = conversationList.querySelector(`a.list-group-item[href*="conversation_id=${currentId}"]`);
    if (newItem) {
        newItem.classList.add('active');
        console.log(`Highlighted conversation ${currentId} in the list.`);
    } else {
        console.warn(`Could not find conversation ${currentId} in the refreshed list to highlight.`);
    }
}
// Make refresh function globally accessible for WebSocket handler (simplest approach without modules)
// This allows websocket_handler.js to call it if backend sends a specific message
window.refreshConversationList = refreshConversationList;


// Function to check for temporary IDs (might be needed if sync fails)
// Consider moving this back here or ensuring sync handles all cases
// function checkForTemporaryIds() {
//     console.log("Checking for temporary IDs...");
//     const messageContainer = document.querySelector('#message-container');
//     if (!messageContainer) return;
//     let foundTemp = false;
//     messageContainer.querySelectorAll('.alert[data-waiting-id="1"]').forEach(msgDiv => {
//         const tempId = msgDiv.getAttribute('data-temp-id');
//         console.log("Found message waiting for ID:", tempId);
//         foundTemp = true;
//     });

//     if (foundTemp) {
//         console.log("Found messages waiting for IDs, attempting sync...");
//         syncConversationData(); // Trigger sync if temp IDs are waiting
//     }
// }

// 完全重建消息元素
function rebuildMessageElement(messageDiv, messageId, content, isUser) {
    console.log(`Rebuilding message element for ID: ${messageId}`);
    
    // 保留原始CSS类和属性
    const alertClass = isUser ? 'alert-primary' : 'alert-secondary';
    
    // 清空元素
    messageDiv.innerHTML = '';
    
    // 设置必要的类和属性
    messageDiv.className = `alert ${alertClass} mb-3`;
    messageDiv.setAttribute('data-message-id', messageId);
    
    // 创建消息头部（用户/助手标识、时间戳和按钮）
    const timestamp = new Date().toLocaleTimeString();
    let buttonsHTML = '';
    
    if (isUser) {
        buttonsHTML = `
            <button class="btn btn-sm btn-outline-primary edit-message-btn ms-2" title="编辑消息">
                <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-secondary regenerate-btn ms-2" title="重新生成回复">
                <i class="bi bi-arrow-clockwise"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger delete-message-btn ms-2" title="删除消息">
                <i class="bi bi-trash"></i>
            </button>
        `;
    } else {
        buttonsHTML = `
            <button class="btn btn-sm btn-outline-danger delete-message-btn ms-2" title="删除消息">
                <i class="bi bi-trash"></i>
            </button>
        `;
    }
    
    // 构建HTML结构
    messageDiv.innerHTML = `
        <div class="d-flex justify-content-between">
            <span>${isUser ? '您' : '助手'}</span>
            <div>
                <small>${timestamp}</small>
                ${buttonsHTML}
            </div>
        </div>
        <hr>
        <p><span class="render-target" data-original-content="${escapeHtml(content)}"></span></p>
    `;
    
    // 触发渲染
    console.log("Triggering render of rebuilt message");
    setTimeout(() => {
        renderMessageContent(messageDiv);
        
        // 确保MathJax在渲染后处理
        setTimeout(() => {
            if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                console.log("Force typesetting after rebuild");
                MathJax.typesetPromise([messageDiv]).catch(err => 
                    console.error("MathJax error in rebuild:", err)
                );
            }
        }, 100);
    }, 0);
    
    return true;
}

// 完成消息编辑
function completeMessageEdit(messageId, newContent, messageDiv, isUser) {
    console.log(`Completing edit for message ${messageId}`);
    
    // 首先重建元素显示新内容
    rebuildMessageElement(messageDiv, messageId, newContent, isUser);
    
    // 如果是临时ID，无需保存到服务器
    if (messageId.startsWith('temp-')) {
        console.log("Temporary message edited locally only");
        return;
    }
    
    // 将编辑发送到服务器
    const realId = getRealMessageId(messageId) || messageId;
    console.log(`Sending edit to server for ID: ${realId}`);
    
    fetch('/chat/api/messages/edit/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({ 'message_id': realId, 'content': newContent })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log("Server confirmed edit success");
            
            // 如果是用户消息，触发重新生成
            if (isUser) {
                console.log("User message edited, triggering regeneration");
                setTimeout(() => {
                    regenerateResponse(realId);
                }, 100); // 短暂延迟确保UI先更新
            }
        } else {
            console.error("Server rejected edit:", data.message);
            alert(`更新消息失败: ${data.message}`);
        }
    })
    .catch(error => {
        console.error('Error updating message:', error);
        alert('更新消息时出错，请稍后再试');
    });
}

// 将消息保存到服务器
function saveMessageToServer(messageId, content, isUser) {
    console.log(`保存消息到服务器: ${messageId}`);
    
    // 如果是临时ID，仅在本地更新
    if (messageId.startsWith('temp-')) {
        console.log("临时消息ID，跳过服务器保存");
        return Promise.resolve(true);
    }
    
    // 获取真实ID (处理临时ID映射)
    const realId = getRealMessageId(messageId) || messageId;
    
    // 发送到服务器
    return fetch('/chat/api/messages/edit/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({ 'message_id': realId, 'content': content })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log("服务器确认消息保存成功");
            return true;
        } else {
            console.error("服务器拒绝消息保存:", data.message);
            alert(`更新消息失败: ${data.message}`);
            return false;
        }
    })
    .catch(error => {
        console.error('保存消息时出错:', error);
        alert('更新消息时出错，请稍后再试');
        return false;
    });
}

// Function to switch Send button to Stop mode
function showStopGenerationButton() {
    const sendButton = document.querySelector('#send-button');
    if (sendButton) {
        console.log("切换到停止模式");
        sendButton.classList.add('stop-mode', 'btn-danger'); // Add stop-mode and danger color
        sendButton.classList.remove('btn-primary'); // Remove primary color
        sendButton.innerHTML = '<i class="bi bi-stop-fill"></i>'; // Change icon (using stop-fill)
        sendButton.title = "终止生成"; // Update tooltip

        // 简化状态管理 - 只用一个主要标志
        window.isGeneratingResponse = true;

        // 只有在终止未进行时才启用按钮
        if (!window.isTerminationInProgress) {
            sendButton.disabled = false;
            window.terminationRequestSent = false;
        } else {
            console.log("显示停止按钮时检测到终止已在进行中，保持按钮禁用状态");
            sendButton.disabled = true;
        }

        // 禁用所有重新生成按钮，除非它们已经在处理中
        const allRegenBtns = document.querySelectorAll('.regenerate-btn');
        allRegenBtns.forEach(btn => {
            if (!btn.classList.contains('btn-processing')) {
                btn.disabled = true;
            }
        });
        console.log(`[showStopGenerationButton] 已禁用 ${allRegenBtns.length} 个重新生成按钮`);
    } else {
        console.warn("找不到发送/停止按钮");
    }
}

// Function to switch Stop button back to Send mode
function hideStopGenerationButton() {
    const sendButton = document.querySelector('#send-button');
    console.log(`[hideStopGenerationButton] 被调用. isTerminationInProgress = ${window.isTerminationInProgress}`);
    
    if (sendButton) {
        console.log("恢复到发送模式");
        sendButton.classList.remove('stop-mode', 'btn-danger');
        sendButton.classList.add('btn-primary');
        sendButton.innerHTML = '<i class="bi bi-send"></i>';
        sendButton.title = "发送消息";
        sendButton.disabled = false;

        // 简化状态管理 - 一次性重置所有状态标志
        window.isGeneratingResponse = false;
        window.terminationRequestSent = false;
        window.isTerminationInProgress = false;
        console.log("[hideStopGenerationButton] 已重置所有状态标志");

        // 清除所有用户消息上的终止请求标志
        const messageContainer = document.querySelector('#message-container');
        if (messageContainer) {
            const userMessages = messageContainer.querySelectorAll('.alert.alert-primary[data-stop-requested="true"]');
            console.log(`[hideStopGenerationButton] 找到 ${userMessages.length} 条带有data-stop-requested的用户消息。正在清除标志。`);
            userMessages.forEach(msgDiv => {
                msgDiv.removeAttribute('data-stop-requested');
                const msgId = msgDiv.getAttribute('data-message-id') || msgDiv.getAttribute('data-temp-id');
                console.log(`  - 已清除消息 ${msgId} 的标志`);
            });
        }

        // 启用所有非处理中的重新生成按钮
        const allRegenBtns = document.querySelectorAll('.regenerate-btn');
        allRegenBtns.forEach(btn => {
            if (!btn.classList.contains('btn-processing')) {
                btn.disabled = false;
            }
        });
        console.log(`[hideStopGenerationButton] 已启用 ${allRegenBtns.length} 个重新生成按钮（不包括仍在处理的按钮）`);
    } else {
        console.warn("找不到发送/停止按钮");
    }
}

// 添加发送终止生成请求的函数
function sendStopGenerationRequest() {
    if (!window.conversationId) {
        console.warn("没有有效的对话ID，无法发送终止请求");
        return;
    }
    
    // 检查是否已经发送过终止请求
    if (window.terminationRequestSent) {
        console.log("已经发送过终止请求，不再重复发送");
        return;
    }
    
    // 标记已经发送过终止请求和正在处理终止请求
    window.terminationRequestSent = true;
    window.isTerminationInProgress = true;
    
    console.log(`正在发送终止生成请求，对话ID: ${window.conversationId}`);
    
    // 立即清除所有加载指示器
    const allLoadingIndicators = document.querySelectorAll('[id^="ai-response-loading-"]');
    if (allLoadingIndicators.length > 0) {
        console.log(`立即清除 ${allLoadingIndicators.length} 个加载指示器`);
        allLoadingIndicators.forEach(indicator => {
            // 将加载指示器替换为终止消息
            const indicatorId = indicator.id;
            const match = indicatorId.match(/ai-response-loading-(.+)/);
            if (match && match[1]) {
                const userMessageId = match[1];
                displayTerminationMessage(userMessageId);
            } else {
                indicator.remove();
            }
        });
    }
    
    // 检查WebSocket连接
    let websocketSent = false;
    if (typeof window.chatSocket !== 'undefined' && window.chatSocket && window.chatSocket.readyState === WebSocket.OPEN) {
        console.log("通过WebSocket发送终止生成请求");
        try {
            window.chatSocket.send(JSON.stringify({
                'type': 'stop_generation'
            }));
            websocketSent = true;
            console.log("WebSocket终止请求已发送");
        } catch (error) {
            console.error("通过WebSocket发送终止请求时出错:", error);
        }
    } else {
        console.warn("WebSocket不可用或未连接，无法通过WebSocket发送终止请求");
        if (typeof window.chatSocket === 'undefined') {
            console.warn("  原因: chatSocket未定义");
        } else if (!window.chatSocket) {
            console.warn("  原因: chatSocket为null");
        } else {
            console.warn(`  原因: chatSocket状态为 ${window.chatSocket.readyState} (OPEN=${WebSocket.OPEN})`);
        }
    }
    
    // 始终通过HTTP API发送请求作为备份
    fetch('/chat/api/stop_generation/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({
            'conversation_id': window.conversationId
        })
    })
    .then(response => {
        if (!response.ok) {
            throw new Error(`HTTP错误: ${response.status}`);
        }
        return response.json();
    })
    .then(data => {
        console.log("终止生成请求响应:", data);
        if (!data.success) {
            console.warn("终止生成请求失败:", data.message);
        } else {
            console.log("终止生成请求成功");
        }
    })
    .catch(error => {
        console.error("发送终止生成请求时出错:", error);
    });
}

// 显示终止消息提示 (Now accepts userMessageId)
function displayTerminationMessage(userMessageId) {
    // Construct the unique ID to find the correct loading indicator
    const uniqueIndicatorId = userMessageId ? `ai-response-loading-${userMessageId}` : null;
    console.log(`[displayTerminationMessage] Looking for indicator with ID: ${uniqueIndicatorId}`);

    let loadingMessage = null;
    if (uniqueIndicatorId) {
        loadingMessage = document.getElementById(uniqueIndicatorId);
    }

    // If specific ID wasn't found OR no ID was provided, try the generic fallback selector
    if (!loadingMessage) {
         console.warn(`[displayTerminationMessage] Indicator with specific ID "${uniqueIndicatorId}" not found or ID not provided. Trying fallback selector '[id^="ai-response-loading-"]'.`);
         loadingMessage = document.querySelector('[id^="ai-response-loading-"]'); // Find *any* loading indicator starting with the prefix
         if (loadingMessage) {
             console.log(`[displayTerminationMessage] Found indicator using fallback selector: ${loadingMessage.id}`);
         } else {
             // Log an error if no indicator is found at all
             console.error(`[displayTerminationMessage] Failed to find any loading indicator using specific ID "${uniqueIndicatorId}" or fallback selector.`);
         }
    } else {
         // Log success if found by specific ID
         console.log(`[displayTerminationMessage] Found indicator using specific ID: ${loadingMessage.id}`);
    }


    if (loadingMessage) {
        const foundId = loadingMessage.id; // Get the actual ID found
        console.log(`[displayTerminationMessage] Replacing indicator ${foundId} with termination message.`);
        // 替换加载指示器为终止消息
        // Keep a unique ID for the termination message too, using the original userMessageId if available
        loadingMessage.id = `ai-response-terminated-${userMessageId || 'unknown'}`;
        loadingMessage.innerHTML = `
            <div class="d-flex justify-content-between">
                <span>系统</span>
                <small>${new Date().toLocaleTimeString()}</small>
            </div>
            <hr>
            <p>生成已被用户终止</p>
        `;
        loadingMessage.className = 'alert alert-warning';
        
        // 5秒后自动消失
        setTimeout(() => {
            if (loadingMessage.parentNode) {
                loadingMessage.style.opacity = '0';
                loadingMessage.style.transition = 'opacity 0.5s ease';
                setTimeout(() => {
                    if (loadingMessage.parentNode) {
                        loadingMessage.remove();
                    }
                }, 500);
            }
        }, 5000);
    } else {
        // 如果找不到加载消息，创建一个新的终止消息
        const messageContainer = document.querySelector('#message-container');
        if (messageContainer) {
            const terminationDiv = document.createElement('div');
            terminationDiv.className = 'alert alert-warning';
            terminationDiv.id = 'ai-response-terminated';
            terminationDiv.innerHTML = `
                <div class="d-flex justify-content-between">
                    <span>系统</span>
                    <small>${new Date().toLocaleTimeString()}</small>
                </div>
                <hr>
                <p>生成已被用户终止</p>
            `;
            messageContainer.appendChild(terminationDiv);
            terminationDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            
            // 5秒后自动消失
            setTimeout(() => {
                if (terminationDiv.parentNode) {
                    terminationDiv.style.opacity = '0';
                    terminationDiv.style.transition = 'opacity 0.5s ease';
                    setTimeout(() => {
                        if (terminationDiv.parentNode) {
                            terminationDiv.remove();
                        }
                    }, 500);
                }
            }, 5000);
        }
    }
}

// 将displayTerminationMessage设为全局函数
window.displayTerminationMessage = displayTerminationMessage;
