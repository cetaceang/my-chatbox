document.addEventListener('DOMContentLoaded', function() {
    const systemPromptModal = document.getElementById('systemPromptModal');
    const saveBtn = document.getElementById('system-prompt-save-btn');
    const promptInput = document.getElementById('system-prompt-input');

    // 确保这些元素存在
    if (!systemPromptModal || !saveBtn || !promptInput) {
        console.warn('System prompt UI elements not found. Feature will be disabled.');
        return;
    }

    // 在模态框显示时，从全局变量加载当前提示词
    systemPromptModal.addEventListener('show.bs.modal', function () {
        promptInput.value = window.systemPrompt || '';
    });

    // 保存按钮的点击事件
    saveBtn.addEventListener('click', async function() {
        const newPrompt = promptInput.value;
        if (!window.conversationId) {
            alert('请先开始一个对话！');
            return;
        }

        // 假设 getCookie 函数在 utils.js 或全局可用
        const csrfToken = getCookie('csrftoken'); 
        if (!csrfToken) {
            console.error('CSRF token not found!');
            alert('无法完成操作，缺少安全令牌。');
            return;
        }

        try {
            const response = await fetch(`/chat/api/conversations/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({
                    id: window.conversationId,
                    system_prompt: newPrompt
                })
            });

            if (response.ok) {
                // 更新全局变量
                window.systemPrompt = newPrompt;
                
                // 手动关闭模态框
                const modal = bootstrap.Modal.getInstance(systemPromptModal);
                modal.hide();

                // 可选：显示一个更友好的通知
                alert('系统提示词已保存！');
            } else {
                const errorData = await response.json();
                alert('保存失败: ' + (errorData.message || '未知错误'));
            }
        } catch (error) {
            console.error('Error saving system prompt:', error);
            alert('保存时发生网络错误。');
        }
    });
});
