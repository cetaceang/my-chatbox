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
        startGeneration: function(tempId) {
            if (!tempId) {
                console.error("[StateManager] startGeneration called without a tempId.");
                return;
            }
            let stateChanged = false;
            console.log(`[StateManager] startGeneration called for tempId: ${tempId}`);

            // Set isGenerating immediately for responsive UI
            if (!state.isGenerating) {
                state.isGenerating = true;
                stateChanged = true;
            }
            if (state.isStopping) {
                state.isStopping = false;
                stateChanged = true;
            }

            // --- CORE CHANGE: Use the user message tempId as the temporary generationId ---
            state.activeGenerationId = tempId;
            state.activeGenerationIds.add(tempId);
            console.log(`[StateManager] Set temporary generation ID to ${tempId}`);
            stateChanged = true;
            // --- END CORE CHANGE ---

            // --- FINAL FIX 2: Do not clear cancelled IDs here. A new generation should not
            // invalidate a previous stop request that is still being processed.
            // if (state.cancelledGenerationIds.size > 0) {
            //     state.cancelledGenerationIds.clear();
            //     stateChanged = true;
            // }

            if (stateChanged) {
                notifySubscribers();
            }
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
         * [REVISED] Handle the start of a generation confirmed by the backend.
         * Under the "Single ID Principle", realGenerationId and tempId are the same.
         * @param {string} generationId - The unique ID for this generation task from the frontend.
         * @param {string} tempId - The same unique ID, passed as tempId for consistency.
         */
        handleGenerationStart: function(generationId, tempId) {
            // Guard against resurrecting a cancelled generation.
            if (state.cancelledGenerationIds.has(generationId)) {
                console.warn(`[StateManager] handleGenerationStart received for a generation ID (${generationId}) that is already cancelled. Ignoring.`);
                return;
            }

            if (!generationId) {
                console.warn(`[StateManager] handleGenerationStart called without a generationId.`);
                return;
            }
            let stateChanged = false;

            // Since we now use a single ID, the logic is much simpler.
            // No more swapping. Just ensure the ID is active.
            if (!state.activeGenerationIds.has(generationId)) {
                state.activeGenerationIds.add(generationId);
                console.log(`[StateManager] Added generation ID ${generationId} to active set.`);
                stateChanged = true;
            }

            // Set the main active ID.
            if (state.activeGenerationId !== generationId) {
                state.activeGenerationId = generationId;
                console.log(`[StateManager] Set activeGenerationId to: ${generationId}`);
                stateChanged = true;
            }

            // Ensure isGenerating is true.
            if (!state.isGenerating) {
                state.isGenerating = true;
                stateChanged = true;
            }
            
            // If a stop was requested while this was in flight, add to cancelled set now.
            if (state.isStopping) {
                console.warn(`[StateManager] handleGenerationStart received for ${generationId}, but a stop is already in progress. Adding to cancelled set.`);
                if (!state.cancelledGenerationIds.has(generationId)) {
                    state.cancelledGenerationIds.add(generationId);
                }
            }

            if (stateChanged) {
                notifySubscribers();
            }
        },

        /**
         * [REVISED] Handle the end of a generation confirmed by the backend.
         * @param {string} generationId - The unique ID for the generation task that ended.
         * @param {string} [status='unknown'] - The status reported by the backend (e.g., 'completed', 'failed', 'cancelled').
         */
        handleGenerationEnd: function(generationId, status = 'unknown') {
            if (!generationId) {
                console.warn('[StateManager] handleGenerationEnd called without a generationId.');
                return;
            }
            let stateChanged = false;

            // --- ROBUST CLEANUP: Remove ID from BOTH active and cancelled sets ---
            // This handles the race condition where a stop request moves the ID from active to cancelled
            // just before the generation_end signal arrives.
            if (state.activeGenerationIds.has(generationId)) {
                state.activeGenerationIds.delete(generationId);
                console.log(`[StateManager] Removed ${generationId} from active set. Status: ${status}.`);
                stateChanged = true;
            }
            if (state.cancelledGenerationIds.has(generationId)) {
                state.cancelledGenerationIds.delete(generationId);
                console.log(`[StateManager] Removed ${generationId} from cancelled set. Status: ${status}.`);
                // This cleanup doesn't trigger a UI notification on its own.
            }

            // If the ending ID was the main active one, clear it.
            if (state.activeGenerationId === generationId) {
                state.activeGenerationId = null;
                console.log(`[StateManager] Cleared activeGenerationId because it matched the ending ID.`);
                stateChanged = true;
            }

            // Update global generating flag ONLY if no more generations are active.
            if (state.activeGenerationIds.size === 0 && state.isGenerating) {
                console.log('[StateManager] Last active generation ended. Setting isGenerating to false.');
                state.isGenerating = false;
                stateChanged = true;
            }
            
            // Also reset the 'isStopping' flag now that the relevant generation has officially ended.
            // This is safe because we are no longer waiting for anything related to this stop action.
            if (state.isStopping && state.activeGenerationIds.size === 0) {
                console.log('[StateManager] Last active generation ended. Resetting isStopping to false.');
                state.isStopping = false;
                stateChanged = true;
            }

            if (stateChanged) {
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
         * [NEW] Optimistically reset the UI state immediately upon stop request.
         * This provides instant feedback to the user, allowing them to send a new message
         * while the backend processes the stop command asynchronously.
         */
        requestStopOptimistic: function() {
            console.log('[StateManager] Optimistic stop requested. Resetting UI state immediately.');
            let stateChanged = false;

            // Move all active IDs to the cancelled set so their streams will be ignored.
            if (state.activeGenerationIds.size > 0) {
                console.log('[StateManager] Moving active generation IDs to cancelled set:', [...state.activeGenerationIds]);
                state.activeGenerationIds.forEach(id => {
                    if (!state.cancelledGenerationIds.has(id)) {
                        state.cancelledGenerationIds.add(id);
                    }
                });
                state.activeGenerationIds.clear();
                stateChanged = true;
            }

            // Immediately reset flags to unblock the UI.
            if (state.isGenerating) {
                state.isGenerating = false;
                stateChanged = true;
            }
            if (state.isStopping) {
                state.isStopping = false; // No longer waiting for backend confirmation in the UI.
                stateChanged = true;
            }
            if (state.activeGenerationId) {
                state.activeGenerationId = null;
                stateChanged = true;
            }

            if (stateChanged) {
                console.log('[StateManager] UI state reset for optimistic stop. Notifying subscribers.');
                notifySubscribers();
            }
        },

        // DEPRECATED: confirmGlobalStop is a dangerous global reset.
        // State is now managed precisely via handleGenerationEnd with a 'stopped' or 'cancelled' status.
        // confirmGlobalStop: function() { ... },

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
