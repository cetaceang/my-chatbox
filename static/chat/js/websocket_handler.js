/* eslint-env browser */
/* globals renderMessageContent, escapeHtml, storeConversationId, tempIdMap */ // Inform linter about globals

// WebSocket connection variable (will be initialized by initWebSocket)
let chatSocket = null;

// 跟踪终止状态
let terminationConfirmSent = false;

// 确保全局变量可用
if (typeof window !== 'undefined') {
    window.chatSocket = null;
    window.terminationConfirmSent = false;
}

// 添加WebSocket状态描述函数
function getWebSocketStateText(readyState) {
    switch (readyState) {
        case WebSocket.CONNECTING:
            return "CONNECTING (0) - 连接中";
        case WebSocket.OPEN:
            return "OPEN (1) - 已连接";
        case WebSocket.CLOSING:
            return "CLOSING (2) - 关闭中";
        case WebSocket.CLOSED:
            return "CLOSED (3) - 已关闭";
        default:
            return "UNKNOWN (" + readyState + ") - 未知状态";
    }
}

// Initialize WebSocket connection
function initWebSocket() {
    // REMOVED old flag resets: window.terminationConfirmSent, terminationConfirmSent
    
    // Get conversation ID from the global scope or data attribute if available
    // This assumes 'conversationId' is set globally by the Django template in chat_page.js or chat.html
    if (typeof conversationId === 'undefined' || !conversationId) {
        console.log("No conversation ID found for WebSocket connection.");
        // Attempt to get from localStorage as a fallback, though redirect logic should handle this
        const storedId = getStoredConversationId();
        if (storedId) {
            console.log("Using stored conversation ID for WebSocket:", storedId);
            // Potentially redirect or use this ID if the main page logic allows
            // For now, just log and prevent connection attempt if no ID set by main page
            // window.location.href = `/chat/?conversation_id=${storedId}`; // Example redirect
        }
        return; // Don't try to connect without an ID
    }

    // Store the current conversation ID (redundant if already done in main page logic, but safe)
    storeConversationId(conversationId);
    console.log("WebSocket Handler: Using conversation ID:", conversationId);

    const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const wsUrl = wsProtocol + window.location.host + '/ws/chat/' + conversationId + '/';
    console.log("Attempting WebSocket connection:", wsUrl); // Log the dynamically generated URL
    
    // Close existing socket if trying to re-initialize
    if (chatSocket && chatSocket.readyState !== WebSocket.CLOSED) {
        console.log("Closing existing WebSocket connection before re-initializing.");
        chatSocket.close(1000, "Re-initializing connection"); // 1000 indicates normal closure
    }

    try {
        chatSocket = new WebSocket(wsUrl);
        
        // 确保全局变量可用
        window.chatSocket = chatSocket;
        
        console.log("WebSocket对象已创建，当前状态:", getWebSocketStateText(chatSocket.readyState));
    } catch (error) {
        console.error("创建WebSocket对象时出错:", error);
        chatSocket = null;
        window.chatSocket = null;
        return;
    }

    chatSocket.onopen = function(e) {
        console.log("WebSocket连接已建立，状态:", getWebSocketStateText(chatSocket.readyState));
    };

    chatSocket.onerror = function(e) {
        console.error("WebSocket错误:", e);
        // Add error hint, but don't show to user directly
        console.log("WebSocket连接失败，将使用HTTP API作为备用方案。");
    };

    chatSocket.onclose = function(e) {
        console.log("WebSocket连接已关闭", e.code, e.reason);
        // Add reconnection logic only if not a normal closure (code 1000)
        if (e.code !== 1000) {
            console.log("WebSocket意外关闭。5秒后尝试重新连接...");
            setTimeout(function() {
                console.log("正在尝试WebSocket重新连接...");
                initWebSocket(); // Retry connection
            }, 5000);
        }
    };

    chatSocket.onmessage = function(e) {
        console.log("Received message:", e.data);
        const data = JSON.parse(e.data);
        const messageContainer = document.querySelector('#message-container');
        if (!messageContainer) {
            console.error("Message container not found, cannot process WebSocket message.");
            return;
        }

        // Handle user message ID update
        if (data.type === 'user_message_id_update' && data.temp_id && data.user_message_id) {
            // Update tempIdMap (assuming it's globally accessible or passed in)
            tempIdMap[data.temp_id] = data.user_message_id;
            console.log(`Received user message ID update: ${data.temp_id} -> ${data.user_message_id}`);

            // Update DOM element's ID
            const userMessageDiv = messageContainer.querySelector(`.alert[data-temp-id="${data.temp_id}"]`);
            if (userMessageDiv) {
                userMessageDiv.setAttribute('data-message-id', data.user_message_id);
                userMessageDiv.removeAttribute('data-waiting-id'); // Remove waiting flag
                console.log(`Updated DOM user message ID: ${data.temp_id} -> ${data.user_message_id}`);
            } else {
                console.warn(`Could not find DOM element for temp ID: ${data.temp_id}`);
            }
             return;
         }

        // --- Handle New Conversation Creation (WebSocket) ---
        // Assumes backend sends this message type after creating a new convo via WebSocket
        if (data.type === 'new_conversation_created' && data.conversation_id) {
            console.log("New conversation created via WebSocket. New ID:", data.conversation_id);

            // Update global ID if it wasn't set (might happen in race conditions)
            if (!window.conversationId) {
                window.conversationId = data.conversation_id;
                storeConversationId(window.conversationId); // Store it
                console.log("Updated global conversationId from WebSocket message.");
            } else if (window.conversationId != data.conversation_id) {
                // This shouldn't normally happen if the connection is for the right ID, but log if it does
                console.warn(`WebSocket message for new conversation ${data.conversation_id} received, but global ID is already ${window.conversationId}.`);
                // Optionally force update global ID and URL? Or just refresh list?
                // window.conversationId = data.conversation_id;
                // storeConversationId(window.conversationId);
            }

            // Update the URL without reloading the page (if not already updated)
            const currentUrl = window.location.search;
            const expectedUrlParam = `conversation_id=${data.conversation_id}`;
            if (!currentUrl.includes(expectedUrlParam)) {
                const newUrl = `/chat/?${expectedUrlParam}`;
                history.pushState({ conversationId: data.conversation_id }, '', newUrl);
                console.log("URL updated to:", newUrl);
            }

            // Refresh the conversation list in the sidebar
            if (typeof window.refreshConversationList === 'function') {
                window.refreshConversationList();
            } else {
                console.error("refreshConversationList function not found on window object.");
            }
            return; // Message handled
        }
        // --- End New Conversation Handling ---

        // --- REMOVED Redundant generation_id handler ---
        // setGenerationId is now handled within generation_started handler

        // Handle generation stopped message
        if (data.type === 'generation_stopped') {
            console.log("收到生成终止确认");

            // Use StateManager to check if already stopped or not generating
            if (!window.ChatStateManager.getState('isGenerating') && !window.ChatStateManager.getState('isStopping')) { // Explicit window call
                 console.log("[WS Stop Confirm] Ignoring stop confirmation because nothing is generating or stopping according to StateManager.");
                 return;
            }
            
            // Confirm stop via StateManager - this resets isGenerating, isStopping, activeGenerationIds
            window.ChatStateManager.confirmGlobalStop(); // Use the renamed function
            // window.ChatStateManager.clearGenerationId(); // REMOVED - confirmGlobalStop already clears activeGenerationIds
            console.log("[WS Stop Confirm] Called ChatStateManager.confirmGlobalStop(). StateManager will notify subscribers to update UI.");
            // REMOVED redundant UI manipulation. State change notification handles UI updates.
            return; // Stop processing here.
        }

        // --- CORRECTED: Handle generation_start (match backend log) ---
        if (data.type === 'generation_start' && data.generation_id) { // *** CHANGED BACK: 'generation_started' to 'generation_start' ***
            console.log(`[WS] Received generation_start signal. GenID: ${data.generation_id}, TempID: ${data.temp_id}`); // 更新日志消息
            window.ChatStateManager.handleGenerationStart(data.generation_id);
            window.ChatStateManager.setGenerationId(data.generation_id); // Also set the single active ID
            // Optionally map temp_id to generation_id here if needed for UI linking
            return; // Message handled
        }
        // --- END ADDED ---

        // --- ADDED: Handle generation_end ---
        if (data.type === 'generation_end' && data.generation_id) {
            console.log(`[WS] Received generation_end signal. GenID: ${data.generation_id}, Status: ${data.status}`);
            window.ChatStateManager.handleGenerationEnd(data.generation_id, data.status);
            return; // Message handled
        }
        // --- END ADDED ---

        // Handle streaming update
        if (data.update && data.content) {
            const generationId = data.generation_id; // Assuming backend sends this
            const isStopping = window.ChatStateManager.getState('isStopping');
            const isCancelled = generationId && window.ChatStateManager.isGenerationCancelled(generationId);

            // --- ADDED: Enhanced Stop/Cancel Check ---
            if (isStopping || isCancelled) {
                const reason = isStopping ? "isStopping is true" : `generation ${generationId} is cancelled`;
                console.log(`[WS Update] Ignoring stream update due to stop condition (${reason}). GenID: ${generationId}`);
                return; // Discard this update
            }
            // --- END ADDED ---

            // 尝试找到与此流关联的用户消息的 temp_id
            const tempId = data.temp_id; // 假设后端在流式更新中也发送 temp_id
            const streamingMessageId = `ai-response-streaming-${tempId || 'current'}`; // 为流式消息创建一个唯一ID

            // 查找是否已存在此流式消息的 div
            let streamingDiv = messageContainer.querySelector(`#${streamingMessageId}`);

            if (!streamingDiv) {
                // 这是此流的第一个块
                console.log(`[WS] First stream chunk for tempId: ${tempId}. Creating new message div.`);

                // 1. 查找并移除加载指示器
                //    需要知道加载指示器的确切ID，它是在 chat_page.js 中创建的 `ai-response-loading-${tempId}`
                const loadingIndicatorId = `ai-response-loading-${tempId}`;
                const loadingIndicator = messageContainer.querySelector(`#${loadingIndicatorId}`);
                if (loadingIndicator) {
                    loadingIndicator.remove();
                    console.log(`[WS] Removed loading indicator: #${loadingIndicatorId}`);
                } else {
                    // 如果特定ID找不到，尝试移除通用的 #ai-response-loading (旧逻辑的后备)
                    const genericLoadingIndicator = messageContainer.querySelector('#ai-response-loading');
                    if (genericLoadingIndicator) {
                        genericLoadingIndicator.remove();
                        console.warn(`[WS] Removed generic loading indicator #ai-response-loading (fallback).`);
                    } else {
                        console.warn(`[WS] Loading indicator #${loadingIndicatorId} not found.`);
                    }
                }

                // 2. 创建新的 AI 消息 div
                streamingDiv = document.createElement('div');
                streamingDiv.className = 'alert alert-secondary mb-3'; // 使用 mb-3 保持间距
                streamingDiv.id = streamingMessageId; // 设置唯一ID，以便后续查找
                streamingDiv.setAttribute('data-streaming-for', tempId || 'unknown'); // 标记它对应的用户消息

                // 构建消息头部
                const header = document.createElement('div');
                header.className = 'd-flex justify-content-between';
                header.innerHTML = `
                    <span>助手</span>
                    <div>
                        <small>${new Date().toLocaleTimeString()}</small>
                        <!-- 稍后在 id_update 中添加按钮 -->
                    </div>
                `;

                // 构建分隔线和内容段落
                const hr = document.createElement('hr');
                const p = document.createElement('p');
                const span = document.createElement('span');
                span.className = 'render-target'; // 用于渲染 Markdown/LaTeX 等
                span.setAttribute('data-original-content', data.content); // 存储原始内容
                p.appendChild(span);

                // 组装消息 div
                streamingDiv.appendChild(header);
                streamingDiv.appendChild(hr);
                streamingDiv.appendChild(p);

                // 添加到容器
                messageContainer.appendChild(streamingDiv);
                console.log(`[WS] Created new streaming message div #${streamingMessageId}`);

                // 初始渲染
                renderMessageContent(streamingDiv);
                streamingDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

            } else {
                // 这是后续的流式块，追加内容
                console.log(`[WS] Appending stream chunk to #${streamingMessageId}`);
                const renderTarget = streamingDiv.querySelector('p > .render-target');
                if (renderTarget) {
                    let currentOriginal = renderTarget.getAttribute('data-original-content') || '';
                    currentOriginal += data.content;
                    renderTarget.setAttribute('data-original-content', currentOriginal);

                    // 重新渲染更新后的内容
                    renderMessageContent(streamingDiv);
                    streamingDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                } else {
                    console.warn(`[WS] Render target not found in #${streamingMessageId}`);
                }
            }
            return; // 处理完毕
        }

        // Handle AI message ID update (after streaming completes)
        if (data.id_update && data.message_id) {
            // 找到对应的流式消息 div
            const tempId = data.temp_id; // 假设后端也发送 temp_id
            const streamingMessageId = `ai-response-streaming-${tempId || 'current'}`;
            const streamingDiv = messageContainer.querySelector(`#${streamingMessageId}`);

            if (streamingDiv) {
                console.log(`[WS] Finalizing streaming message #${streamingMessageId} with real ID ${data.message_id}`);
                // 1. 设置最终的 data-message-id
                streamingDiv.setAttribute('data-message-id', data.message_id);

                // 2. 移除临时的 streaming ID 和标记属性
                streamingDiv.removeAttribute('id');
                streamingDiv.removeAttribute('data-streaming-for');

                // 3. 添加操作按钮（例如删除）
                const headerDiv = streamingDiv.querySelector('.d-flex.justify-content-between > div');
                if (headerDiv && !headerDiv.querySelector('.delete-message-btn')) { // 避免重复添加
                    const deleteBtn = document.createElement('button');
                    deleteBtn.className = 'btn btn-sm btn-outline-danger delete-message-btn ms-2';
                    deleteBtn.title = '删除消息';
                    deleteBtn.innerHTML = '<i class="bi bi-trash"></i>';
                    headerDiv.appendChild(deleteBtn);
                    console.log(`[WS] Added delete button to message ${data.message_id}`);
                }

                // 4. 确保最终内容被完全渲染（以防万一）
                renderMessageContent(streamingDiv);

            } else {
                console.warn(`[WS] ID update received for ${data.message_id}, but streaming div #${streamingMessageId} not found.`);
                // 作为后备，尝试查找最后一个没有 message-id 的助手消息
                const secondaryMessages = messageContainer.querySelectorAll('.alert.alert-secondary:not([data-message-id])');
                 if (secondaryMessages.length > 0) {
                      const lastAssistantMessage = secondaryMessages[secondaryMessages.length - 1];
                      lastAssistantMessage.setAttribute('data-message-id', data.message_id);
                      console.warn(`[WS] Applied ID ${data.message_id} to the last secondary message without an ID (fallback).`);
                      // Add button here too if needed
                 }
            }

            // REMOVED direct call to hideStopGenerationButton - UI handled by StateManager subscription
            // window.isGeneratingResponse = false; // REMOVED old flag
            // Mark generation as ended using the generation_id (if available) associated with the stream
            // We need the backend to send generation_id in the id_update message for this to work reliably.
            // Assuming backend sends data.generation_id in id_update:
            // window.ChatStateManager.handleGenerationEnd(data.generation_id, 'completed'); // Call the new handler
            // console.log(`[WS ID Update] Called ChatStateManager.handleGenerationEnd for GenID: ${data.generation_id}`);
            // If backend doesn't send generation_id here, we can't reliably signal end via ID.
            // The separate 'generation_end' signal becomes crucial.
            console.log(`[WS ID Update] Finalized message ${data.message_id}. Waiting for generation_end signal.`);
            // REMOVED call to endGeneration and clearGenerationId

            return; // 处理完毕
        }


        // Handle regular non-streaming AI message (fallback or initial message) or final streaming message
        // This might be less common if streaming is the primary method
        if (data.type === 'chat_message' && !data.is_user) { // Check type explicitly
            console.log("Received complete AI message via WebSocket:", data);
            const generationId = data.generation_id; // Assuming backend sends this
            const isStopping = window.ChatStateManager.getState('isStopping');
            const isCancelled = generationId && window.ChatStateManager.isGenerationCancelled(generationId);

            // --- ADDED: Enhanced Stop/Cancel Check ---
            if (isStopping || isCancelled) {
                const reason = isStopping ? "isStopping is true" : `generation ${generationId} is cancelled`;
                console.log(`[WS ChatMsg] Ignoring final message due to stop condition (${reason}). GenID: ${generationId}`);
                // Optionally remove loading indicator here if it wasn't removed by streaming handler
                // Consider removing the loading indicator associated with this generation's tempId if applicable
                const tempId = data.temp_id; // Assuming backend sends temp_id too
                if (tempId) {
                    const loadingIndicatorId = `ai-response-loading-${tempId}`;
                    const loadingIndicator = document.getElementById(loadingIndicatorId);
                    if (loadingIndicator) {
                        loadingIndicator.remove();
                        console.log(`[WS ChatMsg] Removed loading indicator ${loadingIndicatorId} for ignored message.`);
                    }
                }
                return; // Discard this message
            }
            // --- END ADDED ---

            // 检查是否已显示终止消息 (This check might become redundant with the StateManager check, but keep for now as fallback)
            const terminationMessage = document.getElementById('ai-response-terminated');
            if (terminationMessage || window.terminationConfirmSent) {
                console.log("检测到终止消息已显示或已处理终止请求，不显示AI回复");
                
                // REMOVED direct call to hideStopGenerationButton - UI handled by StateManager subscription
                // REMOVED: window.isGeneratingResponse = false;
                return;
            }

            // Remove the correct loading indicator associated with the preceding user message
            let loadingIndicatorRemoved = false;
            // Find the last user message element to get its temp_id
            const userMessages = messageContainer.querySelectorAll('.alert.alert-primary[data-temp-id]');
            if (userMessages.length > 0) {
                const lastUserMessage = userMessages[userMessages.length - 1];
                const tempId = lastUserMessage.getAttribute('data-temp-id');
                if (tempId) {
                    const loadingIndicatorId = `ai-response-loading-${tempId}`;
                    const loadingIndicator = document.getElementById(loadingIndicatorId);
                    if (loadingIndicator) {
                        loadingIndicator.remove();
                        console.log(`[WS ChatMsg] Removed specific loading indicator: #${loadingIndicatorId}`);
                        loadingIndicatorRemoved = true;
                    } else {
                        console.warn(`[WS ChatMsg] Specific loading indicator #${loadingIndicatorId} not found.`);
                    }
                } else {
                    console.warn("[WS ChatMsg] Last user message found, but it has no data-temp-id.");
                }
            } else {
                console.warn("[WS ChatMsg] Could not find the last user message to determine temp_id.");
            }

            // Fallback: Try removing the generic one if specific one wasn't found/removed
            if (!loadingIndicatorRemoved) {
                const genericLoadingIndicator = document.getElementById('ai-response-loading');
                if (genericLoadingIndicator) {
                    genericLoadingIndicator.remove();
                    console.warn(`[WS ChatMsg] Removed generic loading indicator #ai-response-loading (fallback).`);
                }
            }

            const messageDiv = document.createElement('div');
            messageDiv.className = 'alert alert-secondary';
            messageDiv.setAttribute('data-message-id', data.message_id || ''); // Use provided ID

            // Parse timestamp safely
            let displayTimestamp = '时间未知';
            if (data.timestamp) {
                try {
                    // Attempt to parse ISO 8601 timestamp
                    displayTimestamp = new Date(data.timestamp).toLocaleTimeString();
                } catch (e) {
                    console.error("Error parsing timestamp from WebSocket:", data.timestamp, e);
                    // Keep '时间未知' or use the raw string if preferred
                    // displayTimestamp = data.timestamp; // Fallback to raw string
                }
            } else {
                 displayTimestamp = new Date().toLocaleTimeString(); // Fallback to current time
            }


            // --- MODIFIED: Extract content from data.message object ---
            const messageContent = (typeof data.message === 'object' && data.message !== null) ? data.message.content : data.message;
            const messageId = (typeof data.message === 'object' && data.message !== null) ? data.message.id : data.message_id; // Get ID from object if possible
            // --- END MODIFIED ---

            messageDiv.innerHTML = `
                <div class="d-flex justify-content-between">
                    <span>助手</span>
                    <div>
                        <small>${displayTimestamp}</small>
                        ${messageId ? // Use extracted messageId
                        `<button class="btn btn-sm btn-outline-danger delete-message-btn ms-2" title="删除消息">
                            <i class="bi bi-trash"></i>
                        </button>` : ''}
                    </div>
                </div>
                <hr>
                <p><span class="render-target" data-original-content="${escapeHtml(messageContent)}">${escapeHtml(messageContent)}</span></p>
            `;
            messageContainer.appendChild(messageDiv);
            renderMessageContent(messageDiv); // Render the content
            messageDiv.scrollIntoView();
            
            // REMOVED direct call to hideStopGenerationButton - UI handled by StateManager subscription
            // REMOVED: window.isGeneratingResponse = false;

            // --- REMOVED: Call to endGeneration ---
            // State is now managed by handleGenerationEnd triggered by the 'generation_end' signal.
            console.log(`[WS ChatMsg] Received non-streaming message ${data.message_id}. Waiting for generation_end signal.`);
            // --- END REMOVED ---
        }
    };
}

// Function to send message via WebSocket
function sendWebSocketMessage(message, modelId, tempId) {
    // 首先检查全局chatSocket变量
    if (typeof window.chatSocket !== 'undefined' && window.chatSocket) {
        chatSocket = window.chatSocket; // 确保使用全局变量
    }
    
    // 详细检查WebSocket状态
    if (!chatSocket) {
        console.warn("WebSocket对象不存在，无法发送消息");
        return false;
    }
    
    if (chatSocket.readyState !== WebSocket.OPEN) {
        console.warn(`WebSocket未连接，当前状态: ${getWebSocketStateText(chatSocket.readyState)}`);
        return false;
    }
    
    // WebSocket已连接，发送消息
    console.log("通过WebSocket发送消息:", message);
    try {
        chatSocket.send(JSON.stringify({
            'message': message,
            'model_id': modelId,
            'temp_id': tempId
        }));
        console.log("WebSocket消息发送成功");
        return true; // 表示消息已发送
    } catch (error) {
        console.error("通过WebSocket发送消息时出错:", error);
        return false; // 表示消息未发送
    }
}
