"""MVP-бот отеля «Морской бриз» для ВКонтакте.

Бот работает через VK Bots Long Poll API (версия 5.131).
Из внешних зависимостей используются только requests и python-dotenv.
"""

import json
import os
import random
import sys
import time

import requests
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

GROUP_TOKEN = os.getenv("GROUP_TOKEN", "").strip()
GROUP_ID = os.getenv("GROUP_ID", "").strip()

API_VERSION = "5.131"
API_URL = "https://api.vk.com/method/"
LONG_POLL_WAIT = 25  # время ожидания событий в секундах (максимум для Bots Long Poll)

# ---------------------------------------------------------------------------
# Тексты сообщений
# ---------------------------------------------------------------------------

MAIN_MENU_TEXT = "Здравствуйте! Это бот отеля «Морской бриз». Выберите раздел:"

SERVICES_TEXT = "Выберите услугу, чтобы узнать подробности:"

PRICE_TEXT = (
    "Прайс-лист отеля «Морской бриз»:\n\n"
    "1. Номер «Стандарт» — 4 500 ₽/сутки\n"
    "2. Номер «Комфорт» — 6 500 ₽/сутки\n"
    "3. Завтрак «Шведский стол» — 650 ₽/чел.\n"
    "4. Трансфер — 1 200 ₽\n"
    "5. СПА-зона — 1 800 ₽/90 минут"
)

MANAGER_TEXT = (
    "Для бронирования отправьте, пожалуйста:\n"
    "1. Имя\n"
    "2. Телефон\n"
    "3. Услугу\n"
    "4. Даты заезда/выезда или удобную дату\n"
    "Отправляя данные, вы соглашаетесь на обработку персональных данных."
)

THANKS_TEXT = (
    "Спасибо! Ваша заявка принята. "
    "Менеджер свяжется с вами в ближайшее время."
)

# ---------------------------------------------------------------------------
# Данные услуг
# ---------------------------------------------------------------------------

SERVICES = {
    "standard": {
        "name": "Номер «Стандарт»",
        "price": "4 500 ₽/сутки",
        "description": (
            "Уютный номер 18 м². Двуспальная или две раздельные кровати, душ, "
            "ТВ, Wi-Fi, ежедневная уборка. Заезд с 14:00, выезд до 12:00."
        ),
    },
    "comfort": {
        "name": "Номер «Комфорт»",
        "price": "6 500 ₽/сутки",
        "description": (
            "Просторный номер 24 м² с видом на парк. Большая кровать, рабочее "
            "место, халат и тапочки. По возможности поздний выезд до 14:00."
        ),
    },
    "breakfast": {
        "name": "Завтрак «Шведский стол»",
        "price": "650 ₽/чел.",
        "description": (
            "Завтрак с 07:30 до 10:30. Каши, выпечка, яйца, фрукты, кофе и чай. "
            "Можно добавить к номеру при бронировании."
        ),
    },
    "transfer": {
        "name": "Трансфер",
        "price": "1 200 ₽",
        "description": (
            "Встреча или проводы в аэропорту/на вокзале. Легковой автомобиль "
            "до 3 пассажиров. Возможна встреча с табличкой."
        ),
    },
    "spa": {
        "name": "СПА-зона",
        "price": "1 800 ₽/90 минут",
        "description": (
            "Сауна, бассейн и зона отдыха. Полотенца и чай включены. "
            "Необходима предварительная запись."
        ),
    },
}

# Порядок услуг в меню «Услуги»
SERVICES_ORDER = ["standard", "comfort", "breakfast", "transfer", "spa"]

# Состояния пользователей хранятся в памяти процесса:
# user_id -> {"screen": ..., "service_id": ..., "awaiting_request": bool}
user_states = {}


class VkApiError(Exception):
    """Ошибка при обращении к VK API или при сетевом запросе."""


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------


