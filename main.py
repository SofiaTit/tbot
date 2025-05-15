import os
import asyncio
import requests
from datetime import datetime, timedelta
from typing import Optional, List
from dotenv import load_dotenv

import dateparser
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///reminders.db')

# Инициализация базы данных
engine = create_engine(DATABASE_URL)
Base = declarative_base()

class Reminder(Base):
    __tablename__ = 'reminders'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    name = Column(String)
    time = Column(DateTime)
    repeat_interval = Column(String, nullable=True)
    is_weather = Column(Boolean, default=False)
    city = Column(String, nullable=True)
    file_id = Column(String, nullable=True)
    file_type = Column(String, nullable=True)
    next_run = Column(DateTime)

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class ReminderStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_time = State()
    waiting_for_weather_city = State()
    waiting_for_file = State()
    editing_reminder = State()
    editing_reminder_time = State()


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
async def get_weather(city: str) -> str:
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        response = requests.get(url)
        data = response.json()

        if data['cod'] != 200:
            return f"Ошибка: {data['message']}"

        weather = (
            f"Погода в {city}:\n"
            f"Температура: {data['main']['temp']}°C\n"
            f"Ощущается как: {data['main']['feels_like']}°C\n"
            f"{data['weather'][0]['description'].capitalize()}\n"
            f"Ветер: {data['wind']['speed']} м/с"
        )
        return weather
    except Exception as e:
        return f"Ошибка получения погоды: {str(e)}"


def create_reminders_keyboard(reminders: List[Reminder]) -> InlineKeyboardMarkup:
    keyboard = []
    for rem in reminders:
        keyboard.append([
            InlineKeyboardButton(
                text=f"✖️ {rem.name}",
                callback_data=f"delete_{rem.id}"
            ),
            InlineKeyboardButton(
                text=f"✏️ {rem.name}",
                callback_data=f"edit_{rem.id}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ================== ОБРАБОТЧИКИ КОМАНД ==================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Создать напоминание"),
             types.KeyboardButton(text="Мои напоминания")],
            [types.KeyboardButton(text="Напоминание о погоде"),
             types.KeyboardButton(text="Напоминания на сегодня")]
        ],
        resize_keyboard=True
    )
    await message.answer(
        f"Привет, {message.from_user.first_name}! Я умный бот-напоминалка.\n"
        "Выберите действие:",
        reply_markup=keyboard
    )


@dp.message(F.text == "Мои напоминания")
async def show_reminders(message: types.Message):
    session = Session()
    reminders = session.query(Reminder).filter(
        (Reminder.user_id == message.from_user.id) &
        ((Reminder.next_run > datetime.now()) | (Reminder.repeat_interval.isnot(None)))
    ).order_by(Reminder.next_run).all()

    if not reminders:
        await message.answer("У вас пока нет активных напоминаний")
        session.close()
        return

    text = "Активные напоминания:\n\n"
    for rem in reminders:
        text += (
            f"• {rem.name}\n"
            f"Следующий запуск: {rem.next_run.strftime('%d.%m.%Y %H:%M')}\n"
            f"{'Повтор: ' + rem.repeat_interval if rem.repeat_interval else ''}\n"
            f"{'Погода в ' + rem.city if rem.is_weather else ''}\n\n"
        )

    await message.answer(
        text,
        reply_markup=create_reminders_keyboard(reminders)
    )
    session.close()


@dp.message(F.text == "Напоминания на сегодня")
async def show_today_reminders(message: types.Message):
    session = Session()
    now = datetime.now()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    reminders = session.query(Reminder).filter(
        Reminder.user_id == message.from_user.id,
        Reminder.next_run >= today_start,
        Reminder.next_run < today_end,
        Reminder.next_run > now
    ).all()

    if not reminders:
        await message.answer("На сегодня напоминаний нет")
    else:
        text = "Напоминания на сегодня:\n\n"
        for rem in reminders:
            text += f"• {rem.name} в {rem.next_run.strftime('%H:%M')}\n"
        await message.answer(text)

    session.close()


@dp.message(F.text == "Создать напоминание")
async def create_reminder(message: types.Message, state: FSMContext):
    await message.answer("Введите название напоминания:")
    await state.set_state(ReminderStates.waiting_for_name)


@dp.message(F.text == "Напоминание о погоде")
async def create_weather_reminder(message: types.Message, state: FSMContext):
    await message.answer("Введите город для отслеживания погоды:")
    await state.set_state(ReminderStates.waiting_for_weather_city)


@dp.message(ReminderStates.waiting_for_weather_city)
async def process_weather_city(message: types.Message, state: FSMContext):
    city = message.text
    weather = await get_weather(city)
    if "Ошибка" in weather:
        await message.answer(weather)
        return

    await state.update_data(city=city, is_weather=True)
    await message.answer("Введите время для напоминания (например: 'каждый день в 8:00'):")
    await state.set_state(ReminderStates.waiting_for_time)


