/* eslint-env browser */
/* globals initWebSocket, syncConversationData, getStoredConversationId, storeConversationId, escapeHtml, renderMessageContent, sendWebSocketMessage, getCookie, tempIdMap, getRealMessageId, deleteConversation, editConversationTitle, deleteMessage, saveMessageEdit, regenerateResponse, displaySystemError, createLoadingIndicator, createAIMessageDiv, getChatSettings, saveChatSettings, initializeChatSettings, initializeEventListeners, updateUIBasedOnState */ // Inform linter about globals

// Global variable for the current conversation ID, initialized from the template
// It's generally better to avoid globals, but necessary here due to split files without a module system
// Ensure this is defined in the HTML template before this script runs.
// let conversationId = null; // This will be set in the HTML template script block

document.addEventListener("DOMContentLoaded", () => {
    console.log("Chat Page Initializing...");

    // 初始化聊天设置
    initializeChatSettings();
    initializeEventListeners();

    // --- 移动 previousState 声明到这里 ---
    let previousState = null; // 用于 updateUIBasedOnState 函数跟踪状态变化

    // Initialize tempIdMap (assuming it's defined in utils.js or globally)
    if (typeof tempIdMap === 'undefined') {
        console.warn("tempIdMap is not defined globally. Initializing locally.");
        window.tempIdMap = {}; // Make it global if not already
    } else {
        tempIdMap = {}; // Reset it on page load
    }

    // --- Conversation ID Handling ---
    const urlParams = new URLSearchParams(window.location.search);
    const conversationIdFromUrl = urlParams.get('conversation_id');
    const storedConversationId = getStoredConversationId();
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

    // --- Initialize State Manager ---
    const modelSelect = document.querySelector('#model-select'); 
    console.log("[Chat Page] Found #model-select element:", modelSelect); 
    const initialModelId = modelSelect ? modelSelect.value : null;
    
    try {
        window.ChatStateManager.init(definitiveConversationId, initialModelId); 
        console.log("ChatStateManager initialized successfully.");

        if (window.ChatStateManager.getState('currentConversationId')) { 
            // Existing conversation
            initWebSocket(); 
            syncConversationData().catch(error => { 
                console.error("Error during initial sync:", error);
            });
        } else {
            // New conversation scenario
            console.log("No conversation ID available. Ready for new conversation.");
            console.log("Skipping WebSocket init and sync - no conversation ID yet.");
        }
        
        window.ChatStateManager.subscribe(updateUIBasedOnState); 

        updateUIBasedOnState(window.ChatStateManager.getState()); 
        console.log("Initial UI state synced after successful init.");

    } catch (error) {
        console.error("[CRITICAL ERROR] Failed to initialize ChatStateManager or run initial setup:", error);
    }
    
    const messageContainer = document.querySelector('#message-container');

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
}); // End of DOMContentLoaded

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

// 添加发送终止生成请求的函数 (Corrected Version)
async function sendStopGenerationRequest(generationIdToStop) { // Now accepts the ID as a parameter
    const conversationId = window.ChatStateManager.getState().currentConversationId;
    if (!conversationId) {
        console.error("[Stop Request] 无法发送终止请求：未找到会话ID");
        return;
    }
    console.log(`[Stop Request] 正在发送终止生成请求到服务器，对话ID: ${conversationId}`);

    console.log(`[Stop Request] Using passed-in ID to stop: ${generationIdToStop}`);

    if (!generationIdToStop) {
        console.error("[Stop Request] CRITICAL: The passed-in generationIdToStop is null or undefined. Cannot send stop request.");
        return;
    }

    try {
        if (window.chatSocket && window.chatSocket.readyState === WebSocket.OPEN) {
            const messagePayload = JSON.stringify({
                type: 'stop_generation',
                generation_id: generationIdToStop
            });
            window.chatSocket.send(messagePayload);
            console.log(`[Stop Request] WebSocket stop_generation message sent: ${messagePayload}`);
        } else {
            // --- HTTP Fallback for Stop Request ---
            const currentStateText = window.chatSocket ? getWebSocketStateText(window.chatSocket.readyState) : 'Not Initialized';
            console.warn(`[Stop Request] WebSocket not open (State: ${currentStateText}). Falling back to HTTP POST.`);
            
            fetch('/chat/api/stop_generation/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({ 'generation_id': generationIdToStop })
            })
            .then(response => {
                if (!response.ok) {
                    // Throw an error to be caught by the catch block
                    return response.json().then(err => { throw new Error(err.message || `HTTP ${response.status}`) });
                }
                return response.json();
            })
            .then(data => {
                if (data.success) {
                    console.log(`[Stop Request] HTTP stop request for GenID ${generationIdToStop} acknowledged by server.`);
                    // The backend will set the stop flag. The frontend will receive a 'cancelled'
                    // generation_end event through the ongoing HTTP stream, which will trigger the UI update.
                } else {
                    throw new Error(data.message || "服务器拒绝了终止请求");
                }
            })
            .catch(err => {
                console.error("[Stop Request] HTTP fallback failed:", err);
                displaySystemError(`发送终止请求失败: ${err.message}`);
                // Reset UI state since the request failed
                if (window.ChatStateManager && typeof window.ChatStateManager.resetStoppingState === 'function') {
                    window.ChatStateManager.resetStoppingState();
                }
            });
        }
    } catch (error) {
        console.error("[Stop Request] 发送终止生成请求时出错:", error);
        if (window.ChatStateManager && typeof window.ChatStateManager.resetStoppingState === 'function') {
            window.ChatStateManager.resetStoppingState();
        }
    }
}

