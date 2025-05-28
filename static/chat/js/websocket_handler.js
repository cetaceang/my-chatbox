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
    // 重置终止状态标志
    window.terminationConfirmSent = false;
    terminationConfirmSent = false;
    
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

    const wsUrl = 'ws://' + window.location.host + '/ws/chat/' + conversationId + '/';
    console.log("Attempting WebSocket connection:", wsUrl);
    
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

        // Handle generation stopped message
        if (data.type === 'generation_stopped') {
            console.log("收到生成终止确认");
            
            // 检查是否已经处理过终止消息或正在处理终止请求
            if (window.terminationConfirmSent || window.isTerminationInProgress) {
                console.log("已经处理过终止消息或正在处理终止请求，忽略重复的终止确认");
                return;
            }
            
            // 标记已经处理过终止消息
            window.terminationConfirmSent = true;
            
            // 立即清除所有加载指示器
            const allLoadingIndicators = document.querySelectorAll('[id^="ai-response-loading-"]');
            if (allLoadingIndicators.length > 0) {
                console.log(`WebSocket终止：立即清除 ${allLoadingIndicators.length} 个加载指示器`);
                allLoadingIndicators.forEach(indicator => {
                    // 将加载指示器替换为终止消息
                    const indicatorId = indicator.id;
                    const match = indicatorId.match(/ai-response-loading-(.+)/);
                    if (match && match[1]) {
                        const userMessageId = match[1];
                        if (typeof displayTerminationMessage === 'function') {
                            displayTerminationMessage(userMessageId);
                        } else {
                            // 如果displayTerminationMessage未定义，简单移除指示器
                            indicator.remove();
                        }
                    } else {
                        indicator.remove();
                    }
                });
            }
            
            // 重置所有活跃的重新生成按钮
            const allRegenBtns = document.querySelectorAll('.regenerate-btn[data-regenerating="true"]');
            if (allRegenBtns.length > 0) {
                console.log(`WebSocket终止：重置 ${allRegenBtns.length} 个重新生成按钮`);
                allRegenBtns.forEach(btn => {
                    btn.removeAttribute('data-regenerating');
                    btn.classList.remove('btn-processing');
                    btn.disabled = false;
                    btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
                });
            }
            
            // 隐藏终止按钮
            if (typeof hideStopGenerationButton === 'function') {
                hideStopGenerationButton();
            } else {
                const stopGenerationContainer = document.querySelector('#stop-generation-container');
                if (stopGenerationContainer) {
                    stopGenerationContainer.style.display = 'none';
                }
                
                // 直接重置全局状态
                window.isGeneratingResponse = false;
                window.terminationRequestSent = false;
                window.isTerminationInProgress = false;
                console.log("WebSocket终止：已直接重置全局状态");
            }
            
            // 检查是否已经显示了终止消息
            const existingTerminationMessage = document.getElementById('ai-response-terminated');
            if (!existingTerminationMessage) {
                // 如果没有显示终止消息，则显示一个
                console.log("未检测到已有终止消息，添加一个新的终止消息");
                
                // 查找并移除加载指示器
                const loadingMessage = document.getElementById('ai-response-loading');
                if (loadingMessage) {
                    loadingMessage.remove();
                }
                
                // 显示终止消息
                if (typeof displayTerminationMessage === 'function') {
                    displayTerminationMessage();
                } else {
                    console.warn("displayTerminationMessage函数未定义，无法显示终止消息");
                }
            } else {
                console.log("已存在终止消息，不再重复显示");
            }
            return;
        }

         // Handle streaming update
         if (data.update && data.content) {
            // 寻找最后的助手消息（可能是加载中的消息）
            let lastAssistantMessage = messageContainer.querySelector('#ai-response-loading');
            if (!lastAssistantMessage) {
                // 如果没有加载中的消息，寻找最后一个次要警告框
                const secondaryMessages = messageContainer.querySelectorAll('.alert.alert-secondary');
                if (secondaryMessages.length > 0) {
                    lastAssistantMessage = secondaryMessages[secondaryMessages.length - 1];
                }
            }

            if (lastAssistantMessage) {
                // 检查消息是否已在状态管理器中
                const messageId = lastAssistantMessage.getAttribute('data-message-id') || '';
                const isLoading = lastAssistantMessage.id === 'ai-response-loading';
                
                if (!isLoading && messageId && window.ChatState && window.ChatState.getMessage(messageId)) {
                    // 使用状态管理器更新消息
                    console.log(`[WS] 使用状态管理器更新消息 ${messageId}`);
                    const messageState = window.ChatState.getMessage(messageId);
                    const newContent = messageState.content + data.content;
                    
                    // 更新状态（这将触发DOM更新）
                    window.ChatState.updateMessage(messageId, newContent);
                } else {
                    // 回退到旧的更新方法
                    console.log(`[WS] 使用传统方法更新消息`);
                    const renderTarget = lastAssistantMessage.querySelector('p > .render-target');
                    
                    if (renderTarget) {
                        // 追加新内容到原始内容属性
                        let currentOriginal = renderTarget.getAttribute('data-original-content') || '';
                        currentOriginal += data.content;
                        renderTarget.setAttribute('data-original-content', currentOriginal);
                        
                        // 重新渲染目标span
                        renderMessageContent(lastAssistantMessage);
                        lastAssistantMessage.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    } else if (isLoading) {
                        // 如果是加载指示器，创建p和render-target
                        const p = document.createElement('p');
                        const span = document.createElement('span');
                        span.className = 'render-target';
                        span.setAttribute('data-original-content', data.content);
                        p.appendChild(span);
                        
                        // 找到插入段落的位置（如hr之后）
                        const hr = lastAssistantMessage.querySelector('hr');
                        if (hr) {
                            hr.insertAdjacentElement('afterend', p);
                        } else {
                            lastAssistantMessage.appendChild(p);
                        }
                        
                        renderMessageContent(lastAssistantMessage);
                        lastAssistantMessage.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                        
                        // 如果存在状态管理器，注册这个消息
                        if (window.ChatState) {
                            console.log('[WS] 注册流式AI消息到状态管理器');
                            window.ChatState.registerMessage(
                                'streaming-' + Date.now(), // 临时ID
                                data.content,
                                false, // 不是用户消息
                                lastAssistantMessage
                            );
                        }
                    } else {
                        console.warn("[WS] 未找到render-target，无法更新内容");
                    }
                }
            } else {
                console.warn("[WS] 未找到合适的助手消息来附加内容");
            }
            return;
        }

        // Handle AI message ID update (after streaming completes)
        if (data.id_update && data.message_id) {
             // Find the last assistant message which should now have content
             const secondaryMessages = messageContainer.querySelectorAll('.alert.alert-secondary');
             let lastAssistantMessage = null;
             if (secondaryMessages.length > 0) {
                  lastAssistantMessage = secondaryMessages[secondaryMessages.length - 1];
             }

             if (lastAssistantMessage && !lastAssistantMessage.hasAttribute('data-message-id')) { // Only update if ID is missing
                 lastAssistantMessage.setAttribute('data-message-id', data.message_id);
                 // Add delete button if it wasn't added during streaming finalization
                 if (!lastAssistantMessage.querySelector('.delete-message-btn')) {
                      const headerDiv = lastAssistantMessage.querySelector('.d-flex.justify-content-between > div');
                      if (headerDiv) {
                           const deleteBtn = document.createElement('button');
                           deleteBtn.className = 'btn btn-sm btn-outline-danger delete-message-btn ms-2';
                           deleteBtn.title = '删除消息';
                           deleteBtn.innerHTML = '<i class="bi bi-trash"></i>';
                           headerDiv.appendChild(deleteBtn);
                      }
                 }
                 // Remove loading indicator class/ID if it was the loading div initially
                 lastAssistantMessage.removeAttribute('id'); // Remove 'ai-response-loading' id
                 console.log(`Updated final assistant message ID: ${data.message_id}`);
             } else if (lastAssistantMessage) {
                  console.log(`Assistant message already has ID: ${lastAssistantMessage.getAttribute('data-message-id')}`);
             } else {
                  console.warn("ID update received, but no assistant message found to update.");
             }
             
             // 隐藏终止按钮，标记生成完成
             if (typeof hideStopGenerationButton === 'function') {
                 hideStopGenerationButton();
             } else {
                 const stopGenerationContainer = document.querySelector('#stop-generation-container');
                 if (stopGenerationContainer) {
                     stopGenerationContainer.style.display = 'none';
                 }
             }
             window.isGeneratingResponse = false;
             
             return;
        }


        // Handle regular non-streaming AI message (fallback or initial message) or final streaming message
        // This might be less common if streaming is the primary method
        if (data.type === 'chat_message' && !data.is_user) { // Check type explicitly
            console.log("Received complete AI message via WebSocket:", data);

            // 检查是否已显示终止消息
            const terminationMessage = document.getElementById('ai-response-terminated');
            if (terminationMessage || window.terminationConfirmSent) {
                console.log("检测到终止消息已显示或已处理终止请求，不显示AI回复");
                
                // 隐藏终止按钮，标记生成完成
                if (typeof hideStopGenerationButton === 'function') {
                    hideStopGenerationButton();
                } else {
                    const stopGenerationContainer = document.querySelector('#stop-generation-container');
                    if (stopGenerationContainer) {
                        stopGenerationContainer.style.display = 'none';
                    }
                }
                window.isGeneratingResponse = false;
                return;
            }

            // Remove any lingering loading indicator
            const waitingMessage = document.getElementById('ai-response-loading');
            if (waitingMessage) {
                waitingMessage.remove();
                console.log("Removed loading indicator.");
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


            messageDiv.innerHTML = `
                <div class="d-flex justify-content-between">
                    <span>助手</span>
                    <div>
                        <small>${displayTimestamp}</small>
                        ${data.message_id ? // Only add delete button if we have an ID
                        `<button class="btn btn-sm btn-outline-danger delete-message-btn ms-2" title="删除消息">
                            <i class="bi bi-trash"></i>
                        </button>` : ''}
                    </div>
                </div>
                <hr>
                <p><span class="render-target" data-original-content="${escapeHtml(data.message)}">${escapeHtml(data.message)}</span></p>
            `;
            messageContainer.appendChild(messageDiv);
            renderMessageContent(messageDiv); // Render the content
            messageDiv.scrollIntoView();
            
            // 隐藏终止按钮，标记生成完成
            if (typeof hideStopGenerationButton === 'function') {
                hideStopGenerationButton();
            } else {
                const stopGenerationContainer = document.querySelector('#stop-generation-container');
                if (stopGenerationContainer) {
                    stopGenerationContainer.style.display = 'none';
                }
            }
            window.isGeneratingResponse = false;
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
