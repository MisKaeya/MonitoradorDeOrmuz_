FROM python:3.11-slim
WORKDIR /app
RUN pip install flask flask-cors requests --no-cache-dir
COPY shared/ /app/shared/
COPY brokers/ /app/brokers/
ARG AREA_FILE=area_01.py
ENV AREA_FILE=${AREA_FILE}
CMD ["sh", "-c", "python -u /app/brokers/${AREA_FILE}"]
