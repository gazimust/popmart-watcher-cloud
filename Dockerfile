FROM python:3.11-slim

# Install Python deps
RUN pip install --no-cache-dir playwright==1.47.2 requests==2.32.3

# Install Chromium + all required OS deps
RUN python -m playwright install --with-deps chromium

# Copy code
WORKDIR /app
COPY popmart_stock_watcher.py .

# Run the watcher
CMD ["python", "popmart_stock_watcher.py"]
