/* eslint-env browser */
/* globals getChatSettings, saveChatSettings */

// --- 全局设置常量 ---
const SETTINGS_KEY = 'chatAppSettings';

// --- 初始化函数 ---
function initializeChatSettings() {
    const streamingToggle = document.getElementById('streaming-toggle');
    const speedSelect = document.getElementById('typing-speed-select');
    const forceHttpToggle = document.getElementById('force-http-toggle');

    if (!streamingToggle || !speedSelect || !forceHttpToggle) {
        console.warn("Settings UI elements not found. Skipping settings initialization.");
        return;
    }

    // 从 localStorage 加载设置
    const settings = getChatSettings();

    // 设置初始状态
    streamingToggle.checked = settings.isStreaming;
    speedSelect.value = settings.typingSpeed;
    forceHttpToggle.checked = settings.forceHttpMode;

    // 根据流式开关状态，决定是否禁用速度选择
    speedSelect.disabled = !settings.isStreaming;

    // --- 事件监听 ---
    // 监听流式开关变化
    streamingToggle.addEventListener('change', (event) => {
        const isStreaming = event.target.checked;
        speedSelect.disabled = !isStreaming;
        saveChatSettings({ isStreaming });
    });

    // 监听打字机速度变化
    speedSelect.addEventListener('change', (event) => {
        const typingSpeed = parseInt(event.target.value, 10);
        saveChatSettings({ typingSpeed });
    });

    // 监听强制HTTP模式开关变化
    forceHttpToggle.addEventListener('change', (event) => {
        const forceHttpMode = event.target.checked;
        saveChatSettings({ forceHttpMode });
        // 提示用户需要刷新才能完全生效
        if (forceHttpMode) {
            alert("已开启强制HTTP模式。建议刷新页面以确保所有连接都使用HTTP。");
        }
    });
}

window.initializeChatSettings = initializeChatSettings;
