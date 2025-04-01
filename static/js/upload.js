/**
 * JavaScript functionality for the upload page
 */
document.addEventListener('DOMContentLoaded', function() {
    const uploadForm = document.getElementById('uploadForm');
    const fileInput = document.getElementById('fileUpload');
    const uploadBtn = document.getElementById('uploadBtn');
    
    if (uploadForm && fileInput && uploadBtn) {
        // Add file validation
        fileInput.addEventListener('change', function() {
            const file = this.files[0];
            if (file) {
                const fileName = file.name;
                const fileExtension = fileName.split('.').pop().toLowerCase();
                
                // Check file extension
                const allowedExtensions = ['html', 'pdf', 'txt'];
                if (!allowedExtensions.includes(fileExtension)) {
                    alert('Недопустимый тип файла. Разрешены только HTML, PDF и TXT файлы.');
                    this.value = ''; // Clear the file input
                    return;
                }
                
                // Check file size (max 10MB)
                const maxSize = 10 * 1024 * 1024; // 10MB in bytes
                if (file.size > maxSize) {
                    alert('Размер файла слишком большой. Максимальный размер: 10MB.');
                    this.value = ''; // Clear the file input
                    return;
                }
                
                // Update button text
                uploadBtn.innerHTML = `<i class="fas fa-upload me-2"></i> Загрузить ${fileName}`;
            } else {
                // Reset button text if no file selected
                uploadBtn.innerHTML = `<i class="fas fa-upload me-2"></i> Загрузить и обработать`;
            }
        });
        
        // Show loading state on form submission
        uploadForm.addEventListener('submit', function(e) {
            // Check if a file is selected
            if (fileInput.files.length === 0) {
                e.preventDefault();
                alert('Пожалуйста, выберите файл для загрузки.');
                return;
            }
            
            // Disable the button and show loading state
            uploadBtn.disabled = true;
            uploadBtn.innerHTML = `
                <span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                Загрузка...
            `;
            
            // Add a progress indicator to the page
            const progressContainer = document.createElement('div');
            progressContainer.className = 'mt-3';
            progressContainer.innerHTML = `
                <div class="progress">
                    <div class="progress-bar progress-bar-striped progress-bar-animated" 
                        role="progressbar" style="width: 100%" aria-valuenow="100" 
                        aria-valuemin="0" aria-valuemax="100"></div>
                </div>
                <p class="text-center mt-2">
                    Пожалуйста, подождите. Файл загружается и обрабатывается...
                </p>
            `;
            
            uploadForm.appendChild(progressContainer);
        });
    }
    
    // Show help tooltip for file types
    const fileHelpTooltip = document.querySelector('[data-bs-toggle="tooltip"]');
    if (fileHelpTooltip) {
        new bootstrap.Tooltip(fileHelpTooltip);
    }
    
    // Initialize the accordion if it exists
    const uploadInstructions = document.getElementById('uploadInstructions');
    if (uploadInstructions) {
        // Make sure first instruction is shown by default
        const firstInstruction = uploadInstructions.querySelector('.accordion-collapse');
        if (firstInstruction) {
            const bsCollapse = new bootstrap.Collapse(firstInstruction, {
                toggle: false
            });
            
            // Uncomment to auto-open first instruction:
            // bsCollapse.show();
        }
    }
});
