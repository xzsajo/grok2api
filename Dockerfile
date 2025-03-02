FROM python:3.10-slim

WORKDIR /app

RUN pip install --no-cache-dir flask requests curl_cffi werkzeug loguru 

COPY . .

ENV PORT=5200
EXPOSE 5200

CMD ["python", "app.py"]
