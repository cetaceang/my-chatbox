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
    } else {
        // New conversation scenario
        console.log("No conversation ID available. Ready for new conversation.");
        // Ensure input/button are enabled to allow sending the first message
        if (messageInput) messageInput.disabled = false;
        if (sendButton) sendButton.disabled = false;
        console.log("Skipping WebSocket init and sync - no conversation ID yet.");
    }

    // --- Event Listeners ---
    // Elements already defined above
    const modelSelect = document.querySelector('#model-select');
    const messageContainer = document.querySelector('#message-container');
    const conversationList = document.querySelector('#conversation-list');

    // Send Button Click
    if (sendButton && messageInput && modelSelect) {
        sendButton.onclick = function() {
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
                }

                // Disable input/button during processing
                messageInput.disabled = true;
                sendButton.disabled = true;
                sendButton.classList.add('btn-processing');
                sendButton.innerHTML = '<i class="bi bi-arrow-clockwise animate-spin"></i>';

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
                    })
                    .finally(() => {
                        // 无论API成功或失败都重新启用输入/按钮
                        messageInput.value = '';
                        messageInput.disabled = false;
                        sendButton.disabled = false;
                        sendButton.classList.remove('btn-processing');
                        sendButton.innerHTML = '<i class="bi bi-send"></i>';
                        messageInput.focus();
                    });
                } else {
                    // 如果通过WebSocket发送，立即重新启用UI
                    messageInput.value = '';
                    messageInput.disabled = false;
                    sendButton.disabled = false;
                    sendButton.classList.remove('btn-processing');
                    sendButton.innerHTML = '<i class="bi bi-send"></i>';
                    messageInput.focus();
                }
            } else if (message === '') {
                // 处理空消息情况
                console.log("消息输入为空");
            } else if (!window.conversationId && !isNewConversation) {
                // 这种情况理论上不应该发生
                console.error("状态不一致: 没有对话ID且未标记为新对话");
                alert("出现错误，请刷新页面");
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
