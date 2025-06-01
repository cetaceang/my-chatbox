// static/chat/js/state_manager.js

/**
 * Central state management for the chat application.
 */
window.ChatStateManager = (function() { // Directly assign IIFE result to window property
    // --- Private State ---
    let state = {
        currentConversationId: null,
        isSyncing: false,           // Is the initial sync or a forced sync running?
        isGenerating: false,        // Is any AI response generation in progress (send or regenerate)?
        isStopping: false,          // Is a stop request currently being processed?
        activeGenerationIds: new Set(), // Set of backend generation_ids currently active
        cancelledGenerationIds: new Set(), // Set of generation_ids explicitly cancelled by user
        // We might not need to store all messages here if the DOM is the source of truth,
        // messages: new Map(), // Example: messageId -> { content, isUser, element, status }
        lastError: null,            // Store the last system error message
        modelId: null,              // Currently selected model ID
        activeGenerationId: null,   // 当前活跃的generation_id
    };

    // --- Private Subscriber Management ---
    let subscribers = [];

    function notifySubscribers() {
        // Convert Set to Array for reliable JSON stringification
        const stateForLog = { ...state, activeGenerationIds: [...state.activeGenerationIds] };
        console.log('[StateManager] Notifying subscribers. Current state:', JSON.stringify(stateForLog));
        // Pass a *copy* of the state to subscribers to prevent direct mutation
        const stateCopy = { ...state, activeGenerationIds: new Set(state.activeGenerationIds) };
        subscribers.forEach(callback => {
            try {
                callback(stateCopy);
            } catch (error) {
                console.error("[StateManager] Error in subscriber callback:", error);
            }
        });
    }


    // --- Private Helper Functions --- (This might become less relevant if state is driven by backend signals)
    function _updateGeneratingStatus() {
        const oldGenerating = state.isGenerating;
        // Base isGenerating on the presence of active backend generation IDs
        state.isGenerating = state.activeGenerationIds.size > 0;
        if (oldGenerating !== state.isGenerating) { // Only log if changed
             console.log(`[StateManager] Updated isGenerating based on activeGenerationIds: ${state.isGenerating}`);
        }
    }

    /**
     * Resets flags related to stopping, typically used when a stop attempt fails
     * or needs to be aborted client-side.
     */
    function resetStoppingState() {
        console.warn("[StateManager] resetStoppingState called. Resetting isStopping=false.");
        let changed = false;
        if (state.isStopping) {
            state.isStopping = false;
            changed = true;
        }
        // Optionally, should we also reset isGenerating if stopping fails?
        // if (state.isGenerating) {
        //     state.isGenerating = false; // Or maybe not? Depends on desired behavior.
        //     changed = true;
        // }
        if (changed) {
            notifySubscribers();
        }
    }

    // --- Public API ---
    const publicApi = { // Create object first
        /**
         * Subscribe to state changes.
         * @param {function} callback - Function to call when state changes. Receives state object.
         */
        subscribe: function(callback) {
            if (typeof callback === 'function' && !subscribers.includes(callback)) {
                subscribers.push(callback);
                console.log('[StateManager] Subscriber added. Total:', subscribers.length);
            }
        },

        /**
         * Unsubscribe from state changes.
         * @param {function} callback - The function to remove.
         */
        unsubscribe: function(callback) {
            subscribers = subscribers.filter(sub => sub !== callback);
            console.log('[StateManager] Subscriber removed. Total:', subscribers.length);
        },

        /**
         * Initialize the state manager.
         * @param {string|null} initialConversationId
         * @param {string|null} initialModelId
         */
        init: function(initialConversationId, initialModelId) {
            const changed = state.currentConversationId !== initialConversationId || state.modelId !== initialModelId;
            state.currentConversationId = initialConversationId;
            state.modelId = initialModelId;
            // Reset other states on init
            state.isGenerating = false;
            state.isStopping = false;
            state.activeGenerationIds.clear();
            state.cancelledGenerationIds.clear(); // Also clear cancelled IDs on init
            state.isSyncing = false;
            state.lastError = null;
            const stateForLog = { ...state, activeGenerationIds: [...state.activeGenerationIds] };
            console.log('[StateManager] Initialized:', JSON.stringify(stateForLog));
            if (changed) { // Notify if key IDs changed during init
                 notifySubscribers();
            }
        },

        /**
         * Get a copy of the current state or a specific property.
         * @param {string} [property] - Optional property name to retrieve.
         * @returns {object|any} - The full state object or the specific property value.
         */
        getState: function(property) {
            if (property) {
                return state[property];
            }
            return { ...state }; // Return a copy to prevent direct mutation
        },

        // --- Setters for specific state properties ---

        setConversationId: function(id) {
            if (state.currentConversationId !== id) {
                state.currentConversationId = id;
                console.log(`[StateManager] Conversation ID set to: ${id}`);
                notifySubscribers();
            }
        },

        setModelId: function(id) {
            if (state.modelId !== id) {
                state.modelId = id;
                console.log(`[StateManager] Model ID set to: ${id}`);
                notifySubscribers();
            }
        },

        setSyncing: function(isSyncing) {
            if (state.isSyncing !== isSyncing) {
                state.isSyncing = isSyncing;
                console.log(`[StateManager] Syncing set to: ${isSyncing}`);
                notifySubscribers();
            }
        },

        setLastError: function(error) {
            if (state.lastError !== error) {
                state.lastError = error;
                console.log(`[StateManager] Last error set: ${error}`);
                notifySubscribers(); // Notify on error changes too
            }
        },

        // --- Action-based State Changes ---

        /**
         * Mark the start of a generation process (send or regenerate) - Primarily for immediate UI feedback.
         * The actual state tracking relies on handleGenerationStart/End from backend signals.
         * @param {string} tempId - The temporary ID of the user message triggering this.
         */
        startGeneration: function(tempId = null) {
            // This function now mainly signals the *intent* to generate for UI purposes.
            // It sets isGenerating immediately, but the backend signal confirms the actual start.
            let stateChanged = false;
            console.log(`[StateManager] startGeneration called (likely from UI action) for tempId: ${tempId}`);

            // Set isGenerating immediately for responsive UI (e.g., disable button)
            if (!state.isGenerating) {
                 console.log('[StateManager] Setting isGenerating=true for immediate UI feedback.');
                 state.isGenerating = true;
                 stateChanged = true;
            }
            // Reset stopping flag if we are starting a new generation attempt
            if (state.isStopping) {
                 console.log('[StateManager] Resetting isStopping flag because new generation started.');
                 state.isStopping = false;
                 stateChanged = true;
            }
            // We no longer add tempId to activeGenerationIds here. That happens in handleGenerationStart.

            // Clear cancelled IDs when starting a new generation attempt
            if (state.cancelledGenerationIds.size > 0) {
                 console.log('[StateManager] Clearing cancelledGenerationIds set as new generation is starting.');
                 state.cancelledGenerationIds.clear();
                 stateChanged = true; // State changed because cancelled IDs were cleared
            }

            console.log(`[StateManager] startGeneration finished. isGenerating: ${state.isGenerating}, isStopping: ${state.isStopping}`);
            if (stateChanged) {
                 notifySubscribers();
            }
            // Note: We don't return anything specific, the backend signal is the source of truth.
        },

        /**
         * [REMOVED] endGeneration is no longer the primary way to manage state.
         * Backend signals handleGenerationStart/End are now used.
         * Keeping the function stubbed out for now in case other parts still call it,
         * but it should ideally be removed later.
         */
        endGeneration: function(userMessageId = null, withError = false) {
             console.warn(`[StateManager] Deprecated endGeneration called with userMessageId: ${userMessageId}. State is now managed by handleGenerationStart/End.`);
             // Optionally, try to map userMessageId (tempId) to a generationId if needed,
             // but the core logic relies on backend signals now.
        },

        /**
         * [NEW] Handle the start of a generation confirmed by the backend.
         * @param {string} generationId - The unique ID for this generation task from the backend.
         */
        handleGenerationStart: function(generationId) {
            if (!generationId) {
                console.warn('[StateManager] handleGenerationStart called without a generationId.');
                return;
            }
            let stateChanged = false;
            if (!state.activeGenerationIds.has(generationId)) {
                state.activeGenerationIds.add(generationId);
                console.log(`[StateManager] Backend confirmed generation start. Added generationId: ${generationId}. Active IDs: ${[...state.activeGenerationIds]}`);
                stateChanged = true; // ID added
            } else {
                 console.log(`[StateManager] Received duplicate generation_start signal for ID: ${generationId}`);
            }

            // Clear cancelled IDs when a new generation *confirmed by backend* starts
            // This ensures that even if UI startGeneration wasn't called, cancelled IDs are cleared.
            if (state.cancelledGenerationIds.size > 0) {
                 console.log('[StateManager] Clearing cancelledGenerationIds set as backend confirmed new generation start.');
                 state.cancelledGenerationIds.clear();
                 stateChanged = true;
            }

            // Ensure isGenerating is true if any generation is active
            if (!state.isGenerating && state.activeGenerationIds.size > 0) {
                console.log('[StateManager] Setting isGenerating=true based on active generation IDs.');
                state.isGenerating = true;
                stateChanged = true;
            }
             // Reset stopping flag if backend confirms a new generation started
             if (state.isStopping) {
                 console.log('[StateManager] Resetting isStopping flag due to confirmed generation start.');
                 state.isStopping = false;
                 stateChanged = true;
             }

            // *** ADDED: Set the single activeGenerationId ***
            if (state.activeGenerationId !== generationId) {
                console.log(`[StateManager] Setting activeGenerationId to: ${generationId}`);
                state.activeGenerationId = generationId;
                stateChanged = true; // State changed because activeGenerationId was set
            }
            // *** END ADDED ***

            if (stateChanged) {
                notifySubscribers();
            }
        },

        /**
         * [NEW] Handle the end of a generation confirmed by the backend.
         * @param {string} generationId - The unique ID for the generation task that ended.
         * @param {string} [status='unknown'] - The status reported by the backend (e.g., 'completed', 'failed', 'cancelled').
         */
        handleGenerationEnd: function(generationId, status = 'unknown') {
             if (!generationId) {
                console.warn('[StateManager] handleGenerationEnd called without a generationId.');
                return;
            }
            let stateChanged = false;
            const wasGenerating = state.isGenerating;

            if (state.activeGenerationIds.has(generationId)) {
                state.activeGenerationIds.delete(generationId);
                console.log(`[StateManager] Backend confirmed generation end for ID: ${generationId}. Status: ${status}. Active IDs remaining: ${[...state.activeGenerationIds]}`);
                stateChanged = true; // ID removed
            } else {
                console.warn(`[StateManager] Received generation_end signal for ID: ${generationId}, but it was not found in active set.`);
                // If the ID wasn't found, maybe isGenerating is stuck true? Check and correct.
            }

            // Update isGenerating status ONLY if the set is now empty
            if (state.activeGenerationIds.size === 0 && state.isGenerating) {
                console.log('[StateManager] Last active generation ended. Setting isGenerating to false.');
                state.isGenerating = false;
                stateChanged = true; // isGenerating changed
            } else if (state.activeGenerationIds.size > 0 && !state.isGenerating) {
                 // Should not happen if logic is correct, but correct it if it does
                 console.warn('[StateManager] Generation ended, but isGenerating was false while others are still active? Setting true.');
                 state.isGenerating = true;
                 stateChanged = true;
            }

            // --- REMOVED: Do not reset isStopping based on generation_end ---
            // The 'generation_stopped' signal is the definitive confirmation.
            // if (state.isStopping && state.activeGenerationIds.size === 0) {
            //     console.log('[StateManager] Last active generation ended while in stopping state. Resetting isStopping to false.');
            //     state.isStopping = false;
            //     stateChanged = true; // isStopping changed
            // }
            // --- END REMOVED ---

            // If the generation was cancelled or stopped by the user, add it to the cancelled set
            if ((status === 'cancelled' || status === 'stopped') && generationId) {
                if (!state.cancelledGenerationIds.has(generationId)) {
                    console.log(`[StateManager] Adding generationId ${generationId} to cancelled set due to status: ${status}.`);
                    state.cancelledGenerationIds.add(generationId);
                    // No state change notification needed just for adding to this set,
                    // other changes (isGenerating, isStopping) will trigger notification if necessary.
                }
            }

            // *** ADDED: Clear activeGenerationId if it matches the one ending ***
            if (generationId && state.activeGenerationId === generationId) {
                console.log(`[StateManager] Clearing activeGenerationId (${state.activeGenerationId}) because it matched the ending ID.`);
                state.activeGenerationId = null;
                stateChanged = true; // State changed because activeGenerationId was cleared
            }
            // *** REMOVED: Do not clear activeGenerationId based on generation_end ***
            // if (generationId && state.activeGenerationId === generationId) {
            //     console.log(`[StateManager] Clearing activeGenerationId (${state.activeGenerationId}) because it matched the ending ID.`);
            //     state.activeGenerationId = null;
            //     stateChanged = true; // State changed because activeGenerationId was cleared
            // }
            // *** END REMOVED ***

            // Let confirmGlobalStop handle resetting isStopping and activeGenerationId globally if a specific 'generation_stopped' message arrives.

            if (status === 'failed' && !state.lastError) {
                 // Optionally set lastError based on backend status
                 // state.lastError = `Generation ${generationId} failed.`;
                 // stateChanged = true;
            }

            if (stateChanged || state.isGenerating !== wasGenerating) {
                notifySubscribers();
            }
        },

        /**
         * Mark the start of a stop request (sent to backend).
         */
        requestStop: function() { // User clicked stop button
            let stateChanged = false;
            // Can only request stop if generating AND not already stopping
            if (state.isGenerating && !state.isStopping) {
                state.isStopping = true; // Mark that a stop has been requested
                console.log('[StateManager] Stop requested by UI. isStopping set to true. Waiting for backend confirmation via generation_end or generation_stopped.');
                stateChanged = true;
            } else if (!state.isGenerating) {
                console.log('[StateManager] Stop requested but nothing is generating.');
            } else if (state.isStopping) {
                 console.log('[StateManager] Stop already requested.');
            }
            if (stateChanged) {
                 notifySubscribers();
            }
        },

        /**
         * Confirm that a global stop process has completed (e.g., received 'generation_stopped' from backend).
         * This resets all generation-related flags forcefully.
         */
        confirmGlobalStop: function() { // Renamed for clarity
            let stateChanged = false;
            if (state.isGenerating) {
                state.isGenerating = false;
                stateChanged = true;
            }
            if (state.isStopping) {
                state.isStopping = false;
                stateChanged = true;
            }
            if (state.activeGenerationIds.size > 0) {
                 console.log('[StateManager] Global stop: Adding active IDs to cancelled set before clearing.');
                 // Add all currently active IDs to the cancelled set
                 state.activeGenerationIds.forEach(id => {
                     if (!state.cancelledGenerationIds.has(id)) {
                         state.cancelledGenerationIds.add(id);
                     }
                 });
                 state.activeGenerationIds.clear();
                 stateChanged = true;
            }
            // *** ADDED: Explicitly clear activeGenerationId on global stop ***
            if (state.activeGenerationId !== null) {
                console.log('[StateManager] Global stop: Clearing activeGenerationId.');
                state.activeGenerationId = null;
                stateChanged = true;
            }
            // *** END ADDED ***
            console.log('[StateManager] Global stop confirmed. All generation flags reset. Cancelled set:', [...state.cancelledGenerationIds]);
            if (stateChanged) {
                 notifySubscribers();
            }
        },

        resetStoppingState,     // ADDED: New function for client-side stop reset

        // --- Utility Getters ---

        /**
         * Check if a specific backend generation task is active.
         * @param {string} generationId
         * @returns {boolean}
         */
        isGenerationActive: function(generationId) { // Renamed for clarity
            return state.activeGenerationIds.has(generationId);
        },

        /**
         * Check if a specific generation task was explicitly cancelled by the user.
         * @param {string} generationId
         * @returns {boolean}
         */
        isGenerationCancelled: function(generationId) {
            const cancelled = state.cancelledGenerationIds.has(generationId);
            if (cancelled) {
                 console.log(`[StateManager] Check: Generation ${generationId} IS in the cancelled set.`);
            }
            return cancelled;
        },

        /**
         * Check if any generation or stopping process is active.
         * Useful for disabling inputs/buttons.
         * @returns {boolean}
         */
        isBusy: function() {
            return state.isGenerating || state.isStopping || state.isSyncing;
        },
        
        /**
         * 设置当前活跃的generation_id
         * @param {string} generationId - 生成回复的唯一标识符
         */
        setGenerationId: function(generationId) {
            if (state.activeGenerationId !== generationId) {
                state.activeGenerationId = generationId;
                console.log(`[StateManager] Generation ID set to: ${generationId}`);
                notifySubscribers();
            }
        },
        
        /**
         * 获取当前活跃的generation_id
         * @returns {string|null} - 当前活跃的generation_id
         */
        getGenerationId: function() {
            return state.activeGenerationId;
        },
        
        /**
         * 清除当前活跃的generation_id并重置isStopping状态
         */
        clearGenerationId: function() {
            if (state.activeGenerationId !== null || state.isStopping) {
                state.activeGenerationId = null;
                // 同时确保isStopping状态被重置
                if (state.isStopping) {
                    state.isStopping = false;
                    console.log('[StateManager] isStopping reset to false');
                }
                console.log('[StateManager] Generation ID cleared and isStopping reset');
                notifySubscribers();
            }
        }
    };
    console.log('[StateManager IIFE] Returning public API object:', publicApi); // Log the object being returned
    return publicApi;
})(); // End of IIFE

console.log('[StateManager Script] Assigned to window.ChatStateManager:', window.ChatStateManager); // Log after assignment

// REMOVED redundant assignment line:
// window.ChatStateManager = ChatStateManager;
