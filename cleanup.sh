#!/bin/bash
# Cleanup script for Zensynora

echo "Cleaning up temporary files..."

# Remove temporary test files
rm -f test_*.py
rm -f *.tmp
rm -f *.zip

# Remove Putty tools if downloaded
rm -rf putty/

# Remove virtual environment (optional, uncomment if needed)
# rm -rf venv/

# Remove .pytest_cache
rm -rf .pytest_cache/
rm -rf __pycache__/

echo "Cleanup complete!"