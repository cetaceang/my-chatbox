document.addEventListener('DOMContentLoaded', function () {
    const themeToggle = document.getElementById('theme-toggle');
    const fancyThemeLink = document.getElementById('fancy-theme-link');
    const body = document.body;

    // Function to apply the selected theme
    function applyTheme(theme) {
        if (theme === 'fancy') {
            fancyThemeLink.disabled = false;
            body.classList.add('theme-fancy');
            themeToggle.checked = true;
        } else {
            fancyThemeLink.disabled = true;
            body.classList.remove('theme-fancy');
            themeToggle.checked = false;
        }
    }

    // On page load, check for saved theme preference
    const savedTheme = localStorage.getItem('chatTheme');
    if (savedTheme) {
        applyTheme(savedTheme);
    }

    // Add event listener for the theme toggle switch
    themeToggle.addEventListener('change', function () {
        if (this.checked) {
            applyTheme('fancy');
            localStorage.setItem('chatTheme', 'fancy');
        } else {
            applyTheme('default');
            localStorage.setItem('chatTheme', 'default');
        }
    });
});
