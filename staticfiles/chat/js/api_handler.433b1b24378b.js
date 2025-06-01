/* eslint-env browser */
/* globals getCookie, getStoredConversationId, storeConversationId, escapeHtml, renderMessageContent, getRealMessageId, tempIdMap, conversationId */ // Inform linter about globals

// Global variable to track the ID of the message currently being regenerated
window.regeneratingMessageId = null;

// From服务器同步会话数据
function syncConversationData(forceRefresh = false) {
    // Use the globally available conversationId or fallback to stored ID
    const syncId = conversationId || getStoredConversationId();

    if (!syncId) {
        console.log("No available conversation ID, cannot sync.");
        return Promise.resolve(); // Return resolved promise if no sync needed
    }

    console.log("Starting conversation data sync, ID:", syncId, "Force Refresh:", forceRefresh);

    const messageContainer = document.querySelector('#message-container');
    if (!messageContainer) {
        console.error("Message container element not found, cannot display sync indicator.");
        return Promise.reject("Message container not found");
    }

    // Remove existing indicator if present
    const existingIndicator = document.getElementById('sync-indicator');
    if (existingIndicator) existingIndicator.remove();

    // Show sync status indicator
    const syncIndicator = document.createElement('div');
    syncIndicator.className = 'alert alert-info';
    syncIndicator.id = 'sync-indicator';
    syncIndicator.innerHTML = `
        <div class="d-flex align-items-center">
            <span>正在同步聊天数据...</span>
            <div class="spinner-border spinner-border-sm ms-2" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
        </div>
    `;
    // Prepend indicator for visibility
    messageContainer.insertBefore(syncIndicator, messageContainer.firstChild);
    console.log("Sync indicator added.");

    const csrfToken = getCookie('csrftoken');
    if (!csrfToken) {
        console.error("CSRF token not found, sync request might fail.");
        // Optionally handle this more gracefully
    }

    // Send sync request - Return the promise chain
    return fetch('/chat/api/sync_conversation/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({
            'conversation_id': syncId
        })
    })
    .then(response => {
        console.log("Received sync response, status:", response.status);
        if (!response.ok) {
            return response.text().then(text => {
                throw new Error(`HTTP error ${response.status}: ${text}`);
            });
        }
        return response.json();
    })
    .then(data => {
        // Remove sync indicator
        const indicator = document.getElementById('sync-indicator');
        if (indicator) {
            indicator.remove();
            console.log("Sync indicator removed.");
        }

        if (data.success) {
            console.log("Conversation sync successful:", data);

            // Check if redirect is needed (sync returned a different conversation)
            const currentConversationIdOnPage = conversationId; // Use the global var
            console.log("Current page conversation ID:", currentConversationIdOnPage, "Synced conversation ID:", data.conversation.id);

            if (currentConversationIdOnPage != data.conversation.id) {
                console.log("Redirecting to synced conversation:", data.conversation.id);
                window.location.href = `/chat/?conversation_id=${data.conversation.id}`; // Use template tag alternative if needed
                return; // Stop processing if redirecting
            }

            // Render messages if needed (page just loaded, mismatch, or forced)
            if (messageContainer) {
                const existingMessages = messageContainer.querySelectorAll('.alert:not(#sync-indicator)'); // Exclude indicator
                console.log("Existing messages on page:", existingMessages.length, "Synced messages:", data.messages.length);

                // --- Adjusted Logic ---
                // Only clear the container if forceRefresh is true.
                // Otherwise, assume the initial template render is mostly correct and try to merge/update.
                if (forceRefresh) {
                    console.log("Force refresh requested. Clearing container and rendering synced messages.");
                    messageContainer.innerHTML = ''; // Clear only on force refresh
                    data.messages.forEach(msg => {
                        // (Render logic remains the same for full render)
                        const messageDiv = document.createElement('div');
                        messageDiv.className = msg.is_user ? 'alert alert-primary' : 'alert alert-secondary';
                        messageDiv.setAttribute('data-message-id', msg.id);

                        // Check for corresponding temp ID
                        for (const tempId in tempIdMap) {
                            if (tempIdMap[tempId] === msg.id) {
                                console.log(`Sync: Found temp ID mapping: ${tempId} -> ${msg.id}`);
                                messageDiv.setAttribute('data-temp-id', tempId);
                                break;
                            }
                        }

                        let timestamp = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();

                        let buttonsHTML = '';
                        if (msg.is_user) {
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

                        messageDiv.innerHTML = `
                            <div class="d-flex justify-content-between">
                                <span>${msg.is_user ? '您' : '助手'}</span>
                                <div>
                                    <small>${timestamp}</small>
                                    ${buttonsHTML}
                                </div>
                            </div>
                            <hr>
                            <p><span class="render-target" data-original-content="${escapeHtml(msg.content)}">${escapeHtml(msg.content)}</span></p>
                        `;

                        messageContainer.appendChild(messageDiv);
                        renderMessageContent(messageDiv);
                    });
                    console.log("Sync complete (force refresh). Messages rendered.");
                    // Scroll to latest message after full render
                    if (messageContainer.lastElementChild) {
                        messageContainer.lastElementChild.scrollIntoView();
                    }

                    // Trigger MathJax typesetting for the container after sync rendering
                    if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                         console.log("Sync Complete: Triggering MathJax typesetting for message container.");
                         MathJax.typesetPromise([messageContainer]).catch((err) => console.error("MathJax typesetting after sync failed:", err));
                    }
                } else { // Not forceRefresh: Check counts and decide whether to fully re-render or just ensure rendering
                    console.log("Sync: Handling non-force refresh.");
                    // --- Revised Logic ---
                    // If the number of messages in the DOM doesn't match the API response,
                    // it's safer to just re-render everything from the API data.
                    if (existingMessages.length !== data.messages.length) {
                        console.warn(`Sync: Message count mismatch (DOM: ${existingMessages.length}, API: ${data.messages.length}). Forcing full re-render.`);
                        messageContainer.innerHTML = ''; // Clear container
                        data.messages.forEach(msg => {
                            // (Render logic - copied from forceRefresh block)
                            const messageDiv = document.createElement('div');
                            messageDiv.className = msg.is_user ? 'alert alert-primary' : 'alert alert-secondary';
                            messageDiv.setAttribute('data-message-id', msg.id);
                            for (const tempId in tempIdMap) {
                                if (tempIdMap[tempId] === msg.id) {
                                    messageDiv.setAttribute('data-temp-id', tempId); break;
                                }
                            }
                            let timestamp = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
                            let buttonsHTML = '';
                            if (msg.is_user) {
                                buttonsHTML = `
                                    <button class="btn btn-sm btn-outline-primary edit-message-btn ms-2" title="编辑消息"><i class="bi bi-pencil"></i></button>
                                    <button class="btn btn-sm btn-outline-secondary regenerate-btn ms-2" title="重新生成回复"><i class="bi bi-arrow-clockwise"></i></button>
                                    <button class="btn btn-sm btn-outline-danger delete-message-btn ms-2" title="删除消息"><i class="bi bi-trash"></i></button>
                                `;
                            } else {
                                buttonsHTML = `<button class="btn btn-sm btn-outline-danger delete-message-btn ms-2" title="删除消息"><i class="bi bi-trash"></i></button>`;
                            }
                            messageDiv.innerHTML = `
                                <div class="d-flex justify-content-between">
                                    <span>${msg.is_user ? '您' : '助手'}</span>
                                    <div><small>${timestamp}</small>${buttonsHTML}</div>
                                </div>
                                <hr>
                                <p><span class="render-target" data-original-content="${escapeHtml(msg.content)}">${escapeHtml(msg.content)}</span></p>
                            `;
                            messageContainer.appendChild(messageDiv);
                            renderMessageContent(messageDiv);
                        });
                        console.log("Sync: Full re-render complete due to count mismatch.");
                        if (messageContainer.lastElementChild) {
                            messageContainer.lastElementChild.scrollIntoView();
                        }
                    } else {
                        // Counts match, just ensure rendering of existing elements
                        console.log("Sync: Message counts match. Ensuring existing messages are rendered.");
                        const existingTargets = messageContainer.querySelectorAll('.alert .render-target:not([data-rendered="true"])');
                        console.log(`Sync: Found ${existingTargets.length} existing targets needing render.`);
                        existingTargets.forEach((target, index) => {
                            const messageDiv = target.closest('.alert');
                            const messageId = messageDiv ? messageDiv.getAttribute('data-message-id') : 'unknown';
                            console.log(`Sync: Processing existing target ${index + 1} for message ID ${messageId}.`);
                            if (messageDiv) {
                                console.log(`Sync: Calling renderMessageContent for existing message ID ${messageId}.`);
                                renderMessageContent(messageDiv);
                            } else {
                                 console.warn(`Sync: Existing target ${index + 1} has no parent .alert.`);
                            }
                        });
                        console.log("Sync: Finished processing existing targets.");
                    }
                     // Also trigger typesetting for the whole container (runs in both cases now)
                     if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                         console.log("Sync Match: Triggering MathJax typesetting for message container.");
                         MathJax.typesetPromise([messageContainer]).catch((err) => console.error("MathJax typesetting after sync match failed:", err));
                     }
                }
            } else {
                console.error("Message container not found after sync success, cannot render.");
            }
        } else {
            console.error("Conversation sync failed:", data.message);
            // Display error to user
             displaySystemError("会话同步失败: " + data.message);
        }
    })
    .catch(error => {
        console.error("Sync request error:", error);
        // Remove sync indicator
        const indicator = document.getElementById('sync-indicator');
        if (indicator) indicator.remove();

        // Handle specific errors like 404 (conversation not found)
        if (error.message && error.message.includes('404')) {
            localStorage.removeItem('currentConversationId');
            console.log("Conversation not found (404), cleared localStorage ID.");
            // Redirect to create a new conversation (adjust URL as needed)
            window.location.href = "/chat/?no_new=0"; // Use template tag alternative if needed
        } else {
            // Display generic error
            displaySystemError("同步聊天数据时出错，请尝试刷新页面。 Error: " + error.message);
        }
    });
}

