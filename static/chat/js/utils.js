/* eslint-env browser */

// --- Utility Functions ---

// Utility function to escape HTML characters
function escapeHtml(unsafe) {
    if (typeof unsafe !== 'string') {
        console.warn("escapeHtml called with non-string value:", unsafe);
        return unsafe; // Return as is if not a string
    }
    // Corrected replacements
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;") // Corrected double quote entity
         .replace(/'/g, "&#039;");
}

// From cookie中获取CSRF Token
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

// 从localStorage获取之前保存的会话ID
function getStoredConversationId() {
    return localStorage.getItem('currentConversationId');
}

// 保存会话ID到localStorage
function storeConversationId(id) {
    if (id) {
        localStorage.setItem('currentConversationId', id);
        console.log('Stored conversation ID:', id);
    }
}

// 存储临时ID到实际ID的映射 - Shared utility state
// Ensure tempIdMap is globally accessible if needed by other modules,
// or pass it explicitly. For now, assuming global access via window.tempIdMap
if (typeof window.tempIdMap === 'undefined') {
    window.tempIdMap = {};
}
let tempIdMap = window.tempIdMap;


// 辅助函数：获取消息的真实ID（处理临时ID）
function getRealMessageId(tempId) {
    // 如果不是临时ID，直接返回
    if (!tempId || !tempId.startsWith('temp-')) {
        return tempId;
    }

    // 尝试从映射表中获取
    if (tempIdMap[tempId]) {
        console.log(`From map: Real ID for ${tempId} -> ${tempIdMap[tempId]}`);
        return tempIdMap[tempId];
    }

    // 尝试从DOM中获取 (Fallback, less reliable if DOM updates lag)
    const messageContainer = document.querySelector('#message-container');
    if (!messageContainer) return null;

    const msgDiv = messageContainer.querySelector(`.alert[data-temp-id="${tempId}"]`);
    if (msgDiv) {
        const realId = msgDiv.getAttribute('data-message-id');
        if (realId && !realId.startsWith('temp-')) {
            // Save to map
            tempIdMap[tempId] = realId;
            console.log(`From DOM: Real ID for ${tempId} -> ${realId}`);
            return realId;
        }
    }

    // 没有找到映射
    console.warn(`Could not resolve real ID for temp ID: ${tempId}`);
    return null; // Return null if no mapping found after checks
}
