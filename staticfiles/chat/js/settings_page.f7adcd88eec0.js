document.addEventListener('DOMContentLoaded', () => {
    // --- Helper Functions ---
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

    const csrfToken = getCookie('csrftoken');

    // --- General Settings Page Logic ---

    // Back to Chat Button
    const backToChatBtn = document.getElementById('back-to-chat-btn');
    if (backToChatBtn) {
        backToChatBtn.addEventListener('click', function() {
            const conversationId = localStorage.getItem('currentConversationId');
            console.log("点击返回按钮，当前存储的会话ID:", conversationId);
            if (conversationId) {
                window.location.href = `/chat/?conversation_id=${conversationId}`; // Assuming chat main URL is /chat/
            } else {
                window.location.href = '/chat/?no_new=1'; // Assuming chat main URL is /chat/
            }
        });
    }

    // --- Admin-Specific Logic ---
    // Check if admin elements exist to determine if the user is an admin
    const addProviderBtn = document.getElementById('add-provider-btn');
    const isAdmin = !!addProviderBtn; // Simple check if admin-only buttons exist

    if (isAdmin) {
        // --- User Management ---
        const userListTableBody = document.getElementById('user-list-table-body');
        const userPagination = document.getElementById('user-pagination');
        const banUserModalElement = document.getElementById('banUserModal');
        const banUserModal = banUserModalElement ? new bootstrap.Modal(banUserModalElement) : null;
        const banUserIdInput = document.getElementById('ban-user-id-input');
        const banUsernameDisplay = document.getElementById('ban-username-display');
        const banDurationDaysInput = document.getElementById('ban-duration-days');
        const confirmBanBtn = document.getElementById('confirm-ban-btn');
        const refreshUserListBtn = document.getElementById('refresh-user-list-btn');
        let currentUserListPage = 1;

        function fetchUsers(page = 1) {
            currentUserListPage = page;
            if (!userListTableBody) return;
            userListTableBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">加载中...</td></tr>';
            if (userPagination) userPagination.innerHTML = '';

            fetch(`/chat/api/admin/users/?page=${page}&per_page=10`, {
                method: 'GET',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken }
            })
            .then(response => {
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                return response.json();
            })
            .then(data => {
                userListTableBody.innerHTML = '';
                if (data.success && data.users) {
                    if (data.users.length === 0) {
                        userListTableBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">暂无其他用户</td></tr>';
                    } else {
                        data.users.forEach(user => {
                            const banExpiresRaw = user.profile.ban_expires_at;
                            let banExpiresDisplay = '-';
                            if (user.profile.is_banned) {
                                banExpiresDisplay = banExpiresRaw ? banExpiresRaw : '永久';
                            }
                            const statusText = user.profile.is_banned ? `<span class="badge bg-danger">已封禁</span>` : `<span class="badge bg-success">正常</span>`;
                            let actionButton = '';
                            if (!user.profile.is_admin) {
                             actionButton = user.profile.is_banned
                                 ? `<button class="btn btn-sm btn-success unban-user-btn me-1" data-user-id="${user.id}" data-username="${user.username}">解封</button>` // Added me-1 for margin
                                 : `<button class="btn btn-sm btn-warning ban-user-btn me-1" data-user-id="${user.id}" data-username="${user.username}">封禁</button>`; // Added me-1 for margin
                             // Add delete button for non-admin users
                             actionButton += `<button class="btn btn-sm btn-danger delete-user-btn" data-user-id="${user.id}" data-username="${user.username}">删除</button>`;
                         } else {
                              actionButton = '<span class="text-muted small fst-italic">管理员</span>';
                         }
                            const row = `
                                <tr data-user-id="${user.id}">
                                    <td>${user.username}</td>
                                    <td>${user.email || '-'}</td>
                                    <td>${user.date_joined}</td>
                                    <td>${statusText}</td>
                                    <td>${banExpiresDisplay}</td>
                                    <td>${actionButton}</td>
                                </tr>`;
                            userListTableBody.insertAdjacentHTML('beforeend', row);
                        });
                    }
                    if (userPagination && data.pagination) renderPagination(data.pagination);
                } else {
                    userListTableBody.innerHTML = `<tr><td colspan="6" class="text-center text-danger">加载用户列表失败: ${data.message || '未知错误'}</td></tr>`;
                }
            })
            .catch(error => {
                console.error('获取用户列表出错:', error);
                userListTableBody.innerHTML = `<tr><td colspan="6" class="text-center text-danger">加载用户列表时发生错误: ${error.message}</td></tr>`;
            });
        }

        function renderPagination(pagination) {
            if (!userPagination) return;
            userPagination.innerHTML = '';
            if (!pagination || pagination.total_pages <= 1) return;

            const createPageItem = (page, label, isDisabled = false, isActive = false) => {
                const li = document.createElement('li');
                li.className = `page-item ${isDisabled ? 'disabled' : ''} ${isActive ? 'active' : ''}`;
                const a = document.createElement('a');
                a.className = 'page-link';
                a.href = '#';
                a.dataset.page = page;
                a.innerHTML = label;
                if (isDisabled) a.setAttribute('aria-disabled', 'true');
                li.appendChild(a);
                return li;
            };

            userPagination.appendChild(createPageItem(pagination.page - 1, '&laquo;', pagination.page <= 1));

            const showPages = new Set([1, pagination.total_pages, pagination.page]);
            if (pagination.page > 1) showPages.add(pagination.page - 1);
            if (pagination.page < pagination.total_pages) showPages.add(pagination.page + 1);

            let lastPage = 0;
            for (let i = 1; i <= pagination.total_pages; i++) {
                if (showPages.has(i)) {
                    if (i > lastPage + 1) {
                        const ellipsis = document.createElement('li');
                        ellipsis.className = 'page-item disabled';
                        ellipsis.innerHTML = '<span class="page-link">...</span>';
                        userPagination.appendChild(ellipsis);
                    }
                    userPagination.appendChild(createPageItem(i, i, false, i === pagination.page));
                    lastPage = i;
                }
            }

            userPagination.appendChild(createPageItem(pagination.page + 1, '&raquo;', pagination.page >= pagination.total_pages));
        }

        if (userPagination) {
            userPagination.addEventListener('click', (e) => {
                e.preventDefault();
                const targetLink = e.target.closest('a.page-link');
                if (targetLink && targetLink.dataset.page && !targetLink.closest('.page-item').classList.contains('disabled')) {
                    fetchUsers(parseInt(targetLink.dataset.page));
                }
            });
        }

        if (userListTableBody) {
            userListTableBody.addEventListener('click', (e) => {
                if (e.target.classList.contains('ban-user-btn')) {
                    const userId = e.target.dataset.userId;
                    const username = e.target.dataset.username;
                    if (banUserIdInput) banUserIdInput.value = userId;
                    if (banUsernameDisplay) banUsernameDisplay.textContent = username;
                    if (banDurationDaysInput) banDurationDaysInput.value = 0;
                    if (banUserModal) banUserModal.show();
                 } else if (e.target.classList.contains('unban-user-btn')) {
                     const userId = e.target.dataset.userId;
                     const username = e.target.dataset.username;
                     if (confirm(`确定要解封用户 ${username} 吗？`)) {
                         manageBanStatus(userId, 'unban');
                     }
                 } else if (e.target.classList.contains('delete-user-btn')) { // Handle delete button click
                     const userId = e.target.dataset.userId;
                     const username = e.target.dataset.username;
                     if (confirm(`确定要永久删除用户 ${username} 吗？此操作不可恢复！`)) {
                         deleteUser(userId);
                     }
                 }
            });
        }

        if (confirmBanBtn) {
            confirmBanBtn.addEventListener('click', () => {
                const userId = banUserIdInput ? banUserIdInput.value : null;
                const duration = banDurationDaysInput ? banDurationDaysInput.value : null;
                if (userId) manageBanStatus(userId, 'ban', duration);
                if (banUserModal) banUserModal.hide();
            });
        }

        if (refreshUserListBtn) {
            refreshUserListBtn.addEventListener('click', () => fetchUsers(currentUserListPage));
        }

        function manageBanStatus(userId, action, duration = null) {
            const payload = { user_id: userId, action: action };
            if (action === 'ban') {
                const durationInt = parseInt(duration);
                payload.ban_duration_days = (!isNaN(durationInt) && durationInt >= 0) ? durationInt : 0;
            }
            fetch('/chat/api/admin/manage_user_ban/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify(payload)
            })
            .then(response => response.json())
            .then(data => {
                alert(data.success ? data.message : `操作失败: ${data.message}`);
                if (data.success) fetchUsers(currentUserListPage);
            })
            .catch(error => {
                console.error('操作出错:', error);
                alert('操作失败，请稍后再试');
            });
        }

        const usersTab = document.getElementById('users-tab');
        if (usersTab) {
            usersTab.addEventListener('shown.bs.tab', () => fetchUsers(1));
            const usersContentPane = document.getElementById('users-content');
            if (usersContentPane && usersContentPane.classList.contains('active')) {
                fetchUsers(1);
         }
     }
 
     // Function to delete a user
     function deleteUser(userId) {
         fetch('/users/api/manage-roles/', { // Use the correct endpoint
             method: 'DELETE',
             headers: {
                 'Content-Type': 'application/json',
                 'X-CSRFToken': csrfToken
             },
             body: JSON.stringify({ user_id: userId })
         })
         .then(response => response.json())
         .then(data => {
             alert(data.success ? data.message : `删除失败: ${data.message}`);
             if (data.success) {
                 fetchUsers(currentUserListPage); // Refresh the list after deletion
             }
         })
         .catch(error => {
             console.error('删除用户出错:', error);
             alert('删除用户失败，请稍后再试');
         });
     }
 
     // --- Provider Management ---
        const providerModalElement = document.getElementById('providerModal');
        const providerModal = providerModalElement ? new bootstrap.Modal(providerModalElement) : null;
        const providerForm = document.getElementById('provider-form');
        const providerModalTitle = document.getElementById('providerModalTitle');
        const providerIdInput = document.getElementById('provider-id');
        const providerNameInput = document.getElementById('provider-name');
        const providerUrlInput = document.getElementById('provider-url');
        const providerKeyInput = document.getElementById('provider-key');
        const providerActiveInput = document.getElementById('provider-active');
        const saveProviderBtn = document.getElementById('save-provider-btn');
        const providerList = document.getElementById('provider-list');

        if (addProviderBtn && providerModal) {
            addProviderBtn.addEventListener('click', () => {
                if (providerModalTitle) providerModalTitle.textContent = '添加服务提供商';
                if (providerIdInput) providerIdInput.value = '';
                if (providerForm) providerForm.reset();
                if (providerActiveInput) providerActiveInput.checked = true; // Default to active
                providerModal.show();
            });
        }

        if (saveProviderBtn && providerModal) {
            saveProviderBtn.addEventListener('click', () => {
                const providerId = providerIdInput ? providerIdInput.value : '';
                const name = providerNameInput ? providerNameInput.value : '';
                const baseUrl = providerUrlInput ? providerUrlInput.value : '';
                const apiKey = providerKeyInput ? providerKeyInput.value : '';
                const isActive = providerActiveInput ? providerActiveInput.checked : true;

                // Require API key only when adding, or if provided when editing
                if (!name || !baseUrl || (!providerId && !apiKey)) {
                    alert('请填写名称、基础URL和API密钥');
                    return;
                }

                const method = providerId ? 'PUT' : 'POST';
                const data = { name, base_url: baseUrl, is_active: isActive };
                if (providerId) data.id = providerId;
                // Only include API key if adding or if it's provided during edit
                if (!providerId || apiKey) data.api_key = apiKey;


                fetch('/chat/api/providers/', {
                    method: method,
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify(data)
                })
                .then(response => response.json())
                .then(data => {
                    alert(data.success ? data.message : `操作失败: ${data.message}`);
                    if (data.success) window.location.reload(); // Reload to see changes
                })
                .catch(error => {
                    console.error('操作出错:', error);
                    alert('操作失败，请稍后再试');
                });
            });
        }

        if (providerList) {
            providerList.addEventListener('click', (e) => {
                const editBtn = e.target.closest('.edit-provider-btn');
                const deleteBtn = e.target.closest('.delete-provider-btn');

                if (editBtn && providerModal) {
                    const providerItem = editBtn.closest('.list-group-item');
                    const providerId = providerItem.dataset.providerId;
                    // Fetch provider details (assuming an endpoint exists or use data from list item if sufficient)
                    // For simplicity, let's assume we need to fetch full details including API key placeholder
                    // In a real app, you might not want to fetch the key, just allow updating it.
                    // Let's populate based on visible data and clear the key field.
                    const name = providerItem.querySelector('h6').textContent;
                    const url = providerItem.querySelector('p').textContent;
                    const isActive = providerItem.querySelector('.provider-active-toggle')?.checked ?? true; // Get current state

                    if (providerModalTitle) providerModalTitle.textContent = '编辑服务提供商';
                    if (providerIdInput) providerIdInput.value = providerId;
                    if (providerNameInput) providerNameInput.value = name;
                    if (providerUrlInput) providerUrlInput.value = url;
                    if (providerKeyInput) providerKeyInput.value = ''; // Clear key field for editing
                    if (providerKeyInput) providerKeyInput.placeholder = '如需更新，请输入新密钥';
                    if (providerActiveInput) providerActiveInput.checked = isActive;
                    providerModal.show();

                } else if (deleteBtn) {
                    const providerItem = deleteBtn.closest('.list-group-item');
                    const providerId = providerItem.dataset.providerId;
                    const providerName = providerItem.querySelector('h6').textContent;
                    if (confirm(`确定要删除服务提供商 "${providerName}" 吗？`)) {
                        fetch('/chat/api/providers/', {
                            method: 'DELETE',
                            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                            body: JSON.stringify({ id: providerId })
                        })
                        .then(response => response.json())
                        .then(data => {
                            alert(data.success ? data.message : `删除失败: ${data.message}`);
                            if (data.success) window.location.reload();
                        })
                        .catch(error => {
                            console.error('删除出错:', error);
                            alert('删除失败，请稍后再试');
                        });
                    }
                }
            });
        }


        // --- Model Management ---
        const addModelBtn = document.getElementById('add-model-btn');
        const modelModalElement = document.getElementById('modelModal');
        const modelModal = modelModalElement ? new bootstrap.Modal(modelModalElement) : null;
        const modelForm = document.getElementById('model-form');
        const modelModalTitle = document.getElementById('modelModalTitle');
        const modelIdInput = document.getElementById('model-id');
        const modelProviderSelect = document.getElementById('model-provider');
        const modelNameInput = document.getElementById('model-name');
        const modelDisplayNameInput = document.getElementById('model-display-name');
        const modelContextInput = document.getElementById('model-context');
        const modelHistoryInput = document.getElementById('model-history');
        const modelActiveInput = document.getElementById('model-active');
        const saveModelBtn = document.getElementById('save-model-btn');
        const modelList = document.getElementById('model-list');


        if (addModelBtn && modelModal) {
            addModelBtn.addEventListener('click', () => {
                if (modelModalTitle) modelModalTitle.textContent = '添加AI模型';
                if (modelIdInput) modelIdInput.value = '';
                if (modelForm) modelForm.reset();
                if (modelActiveInput) modelActiveInput.checked = true; // Default to active
                modelModal.show();
            });
        }

        if (saveModelBtn && modelModal) {
            saveModelBtn.addEventListener('click', () => {
                const modelId = modelIdInput ? modelIdInput.value : '';
                const providerId = modelProviderSelect ? modelProviderSelect.value : '';
                const modelName = modelNameInput ? modelNameInput.value : '';
                const displayName = modelDisplayNameInput ? modelDisplayNameInput.value : '';
                const maxContext = modelContextInput ? modelContextInput.value : 4096;
                const maxHistory = modelHistoryInput ? modelHistoryInput.value : 10;
                const isActive = modelActiveInput ? modelActiveInput.checked : true;

                if (!providerId || !modelName || !displayName) {
                    alert('请选择服务提供商并填写模型名称和显示名称');
                    return;
                }

                const data = {
                    provider_id: providerId,
                    model_name: modelName,
                    display_name: displayName,
                    max_context: maxContext,
                    max_history_messages: maxHistory,
                    is_active: isActive
                };
                if (modelId) data.id = modelId;

                fetch('/chat/api/models/', {
                    method: modelId ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify(data)
                })
                .then(response => response.json())
                .then(data => {
                    alert(data.success ? data.message : `操作失败: ${data.message}`);
                    if (data.success) window.location.reload();
                })
                .catch(error => {
                    console.error('操作出错:', error);
                    alert('操作失败，请稍后再试');
                });
            });
        }

        if (modelList) {
             modelList.addEventListener('click', (e) => {
                 const editBtn = e.target.closest('.edit-model-btn');
                 const deleteBtn = e.target.closest('.delete-model-btn');

                 if (editBtn && modelModal) {
                     const modelItem = editBtn.closest('.list-group-item');
                     const modelId = modelItem.dataset.modelId;

                     // Fetch model details
                     fetch(`/chat/api/models/?id=${modelId}`, {
                         method: 'GET',
                         headers: { 'Content-Type': 'application/json' }
                     })
                     .then(response => response.json())
                     .then(data => {
                         if (data.models && data.models.length > 0) {
                             const model = data.models[0];
                             if (modelModalTitle) modelModalTitle.textContent = '编辑AI模型';
                             if (modelIdInput) modelIdInput.value = model.id;
                             if (modelProviderSelect) modelProviderSelect.value = model.provider_id;
                             if (modelNameInput) modelNameInput.value = model.model_name;
                             if (modelDisplayNameInput) modelDisplayNameInput.value = model.display_name;
                             if (modelContextInput) modelContextInput.value = model.max_context;
                             if (modelHistoryInput) modelHistoryInput.value = model.max_history_messages;
                             if (modelActiveInput) modelActiveInput.checked = model.is_active;
                             modelModal.show();
                         } else {
                             alert('获取模型详情失败');
                         }
                     })
                     .catch(error => {
                         console.error('获取模型详情出错:', error);
                         alert('获取模型详情失败，请稍后再试');
                     });

                 } else if (deleteBtn) {
                     const modelItem = deleteBtn.closest('.list-group-item');
                     const modelId = modelItem.dataset.modelId;
                     const modelName = modelItem.querySelector('h6').textContent;
                     if (confirm(`确定要删除模型 "${modelName}" 吗？`)) {
                         fetch('/chat/api/models/', {
                             method: 'DELETE',
                             headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                             body: JSON.stringify({ id: modelId })
                         })
                         .then(response => response.json())
                         .then(data => {
                             alert(data.success ? data.message : `删除失败: ${data.message}`);
                             if (data.success) window.location.reload();
                         })
                         .catch(error => {
                             console.error('删除出错:', error);
                             alert('删除失败，请稍后再试');
                         });
                     }
                 }
             });
        }

        // Remove old user management listeners if they exist (toggle admin, delete user)
        // These are now handled by the new user management section above.
        // No explicit removal needed if the old elements/listeners are removed from HTML/JS.

    } else {
        // --- Non-Admin Logic ---
        const setSelfAdminBtn = document.getElementById('set-self-admin-btn');
        const currentUserId = document.body.dataset.userId; // Assuming user ID is passed via body data attribute

        function createFirstAdmin() {
            return fetch('/users/api/create-first-admin/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ user_id: currentUserId }) // Use ID from data attribute
            })
            .then(response => response.json());
        }

        if (setSelfAdminBtn && currentUserId) {
            setSelfAdminBtn.addEventListener('click', () => {
                fetch('/users/api/manage-roles/', { // Assuming this endpoint exists for non-admins to request admin
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ user_id: currentUserId, is_admin: true })
                })
                .then(response => {
                    if (response.status === 403) { // Permission denied, try creating first admin
                        return createFirstAdmin();
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.success) {
                        alert('已成功将您设为管理员！页面将刷新以应用更改。');
                        window.location.reload();
                    } else {
                        alert(`设置失败: ${data.message || '未知错误'}`);
                    }
                })
                .catch(error => {
                    console.error('操作出错:', error);
                    alert('操作失败，请稍后再试');
                });
            });
        }
    }
});