// 删除对话的函数 (Restored confirm())
function deleteConversation(convIdToDelete) {
    // Restore the confirmation dialog
    const clickTimestamp = Date.now(); // Timestamp for logging
    console.log(`[${clickTimestamp}] deleteConversation called for ID: ${convIdToDelete}`);
    if (confirm('确定要删除这个对话吗？此操作不可恢复。')) {
        console.log(`[${clickTimestamp}] User confirmed deletion.`);
        // console.warn("DEBUG: Skipping delete confirmation!"); // Remove debug log
        const storedId = getStoredConversationId();
        let isDeletingCurrent = false; // Flag to track if deleting the active one
        if (storedId == convIdToDelete) {
            isDeletingCurrent = true;
            // Remove stored ID immediately to prevent potential race conditions during redirect
            localStorage.removeItem('currentConversationId');
            console.log(`[${clickTimestamp}] Deleting current conversation, cleared localStorage ID: ${convIdToDelete}`);
        }

        console.log(`[${clickTimestamp}] Sending DELETE request to /chat/api/conversations/`);
        fetch('/chat/api/conversations/', {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({ 'id': convIdToDelete })
        })
        .then(response => {
            console.log(`[${clickTimestamp}] Received response with status: ${response.status}, ok: ${response.ok}`);
            // Check status code first
            if (response.ok) { // Status 200-299
                console.log(`[${clickTimestamp}] Response OK, attempting to parse JSON.`);
                // Try to parse JSON, but handle potential empty response for 204 No Content
                return response.text().then(text => {
                    if (text) {
                        try {
                            return JSON.parse(text);
                        } catch (e) {
                            console.error(`[${clickTimestamp}] JSON parse error for OK response:`, e);
                            throw new Error(`服务器响应成功但无法解析内容 (Status: ${response.status})`);
                        }
                    } else {
                         console.log(`[${clickTimestamp}] Response OK but body is empty (likely 204 No Content). Assuming success.`);
                         return { success: true, message: "删除成功 (无内容返回)" }; // Simulate success object
                    }
                });
            } else {
                console.log(`[${clickTimestamp}] Response not OK. Throwing error.`);
                // Try to get text for error context, but don't rely on it always being JSON
                return response.text().then(text => {
                     console.error(`[${clickTimestamp}] Server error response text:`, text);
                     throw new Error(`删除失败 (HTTP ${response.status}): ${text || '无错误详情'}`);
                });
            }
        })
        .then(data => {
            // This block only runs if response.ok was true and JSON parsing (or simulation) succeeded
            console.log(`[${clickTimestamp}] Processing successful response data:`, data);
            if (data.success) {
                console.log(`[${clickTimestamp}] Deletion successful according to data. Redirecting...`);
                // Redirect ONLY on successful deletion
                window.location.href = "/chat/?no_new=1"; // Redirect to clear state
            } else {
                // This case might happen if backend returns 200 OK but success: false
                console.error(`[${clickTimestamp}] Deletion failed according to data:`, data.message);
                alert('删除失败: ' + (data.message || '服务器返回成功但操作未完成'));
            }
        })
        .catch(error => {
            // Catches network errors and errors thrown from the .then blocks
            console.error(`[${clickTimestamp}] Error caught in deleteConversation catch block:`, error);
            // Provide more specific feedback from the error message we created
            alert(`删除对话时出错: ${error.message || '未知错误'}`);
            // if (isDeletingCurrent) {
            //     console.log("Restoring stored ID due to deletion error for current conversation.");
            //     storeConversationId(convIdToDelete); // Restore if deletion failed? Risky.
            // }
        });
    } // End of confirm() block
}

