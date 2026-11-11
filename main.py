#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
WhatsApp бот на Python для определения намерения клиента
(реклама или товар) с интеграцией Google Sheets
"""

import time
import re
import os
import logging
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from pywhatkit import handover
from webwhatsapi import WhatsAPIDriver
from webwhatsapi.objects.message import Message
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from openai import OpenAI

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("whatsapp_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Ключевые слова для классификации намерения клиента
AD_KEYWORDS = [
    'реклама', 'рекламу', 'рекламные', 'реклам', 'продвижение', 'продвинуть',
    'маркетинг', 'рекламировать', 'раскрутка', 'раскрутить', 'промоушен'
]

PRODUCT_KEYWORDS = [
    'товар', 'купить', 'приобрести', 'заказать', 'покупка', 'продукт',
    'цена', 'стоимость', 'сколько стоит', 'предложение', 'продажа'
]

GPT_SYSTEM_PROMPT = (
    "Ты — вежливый ассистент салона красоты. Твоя задача — собрать у пользователя следующие данные: "
    "Имя, телефон, на какую услугу хочет записаться, есть ли вопросы по акциям, стоимости или часам работы. "
    "Задавай вопросы по одному, жди ответа на каждый. После получения всех данных, поблагодари пользователя и сообщи, что заявка принята и с ним свяжутся. "
    "Веди диалог на русском языке."
)

class WhatsAppBot:
    def __init__(self, credentials_file, spreadsheet_id):
        self.credentials_file = credentials_file
        self.spreadsheet_id = spreadsheet_id
        self.driver = None
        self.google_client = None
        self.sheet = None
        self.user_states = {}  # {phone_number: {'history': [], 'collected': {}}}

    def setup_whatsapp(self):
        """Инициализация драйвера WhatsApp Web"""
        logger.info("Инициализация WhatsApp...")
        self.driver = WhatsAPIDriver(profile=None)
        self.driver.wait_for_login()
        logger.info("WhatsApp успешно инициализирован!")

    def setup_google_sheets(self):
        """Инициализация подключения к Google Sheets"""
        logger.info("Подключение к Google Sheets...")
        scope = ['https://spreadsheets.google.com/feeds',
                 'https://www.googleapis.com/auth/drive']
        credentials = ServiceAccountCredentials.from_json_keyfile_name(
            self.credentials_file, scope)
        self.google_client = gspread.authorize(credentials)
        self.sheet = self.google_client.open_by_key(self.spreadsheet_id).sheet1
        logger.info("Подключение к Google Sheets установлено!")

    def determine_intent(self, message_text):
        """Определение намерения клиента по тексту сообщения"""
        message_text = message_text.lower()

        # Проверка на ключевые слова рекламы
        for keyword in AD_KEYWORDS:
            if keyword in message_text:
                return "Реклама"

        # Проверка на ключевые слова товара
        for keyword in PRODUCT_KEYWORDS:
            if keyword in message_text:
                return "Товар"

        # Если намерение не определено
        return "Неопределено"

    def add_to_spreadsheet(self, phone_number, intent_type):
        """Добавление данных в Google Sheets"""
        try:
            current_date = datetime.now().strftime("%d.%m.%Y")
            current_time = datetime.now().strftime("%H:%M:%S")

            # Добавление новой строки с данными
            self.sheet.append_row([current_date, current_time, phone_number, intent_type])
            logger.info(f"Данные добавлены в таблицу: {phone_number}, {intent_type}")
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении данных в таблицу: {e}")
            return False

    def send_email(self, user_data):
        """Отправка собранных данных на email"""
        sender_email = os.getenv("EMAIL_USER")
        receiver_email = os.getenv("EMAIL_TO")
        password = os.getenv("EMAIL_PASS")
        subject = "Новая заявка с WhatsApp-бота"

        if not sender_email or not receiver_email or not password:
            logger.error("EMAIL_USER, EMAIL_PASS или EMAIL_TO не заданы в переменных окружения!")
            return

        body = f"""
        Новая заявка:
        Имя: {user_data.get('name')}
        Телефон: {user_data.get('phone')}
        Услуга: {user_data.get('service')}
        Вопрос: {user_data.get('question')}
        """
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        try:
            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(sender_email, password)
                server.sendmail(sender_email, receiver_email, msg.as_string())
            logger.info("Email успешно отправлен!")
        except Exception as e:
            logger.error(f"Ошибка при отправке email: {e}")

    def extract_user_data(self, history):
        """Пытаемся извлечь имя, телефон, услугу, вопрос из истории диалога"""
        collected = {'name': None, 'phone': None, 'service': None, 'question': None}
        text = ' '.join([msg['content'] for msg in history if msg['role'] == 'user'])
        # Примитивно, можно улучшить
        import re
        name_match = re.search(r'меня зовут ([^.,!\n]+)', text, re.IGNORECASE)
        phone_match = re.search(r'(\+?\d{10,15})', text)
        service_match = re.search(r'(записаться на [^.,!\n]+)', text, re.IGNORECASE)
        question_match = re.search(r'(акци|стоимост|час[ао]в|время работы|нет)', text, re.IGNORECASE)
        if name_match:
            collected['name'] = name_match.group(1).strip()
        if phone_match:
            collected['phone'] = phone_match.group(1).strip()
        if service_match:
            collected['service'] = service_match.group(1).strip()
        if question_match:
            collected['question'] = question_match.group(0).strip()
        return collected

    def process_message(self, message):
        try:
            message_text = message.content.strip()
            sender = message.sender.id
            phone_number = sender.split('@')[0]

            # История диалога для пользователя
            state = self.user_states.get(phone_number, {'history': [], 'collected': {}})
            history = state['history']
            collected = state.get('collected', {})

            # Добавляем сообщение пользователя в историю
            history.append({'role': 'user', 'content': message_text})

            # Формируем сообщения для GPT
            gpt_messages = [
                {'role': 'system', 'content': GPT_SYSTEM_PROMPT},
            ] + history[-10:]  # последние 10 сообщений

            # Запрос к OpenAI (gpt-3.5-turbo отключена 23.10.2026).
            # Модель можно сменить через OPENAI_MODEL без правки кода.
            # temperature убран: модели GPT-5.x могут его не поддерживать.
            client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
            response = client.chat.completions.create(
                model=os.getenv('OPENAI_MODEL', 'gpt-5.6-terra'),
                messages=gpt_messages,
            )
            gpt_reply = response.choices[0].message.content or ''

            # Добавляем ответ GPT в историю
            history.append({'role': 'assistant', 'content': gpt_reply})

            # Отправляем ответ пользователю
            self.driver.chat_send_message(sender, gpt_reply)

            # Пытаемся извлечь все нужные данные
            new_collected = self.extract_user_data(history)
            # Обновляем только если что-то найдено
            for k, v in new_collected.items():
                if v:
                    collected[k] = v

            # Если все данные собраны — отправляем на почту и сбрасываем историю
            if all(collected.values()):
                self.send_email(collected)
                self.driver.chat_send_message(sender, "Спасибо! Ваша заявка принята. Мы свяжемся с вами в ближайшее время.")
                self.user_states.pop(phone_number, None)
            else:
                # Сохраняем историю и собранные данные
                self.user_states[phone_number] = {'history': history, 'collected': collected}
        except Exception as e:
            logger.error(f"Ошибка при обработке сообщения: {e}")

    def run(self):
        """Основной цикл работы бота"""
        logger.info("Бот запущен и готов к работе!")

        try:
            while True:
                # Получение всех непрочитанных сообщений
                unread_messages = self.driver.get_unread()

                # Обработка каждого сообщения
                for chat in unread_messages:
                    for message in chat.messages:
                        # Игнорируем сообщения от групп
                        if not message.sender.is_group:
                            self.process_message(message)

                # Пауза для снижения нагрузки
                time.sleep(3)

        except KeyboardInterrupt:
            logger.info("Бот остановлен вручную.")
        except Exception as e:
            logger.error(f"Произошла ошибка: {e}")
        finally:
            if self.driver:
                self.driver.close()
            logger.info("Бот завершил работу.")


def main():
    # Настройки бота
    CREDENTIALS_FILE = "credentials.json"  # Путь к файлу с учетными данными Google API
    SPREADSHEET_ID = "YOUR_SPREADSHEET_ID"  # ID Google таблицы

    bot = WhatsAppBot(CREDENTIALS_FILE, SPREADSHEET_ID)

    try:
        # Инициализация
        bot.setup_whatsapp()
        bot.setup_google_sheets()

        # Запуск бота
        bot.run()
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")


if __name__ == "__main__":
    main()