FROM python:3.11-slim

# Install Playwright + Chromium in one go (no manual apt list needed)
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements first to leverage Docker layer cache
WORKDIR /app
COPY requirements.txt .

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium + all OS deps via Playwright helper
RUN python -m playwright install --with-deps chromium

# Copy code
COPY popmart_stock_watcher.py .

# Run the watcher
CMD ["python", "popmart_stock_watcher.py"]
