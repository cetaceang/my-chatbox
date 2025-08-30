/* eslint-env browser */
/* globals renderMessageContent, escapeHtml, storeConversationId, getChatSettings, displaySystemError */

let chatSocket = null;

if (typeof window !== 'undefined') {
    window.chatSocket = null;
}

function getWebSocketStateText(readyState) {
    switch (readyState) {
        case WebSocket.CONNECTING: return "CONNECTING (0) - 连接中";
        case WebSocket.OPEN: return "OPEN (1) - 已连接";
        case WebSocket.CLOSING: return "CLOSING (2) - 关闭中";
        case WebSocket.CLOSED: return "CLOSED (3) - 已关闭";
        default: return `UNKNOWN (${readyState}) - 未知状态`;
    }
}

/**
 * 创建或查找用于显示AI响应的DIV。
 * @param {string} tempId - 关联的用户消息临时ID。
 * @param {boolean} isStreaming - 是否为流式响应。
 * @returns {HTMLElement|null} - 找到或创建的DIV元素。
 */
function createOrFindMessageDiv(tempId, isStreaming) {
    const messageContainer = document.querySelector('#message-container');
    if (!messageContainer) return null;

    const messageId = isStreaming ? `ai-response-streaming-${tempId}` : `ai-response-full-${tempId}`;
    let messageDiv = document.getElementById(messageId);

    if (!messageDiv) {
        const loadingIndicator = document.getElementById(`ai-response-loading-${tempId}`);
        if (loadingIndicator) {
            loadingIndicator.remove();
        }

        // 检查是否已存在具有相同tempId的AI消息
        const existingAiMessage = messageContainer.querySelector(`.alert-secondary[data-temp-id="${tempId}"]`);
        if (existingAiMessage) {
            console.log(`[WebSocket] Found existing AI message for tempId ${tempId}, reusing it.`);
            messageDiv = existingAiMessage;
            messageDiv.id = messageId; // Assign the correct streaming/full ID
        } else {
            console.log(`[WebSocket] Creating new AI message div for tempId ${tempId}`);
            messageDiv = document.createElement('div');
            messageDiv.className = 'alert alert-secondary mb-3';
            messageDiv.id = messageId;
            messageDiv.setAttribute('data-temp-id', tempId);

            messageDiv.innerHTML = `
                <div class="d-flex justify-content-between">
                    <span>助手</span>
                    <div><small>${new Date().toLocaleTimeString()}</small></div>
                </div>
                <hr>
                <p><span class="render-target" data-original-content=""></span></p>
            `;
            messageContainer.appendChild(messageDiv);
        }
    }
    return messageDiv;
}


