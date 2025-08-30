document.addEventListener('DOMContentLoaded', function () {
    // 注意：我们现在直接操作 Bootstrap 的 .col-md-3 类
    const sidebar = document.querySelector('.col-md-3'); 
    const sidebarToggler = document.getElementById('sidebar-toggler');
    const overlay = document.querySelector('.overlay');

    if (sidebar && sidebarToggler && overlay) {
        sidebarToggler.addEventListener('click', function () {
            sidebar.classList.toggle('sidebar-active');
            overlay.classList.toggle('active');
        });

        overlay.addEventListener('click', function () {
            sidebar.classList.remove('sidebar-active');
            overlay.classList.remove('active');
        });
    }
});
