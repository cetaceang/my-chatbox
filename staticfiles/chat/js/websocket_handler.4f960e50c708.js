/* eslint-env browser */
/* globals renderMessageContent, escapeHtml, storeConversationId, tempIdMap */ // Inform linter about globals

// WebSocket connection variable (will be initialized by initWebSocket)
let chatSocket = null;

// Initialize WebSocket connection
function initWebSocket() {
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

    console.log("Attempting WebSocket connection: ws://" + window.location.host + "/ws/chat/" + conversationId + "/");
    // Close existing socket if trying to re-initialize
    if (chatSocket && chatSocket.readyState !== WebSocket.CLOSED) {
        console.log("Closing existing WebSocket connection before re-initializing.");
        chatSocket.close(1000, "Re-initializing connection"); // 1000 indicates normal closure
    }

    chatSocket = new WebSocket(
        'ws://' + window.location.host +
        '/ws/chat/' + conversationId + '/');

    chatSocket.onopen = function(e) {
        console.log("WebSocket connection established");
    };

    chatSocket.onerror = function(e) {
        console.error("WebSocket error:", e);
        // Add error hint, but don't show to user directly
        console.log("WebSocket connection failed, will rely on HTTP API as fallback.");
    };

    chatSocket.onclose = function(e) {
        console.log("WebSocket connection closed", e.code, e.reason);
        // Add reconnection logic only if not a normal closure (code 1000)
        if (e.code !== 1000) {
            console.log("WebSocket closed unexpectedly. Attempting to reconnect in 5 seconds...");
            setTimeout(function() {
                console.log("Attempting WebSocket reconnection...");
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
             return;
        }


        // Handle regular non-streaming AI message (fallback or initial message) or final streaming message
        // This might be less common if streaming is the primary method
        if (data.type === 'chat_message' && !data.is_user) { // Check type explicitly
            console.log("Received complete AI message via WebSocket:", data);

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
        }
    };
}

// Function to send message via WebSocket
function sendWebSocketMessage(message, modelId, tempId) {
    if (chatSocket && chatSocket.readyState === WebSocket.OPEN) {
        console.log("Sending message via WebSocket:", message);
        chatSocket.send(JSON.stringify({
            'message': message,
            'model_id': modelId,
            'temp_id': tempId
        }));
        return true; // Indicate message was sent
    } else {
        console.log("WebSocket not open. Cannot send message.");
        return false; // Indicate message was not sent
    }
}
