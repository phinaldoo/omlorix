// Drag and Drop functionality
function setupDragAndDrop() {
    const pinnedSection = document.getElementById('pinnedChats');
    const chatsSection = document.getElementById('history');
    const pinnedContainer = document.getElementById('pinnedChatsContainer');
    // Setup drop zones
    [pinnedSection, chatsSection].forEach(section => {
        if (section) {
            section.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                const target = e.target.closest('.sidebar-element');
                if (target) {
                    target.classList.add('drag-over');
                }
            });

            section.addEventListener('dragleave', (e) => {
                const target = e.target.closest('.sidebar-element');
                if (target) {
                    target.classList.remove('drag-over');
                }
            });

            section.addEventListener('drop', async (e) => {
                e.preventDefault();
                const chatId = e.dataTransfer.getData('text/plain');
                const originalPosition = e.dataTransfer.getData('original-position');

                // Remove visual feedback
                document.querySelectorAll('.sidebar-element').forEach(el => {
                    el.classList.remove('drag-over');
                });

                if (!chatId) return;

                try {
                    if (section.id === 'pinnedChats') {
                        // Dropped on pinned section
                        if (originalPosition) {
                            // Reordering within pinned - find new position
                            const droppedElement = e.target.closest('.sidebar-element');
                            const allPinnedElements = Array.from(pinnedContainer.querySelectorAll('.sidebar-element'));
                            let newPosition = 1;

                            if (droppedElement && droppedElement.dataset.chatId !== chatId) {
                                // Find position relative to other elements
                                const droppedIndex = allPinnedElements.findIndex(el => el.dataset.chatId === droppedElement.dataset.chatId);
                                newPosition = droppedIndex + 1;
                            }

                            await moveChat(chatId, newPosition);
                        } else {
                            // Pin chat from regular chats
                            await pinChat(chatId, 1);
                        }
                    } else if (section.id === 'history') {
                        // Dropped on regular chats section - unpin
                        await unpinChat(chatId);
                    }
                } catch (err) {
                    console.error('Drop operation failed', err);
                }
            });
        }
    });
}
// Initialize drag and drop when DOM is ready
document.addEventListener('DOMContentLoaded', setupDragAndDrop);