function initWebSocket(options = {}) {
    const { isNewConversation = false, initialMessage = null } = options;
    let conversationIdForUrl;

    if (isNewConversation) {
        conversationIdForUrl = 'new';
        console.log("Initializing WebSocket for a new conversation.");
    } else {
        conversationIdForUrl = window.ChatStateManager.getState().currentConversationId;
        if (!conversationIdForUrl) {
            console.error("initWebSocket called for existing conversation, but no ID was found in state.");
            return;
        }
        console.log(`Initializing WebSocket for existing conversation: ${conversationIdForUrl}`);
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const wsUrl = `${wsProtocol}${window.location.host}/ws/chat/${conversationIdForUrl}/`;

    if (chatSocket && chatSocket.readyState !== WebSocket.CLOSED) {
        console.log(`Closing existing WebSocket (State: ${getWebSocketStateText(chatSocket.readyState)}) before creating a new one.`);
        chatSocket.close(1000, "Re-initializing connection");
    }

    try {
        chatSocket = new WebSocket(wsUrl);
        window.chatSocket = chatSocket;
    } catch (error) {
        console.error("创建WebSocket对象时出错:", error);
        return;
    }

    chatSocket.onopen = () => {
        console.log(`WebSocket connection established to ${wsUrl}`);
        // If this is a new conversation, send the initial message immediately after connecting.
        if (isNewConversation && initialMessage) {
            console.log("Sending initial message for new conversation:", initialMessage);
            // 直接发送完整的消息对象，而不是重新构造
            try {
                chatSocket.send(JSON.stringify(initialMessage));
                console.log("Initial message sent successfully");
            } catch (error) {
                console.error("Failed to send initial message:", error);
            }
        }
    };
    chatSocket.onerror = (e) => console.error("WebSocket错误:", e);
    chatSocket.onclose = (e) => {
        console.log("WebSocket连接已关闭", e.code, e.reason);
        // Do not automatically reconnect for 'new' connections or if closed cleanly.
        if (e.code !== 1000 && conversationIdForUrl !== 'new') {
            console.log("Attempting to reconnect in 5 seconds...");
            setTimeout(() => initWebSocket(), 5000);
        }
    };

    chatSocket.onmessage = (e) => {
        const eventData = JSON.parse(e.data);
        const { type, data } = eventData;
        const messageContainer = document.querySelector('#message-container');
        if (!messageContainer) return;

        switch (type) {
            case 'new_conversation_created':
                console.log("New conversation created by backend:", eventData.data);
                const { conversation_id, title } = eventData.data;
                
                // 1. Update the state manager
                window.ChatStateManager.setConversationId(conversation_id);
                
                // 2. Update the URL
                const newUrl = `/chat/?conversation_id=${conversation_id}`;
                history.pushState({ conversationId: conversation_id }, title, newUrl);
                
                // 3. Update the global variable
                window.conversationId = conversation_id;
                
                // 4. Refresh the conversation list in the sidebar
                fetch('/chat/conversation_list/')
                    .then(response => response.text())
                    .then(html => {
                        const conversationListDiv = document.getElementById('conversation-list');
                        if (conversationListDiv) {
                            conversationListDiv.innerHTML = html;
                        }
                    })
                    .catch(error => console.error('Error refreshing conversation list:', error));
                break;

            case 'user_message_id_update':
                // 更新ID管理器
                window.tempIdManager.set(eventData.temp_id, eventData.user_message_id);
                
                // 更新DOM
                const userMessageDiv = messageContainer.querySelector(`.alert[data-temp-id="${eventData.temp_id}"]`);
                if (userMessageDiv) {
                    userMessageDiv.setAttribute('data-message-id', eventData.user_message_id);
                }
                
                // 新增：同步ChatStateManager的状态
                window.ChatState.updateMessageId(eventData.temp_id, eventData.user_message_id);
                break;

            // DEPRECATED: 'generation_stopped' is no longer used.
            // case 'generation_stopped':
            //     window.ChatStateManager.confirmGlobalStop();
            //     break;

            case 'generation_start':
                window.ChatStateManager.handleGenerationStart(data.generation_id, data.temp_id);
                break;

            case 'generation_end': {
                const { generation_id, status, error } = data;

                if (status === 'failed' && error) {
                    console.error(`Generation failed for ID ${generation_id}:`, error);
                    const messageDiv = createOrFindMessageDiv(generation_id, false);
                    if (messageDiv) {
                        let errorMessage = error;
                        try {
                            const errorJson = JSON.parse(error);
                            if (errorJson.error && errorJson.error.message) {
                                errorMessage = errorJson.error.message;
                            }
                        } catch (e) { /* Not a JSON error, use raw text */ }

                        messageDiv.classList.remove('alert-secondary');
                        messageDiv.classList.add('alert-danger');
                        messageDiv.innerHTML = `
                            <div class="d-flex justify-content-between">
                                <span>助手 (错误)</span>
                                <div><small>${new Date().toLocaleTimeString()}</small></div>
                            </div>
                            <hr>
                            <p class="text-danger"><strong>生成失败:</strong></p>
                            <pre class="error-message-pre" style="white-space: pre-wrap; word-break: break-all;"><code>${escapeHtml(errorMessage)}</code></pre>
                        `;
                        messageDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    }
                }

                window.ChatStateManager.handleGenerationEnd(generation_id, status);
                break;
            }

            case 'full_message': {
                const { generation_id, temp_id, content } = data;
                if (window.ChatStateManager.isGenerationCancelled(generation_id)) return;

                const messageDiv = createOrFindMessageDiv(temp_id, false);
                if (messageDiv) {
                    const renderTarget = messageDiv.querySelector('.render-target');
                    renderTarget.setAttribute('data-original-content', content);
                    renderMessageContent(messageDiv, false); // 非流式，不使用打字机
                    messageDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
                break;
            }

            case 'stream_update': {
                const { generation_id, temp_id, content } = data;
                if (window.ChatStateManager.isGenerationCancelled(generation_id)) return;

                const messageDiv = createOrFindMessageDiv(temp_id, true);
                if (messageDiv) {
                    const renderTarget = messageDiv.querySelector('.render-target');
                    const currentContent = renderTarget.getAttribute('data-original-content') || '';
                    renderTarget.setAttribute('data-original-content', currentContent + content);
                    renderMessageContent(messageDiv, true); // 流式，使用打字机
                    messageDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
                break;
            }

            case 'id_update': {
                const { temp_id, message_id } = data;
                const streamingDiv = document.getElementById(`ai-response-streaming-${temp_id}`);
                const fullDiv = document.getElementById(`ai-response-full-${temp_id}`);
                const finalDiv = streamingDiv || fullDiv;

                if (finalDiv) {
                    finalDiv.setAttribute('data-message-id', message_id);
                    finalDiv.removeAttribute('id');
                    finalDiv.removeAttribute('data-temp-id');

                    const headerDiv = finalDiv.querySelector('.d-flex.justify-content-between > div');
                    if (headerDiv && !headerDiv.querySelector('.delete-message-btn')) {
                        const deleteBtn = document.createElement('button');
                        deleteBtn.className = 'btn btn-sm btn-outline-danger delete-message-btn ms-2';
                        deleteBtn.title = '删除消息';
                        deleteBtn.innerHTML = '<i class="bi bi-trash"></i>';
                        headerDiv.appendChild(deleteBtn);
                    }
                }
                break;
            }
        }
    };
}

function sendWebSocketRequest(type, payload) {
    if (!chatSocket || chatSocket.readyState !== WebSocket.OPEN) {
        console.warn(`WebSocket not open, cannot send ${type} request.`);
        if (type === 'regenerate') {
            displaySystemError("无法重新生成：连接已断开，请刷新页面重试。");
        }
        return false;
    }

    const settings = getChatSettings();
    const fullPayload = {
        ...payload,
        type,
        is_streaming: settings.isStreaming,
    };

    try {
        chatSocket.send(JSON.stringify(fullPayload));
        return true;
    } catch (error) {
        console.error(`通过WebSocket发送 ${type} 请求时出错:`, error);
        return false;
    }
}

function sendWebSocketMessage(message, modelId, tempId) {
    return sendWebSocketRequest('chat_message', {
        message,
        model_id: modelId,
        temp_id: tempId,
        generation_id: tempId,
    });
}

function sendWebSocketRegenerate(userMessageId, modelId, tempId) {
    return sendWebSocketRequest('regenerate', {
        message_id: userMessageId,
        model_id: modelId,
        temp_id: tempId,
        generation_id: tempId,
    });
}
