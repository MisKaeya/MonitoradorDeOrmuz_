FROM python:3.11-slim
WORKDIR /app
RUN pip install flask flask-cors flask-socketio requests eventlet --no-cache-dir
COPY shared/ /app/shared/
COPY web/ /app/web/
CMD ["python", "-u", "web/app.py"]