// 编辑对话标题
function editConversationTitle(convIdToEdit) {
    // Find title element using a more specific selector if possible
    const titleElement = document.querySelector(`#conversation-list a[href*="conversation_id=${convIdToEdit}"] .conversation-title`);
    // Fallback if the above is too specific or structure changes
    // const titleElement = document.querySelector(`.conversation-title[data-conversation-id="${convIdToEdit}"]`);

    if (!titleElement) {
        console.error("Could not find title element for conversation ID:", convIdToEdit);
        return;
    }

    const currentTitle = titleElement.textContent.trim();

    // Create input element
    const inputElement = document.createElement('input');
    inputElement.type = 'text';
    inputElement.value = currentTitle;
    inputElement.className = 'form-control form-control-sm d-inline-block w-auto'; // Adjust classes as needed

    // Replace title with input
    titleElement.innerHTML = '';
    titleElement.appendChild(inputElement);
    inputElement.focus();
    inputElement.select();

    // Save function
    function saveTitle() {
        const newTitle = inputElement.value.trim();
        // Restore original title visually first
        titleElement.textContent = currentTitle;

        if (newTitle !== '' && newTitle !== currentTitle) {
            console.log(`Updating title for ${convIdToEdit} to "${newTitle}"`);
            fetch('/chat/api/conversations/', {
                method: 'POST', // Assuming POST updates title
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({ 'id': convIdToEdit, 'title': newTitle })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    titleElement.textContent = newTitle; // Update on success
                    // Also update the header if editing the current conversation
                    if (conversationId == convIdToEdit) {
                         const headerTitle = document.querySelector('.card-header span:first-child');
                         if (headerTitle) headerTitle.textContent = `当前对话：${newTitle}`;
                    }
                } else {
                    alert('更新标题失败: ' + data.message);
                    // Title already restored visually
                }
            })
            .catch(error => {
                console.error('Error updating title:', error);
                alert('更新标题时出错，请稍后再试。');
                // Title already restored visually
            });
        } else {
             console.log("Title unchanged or empty, reverting.");
             // Title already restored visually
        }
        // Clean up listeners
        inputElement.removeEventListener('blur', saveTitle);
        inputElement.removeEventListener('keypress', handleKeyPress);
    }

    // Keypress handler
    function handleKeyPress(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveTitle();
        } else if (e.key === 'Escape') {
             // Restore original title and remove input on Escape
             titleElement.textContent = currentTitle;
             inputElement.removeEventListener('blur', saveTitle);
             inputElement.removeEventListener('keypress', handleKeyPress);
        }
    }

    // Add listeners
    inputElement.addEventListener('blur', saveTitle);
    inputElement.addEventListener('keypress', handleKeyPress);

    // Prevent link navigation when clicking input
    inputElement.addEventListener('click', function(e) {
        e.stopPropagation(); // Stop click from propagating to the parent link
    });
}


