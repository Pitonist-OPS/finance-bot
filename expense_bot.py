import os
import asyncio
import logging
import re
import aiosqlite
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
from datetime import datetime, date
import calendar
import pandas as pd
from dateutil.relativedelta import relativedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# Получаем токен из переменной окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ Переменная BOT_TOKEN не установлена!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

user_selections = {}


def get_main_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Текущий месяц")
    builder.button(text="📈 Вся статистика")
    builder.button(text="📅 Выбрать период")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


async def init_db():
    async with aiosqlite.connect("expenses.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


def generate_chart(summary: pd.Series) -> BytesIO:
    plt.figure(figsize=(8, 6))
    colors = plt.cm.tab20.colors
    wedges, texts, autotexts = plt.pie(
        summary.values,
        labels=summary.index,
        autopct='%1.1f%%',
        startangle=90,
        colors=colors[:len(summary)]
    )
    plt.title("Расходы по категориям", fontsize=14, pad=20)
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


async def generate_report_for_period(chat_id: int, start_date: date, end_date: date):
    async with aiosqlite.connect("expenses.db") as db:
        cursor = await db.execute("""
            SELECT category, amount, created_at
            FROM expenses
            WHERE user_id = ?
              AND date(created_at) BETWEEN ? AND ?
            ORDER BY created_at
        """, (chat_id, start_date.isoformat(), end_date.isoformat()))
        rows = await cursor.fetchall()

    if not rows:
        await bot.send_message(chat_id, "За указанный период расходов нет.")
        return

    df = pd.DataFrame(rows, columns=["category", "amount", "date"])
    summary = df.groupby("category")["amount"].sum().sort_values(ascending=False)
    total = summary.sum()

    period_str = f"{start_date} – {end_date}" if start_date != end_date else str(start_date)
    text = f"📊 Расходы за {period_str}:\n\n"
    for cat, amt in summary.items():
        display_amt = f"{amt:.2f}".rstrip('0').rstrip('.')
        text += f"{display_amt}р — {cat}\n"
    total_display = f"{total:.2f}".rstrip('0').rstrip('.')
    text += f"\nИтого: {total_display}р"

    await bot.send_message(chat_id, text)
    img_bytes = generate_chart(summary)
    await bot.send_photo(chat_id, photo=img_bytes, caption="📈 Диаграмма расходов")


async def send_calendar(chat_id: int, target_date: date, mode: str = "start"):
    year, month = target_date.year, target_date.month
    cal = calendar.monthcalendar(year, month)
    month_name = calendar.month_name[month]

    kb = []
    kb.append([
        InlineKeyboardButton(text="◀️", callback_data=f"cal_prev_{year}_{month}_{mode}"),
        InlineKeyboardButton(text=f"{month_name} {year}", callback_data="ignore"),
        InlineKeyboardButton(text="▶️", callback_data=f"cal_next_{year}_{month}_{mode}")
    ])

    kb.append([
        InlineKeyboardButton(text=day, callback_data="ignore")
        for day in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    ])

    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                row.append(
                    InlineKeyboardButton(
                        text=str(day),
                        callback_data=f"cal_day_{year}_{month}_{day}_{mode}"
                    )
                )
        kb.append(row)

    if mode == "start":
        kb.append([
            InlineKeyboardButton(
                text="✅ Только этот день",
                callback_data=f"cal_single_{year}_{month}_{target_date.day}"
            )
        ])

    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    action = "начало" if mode == "start" else "конец"
    await bot.send_message(chat_id, f"Выберите {action} периода:", reply_markup=markup)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Отправляй расходы в формате:\n<code>45р еда</code>\nИли список:\n<code>45р еда\n14р напитки</code>",
        reply_markup=get_main_kb(),
        parse_mode="HTML"
    )


@dp.message(F.text == "📊 Текущий месяц")
async def show_month_stats(message: types.Message):
    user_id = message.from_user.id
    now = datetime.now()
    current_month = now.month
    current_year = now.year

    async with aiosqlite.connect("expenses.db") as db:
        cursor = await db.execute("""
            SELECT category, SUM(amount) 
            FROM expenses 
            WHERE user_id = ? 
              AND strftime('%Y', created_at) = ?
              AND strftime('%m', created_at) = ?
            GROUP BY category 
            ORDER BY SUM(amount) DESC
        """, (user_id, str(current_year), f"{current_month:02d}"))
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("У вас пока нет расходов в этом месяце.")
        return

    text = f"📊 Расходы за {now.strftime('%B %Y')}:\n\n"
    total = 0.0
    for category, amount in rows:
        display_amt = f"{amount:.2f}".rstrip('0').rstrip('.')
        text += f"{display_amt}р — {category}\n"
        total += amount
    total_display = f"{total:.2f}".rstrip('0').rstrip('.')
    text += f"\nИтого: {total_display}р"
    await message.answer(text)