def make_button(label, payload, color="secondary"):
    """Собирает одну кнопку клавиатуры ВКонтакте с payload-командой."""
    return {
        "action": {
            "type": "text",
            "label": label,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
        "color": color,
    }


def make_keyboard(rows):
    """Собирает JSON-клавиатуру из списка рядов кнопок."""
    return json.dumps(
        {"one_time": False, "buttons": rows},
        ensure_ascii=False,
    )


def main_menu_keyboard():
    """Клавиатура главного меню."""
    return make_keyboard(
        [
            [make_button("Услуги", {"cmd": "services"}, "primary")],
            [make_button("Прайс-лист", {"cmd": "price"}, "primary")],
            [make_button("Связаться с менеджером", {"cmd": "manager"}, "primary")],
        ]
    )


def services_keyboard():
    """Клавиатура со списком услуг."""
    buttons = [
        make_button(SERVICES[service_id]["name"], {"cmd": "service", "id": service_id})
        for service_id in SERVICES_ORDER
    ]
    # Раскладываем услуги по две кнопки в ряд
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([make_button("Назад", {"cmd": "back"}, "negative")])
    return make_keyboard(rows)


def service_keyboard(service_id):
    """Клавиатура карточки услуги."""
    return make_keyboard(
        [
            [make_button("Забронировать", {"cmd": "book", "id": service_id}, "positive")],
            [make_button("Назад", {"cmd": "back"}, "negative")],
        ]
    )


# ---------------------------------------------------------------------------
# Работа с VK API
# ---------------------------------------------------------------------------


def api(method, params=None):
    """Вызывает метод VK API и возвращает поле response.

    Сетевые ошибки и ошибки VK API превращаются в VkApiError,
    чтобы вызывающий код мог обработать их без падения бота.
    """
    request_data = {"access_token": GROUP_TOKEN, "v": API_VERSION}
    if params:
        request_data.update(params)

    try:
        response = requests.post(API_URL + method, data=request_data, timeout=20)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise VkApiError(f"Сетевая ошибка при вызове {method}: {exc}") from exc
    except ValueError as exc:
        raise VkApiError(f"Некорректный ответ VK API при вызове {method}") from exc

    if "error" in data:
        error = data["error"]
        raise VkApiError(
            f"Ошибка VK API {method}: "
            f"{error.get('error_code')} — {error.get('error_msg')}"
        )

    return data.get("response")


def send_message(peer_id, text, keyboard=None):
    """Отправляет сообщение пользователю.

    Ошибки отправки только логируются: одно неудачное сообщение
    не должно останавливать работу бота.
    """
    params = {
        "peer_id": peer_id,
        "message": text,
        "random_id": random.randint(1, 2 ** 31 - 1),
    }
    if keyboard is not None:
        params["keyboard"] = keyboard

    try:
        api("messages.send", params)
    except VkApiError as exc:
        print(f"Не удалось отправить сообщение {peer_id}: {exc}")


def get_long_poll_server():
    """Запрашивает адрес Long Poll сервера, ключ и текущий ts."""
    data = api("groups.getLongPollServer", {"group_id": GROUP_ID})
    return data["server"], data["key"], data["ts"]


# ---------------------------------------------------------------------------
# Логика бота
# ---------------------------------------------------------------------------


def service_card_text(service_id):
    """Формирует текст карточки услуги."""
    service = SERVICES[service_id]
    return (
        f"{service['name']}\n\n"
        f"Описание: {service['description']}\n\n"
        f"Цена: {service['price']}"
    )


def booking_text(service_id=None):
    """Формирует текст перехода к заявке менеджеру."""
    if service_id in SERVICES:
        return f"Вы выбрали: {SERVICES[service_id]['name']}\n\n{MANAGER_TEXT}"
    return MANAGER_TEXT


def parse_payload(raw_payload):
    """Разбирает payload кнопки в словарь (или возвращает пустой словарь)."""
    if not raw_payload:
        return {}
    try:
        payload = json.loads(raw_payload)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_action(text, payload):
    """Определяет действие по payload кнопки или по тексту сообщения.

    Возвращает пару (команда, id_услуги). Команда "unknown" означает,
    что сообщение не распознано.
    """
    command = payload.get("cmd")
    if command:
        return command, payload.get("id")

    normalized = text.strip().lower()

    if normalized in ("меню", "начать", "start", "/start", "menu"):
        return "menu", None
    if normalized == "услуги":
        return "services", None
    if normalized == "прайс-лист":
        return "price", None
    if normalized == "связаться с менеджером":
        return "manager", None
    if normalized == "забронировать":
        return "book", None
    if normalized == "назад":
        return "back", None

    # Кнопки услуг отправляют своё название как текст сообщения
    for service_id, service in SERVICES.items():
        if normalized == service["name"].lower():
            return "service", service_id

    return "unknown", None


def show_main_menu(peer_id, state):
    """Показывает главное меню."""
    state["screen"] = "main"
    state["service_id"] = None
    state["awaiting_request"] = False
    send_message(peer_id, MAIN_MENU_TEXT, main_menu_keyboard())


def handle_message(message):
    """Обрабатывает входящее сообщение из Long Poll."""
    user_id = message.get("from_id")
    peer_id = message.get("peer_id", user_id)
    text = (message.get("text") or "").strip()
    payload = parse_payload(message.get("payload"))

    state = user_states.setdefault(
        user_id,
        {"screen": "main", "service_id": None, "awaiting_request": False},
    )

    # Режим ожидания заявки: любой текст считается заявкой
    if state["awaiting_request"]:
        if not text:
            return
        state["awaiting_request"] = False
        state["service_id"] = None
        send_message(peer_id, THANKS_TEXT, main_menu_keyboard())
        state["screen"] = "main"
        return

    command, service_id = resolve_action(text, payload)

    if command == "menu":
        show_main_menu(peer_id, state)

    elif command == "services":
        state["screen"] = "services"
        state["service_id"] = None
        send_message(peer_id, SERVICES_TEXT, services_keyboard())

    elif command == "price":
        state["screen"] = "main"
        send_message(peer_id, PRICE_TEXT, main_menu_keyboard())

    elif command == "manager":
        state["awaiting_request"] = True
        state["service_id"] = None
        # Клавиатуру не отправляем: пользователь должен ввести текст заявки
        send_message(peer_id, MANAGER_TEXT)

    elif command == "service" and service_id in SERVICES:
        state["screen"] = "service"
        state["service_id"] = service_id
        send_message(peer_id, service_card_text(service_id), service_keyboard(service_id))

    elif command == "book":
        # Если услуга не пришла в payload, берём её из состояния
        if service_id not in SERVICES:
            service_id = state.get("service_id")
        state["awaiting_request"] = True
        state["service_id"] = service_id if service_id in SERVICES else None
        send_message(peer_id, booking_text(state["service_id"]))

    elif command == "back":
        # Из карточки услуги возвращаемся к списку услуг, иначе — в главное меню
        if state["screen"] == "service":
            state["screen"] = "services"
            state["service_id"] = None
            send_message(peer_id, SERVICES_TEXT, services_keyboard())
        else:
            show_main_menu(peer_id, state)

    else:
        # Неизвестное сообщение — показываем главное меню
        show_main_menu(peer_id, state)


# ---------------------------------------------------------------------------
# Long Poll
# ---------------------------------------------------------------------------


def run_long_poll():
    """Основной цикл получения событий через Bots Long Poll API."""
    server, key, ts = get_long_poll_server()
    print("Бот запущен. Ожидаю сообщения...")

    while True:
        params = {
            "act": "a_check",
            "key": key,
            "ts": ts,
            "wait": LONG_POLL_WAIT,
            "mode": 2,
            "version": 3,
        }

        try:
            response = requests.get(server, params=params, timeout=LONG_POLL_WAIT + 10)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            print(f"Сетевая ошибка Long Poll: {exc}")
            time.sleep(5)
            continue
        except ValueError:
            print("Некорректный ответ Long Poll сервера")
            time.sleep(5)
            continue

        # Сервер сообщает о проблеме с ключом или ts
        if "failed" in data:
            failed = data.get("failed")
            if failed == 1:
                # История событий обновилась — продолжаем с нового ts
                ts = data.get("ts", ts)
            elif failed in (2, 3):
                # Ключ устарел или потеряна история — получаем новые данные
                time.sleep(1)
                try:
                    server, key, ts = get_long_poll_server()
                except VkApiError as exc:
                    print(f"Не удалось обновить Long Poll сервер: {exc}")
                    time.sleep(5)
            else:
                time.sleep(5)
            continue

        ts = data.get("ts", ts)

        for update in data.get("updates", []):
            if update.get("type") != "message_new":
                continue
            try:
                handle_message(update["object"]["message"])
            except Exception as exc:  # одно сообщение не должно ронять бота
                print(f"Ошибка обработки сообщения: {exc}")


def main():
    # Корректный вывод кириллицы в консоли Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not GROUP_TOKEN or not GROUP_ID:
        print(
            "Не заданы GROUP_TOKEN и/или GROUP_ID.\n"
            "Создайте файл .env на основе .env.example и заполните значения."
        )
        sys.exit(1)

    try:
        run_long_poll()
    except KeyboardInterrupt:
        print("\nБот остановлен.")
    except VkApiError as exc:
        print(f"Критическая ошибка VK API: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
