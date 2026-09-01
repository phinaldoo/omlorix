const headerThreeDotsButton = document.getElementById('headerDotsButton');
const selectDropdown = document.getElementById('headerDotsButtonDropdown');
const headerOpenModelSettingsButton = document.getElementById('openModelSettingsButton');
const headerDropdownPanelNavigator = window.createDropdownPanelNavigator?.({
    dropdown: selectDropdown,
});
const headerDropdownController = window.createDropdownController?.({
    id: 'header-dots-dropdown',
    trigger: headerThreeDotsButton,
    dropdown: selectDropdown,
    root: headerThreeDotsButton?.closest('.main-header-button-dropdown'),
    escapePriority: 50,
    onBeforeOpen: () => headerDropdownPanelNavigator?.reset({ focus: false }),
    onClose: () => headerDropdownPanelNavigator?.reset({ focus: false }),
});

function setHeaderDropdownOpen(isOpen) {
    if (!headerDropdownController) {
        return;
    }

    headerDropdownController[isOpen ? 'open' : 'close']({ reason: 'api' });
}

function closeHeaderDropdown() {
    setHeaderDropdownOpen(false);
}

headerOpenModelSettingsButton?.addEventListener('click', closeHeaderDropdown);

document.addEventListener('i18n:updated', () => {
    headerDropdownPanelNavigator?.syncHeight();
});

setHeaderDropdownOpen(false);
window.closeHeaderDropdown = closeHeaderDropdown;
