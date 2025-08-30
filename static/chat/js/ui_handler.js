/* eslint-env browser */
/* globals escapeHtml, renderMessageContent */

// --- Central UI Update Function ---
// Keep track of the previous state to detect transitions
let previousState = null; 
function updateUIBasedOnState(newState) {
    console.log("[UI Update] State changed:", newState);
    // Check if previousState has been initialized (is not undefined and not null)
    const hasPreviousState = typeof previousState !== 'undefined' && previousState !== null;

    console.log("[UI Update] State changed:", newState);
    if (hasPreviousState) {
        console.log("[UI Update] Previous state:", previousState);
    } else {
        console.log("[UI Update] Previous state: null or undefined (Initial call)");
    }
    const sendButton = document.querySelector('#send-button');
    const messageInput = document.querySelector('#message-input');
    const uploadFileBtn = document.querySelector('#upload-file-btn');
    const allRegenBtns = document.querySelectorAll('.regenerate-btn');

    if (!sendButton || !messageInput || !uploadFileBtn) {
        console.warn("[UI Update] Required UI elements not found.");
        return;
    }

    // Determine busy state based on new state properties
    const isBusy = newState.isGenerating || newState.isStopping || newState.activeGenerationIds.size > 0;
    const isGeneratingOrStopping = newState.isGenerating || newState.isStopping;

    // --- Send/Stop Button Logic ---
    if (isGeneratingOrStopping) {
        // Switch to Stop Mode
        if (!sendButton.classList.contains('stop-mode')) {
            sendButton.classList.add('stop-mode', 'btn-danger');
            sendButton.classList.remove('btn-primary');
            sendButton.innerHTML = '<i class="bi bi-stop-fill"></i>';
            sendButton.title = "终止生成";
            console.log("[UI Update] Switched to Stop Mode.");
        }
        // Disable stop button ONLY if termination is already confirmed/in progress server-side (isStopping=true)
        sendButton.disabled = newState.isStopping;
    } else {
        // Switch back to Send Mode
        if (sendButton.classList.contains('stop-mode')) {
            sendButton.classList.remove('stop-mode', 'btn-danger');
            sendButton.classList.add('btn-primary');
            sendButton.innerHTML = '<i class="bi bi-send"></i>';
            sendButton.title = "发送消息";
            console.log("[UI Update] Switched back to Send Mode.");
        }
        // Enable send button if not busy
        sendButton.disabled = isBusy; // Simplified: disable if busy for any reason

        // --- Reset stop-requested marker when stopping ends ---
        // Check if the state transitioned *from* stopping *to* not stopping
        // ADDED null check for previousState
        if (previousState && previousState.isStopping && !newState.isStopping) {
             console.log("[UI Update] Detected transition: Stopping ended. Removing data-stop-requested attribute.");
             const stoppedMessage = document.querySelector('.alert.alert-primary[data-stop-requested="true"]');
             if (stoppedMessage) {
                 stoppedMessage.removeAttribute('data-stop-requested');
                 console.log("[UI Update] Removed data-stop-requested attribute from:", stoppedMessage);
             } else {
                  console.warn("[UI Update] Stopping ended, but no message found with data-stop-requested attribute.");
             }
        }
        // --- End Reset stop-requested marker ---
    }

    // --- Message Input & Upload Button ---
    // Disable if generating, stopping, or regenerating
    messageInput.disabled = isBusy;
    uploadFileBtn.disabled = isBusy;

    // --- Regenerate Buttons (Simplified Logic) ---
    // Disable ALL regenerate buttons if the system is busy (generating, stopping, etc.)
    // Reset any 'processing' state if the system is no longer busy.
    allRegenBtns.forEach(btn => {
        btn.disabled = isBusy;
        if (!isBusy && btn.classList.contains('btn-processing')) {
             // Reset button appearance if no longer busy
             btn.classList.remove('btn-processing');
             btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
             console.log(`[UI Update] Reset regenerate button (ID: ${btn.closest('.alert')?.getAttribute('data-message-id')}) as system is no longer busy.`);
        } else if (isBusy && !btn.disabled) {
             // This case shouldn't happen if logic above is correct, but log if it does
             console.warn(`[UI Update] Regenerate button for ${btn.closest('.alert')?.getAttribute('data-message-id')} should be disabled but isn't.`);
        }
    });
    // --- End Simplified Regenerate Buttons Logic ---

    console.log(`[UI Update] Final states - Send/Stop Button Disabled: ${sendButton.disabled}, Input Disabled: ${messageInput.disabled}, Upload Disabled: ${uploadFileBtn.disabled}`);

    // Update previous state for the next call
    // Use activeGenerationIds correctly when copying
    previousState = { ...newState, activeGenerationIds: new Set(newState.activeGenerationIds) }; // Store a copy using the correct Set name
}


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

// 显示终止消息提示 (Now accepts userMessageId)
function displayTerminationMessage(userMessageId) {
    // Construct the unique ID to find the correct loading indicator
    const uniqueIndicatorId = userMessageId ? `ai-response-loading-${userMessageId}` : null;
    console.log(`[displayTerminationMessage] Attempting to find indicator with specific ID: ${uniqueIndicatorId}`);

    let loadingMessage = null;
    if (uniqueIndicatorId) {
        loadingMessage = document.getElementById(uniqueIndicatorId);
    }

    // If specific ID wasn't found OR no ID was provided, try a more targeted fallback: find the *last* loading indicator
    if (!loadingMessage) {
         console.warn(`[displayTerminationMessage] Indicator with specific ID "${uniqueIndicatorId}" not found or ID not provided. Trying fallback: find the last element matching '[id^="ai-response-loading-"]'.`);
         const allLoadingIndicators = document.querySelectorAll('[id^="ai-response-loading-"]');
         if (allLoadingIndicators.length > 0) {
             loadingMessage = allLoadingIndicators[allLoadingIndicators.length - 1]; // Get the last one
             console.log(`[displayTerminationMessage] Found indicator using fallback (last matching element): ${loadingMessage.id}`);
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
        const originalUserMessageId = userMessageId || foundId.replace('ai-response-loading-', ''); // Try to preserve the original ID for the termination message
        console.log(`[displayTerminationMessage] Replacing indicator ${foundId} with termination message.`);
        // 替换加载指示器为终止消息
        // Keep a unique ID for the termination message too, using the original userMessageId if available
        loadingMessage.id = `ai-response-terminated-${originalUserMessageId || 'unknown'}`;
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

window.updateUIBasedOnState = updateUIBasedOnState;
window.rebuildMessageElement = rebuildMessageElement;
window.refreshConversationList = refreshConversationList;
window.highlightCurrentConversation = highlightCurrentConversation;
window.displayTerminationMessage = displayTerminationMessage;