// 删除消息 (Handles both temp and real IDs)
function deleteMessage(messageId, messageDiv) {
    if (!messageId || !messageDiv) return;

    // Handle temporary message deletion
    if (messageId.startsWith('temp-')) {
        console.log("Deleting temporary message:", messageId);
        messageDiv.remove();
        // If it's a user message, remove the next AI message (likely the loading/response placeholder)
        if (messageDiv.classList.contains('alert-primary')) {
            const nextElement = messageDiv.nextElementSibling;
            if (nextElement && nextElement.classList.contains('alert-secondary')) {
                nextElement.remove();
                console.log("Removed associated temporary AI response placeholder.");
            }
        }
        return;
    }

    // Handle real message deletion
    if (confirm('确定要删除这条消息吗？此操作不可恢复。')) {
        console.log("Requesting deletion for message ID:", messageId);
        fetch('/chat/api/messages/delete/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({ 'message_id': messageId })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                console.log("Message deleted successfully from server.");
                const messageToRemove = document.querySelector(`.alert[data-message-id="${messageId}"]`);
                let nextAIMessageToRemove = null;

                // If deleting a user message, find the next AI message
                if (messageToRemove && messageToRemove.classList.contains('alert-primary')) {
                    let nextSibling = messageToRemove.nextElementSibling;
                    // Skip non-alert elements if any exist between messages
                    while(nextSibling && !nextSibling.classList.contains('alert')) {
                        nextSibling = nextSibling.nextElementSibling;
                    }
                    if (nextSibling && nextSibling.classList.contains('alert-secondary')) {
                        nextAIMessageToRemove = nextSibling;
                    }
                }

                // Remove the message(s) from DOM
                if (messageToRemove) messageToRemove.remove();
                if (nextAIMessageToRemove) nextAIMessageToRemove.remove();
                console.log("Message(s) removed from DOM.");

                // Optional: Sync data after a short delay to ensure consistency across clients
                // setTimeout(() => syncConversationData(true), 500);
            } else {
                alert('删除失败: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error deleting message:', error);
            alert('删除消息时出错，请稍后再试。');
        });
    }
}

// 保存编辑后的消息 (Handles both temp and real IDs)
function saveMessageEdit(messageId, newContent, messageDiv, originalHTML) {
    console.log(`Attempting to save edit for message: ${messageId}`);

    // 完全重构消息元素的内容部分
    function rebuildMessageContent(container, content) {
        // 找到消息内容的p元素
        const contentP = container.querySelector('p');
        if (!contentP) {
            console.error("Cannot find p element in message div");
            return false;
        }
        
        // 清空p元素内容
        contentP.innerHTML = '';
        
        // 创建新的render-target元素
        const renderTarget = document.createElement('span');
        renderTarget.className = 'render-target';
        renderTarget.setAttribute('data-original-content', content);
        
        // 将新元素添加到p中
        contentP.appendChild(renderTarget);
        
        // 确保renderMessageContent函数在下一轮事件循环中执行
        setTimeout(() => {
            console.log("Calling renderMessageContent after rebuild");
            renderMessageContent(container);
            
            // 再次延迟调用MathJax渲染，确保内容已完全加载
            setTimeout(() => {
                if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                    console.log("Force typesetting after edit with MathJax");
                    MathJax.typesetPromise([container]).catch(err => 
                        console.error("MathJax error:", err)
                    );
                }
            }, 100);
        }, 0);
        
        return true;
    }

    // 恢复原始的DOM结构（按钮、布局等）
    messageDiv.innerHTML = originalHTML;
    messageDiv.removeAttribute('data-original-html');

    // 处理临时消息编辑（仅更新DOM）
    if (messageId.startsWith('temp-')) {
        console.log("Editing temporary message (local only)");
        if (rebuildMessageContent(messageDiv, newContent)) {
            console.log("Temporary message updated successfully");
        } else {
            console.error("Failed to update temporary message content");
        }
        return;
    }

    // 处理真实消息编辑（发送到服务器）
    const realId = getRealMessageId(messageId) || messageId;

    console.log(`Sending edit request for message ID: ${realId}`);
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
            console.log("Message edit saved successfully on server");
            
            // 重建消息内容
            if (rebuildMessageContent(messageDiv, newContent)) {
                console.log("Message content updated successfully in DOM");
                
                // 如果是用户消息，触发重新生成
                if (messageDiv.classList.contains('alert-primary')) {
                    console.log("User message edited, triggering regeneration");
                    regenerateResponse(realId);
                }
            } else {
                console.error("Failed to update message content in DOM");
            }
        } else {
            alert('更新消息失败: ' + data.message);
            // 获取原始内容
            const originalRenderTarget = messageDiv.querySelector('p > .render-target');
            const originalContent = originalRenderTarget ? 
                originalRenderTarget.getAttribute('data-original-content') : '';
                
            // 重建原始内容
            if (originalContent) {
                rebuildMessageContent(messageDiv, originalContent);
            }
        }
    })
    .catch(error => {
        console.error('Error updating message:', error);
        alert('更新消息时出错，请稍后再试。');
        
        // 获取原始内容
        const originalRenderTarget = messageDiv.querySelector('p > .render-target');
        const originalContent = originalRenderTarget ? 
            originalRenderTarget.getAttribute('data-original-content') : '';
            
        // 重建原始内容
        if (originalContent) {
            rebuildMessageContent(messageDiv, originalContent);
        }
    });
}

