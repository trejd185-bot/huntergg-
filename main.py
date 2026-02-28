import os
import json
import time
import requests
import re
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- НАСТРОЙКИ ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHANNEL = os.environ.get("TG_CHANNEL")
HISTORY_FILE = "shop_history.json"

# Сколько минут работать перед перезагрузкой (GitHub лимит ~15 мин)
WORK_DURATION_MINUTES = 10 

# Параметры поиска
MIN_DISCOUNT = 40  # Искать скидку от 40%
MIN_PRICE = 2000   # Игнорировать дешевле 2000р

# Ссылки (Задачи)
TASKS = [
    {
        'shop': '🟣 WILDBERRIES',
        'url': 'https://www.wildberries.ru/catalog/0/search.aspx?search=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA&sort=popular',
        'type': 'wb'
    },
    {
        'shop': '🔵 OZON',
        'url': 'https://www.ozon.ru/category/noutbuki-15692/?sorting=discount',
        'type': 'ozon'
    },
    {
        'shop': '🟡 YANDEX MARKET',
        'url': 'https://market.yandex.ru/catalog--noutbuki/54544/list?local-offers-first=0&how=dpop',
        'type': 'yandex'
    }
]

# Слова-исключения (аксессуары)
BAD_WORDS = ["чехол", "стекло", "пленка", "держатель", "кабель", "зарядка", "подставка", "аксессуар", "кронштейн", "сумка"]

# --- БАЗА ДАННЫХ ---
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f: return json.load(f)
        except: return []
    return []

def save_history(data):
    try:
        with open(HISTORY_FILE, 'w') as f: json.dump(data[-500:], f)
    except: pass

def send_telegram(text):
    print(f"📤 TG: {text}")
    if not TG_TOKEN or not TG_CHANNEL: return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", 
                      json={'chat_id': TG_CHANNEL, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': False})
    except Exception as e: print(f"TG Err: {e}")

def parse_price(text):
    try:
        # Оставляем только цифры
        clean = re.sub(r'[^\d]', '', text)
        return int(clean)
    except: return 0

# --- ДРАЙВЕР (Стелс-режим) ---
def get_driver():
    options = Options()
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # Случайный User-Agent
    agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
    ]
    options.add_argument(f"user-agent={random.choice(agents)}")
    
    # Отключение автоматизации
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    # JS Маскировка
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

# --- ЛОГИКА WB ---
def scan_wb(driver, url, history):
    print("🟣 WB...")
    try:
        driver.get(url)
        # Скролл
        driver.execute_script("window.scrollTo(0, 1000);")
        time.sleep(3)
        
        cards = driver.find_elements(By.CLASS_NAME, "product-card")
        if not cards: return history
        
        for card in cards:
            try:
                link_el = card.find_element(By.CLASS_NAME, "product-card__link")
                link = link_el.get_attribute("href")
                if link in history: continue
                
                name = card.find_element(By.CLASS_NAME, "product-card__name").text.strip()
                if any(w in name.lower() for w in BAD_WORDS): continue
                
                price_el = card.find_element(By.CLASS_NAME, "price__lower-price")
                price = parse_price(price_el.text)
                if price < MIN_PRICE: continue
                
                # Старая цена
                try:
                    old_el = card.find_element(By.TAG_NAME, "del")
                    old_price = parse_price(old_el.text)
                except: old_price = price
                
                if old_price > price:
                    discount = int(((old_price - price) / old_price) * 100)
                    if discount >= MIN_DISCOUNT:
                        send_alert("WILDBERRIES", name, price, old_price, discount, link)
                        history.append(link)
            except: continue
    except Exception as e:
        print(f"WB Err: {e}")
    return history

