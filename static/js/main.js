/**
 * Main JavaScript file for VK Post Parser
 */
document.addEventListener('DOMContentLoaded', function() {
    // Enable tooltips everywhere
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Auto-hide alerts after 5 seconds
    setTimeout(function() {
        var alerts = document.querySelectorAll('.alert:not(.alert-dismissible)');
        alerts.forEach(function(alert) {
            alert.classList.add('fade');
            setTimeout(function() {
                alert.remove();
            }, 500);
        });
    }, 5000);

    // Add proper styling to links with external targets
    var externalLinks = document.querySelectorAll('a[target="_blank"]');
    externalLinks.forEach(function(link) {
        // Check if link doesn't already have an icon
        if (!link.querySelector('.fa-external-link-alt')) {
            // Don't add icon if link already has other icons
            if (!link.querySelector('.fa')) {
                var icon = document.createElement('i');
                icon.className = 'fas fa-external-link-alt ms-1 small';
                link.appendChild(icon);
            }
        }
    });

    // Add active class to current navigation item
    var currentPath = window.location.pathname;
    var navLinks = document.querySelectorAll('.navbar-nav .nav-link');
    
    navLinks.forEach(function(link) {
        var href = link.getAttribute('href');
        if (href === currentPath) {
            link.classList.add('active');
        } else if (currentPath.startsWith(href) && href !== '/') {
            link.classList.add('active');
        }
    });

    // Format timestamps to local time if needed
    var timestamps = document.querySelectorAll('.timestamp-local');
    timestamps.forEach(function(el) {
        var utcTime = el.getAttribute('data-utc');
        if (utcTime) {
            var date = new Date(utcTime);
            el.textContent = date.toLocaleString();
        }
    });
    
    // Initialize countdown timers for pending posts
    initCountdownTimers();
    
    // Refresh countdown timers every second
    setInterval(updateCountdownTimers, 1000);
});

