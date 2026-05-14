@echo off
echo ==========================================================
echo         Preparing and Pushing to OCR_document_reader
echo ==========================================================
echo.

if not exist README.md (
    echo [ERROR] Must run from the project root directory!
    pause
    exit /b
)

echo 1. Removing nested .git folders to avoid conflicts...
if exist "OCR-document-parser\.git" (
    echo    Removing OCR-document-parser\.git...
    rmdir /s /q "OCR-document-parser\.git"
)
if exist "NLP-entity-extractor\.git" (
    echo    Removing NLP-entity-extractor\.git...
    rmdir /s /q "NLP-entity-extractor\.git"
)

echo.
echo 2. Initializing root Git repository...
git init

echo.
echo 3. Setting up remote repository...
git remote remove origin >nul 2>&1
git remote add origin https://github.com/suer-tech/OCR_document_reader.git
echo    Remote set to https://github.com/suer-tech/OCR_document_reader.git

echo.
echo 4. Staging files (.gitignore excludes venv, idea, logs, data)...
git add .

echo.
echo 5. Creating commit...
git commit -m "Initial unified repository commit (OCR document reader)"

echo.
echo 6. Renaming branch to main...
git branch -M main

echo.
echo 7. Pushing to GitHub...
git push -u origin main

echo.
echo ==========================================================
echo                         DONE!
echo ==========================================================
pause