/**
 * 检查指定消息是否请求了终止生成
 * @param {string} messageId - 要检查的消息ID
 * @return {boolean} - 如果消息请求了终止则返回true
 */
function isStopRequestedForMessage(messageId) {
    if (!messageId) return false;
    
    // 查找用户消息元素，支持临时ID和正式ID
    const userDiv = document.querySelector(`.alert[data-message-id="${messageId}"], .alert[data-temp-id="${messageId}"]`);
    if (!userDiv) return false;
    
    // 检查是否设置了终止请求标志
    return userDiv.getAttribute('data-stop-requested') === 'true';
}

// 重新生成AI回复
function regenerateResponse(userMessageId) {
    console.log(`[Regen Start ${userMessageId}] 开始执行。`);

    // 安全检查：确保有效的消息ID
    if (!userMessageId) {
        console.error("无法重新生成：消息ID无效");
        return;
    }

    // 如果已经在处理终止，不启动新的生成
    if (window.isTerminationInProgress) {
        console.log(`[Regen Start ${userMessageId}] 终止正在进行中，不启动新的生成`);
        return;
    }

    // Declare userMessageDiv ONCE at the beginning
    const userMessageDiv = document.querySelector(`.alert[data-message-id="${userMessageId}"], .alert[data-temp-id="${userMessageId}"]`);
    if (!userMessageDiv) {
        console.error(`[Regen Start ${userMessageId}] Could not find user message div.`);
        return;
    }

    // --- Check if a stop was requested specifically for THIS message ---
    // This check remains important in case the stop happened *before* this function was called
    if (userMessageDiv.getAttribute('data-stop-requested') === 'true') {
        console.warn(`[Regen Start ${userMessageId}] Blocked: Stop was previously requested for this specific message.`);
        displaySystemError("此消息的停止请求正在处理中...", userMessageDiv); // More specific error
        return; // Prevent regeneration from starting
    }
    // --- End specific stop check ---

    const regenBtn = userMessageDiv.querySelector('.regenerate-btn'); // Use the existing userMessageDiv
    if (!regenBtn) {
        console.error("Could not find regenerate button for message ID:", userMessageId);
        return;
    }

    // --- Prevent starting if already regenerating (check button attribute) ---
    console.log(`[Regen Check ${userMessageId}] Checking data-regenerating attribute. Current value:`, regenBtn.getAttribute('data-regenerating'));
    if (regenBtn.getAttribute('data-regenerating') === 'true') {
        console.warn(`[Regen Check ${userMessageId}] Blocked: Regeneration for message ID ${userMessageId} is already in progress (button state). Ignoring duplicate request.`);
        displaySystemError("请稍候，上一次请求仍在处理中...", userMessageDiv); // Show brief error
        // No need to reset button here, the existing process's finally block should handle it.
        return; // Stop execution
    }
    // --- End prevention check ---

    // Clear any previous stop request flag for this message before starting
    userMessageDiv.removeAttribute('data-stop-requested');
    console.log(`[Regen Start ${userMessageId}] Cleared data-stop-requested attribute.`);

    // Mark button as regenerating and disable
    regenBtn.setAttribute('data-regenerating', 'true');
    console.log(`[Regen Start ${userMessageId}] Set data-regenerating="true".`);
    regenBtn.classList.add('btn-processing'); // Add spinner class
    regenBtn.disabled = true;
    regenBtn.innerHTML = '<i class="bi bi-arrow-clockwise animate-spin"></i>'; // Show spinner

    // --- Set global generating flag ---
    window.isGeneratingResponse = true; // Mark that *a* generation process has started
    console.log(`[Regen Start ${userMessageId}] Set window.isGeneratingResponse = true.`);
    // --- End setting global flag ---

    console.log(`[Regen Start ${userMessageId}] Proceeding with regeneration for user message ID: ${userMessageId}`);

    const modelSelect = document.querySelector('#model-select');
    if (!modelSelect) {
         console.error("Model select dropdown not found.");
         alert("无法找到模型选择器，无法重新生成。");
         return;
    }
    const modelId = modelSelect.value;
    console.log("Using model ID:", modelId);

    // Resolve temporary ID if necessary
    let realUserMessageId = userMessageId;
    if (userMessageId.startsWith('temp-')) {
        realUserMessageId = getRealMessageId(userMessageId);
        if (!realUserMessageId) {
            console.warn("Could not resolve temporary ID for regeneration:", userMessageId);
            // Try syncing and retrying after a delay
            syncConversationData().then(() => {
                setTimeout(() => {
                    const resolvedId = getRealMessageId(userMessageId);
                    if (resolvedId) {
                        console.log("Successfully resolved temp ID after sync:", resolvedId);
                        regenerateResponse(resolvedId); // Retry with resolved ID
                    } else {
                        console.error("Still unable to resolve temp ID after sync:", userMessageId);
                        alert('无法重新生成临时消息的回复，请等待消息保存或刷新页面后重试。');
                        // Restore button state if needed (assuming button was disabled)
                        const userMessageDiv = document.querySelector(`.alert[data-temp-id="${userMessageId}"]`);
                        const regenBtn = userMessageDiv?.querySelector('.regenerate-btn');
                        if (regenBtn) {
                             regenBtn.classList.remove('btn-processing');
                             regenBtn.disabled = false;
                             regenBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
                        }
                    }
                }, 500);
            }).catch(error => {
                console.error("Sync failed during regenerate temp ID resolution:", error);
                alert('同步数据失败，无法重新生成回复。请刷新页面后重试。');
            });
            return; // Exit early, wait for sync/retry
        }
        console.log(`Resolved temp ID ${userMessageId} to ${realUserMessageId}`);
    }

    const messageContainer = document.querySelector('#message-container');
    if (!messageContainer) {
        console.error("Message container not found.");
        // Restore button state before returning
        if (regenBtn) {
            regenBtn.removeAttribute('data-regenerating');
            regenBtn.classList.remove('btn-processing');
            regenBtn.disabled = false;
            regenBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
        }
        return;
    }

    // --- REMOVED Redundant declaration of userMessageDiv ---
    // We already declared userMessageDiv at the top using the initial userMessageId.
    // If it was a temp ID, realUserMessageId will be updated, but userMessageDiv element remains the same.
    // We just need to ensure the element still exists after potential async operations.
    if (!document.body.contains(userMessageDiv)) {
         console.error("User message div no longer exists in DOM after resolving ID for:", realUserMessageId);
         // Button state might already be reset by finally, or was never set if temp ID failed early
        return;
    }

    // Remove subsequent AI messages
    let nextElement = userMessageDiv.nextElementSibling;
    const messagesToRemove = [];
    while (nextElement) {
        if (nextElement.classList.contains('alert')) {
            if (nextElement.classList.contains('alert-secondary')) {
                messagesToRemove.push(nextElement);
            } else {
                // Stop if we hit another user message
                break;
            }
        }
        nextElement = nextElement.nextElementSibling;
    }
    console.log(`Removing ${messagesToRemove.length} subsequent AI message(s).`);
    messagesToRemove.forEach(msg => msg.remove());

    // Add loading indicator FIRST with unique ID
    const uniqueIndicatorId = `ai-response-loading-${userMessageId}`;
    const loadingDiv = createLoadingIndicator(uniqueIndicatorId); // Pass ID
    userMessageDiv.insertAdjacentElement('afterend', loadingDiv);
    loadingDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    // THEN Show the stop button
    if (typeof showStopGenerationButton === 'function') {
        console.log("显示重新生成时的终止按钮");
        showStopGenerationButton();
    } else {
        console.warn("找不到showStopGenerationButton函数");
        // 应急处理，设置基本状态标志
        window.isGeneratingResponse = true;
        window.terminationRequestSent = false;
        window.isTerminationInProgress = false;
    }

    // 发送重新生成请求
    console.log("发送重新生成请求，消息ID:", realUserMessageId);
    
    // 增加重试机制
    const maxRetries = 2;
    let retryCount = 0;
    
    function sendRegenerateRequest() {
        fetch('/chat/api/messages/regenerate/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({
                'message_id': realUserMessageId,
                'model_id': modelId  // 添加必要的model_id参数
            })
        })
        .then(response => {
            if (!response.ok) {
                const error = new Error(`HTTP错误: ${response.status}`);
                error.status = response.status;
                throw error;
            }
            return response.json();
        })
        .then(data => {
            console.log("重新生成响应数据:", data);
            
            // 检查是否终止
            const stopRequestedForThis = isStopRequestedForMessage(userMessageId);
            if (window.isTerminationInProgress || stopRequestedForThis) {
                console.log(`[Regen Success ${userMessageId}] 忽略响应。终止进行中: ${window.isTerminationInProgress}, 此消息请求终止: ${stopRequestedForThis}`);
                
                // 如果没有其他活跃的重新生成，隐藏停止按钮
                if (!document.querySelector('.regenerate-btn[data-regenerating="true"]')) {
                    console.log(`[Regen Success ${userMessageId}] 没有其他活跃的重新生成。调用hideStopGenerationButton。`);
                    if (typeof hideStopGenerationButton === 'function') hideStopGenerationButton();
                }
                return;
            }
            
            // 处理成功响应
            if (data.success) {
                console.log(`[Regen Success ${userMessageId}] Rendering new message ID: ${data.message_id}`);

                // --- 在插入新消息前，移除本次重新生成对应的加载指示器 ---
                const uniqueIndicatorId = `ai-response-loading-${userMessageId}`;
                const loadingIndicator = document.getElementById(uniqueIndicatorId);
                if (loadingIndicator && loadingIndicator.parentNode) {
                    loadingIndicator.remove();
                    console.log(`[Regen Success ${userMessageId}] 已移除加载指示器: ${uniqueIndicatorId}`);
                } else {
                    // 如果找不到特定ID的指示器，尝试移除通用的指示器（作为后备）
                    const genericLoadingIndicator = document.getElementById('ai-response-loading');
                    if (genericLoadingIndicator && genericLoadingIndicator.parentNode) {
                        genericLoadingIndicator.remove();
                         console.warn(`[Regen Success ${userMessageId}] 未找到特定加载指示器 ${uniqueIndicatorId}，但移除了通用指示器`);
                    } else {
                        console.warn(`[Regen Success ${userMessageId}] 无法找到要移除的加载指示器: ${uniqueIndicatorId}`);
                    }
                }
                // --- 结束移除加载指示器 ---

                const aiMessageDiv = createAIMessageDiv(data.content, data.message_id, data.timestamp);
                // Ensure userMessageDiv still exists before inserting after it
                if (document.body.contains(userMessageDiv)) {
                    userMessageDiv.insertAdjacentElement('afterend', aiMessageDiv);
                    renderMessageContent(aiMessageDiv); // Render the new message
                    aiMessageDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                } else {
                     console.warn(`[Regen Success ${userMessageId}] User message div disappeared before AI response could be inserted.`);
                }
            } else {
                // Handle backend errors (e.g., explicit termination message from API, other errors)
                console.error(`[Regen Fail ${userMessageId}] Regeneration failed: ${data.message}`);
                // Display error ONLY IF it's not a user termination message from the API itself
                if (data.message !== '生成已被用户终止') {
                    displaySystemError(`重新生成回复失败: ${data.message}`, userMessageDiv);
                } else {
                    console.log(`[Regen Fail ${userMessageId}] Regeneration terminated by user (API response). Skipping system error display.`);
                    
                    // 特殊处理服务器返回的"生成已被用户终止"消息
                    console.log(`[Regen Fail ${userMessageId}] 收到生成被终止的消息，强制重置所有状态`);
                    
                    // 清除任何活跃的重新生成标志
                    const allRegenBtns = document.querySelectorAll('.regenerate-btn[data-regenerating="true"]');
                    allRegenBtns.forEach(btn => {
                        btn.removeAttribute('data-regenerating');
                        btn.classList.remove('btn-processing');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
                    });
                    
                    // 使用hideStopGenerationButton重置所有状态
                    if (typeof hideStopGenerationButton === 'function') {
                        hideStopGenerationButton();
                    } else {
                        // 备用方案：直接重置状态变量
                        window.isGeneratingResponse = false;
                        window.terminationRequestSent = false;
                        window.isTerminationInProgress = false;
                        console.log(`[Regen Fail ${userMessageId}] 已强制重置状态变量`);
                    }
                    
                    // 移除加载指示器
                    const uniqueIndicatorId = `ai-response-loading-${userMessageId}`;
                    const loadingIndicator = document.getElementById(uniqueIndicatorId);
                    if (loadingIndicator && loadingIndicator.parentNode) {
                        loadingIndicator.remove();
                    }
                    
                    // 提前返回，不执行后续检查
                    return;
                }
            }
            
            // 检查是否还有其他活跃的重新生成
            if (!document.querySelector('.regenerate-btn[data-regenerating="true"]:not([data-message-id="' + userMessageId + '"])')) {
                console.log(`[Regen Success/Fail ${userMessageId}] 没有其他活跃的重新生成。调用hideStopGenerationButton。`);
                if (typeof hideStopGenerationButton === 'function') hideStopGenerationButton();
            } else {
                console.log(`[Regen Success/Fail ${userMessageId}] 其他重新生成仍然活跃。保持停止按钮可见。`);
            }
        })
        .catch(error => {
            console.error('重新生成响应出错:', error);
            
            // 重试逻辑
            if (retryCount < maxRetries && !window.isTerminationInProgress) {
                retryCount++;
                console.log(`重新生成请求失败，尝试重试 #${retryCount}/${maxRetries}`);
                setTimeout(() => {
                    sendRegenerateRequest();
                }, 1000 * retryCount); // 每次重试增加延迟
                return;
            }
            
            console.log(`[regenerateResponse .catch] 捕获错误。isTerminationInProgress = ${window.isTerminationInProgress}`);
            
            // 移除加载指示器
            const uniqueIndicatorId = `ai-response-loading-${userMessageId}`;
            const loadingIndicator = document.getElementById(uniqueIndicatorId);
            if (loadingIndicator) {
                setTimeout(() => {
                    if (loadingIndicator.parentNode) {
                        loadingIndicator.remove();
                        console.log("[Regen .catch] 延迟后移除加载指示器。");
                    }
                }, 300);
            }
            
            // 显示错误消息
            const errorMsg = `重新生成失败: ${error.message || '未知错误'}`;
            
            // 重置重新生成按钮状态
            const regenBtn = document.querySelector(`.regenerate-btn[data-message-id="${userMessageId}"]`);
            if (regenBtn) {
                regenBtn.removeAttribute('data-regenerating');
                regenBtn.classList.remove('btn-processing');
                regenBtn.disabled = false;
                regenBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
            }
            
            // 检查是否还有其他活跃的重新生成
            const otherRegenActive = !!document.querySelector('.regenerate-btn[data-regenerating="true"]');
            if (!otherRegenActive) {
                window.isGeneratingResponse = false;
                console.log(`[Regen .catch ${userMessageId}] 没有其他活跃的重新生成。确保停止按钮隐藏。`);
                if (typeof hideStopGenerationButton === 'function') hideStopGenerationButton();
            }
        })
        .finally(() => {
            // 重置重新生成按钮
            const regenBtn = document.querySelector(`.alert[data-message-id="${userMessageId}"] .regenerate-btn, .alert[data-temp-id="${userMessageId}"] .regenerate-btn`); // More specific selector
            if (regenBtn) {
                regenBtn.removeAttribute('data-regenerating');
                regenBtn.classList.remove('btn-processing');
                regenBtn.disabled = false;
                regenBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
                console.log(`[Regen Finally ${userMessageId}] 已重置重新生成按钮状态`);
            } else {
                 console.error(`[Regen Finally ${userMessageId}] 错误：无法找到要重置的重新生成按钮！选择器: .alert[data-message-id="${userMessageId}"] .regenerate-btn`);
            }
            
            // 检查是否还有其他活跃的重新生成
            // 使用强制延迟确保DOM已更新
            setTimeout(() => {
                // 更准确地检查是否有其他活跃的重新生成按钮
                const activeRegenBtns = document.querySelectorAll('.regenerate-btn[data-regenerating="true"]');
                const otherRegenActive = activeRegenBtns.length > 0;
                
                console.log(`[Regen Finally ${userMessageId}] 检测到${activeRegenBtns.length}个活跃的重新生成按钮`);
                
                if (!otherRegenActive) {
                    console.log(`[Regen Finally ${userMessageId}] 没有其他活跃的重新生成。设置 window.isGeneratingResponse = false。`);
                    window.isGeneratingResponse = false;
                    console.log(`[Regen Finally ${userMessageId}] 确保停止按钮隐藏。`);
                    if (typeof hideStopGenerationButton === 'function') hideStopGenerationButton();
                } else {
                    // 仍显示活跃状态，但这可能是UI状态不一致导致的
                    console.log(`[Regen Finally ${userMessageId}] 警告：检测到${activeRegenBtns.length}个活跃的重新生成按钮，但可能是状态不一致导致的`);
                    console.log(`[Regen Finally ${userMessageId}] 为安全起见，强制重置状态`);
                    window.isGeneratingResponse = false;
                    if (typeof hideStopGenerationButton === 'function') hideStopGenerationButton();
                }
            }, 10); // 短延迟确保DOM已更新
        });
    }
    
    // 启动请求
    sendRegenerateRequest();
}