function handleGenerationRequest({ generationId, message, modelId, isRegenerate, userMessageId, userMessageDiv }) {
    const messageContainer = document.querySelector('#message-container');
    const messageInput = document.querySelector('#message-input');

    // Start the generation process in the state manager using the single, unique generationId.
    window.ChatStateManager.startGeneration(generationId);

    let messageDivToAppendAfter = userMessageDiv;

    if (!isRegenerate) {
        // This is a new message.
        // The temporary ID for the user message div is the generationId.
        const newUserMessageDiv = window.MessageFactory.createUserMessage(generationId, message);
        messageContainer.appendChild(newUserMessageDiv);
        renderMessageContent(newUserMessageDiv);
        newUserMessageDiv.scrollIntoView();
        messageDivToAppendAfter = newUserMessageDiv; // The loading indicator should appear after this new div.
        messageInput.value = '';
        messageInput.focus();
    }

    // Add loading indicator, consistently using the generationId for its unique ID.
    const loadingDiv = window.MessageFactory.createLoadingIndicator(`ai-response-loading-${generationId}`);
    messageDivToAppendAfter.after(loadingDiv);
    loadingDiv.scrollIntoView();

    // --- NEW LOGIC: Unified WebSocket Handling ---
    // If there's no conversation ID, the first message will establish the WebSocket connection.
    if (!window.ChatStateManager.getState().currentConversationId) {
        console.log("No conversation ID found. Initializing WebSocket to create a new conversation.");
        initWebSocket({
            isNewConversation: true,
            initialMessage: {
                type: isRegenerate ? 'regenerate' : 'chat_message',
                text: message,
                model_id: modelId,
                generation_id: generationId,
                user_message_id: userMessageId // Will be null for new messages, which is correct
            }
        });
    } else {
        // Existing conversation, send message through the already open WebSocket.
        // The websocket_handler now contains the HTTP fallback logic, so no extra check is needed here.
        if (isRegenerate) {
            sendWebSocketRegenerate(userMessageId, modelId, generationId);
        } else {
            sendWebSocketMessage(message, modelId, generationId);
        }
    }
}

// 重新生成回复
function regenerateResponse(userMessageId) {
    const userMessageDiv = document.querySelector(`.alert.alert-primary[data-message-id="${userMessageId}"], .alert.alert-primary[data-temp-id="${userMessageId}"]`);
    if (!userMessageDiv) {
        console.error(`无法找到用户消息DIV: ${userMessageId}`);
        return;
    }

    const modelId = document.querySelector('#model-select').value;
    if (!modelId) {
        console.error("无法找到模型ID");
        return;
    }

    // 删除旧的AI回复（无论是成功、失败还是加载中）
    let nextElement = userMessageDiv.nextElementSibling;
    while (nextElement && !nextElement.classList.contains('alert-primary')) {
        const elementToRemove = nextElement;
        nextElement = nextElement.nextElementSibling;
        console.log(`[Regenerate] Removing previous AI response element: ${elementToRemove.id || elementToRemove.className}`);
        elementToRemove.remove();
    }
    
    // 为这次重新生成操作创建一个全新的、唯一的ID
    const newGenerationId = window.generateUUID();

    // 确保在调用handleGenerationRequest之前，DOM已经更新
    setTimeout(() => {
        handleGenerationRequest({
            generationId: newGenerationId,
            modelId: modelId,
            isRegenerate: true,
            userMessageId: userMessageId,
            userMessageDiv: userMessageDiv
        });
    }, 0);
}
