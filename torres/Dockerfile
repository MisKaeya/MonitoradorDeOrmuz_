FROM python:3.11-slim
WORKDIR /app
RUN pip install flask flask-cors requests --no-cache-dir
COPY shared/ /app/shared/
COPY torres/torre.py /app/torre.py
CMD ["python", "-u", "torre.py"]
