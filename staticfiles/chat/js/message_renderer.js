/* eslint-env browser, amd */
/* globals marked, MathJax, getChatSettings */ // Inform linter about globals

// --- 全局渲染状态管理器 ---
const renderingState = {};

/**
 * 主渲染函数，根据用户设置选择渲染方式
 * @param {HTMLElement} messageElement - 消息的DOM元素
 * @param {boolean} isNewStream - 是否为新的流式消息
 */
function renderMessageContent(messageElement, isNewStream = false) {
    const messageId = messageElement.getAttribute('data-message-id') || messageElement.getAttribute('data-temp-id');
    if (!messageId) {
        console.error("[RenderMessage] Message element is missing a required ID.");
        return;
    }

    const renderTarget = messageElement.querySelector('p > .render-target');
    if (!renderTarget) {
        console.warn(`[RenderMessage] Target 'p > .render-target' not found for ID ${messageId}`);
        return;
    }

    const originalContent = renderTarget.getAttribute('data-original-content');
    if (originalContent === null) {
        console.warn(`[RenderMessage] Target missing data-original-content for ID ${messageId}`);
        return;
    }

    // 获取用户设置
    const settings = getChatSettings();
    const isUserMessage = messageElement.classList.contains('alert-primary');

    // 决定渲染路径
    if (isUserMessage || !settings.isStreaming || !isNewStream) {
        // 用户消息、关闭流式响应或非新流式消息时，立即渲染
        renderInstantly(messageId, renderTarget, originalContent);
    } else {
        // AI消息、开启流式响应且为新流式消息时，使用打字机效果
        renderWithTypingEffect(messageId, renderTarget, originalContent, settings.typingSpeed);
    }
}

/**
 * 立即渲染消息内容（无打字机效果）
 * @param {string} messageId - 消息ID
 * @param {HTMLElement} renderTarget - 渲染目标元素
 * @param {string} originalContent - 原始文本内容
 */
function renderInstantly(messageId, renderTarget, originalContent) {
    console.log(`[RenderInstantly] Rendering ID ${messageId}`);
    try {
        const processedContent = preprocessContent(originalContent);
        const renderedMarkdown = marked.parse(processedContent, { breaks: true });
        renderTarget.innerHTML = renderedMarkdown;

        // 渲染数学公式
        if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
            // 将 MathJax 调用延迟到下一个事件循环，以避免阻塞渲染
            setTimeout(() => {
                MathJax.typesetPromise([renderTarget]).catch((err) => {
                    console.error(`[RenderInstantly] MathJax error for ID ${messageId}:`, err);
                });
            }, 0);
        }
        renderTarget.setAttribute('data-rendered', 'true');
    } catch (error) {
        console.error(`[RenderInstantly] CRITICAL ERROR for ID ${messageId}:`, error);
        renderTarget.textContent = originalContent; // Fallback
        renderTarget.setAttribute('data-rendered', 'error');
    }
}

/**
 * 使用打字机效果渲染消息内容
 * @param {string} messageId - 消息ID
 * @param {HTMLElement} renderTarget - 渲染目标元素
 * @param {string} originalContent - 原始文本内容
 * @param {number} typingSpeed - 打字速度（毫秒/字符）
 */
function renderWithTypingEffect(messageId, renderTarget, originalContent, typingSpeed) {
    const state = renderingState[messageId] || {
        isRendering: false,
        animationFrameId: null,
        renderedLength: 0,
        cursor: null,
    };
    renderingState[messageId] = state;

    if (state.isRendering) {
        clearTimeout(state.animationFrameId);
    }

    state.content = originalContent;
    state.isRendering = true;

    function type() {
        if (state.renderedLength >= state.content.length) {
            finishRendering();
            return;
        }

        // 逐步增加渲染长度
        state.renderedLength += 1;
        
        // 优化：不是每个字符都重新解析和渲染，而是在一定长度或特定字符后更新
        // 这里为了简单和稳定，我们还是每次都重新渲染，但只渲染到当前长度
        const currentSubstring = state.content.substring(0, state.renderedLength);
        const processedContent = preprocessContent(currentSubstring);
        const renderedMarkdown = marked.parse(processedContent, { breaks: true });
        
        renderTarget.innerHTML = renderedMarkdown;

        // 确保光标始终在最后
        if (!state.cursor) {
            state.cursor = document.createElement('span');
            state.cursor.className = 'typing-cursor';
            state.cursor.textContent = '▋';
        }
        renderTarget.appendChild(state.cursor);
        
        // 滚动到视图
        renderTarget.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        state.animationFrameId = setTimeout(type, typingSpeed);
    }

    function finishRendering() {
        console.log(`[TypingEffect] Typing finished for ${messageId}.`);
        if (state.cursor) {
            state.cursor.remove();
            state.cursor = null;
        }
        
        // 最终渲染完整内容
        const finalProcessedContent = preprocessContent(state.content);
        const finalRenderedMarkdown = marked.parse(finalProcessedContent, { breaks: true });
        renderTarget.innerHTML = finalRenderedMarkdown;

        if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
            MathJax.typesetPromise([renderTarget]).catch((err) => {
                console.error(`[TypingEffect] MathJax error for ID ${messageId}:`, err);
            });
        }

        renderTarget.setAttribute('data-rendered', 'true');
        state.isRendering = false;
        state.renderedLength = state.content.length; // 确保长度同步
        console.log(`[TypingEffect] SUCCESS for ID ${messageId}`);
    }

    // 启动或继续动画
    type();
}

/**
 * 预处理内容，例如处理 <think> 标签
 * @param {string} content - 原始内容
 * @returns {string} - 处理后的内容
 */
function preprocessContent(content) {
    const thinkRegex = /<think>([\s\S]*?)<\/think>/g;
    if (thinkRegex.test(content)) {
        // 重置正则表达式状态以确保替换成功
        thinkRegex.lastIndex = 0;
        content = content.replace(thinkRegex, (match, thinkContent) => {
            const innerHtml = marked.parse(thinkContent.trim(), { breaks: true });
            return `<details class="thinking-chain">
                        <summary>查看思考过程</summary>
                        <div class="thinking-content">${innerHtml}</div>
                    </details>`;
        });
    }
    return content;
}
