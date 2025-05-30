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

// =========================================================================
// Chat State Management System - 新增的状态管理系统
// =========================================================================

class ChatStateManager {
    constructor() {
        this.messages = {}; // 消息状态存储: {id: {content, isUser, element, version, isUpdating}}
        this.updateQueue = {}; // 更新队列: {id: [{content, version, callback}]}
        this.listeners = []; // 状态变化监听器
        this.processingUpdates = false;
    }

    // 注册消息元素
    registerMessage(id, content, isUser, element) {
        console.log(`[ChatState] Registering message ${id}`);
        if (this.messages[id]) {
            console.warn(`[ChatState] Message ${id} already registered, updating`);
        }
        
        this.messages[id] = {
            content,
            isUser,
            element,
            version: 1,
            isUpdating: false
        };
        
        return this.messages[id];
    }

    // 获取消息状态
    getMessage(id) {
        return this.messages[id];
    }

    // 更新消息内容
    updateMessage(id, content, callback) {
        console.log(`[ChatState] Updating message ${id}`);
        
        // 确保消息存在
        if (!this.messages[id]) {
            console.warn(`[ChatState] Message ${id} not found for update`);
            return false;
        }
        
        // 如果消息正在更新，添加到队列
        if (this.messages[id].isUpdating) {
            console.log(`[ChatState] Message ${id} is updating, queueing this update`);
            if (!this.updateQueue[id]) {
                this.updateQueue[id] = [];
            }
            this.updateQueue[id].push({
                content,
                version: this.messages[id].version + 1,
                callback
            });
            return true;
        }
        
        // 否则立即执行更新
        this._executeUpdate(id, content, this.messages[id].version + 1, callback);
        return true;
    }

    // 内部方法：执行更新
    _executeUpdate(id, content, version, callback) {
        if (!this.messages[id]) return false;
        
        const message = this.messages[id];
        message.isUpdating = true;
        
        // 更新内存中的状态
        message.content = content;
        message.version = version;
        
        console.log(`[ChatState] Executing update for ${id}, version ${version}`);
        
        // 通知所有监听器
        this._notifyListeners({
            type: 'message_updated',
            id,
            content,
            version
        });
        
        // 标记更新完成
        setTimeout(() => {
            message.isUpdating = false;
            
            // 检查队列中是否有更多更新
            if (this.updateQueue[id] && this.updateQueue[id].length > 0) {
                const nextUpdate = this.updateQueue[id].shift();
                console.log(`[ChatState] Processing queued update for ${id}`);
                this._executeUpdate(id, nextUpdate.content, nextUpdate.version, nextUpdate.callback);
            }
            
            // 执行回调
            if (callback && typeof callback === 'function') {
                callback(id, content, version);
            }
        }, 10);
        
        return true;
    }

    // 添加状态监听器
    addListener(listener) {
        if (typeof listener === 'function') {
            this.listeners.push(listener);
            return true;
        }
        return false;
    }

    // 移除状态监听器
    removeListener(listener) {
        const index = this.listeners.indexOf(listener);
        if (index !== -1) {
            this.listeners.splice(index, 1);
            return true;
        }
        return false;
    }

    // 通知所有监听器
    _notifyListeners(event) {
        this.listeners.forEach(listener => {
            try {
                listener(event);
            } catch (error) {
                console.error('[ChatState] Error in listener:', error);
            }
        });
    }

    // 删除消息
    removeMessage(id) {
        if (this.messages[id]) {
            delete this.messages[id];
            if (this.updateQueue[id]) {
                delete this.updateQueue[id];
            }
            
            this._notifyListeners({
                type: 'message_removed',
                id
            });
            
            return true;
        }
        return false;
    }
}

// 创建全局状态管理器实例
if (typeof window.ChatState === 'undefined') {
    window.ChatState = new ChatStateManager();
}

// =========================================================================
// 消息组件工厂 - 统一创建和更新消息元素
// =========================================================================

class MessageComponentFactory {
    constructor(stateManager) {
        this.stateManager = stateManager;
        this.setupStateListener();
    }

