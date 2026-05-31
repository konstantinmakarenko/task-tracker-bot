FROM python:3.11-slim

WORKDIR /app

# Устанавливаем переменную для немедленного вывода логов
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Запускаем с флагом -u для unbuffered output
CMD ["python", "-u", "bot.py"]