# --- ЛОГИКА OZON ---
def scan_ozon(driver, url, history):
    print("🔵 OZON...")
    try:
        driver.get(url)
        time.sleep(5)
        
        if "Access denied" in driver.title or "Captcha" in driver.title:
            print("⚠️ OZON Block.")
            return history
            
        # Ищем через ссылки, так как классы меняются
        links = driver.find_elements(By.TAG_NAME, "a")
        
        count = 0
        for a in links:
            try:
                text = a.text
                if "₽" not in text: continue
                
                # Парсим текст ссылки
                # Пример: "Ноутбук... 50 000 ₽ 100 000 ₽"
                nums = re.findall(r'(\d[\d\s]*)\s?₽', text)
                if len(nums) < 2: continue # Нужна и старая и новая цена
                
                prices = sorted([parse_price(n) for n in nums])
                price = prices[0]     # Меньшая
                old_price = prices[-1] # Большая
                
                if price < MIN_PRICE: continue
                
                # Ищем название (обычно длинный текст без цифр)
                lines = text.split('\n')
                name = lines[0]
                if len(name) < 5: name = "Товар Ozon"
                
                if any(w in name.lower() for w in BAD_WORDS): continue
                
                href = a.get_attribute("href")
                if not href or href in history: continue
                if "ozon.ru" not in href: continue

                discount = int(((old_price - price) / old_price) * 100)
                
                if discount >= MIN_DISCOUNT:
                    send_alert("OZON", name, price, old_price, discount, href)
                    history.append(href)
                    count += 1
                    if count >= 3: break
            except: continue
    except Exception as e:
        print(f"Ozon Err: {e}")
    return history

# --- ЛОГИКА YANDEX ---
def scan_yandex(driver, url, history):
    print("🟡 YANDEX...")
    try:
        driver.get(url)
        time.sleep(5)
        
        if "Captcha" in driver.title:
            print("⚠️ Yandex Block.")
            return history
            
        # Ищем карточки по атрибуту data-auto (стабильный)
        cards = driver.find_elements(By.CSS_SELECTOR, '[data-auto="product-card"]')
        
        for card in cards:
            try:
                text = card.text
                if "₽" not in text: continue
                
                try:
                    link_el = card.find_element(By.TAG_NAME, "a")
                    href = link_el.get_attribute("href")
                except: continue
                
                if href in history: continue
                
                nums = re.findall(r'(\d[\d\s]*)\s?₽', text)
                if len(nums) < 2: continue
                
                prices = sorted([parse_price(n) for n in nums])
                price = prices[0]
                old_price = prices[-1]
                
                if price < MIN_PRICE: continue
                
                lines = text.split('\n')
                name = lines[0]
                if any(w in name.lower() for w in BAD_WORDS): continue
                
                discount = int(((old_price - price) / old_price) * 100)
                
                if discount >= MIN_DISCOUNT:
                    send_alert("YANDEX", name, price, old_price, discount, href)
                    history.append(href)
            except: continue
    except Exception as e:
        print(f"Yandex Err: {e}")
    return history

def send_alert(shop, name, price, old, discount, link):
    if len(name) > 100: name = name[:100] + "..."
    icon = "🟣"
    if shop == "OZON": icon = "🔵"
    if shop == "YANDEX": icon = "🟡"
    
    msg = (
        f"{icon} <b>{shop} | -{discount}%</b>\n\n"
        f"📦 <b>{name}</b>\n"
        f"❌ {old} ₽\n"
        f"✅ <b>{price} ₽</b>\n"
        f"🔗 <a href='{link}'>КУПИТЬ</a>"
    )
    send_telegram(msg)
    time.sleep(1)

# --- ГЛАВНЫЙ ЦИКЛ ---
def run_eternal():
    print("🚀 Бот запущен (WB + OZON + YM)")
    history = load_history()
    driver = get_driver()
    start_time = time.time()
    
    try:
        while True:
            # Проверка времени (10 минут)
            elapsed = (time.time() - start_time) / 60
            if elapsed >= WORK_DURATION_MINUTES:
                print("⏰ Перезагрузка...")
                break
            
            # Проход по задачам
            for task in TASKS:
                if task['type'] == 'wb':
                    history = scan_wb(driver, task['url'], history)
                elif task['type'] == 'ozon':
                    history = scan_ozon(driver, task['url'], history)
                elif task['type'] == 'yandex':
                    history = scan_yandex(driver, task['url'], history)
                
                # Сохраняем после каждого магазина
                save_history(history)
            
            # Пауза между кругами
            print("💤 Сплю 2 минуты...")
            time.sleep(120)
            
    except Exception as e:
        print(f"Global Err: {e}")
        save_history(history)
    finally:
        driver.quit()

if __name__ == "__main__":
    run_eternal()