// Функция для парсинга поста
function parsePost(postId) {
    const button = document.querySelector(`button[data-post-id="${postId}"]`);
    if (!button) return;
    
    // Отключаем кнопку и показываем спиннер
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Парсинг...';
    
    fetch(`/api/parse-post/${postId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            // Показываем успешное сообщение
            const alert = document.createElement('div');
            alert.className = 'alert alert-success alert-dismissible fade show';
            alert.innerHTML = `
                ${data.message}
                ${data.result_id ? `<a href="/results/${data.result_id}" class="alert-link">Посмотреть результат</a>` : ''}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            `;
            const container = document.querySelector('.container');
            if (container && container.firstChild) {
                container.insertBefore(alert, container.firstChild);
            } else if (container) {
                container.appendChild(alert);
            }
            
            // Обновляем статус поста
            const statusBadge = document.querySelector(`#post-status-${postId}`);
            if (statusBadge) {
                statusBadge.className = 'badge bg-success';
                statusBadge.textContent = 'Завершен';
            }
            
            // Удаляем кнопку парсинга
            button.remove();
        } else {
            // Возвращаем кнопку в исходное состояние и показываем ошибку
            button.disabled = false;
            button.innerHTML = 'Парсить сейчас';
            
            const alert = document.createElement('div');
            alert.className = 'alert alert-danger alert-dismissible fade show';
            alert.innerHTML = `
                Ошибка: ${data.message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            `;
            document.querySelector('.container').insertBefore(alert, document.querySelector('.container').firstChild);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        button.disabled = false;
        button.innerHTML = 'Парсить сейчас';
        
        const alert = document.createElement('div');
        alert.className = 'alert alert-danger alert-dismissible fade show';
        alert.innerHTML = `
            Ошибка при парсинге поста
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        document.querySelector('.container').insertBefore(alert, document.querySelector('.container').firstChild);
    });
}

/**
 * Function to confirm dangerous actions with dialog
 * @param {string} message - Confirmation message to display
 * @param {function} callback - Callback function to execute if confirmed
 */
function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
}

/**
 * Function to format relative time
 * @param {Date} date - Date to format
 * @returns {string} Formatted relative time
 */
function formatRelativeTime(date) {
    const now = new Date();
    const diffInSeconds = Math.floor((now - date) / 1000);
    
    if (diffInSeconds < 60) {
        return 'только что';
    }
    
    const diffInMinutes = Math.floor(diffInSeconds / 60);
    if (diffInMinutes < 60) {
        return `${diffInMinutes} ${pluralize(diffInMinutes, 'минуту', 'минуты', 'минут')} назад`;
    }
    
    const diffInHours = Math.floor(diffInMinutes / 60);
    if (diffInHours < 24) {
        return `${diffInHours} ${pluralize(diffInHours, 'час', 'часа', 'часов')} назад`;
    }
    
    const diffInDays = Math.floor(diffInHours / 24);
    if (diffInDays < 30) {
        return `${diffInDays} ${pluralize(diffInDays, 'день', 'дня', 'дней')} назад`;
    }
    
    // For older dates, return the actual date
    return date.toLocaleDateString();
}

/**
 * Helper function for pluralization in Russian
 * @param {number} count - Number to get plural form for
 * @param {string} form1 - Form for 1
 * @param {string} form2 - Form for 2-4
 * @param {string} form3 - Form for 5-20
 * @returns {string} Correct plural form
 */
function pluralize(count, form1, form2, form3) {
    let n = Math.abs(count) % 100;
    if (n > 10 && n < 20) return form3;
    n = n % 10;
    if (n === 1) return form1;
    if (n > 1 && n < 5) return form2;
    return form3;
}

/**
 * Initialize all countdown timers on the page
 */
function initCountdownTimers() {
    const countdownElements = document.querySelectorAll('.countdown-timer');
    countdownElements.forEach(function(element) {
        // Получаем время парсинга из атрибута data-parse-time
        const parseTimeStr = element.getAttribute('data-parse-time');
        if (parseTimeStr) {
            // Сохраняем дату парсинга как объект Date для дальнейших расчетов
            element.parseTime = new Date(parseTimeStr);
            // Устанавливаем начальное значение
            updateSingleCountdown(element);
        }
    });
}

/**
 * Update all countdown timers on the page
 */
function updateCountdownTimers() {
    const countdownElements = document.querySelectorAll('.countdown-timer');
    countdownElements.forEach(updateSingleCountdown);
}

/**
 * Update a single countdown timer
 * @param {HTMLElement} element - The countdown element to update
 */
function updateSingleCountdown(element) {
    if (!element.parseTime) return;
    
    const now = new Date();
    const timeDiff = element.parseTime - now;
    
    // Если время парсинга уже наступило
    if (timeDiff <= 0) {
        element.innerHTML = '<span class="text-success">Готово к парсингу</span>';
        element.classList.add('parsing-now');
        return;
    }
    
    // Расчет оставшегося времени
    const days = Math.floor(timeDiff / (1000 * 60 * 60 * 24));
    const hours = Math.floor((timeDiff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
    const minutes = Math.floor((timeDiff % (1000 * 60 * 60)) / (1000 * 60));
    const seconds = Math.floor((timeDiff % (1000 * 60)) / 1000);
    
    // Формирование строки обратного отсчета
    let countdownText = '';
    
    if (days > 0) {
        countdownText += `${days} ${pluralize(days, 'день', 'дня', 'дней')} `;
    }
    
    if (hours > 0 || days > 0) {
        countdownText += `${hours} ${pluralize(hours, 'час', 'часа', 'часов')} `;
    }
    
    if (minutes > 0 || hours > 0 || days > 0) {
        countdownText += `${minutes} ${pluralize(minutes, 'минута', 'минуты', 'минут')} `;
    }
    
    countdownText += `${seconds} ${pluralize(seconds, 'секунда', 'секунды', 'секунд')}`;
    
    // Добавляем визуальное оформление
    if (timeDiff < 60000) { // Менее минуты
        element.innerHTML = `<span class="text-danger fw-bold">${countdownText}</span>`;
    } else if (timeDiff < 3600000) { // Менее часа
        element.innerHTML = `<span class="text-warning">${countdownText}</span>`;
    } else {
        element.innerHTML = countdownText;
    }
}