// Helper to create AI loading indicator with a unique ID
function createLoadingIndicator(uniqueId) {
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'alert alert-secondary';
    loadingDiv.id = uniqueId; // Use the provided unique ID
    const timestamp = new Date().toLocaleTimeString();
    loadingDiv.innerHTML = `
        <div class="d-flex justify-content-between">
            <span>助手</span>
            <div>
                <small>${timestamp}</small>
                <div class="spinner-border spinner-border-sm ms-2" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
            </div>
        </div>
        <hr>
    `;
    return loadingDiv;
}

// Helper to create AI message div
function createAIMessageDiv(content, messageId, isoTimestamp) {
    const aiMessageDiv = document.createElement('div');
    aiMessageDiv.className = 'alert alert-secondary';
    if (messageId) {
        aiMessageDiv.setAttribute('data-message-id', messageId);
    }
    // Parse timestamp safely
    let displayTimestamp = '时间未知';
    if (isoTimestamp) {
        try {
            // Attempt to parse ISO 8601 timestamp
            displayTimestamp = new Date(isoTimestamp).toLocaleTimeString();
        } catch (e) {
            console.error("Error parsing timestamp from API:", isoTimestamp, e);
            // Keep '时间未知' or use the raw string if preferred
            // displayTimestamp = isoTimestamp; // Fallback to raw string
        }
    } else {
         displayTimestamp = new Date().toLocaleTimeString(); // Fallback to current time
    }

    aiMessageDiv.innerHTML = `
        <div class="d-flex justify-content-between">
            <span>助手</span>
            <div>
                <small>${displayTimestamp}</small>
                ${messageId ? // Only add delete button if we have an ID
                `<button class="btn btn-sm btn-outline-danger delete-message-btn ms-2" title="删除消息">
                    <i class="bi bi-trash"></i>
                </button>` : ''}
            </div>
        </div>
        <hr>
        <p><span class="render-target" data-original-content="${escapeHtml(content)}">${escapeHtml(content)}</span></p>
    `;
    return aiMessageDiv;
}

