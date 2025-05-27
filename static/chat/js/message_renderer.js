/* eslint-env browser, amd */
/* globals marked, MathJax */ // Inform linter about globals

// Updated rendering function for Marked and MathJax
function renderMessageContent(messageElement) {
    const messageIdForLog = messageElement ? (messageElement.getAttribute('data-message-id') || messageElement.getAttribute('data-temp-id') || 'N/A') : 'unknown-element';
    console.log(`renderMessageContent: Called for message ID ${messageIdForLog}.`); // Log entry

    const renderTarget = messageElement.querySelector('p > .render-target');
    if (!renderTarget) { // Check if target exists
        console.warn(`renderMessageContent: Target 'p > .render-target' not found for ID ${messageIdForLog}.`);
        return;
    }

    if (renderTarget) { // This 'if' might be redundant now, but keep structure
        // Get original content from data attribute
        const originalContent = renderTarget.getAttribute('data-original-content');
        if (originalContent === null) {
             console.warn(`renderMessageContent: Target missing data-original-content for ID ${messageIdForLog}.`);
             return; // Skip if no original content found
        }

        // Check if already rendered (simple check)
        if (renderTarget.getAttribute('data-rendered') === 'true') {
            console.log(`renderMessageContent: Skipped, already rendered for ID ${messageIdForLog}.`);
            return;
        }

        // const messageIdForLog = messageElement.getAttribute('data-message-id') || messageElement.getAttribute('data-temp-id') || 'N/A'; // Moved up
        console.log(`[Render Start] ID: ${messageIdForLog}`); // Keep existing log
        console.log(`[Render] Original Content:`, originalContent); // Keep existing log

        try {
            // 1. Render Markdown
            console.log(`[Render] Calling marked.parse() for ID: ${messageIdForLog}`);
            // Ensure marked is available
            if (typeof marked === 'undefined') {
                console.error("Marked library is not loaded.");
                renderTarget.innerHTML = escapeHtml(originalContent); // Fallback
                renderTarget.setAttribute('data-rendered', 'error');
                renderTarget.innerHTML = escapeHtml(originalContent); // Fallback
                renderTarget.setAttribute('data-rendered', 'error');
                console.error(`[Render Error] Marked library not loaded for ID: ${messageIdForLog}`);
                return;
            }
            const renderedMarkdown = marked.parse(originalContent, { breaks: true });
            console.log(`[Render] Marked Output (length: ${renderedMarkdown.length}):`, renderedMarkdown.substring(0, 100) + (renderedMarkdown.length > 100 ? '...' : '')); // Log beginning of marked output

            renderTarget.innerHTML = renderedMarkdown; // Render Markdown first
            console.log(`[Render] Set innerHTML for ID: ${messageIdForLog}. Current innerHTML length: ${renderTarget.innerHTML.length}`);

            // 2. Tell MathJax to typeset the math in this specific element.
            // Ensure MathJax is available and configured
            if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                console.log(`[Render] Calling MathJax.typesetPromise() for ID: ${messageIdForLog}`);
                MathJax.typesetPromise([renderTarget]).then(() => {
                    console.log(`[Render] MathJax typesetting complete for ID: ${messageIdForLog}`);
                }).catch((err) => {
                    console.error(`[Render Error] MathJax typesetting failed for ID: ${messageIdForLog}:`, err);
                    renderTarget.setAttribute('data-rendered', 'mathjax-error');
                });
            } else {
                console.warn(`[Render Warn] MathJax not ready or typesetPromise unavailable for ID: ${messageIdForLog}`);
                // MathJax might process it later automatically if configured globally
            }

            // Mark as rendered (even if MathJax is pending/failed, Markdown is done)
            renderTarget.setAttribute('data-rendered', 'true');
            console.log(`[Render Success] ID: ${messageIdForLog}`);

        } catch (error) {
            console.error(`[Render Error] Uncaught error during rendering for ID: ${messageIdForLog}:`, error, "Element:", messageElement);
            // Restore original escaped content on error to prevent broken state
            // Ensure escapeHtml is available (it should be global or imported if using modules)
            if (typeof escapeHtml === 'function') {
                 renderTarget.innerHTML = escapeHtml(originalContent);
            } else {
                 // Basic fallback if escapeHtml isn't available
                 renderTarget.textContent = originalContent;
            }
            renderTarget.setAttribute('data-rendered', 'error'); // Mark as error
        }
    } else {
         // If the structure is old (no .render-target), log a warning or skip
         // console.warn("Message element missing '.render-target' span:", messageElement);
    }
}
