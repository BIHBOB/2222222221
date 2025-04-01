/**
 * JavaScript functionality for the archive page
 */
document.addEventListener('DOMContentLoaded', function() {
    // Handle delete file modal
    const deleteFileModal = document.getElementById('deleteFileModal');
    if (deleteFileModal) {
        const deleteFileForm = document.getElementById('deleteFileForm');
        const fileNameToDelete = document.getElementById('fileNameToDelete');
        
        // Update modal content when showing
        deleteFileModal.addEventListener('show.bs.modal', function(event) {
            // Button that triggered the modal
            const button = event.relatedTarget;
            
            // Extract info from data attributes
            const fileId = button.getAttribute('data-file-id');
            const fileName = button.getAttribute('data-file-name');
            
            // Update form action URL
            if (deleteFileForm) {
                deleteFileForm.action = `/archive/file/${fileId}/delete`;
            }
            
            // Update modal content
            if (fileNameToDelete) {
                fileNameToDelete.textContent = fileName;
            }
        });
    }
    
    // Handle batch selection of files for deletion
    const selectAllCheckbox = document.getElementById('selectAllFiles');
    const fileCheckboxes = document.querySelectorAll('.file-checkbox');
    const batchActionBtn = document.getElementById('batchActionBtn');
    
    if (selectAllCheckbox && fileCheckboxes.length > 0) {
        // Select/deselect all files
        selectAllCheckbox.addEventListener('change', function() {
            const isChecked = this.checked;
            fileCheckboxes.forEach(checkbox => {
                checkbox.checked = isChecked;
            });
            
            // Update batch action button
            updateBatchActionButton();
        });
        
        // Update select all checkbox state when individual checkboxes change
        fileCheckboxes.forEach(checkbox => {
            checkbox.addEventListener('change', function() {
                const allChecked = Array.from(fileCheckboxes).every(cb => cb.checked);
                const anyChecked = Array.from(fileCheckboxes).some(cb => cb.checked);
                
                selectAllCheckbox.checked = allChecked;
                selectAllCheckbox.indeterminate = anyChecked && !allChecked;
                
                // Update batch action button
                updateBatchActionButton();
            });
        });
        
        // Function to update batch action button state
        function updateBatchActionButton() {
            if (!batchActionBtn) return;
            
            const selectedCount = Array.from(fileCheckboxes).filter(cb => cb.checked).length;
            
            if (selectedCount > 0) {
                batchActionBtn.disabled = false;
                batchActionBtn.innerHTML = `<i class="fas fa-trash me-2"></i> Удалить выбранные (${selectedCount})`;
            } else {
                batchActionBtn.disabled = true;
                batchActionBtn.innerHTML = `<i class="fas fa-trash me-2"></i> Выберите файлы для удаления`;
            }
        }
        
        // Initialize batch action button state
        if (batchActionBtn) {
            updateBatchActionButton();
        }
    }
    
    // Handle refresh file status
    const refreshButtons = document.querySelectorAll('.refresh-file-status');
    
    if (refreshButtons.length > 0) {
        refreshButtons.forEach(button => {
            button.addEventListener('click', function() {
                const fileId = this.getAttribute('data-file-id');
                
                // Show loading state
                const originalHTML = this.innerHTML;
                this.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
                this.disabled = true;
                
                // Make API request to check file status
                fetch(`/api/file-status/${fileId}`)
                    .then(response => response.json())
                    .then(data => {
                        // Update status display
                        const statusBadge = document.getElementById(`file-status-${fileId}`);
                        if (statusBadge) {
                            // Remove existing status classes
                            statusBadge.classList.remove('bg-warning', 'bg-success', 'bg-danger');
                            
                            // Add appropriate class
                            if (data.status === 'processing') {
                                statusBadge.classList.add('bg-warning');
                                statusBadge.textContent = 'Обработка';
                            } else if (data.status === 'processed') {
                                statusBadge.classList.add('bg-success');
                                statusBadge.textContent = 'Обработан';
                            } else {
                                statusBadge.classList.add('bg-danger');
                                statusBadge.textContent = 'Ошибка';
                            }
                        }
                        
                        // Reset button
                        this.innerHTML = originalHTML;
                        this.disabled = false;
                    })
                    .catch(error => {
                        console.error('Error:', error);
                        // Reset button
                        this.innerHTML = originalHTML;
                        this.disabled = false;
                        
                        // Show error notification
                        alert('Не удалось обновить статус файла.');
                    });
            });
        });
    }
});
