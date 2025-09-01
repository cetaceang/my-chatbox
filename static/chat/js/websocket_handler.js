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
    const settings = getChatSettings();
    if (settings.forceHttpMode) {
        console.log("强制HTTP模式已开启，跳过WebSocket初始化。");
        if (chatSocket && chatSocket.readyState !== WebSocket.CLOSED) {
            chatSocket.close(1000, "切换到HTTP模式");
        }
        return;
    }

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
    const settings = getChatSettings();

    // 如果强制HTTP模式开启，或WebSocket不可用，则回退到HTTP
    if (settings.forceHttpMode || !chatSocket || chatSocket.readyState !== WebSocket.OPEN) {
        if (settings.forceHttpMode) {
            console.log(`[HTTP Fallback] 强制HTTP模式已开启，为 ${type} 请求使用HTTP。`);
        } else {
            console.warn(`[HTTP Fallback] WebSocket not open, falling back to HTTP for ${type} request.`);
        }
        sendHttpRequestFallback(type, payload); // 注意：sendHttpRequestFallback 内部会再次获取settings
        return true; // 假设请求已被处理
    }

    const fullPayload = {
        ...payload,
        type,
        is_streaming: settings.isStreaming,
    };

    try {
        chatSocket.send(JSON.stringify(fullPayload));
        return true;
    } catch (error) {
        console.error(`通过WebSocket发送 ${type} 请求时出错，回退到HTTP:`, error);
        // 如果发送失败，也回退到HTTP
        sendHttpRequestFallback(type, payload);
        return true; // 假设请求已被处理
    }
}

// --- 新增：HTTP回退逻辑 ---

async function sendHttpRequestFallback(type, payload, settings) { // settings can be passed in
    console.log(`[HTTP Fallback] Sending request for type: ${type}`);
    
    // If settings are not passed, get them.
    const currentSettings = settings || getChatSettings();
    const conversationId = window.ChatStateManager.getState().currentConversationId;
    if (!conversationId) {
        displaySystemError("无法发送请求：当前会话ID未知。");
        return;
    }

    // --- 动态准备请求体和头部 ---
    let requestBody;
    const headers = {
        'X-CSRFToken': getCookie('csrftoken'),
        // 'Content-Type' 将由浏览器根据请求体自动设置
    };

    if (type === 'image_upload' && payload.file) {
        // 如果是图片上传，使用 FormData
        requestBody = new FormData();
        requestBody.append('conversation_id', conversationId);
        requestBody.append('model_id', payload.model_id);
        requestBody.append('message', payload.message);
        requestBody.append('is_regenerate', false); // 图片上传总是新消息
        requestBody.append('is_streaming', currentSettings.isStreaming);
        requestBody.append('generation_id', payload.generation_id);
        requestBody.append('file', payload.file, payload.file_name); // 添加文件
    } else {
        // 对于其他请求，使用 JSON
        headers['Content-Type'] = 'application/json';
        requestBody = JSON.stringify({
            conversation_id: conversationId,
            model_id: payload.model_id,
            message: payload.message,
            is_regenerate: type === 'regenerate',
            message_id: payload.message_id,
            is_streaming: currentSettings.isStreaming,
            generation_id: payload.generation_id,
        });
    }
    // --- 结束动态准备 ---

    const tempId = payload.temp_id || payload.generation_id;
    if (tempId) {
        window.ChatStateManager.handleGenerationStart(tempId, tempId);
    }

    try {
        const response = await fetch('/chat/api/http_chat/', {
            method: 'POST',
            headers: headers,
            body: requestBody,
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP error! status: ${response.status}, text: ${errorText}`);
        }

        const contentType = response.headers.get("content-type");
        if (contentType && contentType.indexOf("text/event-stream") !== -1) {
            // 处理SSE流
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const events = buffer.split('\n\n');
                buffer = events.pop();
                for (const eventString of events) {
                    if (!eventString.trim()) continue;
                    const eventTypeMatch = eventString.match(/event: (.*)/);
                    const eventDataMatch = eventString.match(/data: (.*)/);
                    if (eventTypeMatch && eventDataMatch) {
                        handleStreamEvent({ type: eventTypeMatch[1], data: JSON.parse(eventDataMatch[1]) });
                    }
                }
            }
        } else {
            // 处理JSON响应
            const data = await response.json();
            if (data.success) {
                // 模拟 onmessage 事件流
                handleStreamEvent({ type: 'full_message', data: { generation_id: data.generation_id, temp_id: tempId, content: data.content } });
                handleStreamEvent({ type: 'id_update', data: { temp_id: tempId, message_id: data.message_id } });
                handleStreamEvent({ type: 'generation_end', data: { generation_id: data.generation_id, status: 'completed' } });
            } else {
                throw new Error(data.error || '非流式响应报告未知错误');
            }
        }

    } catch (error) {
        console.error('[HTTP Fallback] Request failed:', error);
        displaySystemError(`HTTP请求失败: ${error.message}`);
        if (tempId) {
            window.ChatStateManager.handleGenerationEnd(tempId, 'failed');
        }
    }
}

function handleStreamEvent(event) {
    // 复用 WebSocket 的 onmessage 处理器来处理来自HTTP的事件
    const mockWebSocketEvent = { data: JSON.stringify(event) };
    if (window.chatSocket && typeof window.chatSocket.onmessage === 'function') {
        window.chatSocket.onmessage(mockWebSocketEvent);
    } else {
        // 如果 onmessage 不可用，提供一个最小化的备用处理器
        console.warn("chatSocket.onmessage not available, using minimal event handler for HTTP fallback.");
        const { type, data } = event;
        switch (type) {
            case 'generation_start':
                window.ChatStateManager.handleGenerationStart(data.generation_id, data.temp_id);
                break;
            case 'stream_update':
            case 'full_message': {
                const { generation_id, temp_id, content } = data;
                if (window.ChatStateManager.isGenerationCancelled(generation_id)) return;
                const isStreaming = type === 'stream_update';
                const messageDiv = createOrFindMessageDiv(temp_id, isStreaming);
                if (messageDiv) {
                    const renderTarget = messageDiv.querySelector('.render-target');
                    const currentContent = isStreaming ? (renderTarget.getAttribute('data-original-content') || '') : '';
                    renderTarget.setAttribute('data-original-content', currentContent + content);
                    renderMessageContent(messageDiv, isStreaming);
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
                }
                break;
            }
            case 'generation_end':
                window.ChatStateManager.handleGenerationEnd(data.generation_id, data.status);
                break;
            case 'error':
                displaySystemError(data.message);
                break;
        }
    }
}

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
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

window.sendWebSocketRequest = sendWebSocketRequest;
