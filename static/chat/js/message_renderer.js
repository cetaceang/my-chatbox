/* eslint-env browser, amd */
/* globals marked, MathJax */ // Inform linter about globals

// Updated rendering function for Marked and MathJax
function renderMessageContent(messageElement) {
    const messageIdForLog = messageElement ? (messageElement.getAttribute('data-message-id') || messageElement.getAttribute('data-temp-id') || 'N/A') : 'unknown-element';
    console.log(`[RenderMessage] START for ID ${messageIdForLog}`); // Log entry

    const renderTarget = messageElement.querySelector('p > .render-target');
    if (!renderTarget) { // Check if target exists
        console.warn(`[RenderMessage] ERROR: Target 'p > .render-target' not found for ID ${messageIdForLog}`);
        return;
    }

    // Get original content from data attribute
    const originalContent = renderTarget.getAttribute('data-original-content');
    if (originalContent === null) {
        console.warn(`[RenderMessage] ERROR: Target missing data-original-content for ID ${messageIdForLog}`);
        return; // Skip if no original content found
    }

    console.log(`[RenderMessage] Processing content for ID ${messageIdForLog}, content length: ${originalContent.length}`);
    console.log(`[RenderMessage] Content preview: ${originalContent.substring(0, 50)}${originalContent.length > 50 ? '...' : ''}`);

    // 确保内容不会重复显示的关键步骤：
    // 1. 清除渲染标记
    renderTarget.removeAttribute('data-rendered');
    
    // 2. 完全清空内容 - 两次操作确保清理彻底
    renderTarget.innerHTML = '';
    renderTarget.textContent = '';
    console.log(`[RenderMessage] Target cleared for ID ${messageIdForLog}`);

    try {
        // 处理思维链标签 <think></think>
        let processedContent = originalContent;
        const thinkRegex = /<think>([\s\S]*?)<\/think>/g;
        let hasThinkContent = false;
        
        // 检查是否包含思维链
        if (thinkRegex.test(originalContent)) {
            hasThinkContent = true;
            console.log(`[RenderMessage] Found thinking chain in message ID ${messageIdForLog}`);
            
            // 重置正则表达式状态
            thinkRegex.lastIndex = 0;
            
            // 替换思维链为可折叠区域
            processedContent = originalContent.replace(thinkRegex, (match, thinkContent) => {
                return `<details class="thinking-chain">
                    <summary>查看思考过程</summary>
                    <div class="thinking-content">${thinkContent}</div>
                </details>`;
            });
        }

        // 3. 渲染Markdown
        console.log(`[RenderMessage] Calling marked.parse() for ID ${messageIdForLog}`);
        if (typeof marked === 'undefined') {
            console.error(`[RenderMessage] ERROR: Marked library not loaded for ID ${messageIdForLog}`);
            renderTarget.textContent = processedContent; // Fallback to plain text
            renderTarget.setAttribute('data-rendered', 'error');
            return;
        }
        
        const renderedMarkdown = marked.parse(processedContent, { breaks: true });
        console.log(`[RenderMessage] Marked rendered ${renderedMarkdown.length} chars for ID ${messageIdForLog}`);
        
        // 4. 设置渲染后的HTML内容
        renderTarget.innerHTML = renderedMarkdown;
        console.log(`[RenderMessage] HTML set for ID ${messageIdForLog}, current length: ${renderTarget.innerHTML.length}`);

        // 如果有思维链，添加样式
        if (hasThinkContent) {
            const detailsElements = renderTarget.querySelectorAll('details.thinking-chain');
            detailsElements.forEach(details => {
                details.classList.add('thinking-chain');
                details.querySelector('summary').classList.add('thinking-summary');
                details.querySelector('.thinking-content').classList.add('thinking-content');
            });
        }

        // 5. 处理MathJax渲染
        if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
            console.log(`[RenderMessage] Starting MathJax typesetting for ID ${messageIdForLog}`);
            MathJax.typesetPromise([renderTarget])
                .then(() => {
                    console.log(`[RenderMessage] MathJax typesetting completed for ID ${messageIdForLog}`);
                })
                .catch((err) => {
                    console.error(`[RenderMessage] MathJax error for ID ${messageIdForLog}:`, err);
                    renderTarget.setAttribute('data-rendered', 'mathjax-error');
                });
        } else {
            console.warn(`[RenderMessage] MathJax not available for ID ${messageIdForLog}`);
        }

        // 6. 标记为已渲染完成
        renderTarget.setAttribute('data-rendered', 'true');
        console.log(`[RenderMessage] SUCCESS for ID ${messageIdForLog}`);

    } catch (error) {
        console.error(`[RenderMessage] CRITICAL ERROR for ID ${messageIdForLog}:`, error);
        // 出错时恢复原始内容
        if (typeof escapeHtml === 'function') {
            renderTarget.innerHTML = escapeHtml(originalContent);
        } else {
            renderTarget.textContent = originalContent;
        }
        renderTarget.setAttribute('data-rendered', 'error');
    }
}