@dp.message(ReminderStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(
        "Введите время напоминания:\nПримеры:\n"
        "- Завтра в 10:00\n"
        "- Каждый день в 9:30\n"
        "- 15 мая в 19:30\n"
        "- Через 10 минут"
    )
    await state.set_state(ReminderStates.waiting_for_time)


@dp.message(ReminderStates.waiting_for_time)
async def process_time(message: types.Message, state: FSMContext):
    time_str = message.text
    data = await state.get_data()

    # Определяем повторение
    repeat = None
    clean_time_str = time_str.lower()

    if "каждый день" or "Каждый день" in clean_time_str:
        repeat = "ежедневно"
        time_str = time_str.lower().replace("каждый день", "").strip()
    elif "каждый месяц" or "Каждый месяц" in clean_time_str:
        repeat = "ежемесячно"
        time_str = time_str.lower().replace("каждый месяц", "").strip()

    # Парсим время с улучшенными настройками
    parsed_time = dateparser.parse(
        time_str,
        languages=['ru'],
        settings={
            'PREFER_DATES_FROM': 'future',
            'RELATIVE_BASE': datetime.now(),
            'DATE_ORDER': 'DMY',
            'PREFER_LOCALE_DATE_ORDER': True
        }
    )

    if not parsed_time:
        return await message.answer("Не могу распознать время. Попробуйте еще раз.")

    # Корректировка времени для повторяющихся событий
    now = datetime.now()
    if parsed_time < now and repeat:
        if repeat == "daily":
            parsed_time += timedelta(days=1)
        elif repeat == "monthly":
            parsed_time = parsed_time.replace(year=now.year, month=now.month + 1)
            if parsed_time < now:
                parsed_time = parsed_time.replace(month=parsed_time.month + 1)

    await state.update_data(
        time=parsed_time,
        repeat_interval=repeat,
        next_run=parsed_time
    )

    if data.get('is_weather'):
        await save_and_schedule(message.from_user.id, await state.get_data())
        await message.answer("Напоминание о погоде создано!")
        await state.clear()
    else:
        await message.answer("Прикрепите файл если нужно или нажмите /skip")
        await state.set_state(ReminderStates.waiting_for_file)


@dp.message(ReminderStates.waiting_for_file, F.document | F.photo | F.audio)
async def process_file(message: types.Message, state: FSMContext):
    file_id = None
    file_type = None

    if message.document:
        file_id = message.document.file_id
        file_type = 'document'
    elif message.photo:
        file_id = message.photo[-1].file_id
        file_type = 'photo'
    elif message.audio:
        file_id = message.audio.file_id
        file_type = 'audio'

    await state.update_data(file_id=file_id, file_type=file_type)
    data = await state.get_data()
    await save_and_schedule(message.from_user.id, data, file_id, file_type)
    await message.answer("✅ Напоминание создано!")
    await state.clear()


@dp.message(Command("skip"))
async def skip_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await save_and_schedule(message.from_user.id, data)
    await state.clear()


@dp.callback_query(F.data.startswith("delete_"))
async def delete_reminder(callback: types.CallbackQuery):
    try:
        reminder_id = int(callback.data.split("_")[1])
        session = Session()
        reminder = session.get(Reminder, reminder_id)

        if reminder and reminder.user_id == callback.from_user.id:
            session.delete(reminder)
            session.commit()

            # Возвращаем основную клавиатуру
            keyboard = types.ReplyKeyboardMarkup(
                keyboard=[
                    [types.KeyboardButton(text="Создать напоминание"),
                     types.KeyboardButton(text="Мои напоминания")],
                    [types.KeyboardButton(text="Напоминание о погоде"),
                     types.KeyboardButton(text="Напоминания на сегодня")]
                ],
                resize_keyboard=True
            )

            await callback.message.answer("Напоминание удалено ✅",
                                          reply_markup=keyboard)
        else:
            await callback.answer("Ошибка удаления")

    except Exception as e:
        await callback.answer(f"Ошибка: {str(e)}")
    finally:
        session.close()

@dp.callback_query(F.data.startswith("edit_"))
async def edit_reminder(callback: types.CallbackQuery, state: FSMContext):
    reminder_id = int(callback.data.split("_")[1])
    session = Session()
    reminder = session.get(Reminder, reminder_id)

    if reminder and reminder.user_id == callback.from_user.id:
        await state.update_data(edit_id=reminder_id)
        await callback.message.answer(
            "Введите новое название напоминания (или /skip чтобы оставить текущее):",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.set_state(ReminderStates.editing_reminder)
        await callback.answer()
    else:
        await callback.answer("Ошибка редактирования")

    session.close()


@dp.message(ReminderStates.editing_reminder)
async def process_edit(message: types.Message, state: FSMContext):
    if message.text != "/skip":
        data = await state.get_data()
        session = Session()
        reminder = session.get(Reminder, data['edit_id'])

        if reminder:
            reminder.name = message.text
            session.commit()

        session.close()

    await message.answer("Введите новое время напоминания (или /skip чтобы оставить текущее):")
    await state.set_state(ReminderStates.editing_reminder_time)


@dp.message(ReminderStates.editing_reminder_time)
async def process_edit_time(message: types.Message, state: FSMContext):
    data = await state.get_data()
    session = Session()
    reminder = session.get(Reminder, data['edit_id'])

    try:
        if message.text != "/skip":
            # Парсим новое время
            parsed_time = dateparser.parse(
                message.text,
                languages=['ru'],
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'RELATIVE_BASE': datetime.now()
                }
            )

            if parsed_time:
                reminder.time = parsed_time  # Обновляем основное время
                reminder.next_run = parsed_time  # Обновляем next_run
                session.commit()

        # Возвращаем основную клавиатуру
        keyboard = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Создать напоминание"),
                 types.KeyboardButton(text="Мои напоминания")],
                [types.KeyboardButton(text="Напоминание о погоде"),
                 types.KeyboardButton(text="Напоминания на сегодня")]
            ],
            resize_keyboard=True
        )

        # Обновляем список напоминаний
        reminders = session.query(Reminder).filter(
            (Reminder.user_id == message.from_user.id) &
            ((Reminder.next_run > datetime.now()) | (Reminder.repeat_interval.isnot(None)))
        ).all()

        text = "Активные напоминания:\n\n"
        for rem in reminders:
            text += (
                f"• {rem.name}\n"
                f"Следующий запуск: {rem.next_run.strftime('%d.%m.%Y %H:%M')}\n\n"
            )

        await message.answer("Напоминание обновлено ✅\n\n" + text,
                             reply_markup=keyboard)
        await message.answer("Выберите действие:",
                             reply_markup=create_reminders_keyboard(reminders))

    except Exception as e:
        await message.answer(f"Ошибка обновления: {str(e)}")
    finally:
        session.close()
        await state.clear()