    // 设置状态变化监听
    setupStateListener() {
        this.stateManager.addListener((event) => {
            if (event.type === 'message_updated') {
                this.updateDOMForMessage(event.id);
            }
        });
    }

    // 创建用户消息组件
    createUserMessage(id, content) {
        console.log(`[MessageFactory] Creating user message ${id}`);
        
        // 创建消息元素
        const messageDiv = document.createElement('div');
        messageDiv.className = 'alert alert-primary';
        messageDiv.setAttribute('data-message-id', id);
        
        if (id.startsWith('temp-')) {
            messageDiv.setAttribute('data-temp-id', id);
            messageDiv.setAttribute('data-waiting-id', '1');
        }
        
        // 构建消息内容
        this._buildMessageStructure(messageDiv, content, true);
        
        // 注册到状态管理器
        this.stateManager.registerMessage(id, content, true, messageDiv);
        
        return messageDiv;
    }

    // 创建AI消息组件
    createAIMessage(id, content) {
        console.log(`[MessageFactory] Creating AI message ${id}`);
        
        // 创建消息元素
        const messageDiv = document.createElement('div');
        messageDiv.className = 'alert alert-secondary';
        
        if (id) {
            messageDiv.setAttribute('data-message-id', id);
        }
        
        // 构建消息内容
        this._buildMessageStructure(messageDiv, content, false);
        
        // 注册到状态管理器
        this.stateManager.registerMessage(id, content, false, messageDiv);
        
        return messageDiv;
    }

    // 创建加载指示器
    createLoadingIndicator() {
        const loadingDiv = document.createElement('div');
        loadingDiv.className = 'alert alert-secondary';
        loadingDiv.id = 'ai-response-loading';
        
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

    // 创建错误消息
    createErrorMessage(errorText) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'alert alert-danger';
        
        errorDiv.innerHTML = `
            <div class="d-flex justify-content-between">
                <span>系统</span>
                <small>${new Date().toLocaleTimeString()}</small>
            </div>
            <hr>
            <p>${escapeHtml(errorText)}</p>
        `;
        
        return errorDiv;
    }

    // 更新指定消息的DOM
    updateDOMForMessage(id) {
        const message = this.stateManager.getMessage(id);
        if (!message || !message.element) {
            console.warn(`[MessageFactory] Cannot update DOM for message ${id}, not found or no element`);
            return false;
        }
        
        console.log(`[MessageFactory] Updating DOM for message ${id}`);
        
        // 获取或创建渲染目标
        let renderTarget = message.element.querySelector('p > .render-target');
        
        if (!renderTarget) {
            console.log(`[MessageFactory] Creating new render target for ${id}`);
            const p = message.element.querySelector('p');
            if (!p) {
                const pElement = document.createElement('p');
                message.element.appendChild(pElement);
                renderTarget = document.createElement('span');
                renderTarget.className = 'render-target';
                pElement.appendChild(renderTarget);
            } else {
                p.innerHTML = '';
                renderTarget = document.createElement('span');
                renderTarget.className = 'render-target';
                p.appendChild(renderTarget);
            }
        }
        
        // 更新渲染目标
        renderTarget.setAttribute('data-original-content', message.content);
        renderTarget.removeAttribute('data-rendered');
        
        // 清空内容
        renderTarget.innerHTML = '';
        
        // 重新渲染
        if (typeof renderMessageContent === 'function') {
            setTimeout(() => {
                console.log(`[MessageFactory] Triggering render for ${id}`);
                renderMessageContent(message.element);
            }, 0);
        } else {
            console.error('[MessageFactory] renderMessageContent function not available');
            renderTarget.textContent = message.content;
        }
        
        return true;
    }

    // 内部方法：构建消息结构
    _buildMessageStructure(element, content, isUser) {
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
        
        element.innerHTML = `
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
    }
}

// 创建全局组件工厂实例
if (typeof window.MessageFactory === 'undefined') {
    window.MessageFactory = new MessageComponentFactory(window.ChatState);
}