@dp.message(F.text == "📈 Вся статистика")
async def show_all_stats(message: types.Message):
    user_id = message.from_user.id
    async with aiosqlite.connect("expenses.db") as db:
        cursor = await db.execute("""
            SELECT category, SUM(amount) 
            FROM expenses 
            WHERE user_id = ? 
            GROUP BY category 
            ORDER BY SUM(amount) DESC
        """, (user_id,))
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("У вас пока нет расходов.")
        return

    text = "📈 Все ваши расходы:\n\n"
    total = 0.0
    for category, amount in rows:
        display_amt = f"{amount:.2f}".rstrip('0').rstrip('.')
        text += f"{display_amt}р — {category}\n"
        total += amount
    total_display = f"{total:.2f}".rstrip('0').rstrip('.')
    text += f"\nИтого: {total_display}р"
    await message.answer(text)


@dp.message(F.text == "📅 Выбрать период")
async def choose_period_start(message: types.Message):
    now = datetime.now().date()
    await send_calendar(message.chat.id, now, mode="start")


@dp.message()
async def handle_expense(message: types.Message):
    text = message.text.strip()
    if not text:
        return

    entries = re.split(r'[\n,;]+', text)
    entries = [entry.strip() for entry in entries if entry.strip()]

    if not entries:
        await message.answer("Пустое сообщение. Пример:\n<code>45р еда\n14р напитки</code>", parse_mode="HTML")
        return

    user_id = message.from_user.id
    success_count = 0
    errors = []

    async with aiosqlite.connect("expenses.db") as db:
        for entry in entries:
            entry_lower = entry.lower()
            match = re.match(r'^([\d,\.]+)\s*[рруб]*\s+(.+)$', entry_lower)

            if not match:
                errors.append(f"«{entry}» — неверный формат")
                continue

            try:
                amount_str = match.group(1).replace(',', '.')
                amount = float(amount_str)
                if amount <= 0:
                    raise ValueError("Сумма должна быть положительной")
                amount = round(amount, 2)
                category = match.group(2).strip()

                await db.execute(
                    "INSERT INTO expenses (user_id, category, amount) VALUES (?, ?, ?)",
                    (user_id, category, amount)
                )
                success_count += 1
            except Exception:
                errors.append(f"«{entry}» — ошибка суммы")

        await db.commit()

    response = []
    if success_count > 0:
        response.append(f"✅ Добавлено записей: {success_count}")
    if errors:
        response.append("❌ Ошибки:\n" + "\n".join(errors))

    if not response:
        response.append("Не удалось распознать ни одной записи.")

    await message.answer("\n".join(response))


@dp.callback_query(lambda c: c.data and c.data.startswith("cal_"))
async def handle_calendar(callback: types.CallbackQuery):
    await callback.answer()
    data = callback.data.split("_")
    chat_id = callback.message.chat.id

    if data[1] == "prev":
        year, month, mode = int(data[2]), int(data[3]), data[4]
        new_date = date(year, month, 1) - relativedelta(months=1)
        await send_calendar(chat_id, new_date, mode)
        await callback.message.delete()

    elif data[1] == "next":
        year, month, mode = int(data[2]), int(data[3]), data[4]
        new_date = date(year, month, 1) + relativedelta(months=1)
        await send_calendar(chat_id, new_date, mode)
        await callback.message.delete()

    elif data[1] == "day":
        year, month, day, mode = int(data[2]), int(data[3]), int(data[4]), data[5]
        selected = date(year, month, day)

        if mode == "start":
            user_selections[chat_id] = {"start": selected}
            await callback.message.delete()
            await send_calendar(chat_id, selected, mode="end")

        elif mode == "end":
            if chat_id not in user_selections:
                await callback.message.edit_text("Ошибка: начальная дата не выбрана.")
                return
            start = user_selections[chat_id]["start"]
            if selected < start:
                await callback.answer("Конец не может быть раньше начала!", show_alert=True)
                return
            await generate_report_for_period(chat_id, start, selected)
            user_selections.pop(chat_id, None)

    elif data[1] == "single":
        year, month, day = int(data[2]), int(data[3]), int(data[4])
        selected = date(year, month, day)
        await generate_report_for_period(chat_id, selected, selected)

    elif data[1] == "ignore":
        pass


async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())