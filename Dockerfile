# Stage 1: Build stage
FROM python:3.11-slim as builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Configure pip to use a domestic mirror permanently
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# Install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt

# Stage 2: Final stage
FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Copy wheels from builder stage
COPY --from=builder /app/wheels /wheels

# Install dependencies from wheels
RUN pip install --no-cache /wheels/*

# Copy project code
COPY . .

# Collect static files
RUN python manage.py collectstatic --noinput

# Expose port for daphne
EXPOSE 3000

# Set the default command to run daphne
CMD ["daphne", "-b", "0.0.0.0", "-p", "3000", "config.asgi:application"]
