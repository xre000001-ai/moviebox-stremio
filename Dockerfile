FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY addon.py logo.png ./
ENV PORT=7000
EXPOSE 7000
CMD ["python3", "addon.py"]
