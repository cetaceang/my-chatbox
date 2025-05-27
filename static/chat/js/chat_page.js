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


                const tempId = 'temp-' + Date.now();

                // Display user message immediately
                const userMessageDiv = document.createElement('div');
                userMessageDiv.className = 'alert alert-primary';
                userMessageDiv.setAttribute('data-message-id', tempId);
                userMessageDiv.setAttribute('data-temp-id', tempId); // Store temp ID
                userMessageDiv.setAttribute('data-waiting-id', '1'); // Mark as waiting for real ID
                const timestamp = new Date().toLocaleTimeString();
                userMessageDiv.innerHTML = `
                    <div class="d-flex justify-content-between">
                        <span>您</span>
                        <div>
                            <small>${timestamp}</small>
                            <button class="btn btn-sm btn-outline-primary edit-message-btn ms-2" title="编辑消息">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-secondary regenerate-btn ms-2" title="重新生成回复">
                                <i class="bi bi-arrow-clockwise"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger delete-message-btn ms-2" title="删除消息">
                                <i class="bi bi-trash"></i>
                            </button>
                        </div>
                    </div>
                    <hr>
                    <p><span class="render-target" data-original-content="${escapeHtml(message)}">${escapeHtml(message)}</span></p>
                `;
                if (messageContainer) {
                    messageContainer.appendChild(userMessageDiv);
                    renderMessageContent(userMessageDiv); // Render user message
                    userMessageDiv.scrollIntoView();
                }

                // Add AI loading indicator
                const loadingDiv = createLoadingIndicator(); // Use helper from api_handler
                 if (messageContainer) {
                    messageContainer.appendChild(loadingDiv);
                    loadingDiv.scrollIntoView();
                 }


                // Attempt to send via WebSocket first
                const sentViaWebSocket = sendWebSocketMessage(message, modelId, tempId);

                if (!sentViaWebSocket) {
                    console.log("WebSocket unavailable, falling back to HTTP API for sending.");
                    // Fallback to HTTP API
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
                        // Update user message ID if provided by API response
                        if (data.user_message_id) {
                            tempIdMap[tempId] = data.user_message_id;
                            console.log(`API Response: Updated tempIdMap: ${tempId} -> ${data.user_message_id}`);
                            const msgDiv = messageContainer?.querySelector(`.alert[data-temp-id="${tempId}"]`);
                            if (msgDiv) {
                                msgDiv.setAttribute('data-message-id', data.user_message_id);
                                msgDiv.removeAttribute('data-waiting-id');
                                console.log(`API Response: Updated DOM user message ID.`);
                            }
                        }

                        // --- Handle New Conversation Creation (HTTP API) ---
                        // Assumes backend returns 'new_conversation_id' when a new convo is created
                        if (isNewConversation && data.success && data.new_conversation_id) {
                            console.log("New conversation created via API. New ID:", data.new_conversation_id);
                            window.conversationId = data.new_conversation_id; // Update global ID
                            storeConversationId(window.conversationId); // Store it

                            // Update the URL without reloading the page
                            const newUrl = `/chat/?conversation_id=${window.conversationId}`;
                            history.pushState({ conversationId: window.conversationId }, '', newUrl);
                            console.log("URL updated to:", newUrl);

                            // Refresh the conversation list in the sidebar
                            refreshConversationList();

                            // Initialize WebSocket with the new ID
                            initWebSocket();
                        }
                        // --- End New Conversation Handling ---


                        // Remove loading indicator
                        loadingDiv.remove();

                        if (data.success) {
                            // Display AI response
                            const aiMessageDiv = createAIMessageDiv(data.message, data.message_id, data.timestamp); // Use helper
                            if (messageContainer) {
                                messageContainer.appendChild(aiMessageDiv);
                                renderMessageContent(aiMessageDiv); // Render AI message
                                aiMessageDiv.scrollIntoView();
                            }
                        } else {
                            console.error("API Error:", data.message);
                            // If creating a new conversation failed, reset conversationId
                            if (isNewConversation) {
                                window.conversationId = null;
                            }
                            displaySystemError(`处理AI回复出错: ${data.message}`); // Use helper
                        }
                    })
                    .catch(error => {
                        console.error('Error sending message via API:', error);
                        loadingDiv.remove(); // Ensure loading removed on error
                        displaySystemError(`发送消息失败: ${error.message}`); // Use helper
                    })
                    .finally(() => {
                        // Re-enable input/button regardless of API success/failure
                        messageInput.value = '';
                        messageInput.disabled = false;
                        sendButton.disabled = false;
                        sendButton.classList.remove('btn-processing');
                        sendButton.innerHTML = '<i class="bi bi-send"></i>';
                        messageInput.focus();
                    });
                } else {
                     // If sent via WebSocket, re-enable UI immediately
                     messageInput.value = '';
                     messageInput.disabled = false;
                     sendButton.disabled = false;
                     sendButton.classList.remove('btn-processing');
                      sendButton.innerHTML = '<i class="bi bi-send"></i>';
                      messageInput.focus();
                 }
            } else if (message === '') {
                 // Handle empty message case separately if needed
                 console.log("Message input is empty.");
            } else if (!window.conversationId && !isNewConversation) {
                 // This case should ideally not happen if logic is correct
                 console.error("State inconsistency: No conversation ID and not flagged as new conversation.");
                 alert("出现错误，请刷新页面。");
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
                const renderTarget = messageDiv.querySelector('p > .render-target');
                if (!renderTarget) return; // Cannot edit if structure is wrong

                const originalContent = renderTarget.getAttribute('data-original-content');
                const originalHTML = messageDiv.innerHTML; // Store original structure

                // Replace content with textarea
                messageDiv.innerHTML = `
                    <div class="mb-2">
                        <textarea class="form-control edit-textarea" rows="3">${escapeHtml(originalContent)}</textarea>
                    </div>
                    <div class="d-flex justify-content-end">
                        <button class="btn btn-sm btn-secondary cancel-edit-btn me-2">取消</button>
                        <button class="btn btn-sm btn-primary save-edit-btn">保存</button>
                    </div>
                `;
                // Store original HTML and ID for save/cancel
                messageDiv.setAttribute('data-original-html', originalHTML);
                messageDiv.setAttribute('data-editing-id', messageId); // Store ID for save button
                messageDiv.querySelector('.edit-textarea').focus();
            }

            // Cancel Edit Button
            else if (target.closest('.cancel-edit-btn')) {
                e.stopPropagation();
                const originalHTML = messageDiv.getAttribute('data-original-html');
                if (originalHTML) {
                    messageDiv.innerHTML = originalHTML;
                    messageDiv.removeAttribute('data-original-html');
                    messageDiv.removeAttribute('data-editing-id');
                    renderMessageContent(messageDiv); // Re-render original content
                }
            }

            // Save Edit Button
            else if (target.closest('.save-edit-btn')) {
                e.stopPropagation();
                const editingId = messageDiv.getAttribute('data-editing-id');
                const originalHTML = messageDiv.getAttribute('data-original-html');
                const newContent = messageDiv.querySelector('.edit-textarea').value;
                if (editingId && originalHTML) {
                    saveMessageEdit(editingId, newContent, messageDiv, originalHTML); // Use function from api_handler
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
