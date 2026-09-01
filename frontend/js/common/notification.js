class NotificationManager {
    static TYPES = ['error', 'success', 'warning', 'info'];
    static DEFAULT_DURATION = 5000;
    static ANIMATION_DURATION = 300;
  
    constructor(containerId = 'notificationContainer') {
      this.container = document.getElementById(containerId);
      if (!this.container) {
        throw new Error(`Container "${containerId}" not found`);
      }
      
      this.notifications = new Map();
      this.notificationCount = 0;
      this.zIndexCounter = 0;
      
      this.container.addEventListener('mouseenter', () => 
        this.container.classList.add('expanded')
      );
      this.container.addEventListener('mouseleave', () => 
        this.container.classList.remove('expanded')
      );
    }
  
    show(message, type = 'error', durationOrOptions = NotificationManager.DEFAULT_DURATION) {
      if (!message) return null;

      const id = `notification-${++this.notificationCount}`;
      const validType = NotificationManager.TYPES.includes(type) ? type : 'error';
      const options = durationOrOptions && typeof durationOrOptions === 'object'
        ? durationOrOptions
        : { duration: durationOrOptions };
      const duration = Number.isFinite(Number(options.duration))
        ? Number(options.duration)
        : NotificationManager.DEFAULT_DURATION;
      
      const notification = this.createElement(id, message, validType, options);
      notification.style.zIndex = `${++this.zIndexCounter}`;
      this.container.insertBefore(notification, this.container.firstChild);
  
      const timeoutId = duration > 0 
        ? setTimeout(() => this.remove(id), duration) 
        : null;
  
      this.notifications.set(id, { element: notification, timeoutId });
      
      return id;
    }
  
    createElement(id, message, type, options = {}) {
      const notification = document.createElement('div');
      notification.className = `notification ${type}`;
      notification.id = id;
      notification.setAttribute('role', 'alert');
  
      const text = document.createElement('span');
      text.className = 'notification-text';
      text.textContent = message;

      const content = document.createElement('div');
      content.className = 'notification-content';
      content.appendChild(text);

      if (typeof options.onAction === 'function' && options.actionLabel) {
        const actionButton = document.createElement('button');
        actionButton.type = 'button';
        actionButton.className = 'notification-action';
        actionButton.textContent = String(options.actionLabel);
        let actionInProgress = false;
        actionButton.onclick = async (event) => {
          event.stopPropagation();
          if (actionInProgress) return;
          actionInProgress = true;
          actionButton.disabled = true;
          try {
            await options.onAction();
          } catch (error) {
            console.error('Notification action failed', error);
          } finally {
            this.remove(id);
          }
        };
        content.appendChild(actionButton);
      }
  
      const closeBtn = document.createElement('button');
      closeBtn.className = 'notification-close';
      const closeLabel = typeof window.getTranslation === 'function'
        ? window.getTranslation('btn_close', 'Close')
        : 'Close';
      closeBtn.setAttribute('aria-label', closeLabel);

      const hasIconFactory = typeof Icons !== 'undefined' && Icons?.createSvgElement && Icons?.close;
      const closeIcon = hasIconFactory
        ? Icons.createSvgElement(Icons.close, 'notification-close-icon')
        : document.createElement('span');
      closeIcon.classList.add('notification-close-icon');
      closeIcon.setAttribute('aria-hidden', 'true');
      closeIcon.setAttribute('focusable', 'false');
      if (!hasIconFactory) {
        closeIcon.textContent = '×';
      }

      closeBtn.onclick = (e) => {
        e.stopPropagation();
        this.remove(id);
      };
      closeBtn.appendChild(closeIcon);
  
      notification.appendChild(content);
      notification.appendChild(closeBtn);
      
      return notification;
    }
  
    remove(id) {
      const data = this.notifications.get(id);
      if (!data) return false;
  
      const { element, timeoutId } = data;
      
      if (timeoutId) clearTimeout(timeoutId);
      
      element.classList.add('removing');
      
      setTimeout(() => {
        element.remove();
        this.notifications.delete(id);
        
        if (this.notifications.size === 0) {
          this.container.classList.remove('expanded');
          this.zIndexCounter = 0;
        }
      }, NotificationManager.ANIMATION_DURATION);
  
      return true;
    }
  
    clear() {
      Array.from(this.notifications.keys()).forEach(id => this.remove(id));
    }
  }
  
  // Global instance
  const notifications = new NotificationManager();
  
  // Public API
  const notify = (message, type = 'error', duration) => 
    notifications.show(message, type, duration);
  
  const notifyError = (message, duration) => 
    notify(message, 'error', duration);
  
  const notifySuccess = (message, duration) => 
    notify(message, 'success', duration);
  
  const notifyWarning = (message, duration) => 
    notify(message, 'warning', duration);
  
  const notifyInfo = (message, duration) => 
    notify(message, 'info', duration);

  Object.assign(window, {
    notify,
    notifyError,
    notifySuccess,
    notifyWarning,
    notifyInfo,
  });
  
  // Optional: ESC key support
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') notifications.clear();
  });