async def save_and_schedule(user_id: int, data: dict, file_id: Optional[str] = None, file_type: Optional[str] = None):
    session = Session()

    reminder = Reminder(
        user_id=user_id,
        name=data.get('name', 'Напоминание о погоде'),
        time=data['time'],
        repeat_interval=data.get('repeat_interval'),
        is_weather=data.get('is_weather', False),
        city=data.get('city'),
        file_id=file_id,
        file_type=file_type,
        next_run=data['next_run']
    )

    session.add(reminder)
    session.commit()

    message_text = (
        f"✅ Напоминание '{reminder.name}' создано!\n"
        f"Следующий запуск: {reminder.next_run.strftime('%d.%m.%Y %H:%M')}"
    )

    if reminder.repeat_interval:
        message_text += f"\nПовтор: {reminder.repeat_interval}"
    if reminder.is_weather:
        message_text += f"\nГород: {reminder.city}"

    await bot.send_message(user_id, message_text)
    session.close()

    asyncio.create_task(schedule_reminder(reminder))


async def schedule_reminder(reminder: Reminder):
    while True:
        now = datetime.now()
        delay = (reminder.next_run - now).total_seconds()

        if delay > 0:
            await asyncio.sleep(delay)
            await send_reminder(reminder)

            if reminder.repeat_interval:
                session = Session()
                if reminder.repeat_interval == 'daily':
                    reminder.next_run += timedelta(days=1)
                elif reminder.repeat_interval == 'monthly':
                    reminder.next_run = reminder.next_run.replace(month=reminder.next_run.month + 1)
                session.commit()
                session.close()
            else:
                break
        else:
            break


async def send_reminder(reminder: Reminder):
    try:
        if reminder.is_weather:
            # Исправлено: добавлен запрос погоды для напоминаний о погоде
            weather_text = await get_weather(reminder.city)
            text = f"⏰ Напоминание о погоде в {reminder.city}:\n{weather_text}"
            await bot.send_message(reminder.user_id, text)
        else:
            text = f"⏰ Напоминание: {reminder.name}"
            if reminder.file_id:
                if reminder.file_type == 'photo':
                    await bot.send_photo(reminder.user_id, reminder.file_id, caption=text)
                elif reminder.file_type == 'document':
                    await bot.send_document(reminder.user_id, reminder.file_id, caption=text)
                elif reminder.file_type == 'audio':
                    await bot.send_audio(reminder.user_id, reminder.file_id, caption=text)
            else:
                await bot.send_message(reminder.user_id, text)
    except Exception as e:
        print(f"Ошибка отправки: {e}")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)

    # Загрузка существующих напоминаний
    session = Session()
    reminders = session.query(Reminder).filter(Reminder.next_run > datetime.now()).all()
    for rem in reminders:
        asyncio.create_task(schedule_reminder(rem))
    session.close()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

Thread(target=run).start()

from aiohttp import web

async def web_server():
    app = web.Application()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    return app, runner, site

async def main():
    # Инициализация веб-сервера
    web_app, runner, site = await web_server()

    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    session = Session()
    reminders = session.query(Reminder).filter(Reminder.next_run > datetime.now()).all()
    for rem in reminders:
        asyncio.create_task(schedule_reminder(rem))
    session.close()

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")