// Helper to display system errors in the chat interface
function displaySystemError(errorMessage, insertAfterElement = null) {
     const errorDiv = document.createElement('div');
     errorDiv.className = 'alert alert-danger';
     errorDiv.innerHTML = `
         <div class="d-flex justify-content-between">
             <span>系统</span>
             <div>
                 <small>${new Date().toLocaleTimeString()}</small>
                 <button class="btn btn-sm btn-close ms-2" aria-label="关闭"></button>
             </div>
         </div>
         <hr>
         <p>${escapeHtml(errorMessage)}</p>
     `;
     
     // 添加关闭按钮事件
     const closeBtn = errorDiv.querySelector('.btn-close');
     if (closeBtn) {
         closeBtn.addEventListener('click', function() {
             errorDiv.remove();
         });
     }
     
     const messageContainer = document.querySelector('#message-container');
     if (insertAfterElement && insertAfterElement.parentNode === messageContainer) {
          insertAfterElement.insertAdjacentElement('afterend', errorDiv);
     } else if (messageContainer) {
          messageContainer.appendChild(errorDiv);
     } else {
          console.error("Cannot display system error, message container not found.");
          return; // Exit if no container
     }
     errorDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
     
     // 5秒后自动消失
     setTimeout(() => {
         if (errorDiv.parentNode) {
             errorDiv.style.opacity = '0';
             errorDiv.style.transition = 'opacity 0.5s ease';
             setTimeout(() => errorDiv.remove(), 500);
         }
     }, 5000);
}
