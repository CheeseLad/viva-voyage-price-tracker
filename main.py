import json
import os
import csv
import time
import urllib.error
import urllib.request
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

load_dotenv()

URL = os.getenv("URL")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL"))
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")
DEBUG_LOGGING = os.getenv("DEBUG_LOGGING", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
CSV_FILE = os.getenv("CSV_FILE", "price_history.csv")

CSV_HEADERS = [
    "timestamp",
    "inside_pp_eur",
    "inside_pp_gbp",
    "inside_total_eur",
    "inside_total_gbp",
    "outside_pp_eur",
    "outside_pp_gbp",
    "outside_total_eur",
    "outside_total_gbp",
    "balcony_pp_eur",
    "balcony_pp_gbp",
    "balcony_total_eur",
    "balcony_total_gbp",
]


def debug_log(message):
    if DEBUG_LOGGING:
        print(f"[DEBUG] {message}")


def parse_money(text):
    return float(text.replace("£", "").replace(",", ""))


def append_best_prices_to_csv(room_results):
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "inside_pp_eur": "",
        "inside_pp_gbp": "",
        "inside_total_eur": "",
        "inside_total_gbp": "",
        "outside_pp_eur": "",
        "outside_pp_gbp": "",
        "outside_total_eur": "",
        "outside_total_gbp": "",
        "balcony_pp_eur": "",
        "balcony_pp_gbp": "",
        "balcony_total_eur": "",
        "balcony_total_gbp": "",
    }

    room_key_map = {
        "Inside": (
            "inside_pp_eur",
            "inside_pp_gbp",
            "inside_total_eur",
            "inside_total_gbp",
        ),
        "Outside": (
            "outside_pp_eur",
            "outside_pp_gbp",
            "outside_total_eur",
            "outside_total_gbp",
        ),
        "Balcony": (
            "balcony_pp_eur",
            "balcony_pp_gbp",
            "balcony_total_eur",
            "balcony_total_gbp",
        ),
    }

    for room_name, fields in room_key_map.items():
        room_data = room_results.get(room_name)
        if not room_data:
            continue

        best_pp, best_total = room_data["best"]
        best_pp_eur, best_total_eur = room_data["best_eur"]

        pp_eur_field, pp_gbp_field, total_eur_field, total_gbp_field = fields
        row[pp_gbp_field] = parse_money(best_pp)
        row[total_gbp_field] = parse_money(best_total)
        row[pp_eur_field] = round(best_pp_eur, 2) if best_pp_eur is not None else ""
        row[total_eur_field] = (
            round(best_total_eur, 2) if best_total_eur is not None else ""
        )

    file_exists = os.path.exists(CSV_FILE)
    should_write_header = (not file_exists) or os.path.getsize(CSV_FILE) == 0

    with open(CSV_FILE, "a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADERS)
        if should_write_header:
            writer.writeheader()
        writer.writerow(row)


def gbp_to_eur(amount):
    if amount is None:
        debug_log("Skipping GBP->EUR conversion because amount is None")
        return None

    if not EXCHANGE_RATE_API_KEY:
        debug_log("Skipping GBP->EUR conversion because EXCHANGE_RATE_API_KEY is missing")
        return None

    url = (
        f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/pair/GBP/EUR/{amount}"
    )

    try:
        debug_log(f"Requesting GBP->EUR conversion for amount {amount}")
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if payload.get("result") != "success":
            debug_log(f"Exchange API returned non-success result: {payload.get('result')}")
            return None

        debug_log("GBP->EUR conversion request succeeded")
        return float(payload.get("conversion_result"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TypeError):
        debug_log("GBP->EUR conversion request failed")
        return None


def format_best_price(gbp_text, eur_amount=None):
    gbp_amount = parse_money(gbp_text)
    if eur_amount is None:
        eur_amount = gbp_to_eur(gbp_amount)

    if eur_amount is None:
        return f"GBP {gbp_text} | EUR unavailable"

    return f"GBP {gbp_text} | EUR €{eur_amount:,.2f}"


def get_fare_error_message(driver):
    alerts = driver.find_elements(By.CSS_SELECTOR, "div.alert.alert-stop")

    for alert in alerts:
        message = " ".join(alert.text.split())
        if message:
            return message

    return None


def wait_for_prices_or_error(driver, timeout=20):
    wait = WebDriverWait(driver, timeout)
    debug_log(f"Waiting up to {timeout}s for prices or fare error")

    def condition(current_driver):
        error_message = get_fare_error_message(current_driver)
        if error_message:
            debug_log(f"Fare error detected while waiting: {error_message}")
            return ("error", error_message)

        rows = current_driver.find_elements(By.CSS_SELECTOR, ".category-price-table-row")
        if rows:
            debug_log(f"Detected {len(rows)} price rows while waiting")
            return ("prices", True)

        return False

    return wait.until(condition)

def get_all_room_types(wait, driver):
    room_tabs = [
        ("Inside", "Inside_RoomTab"),
        ("Outside", "Outside_RoomTab"),
        ("Balcony", "Balcony_RoomTab"),
#        ("Suite", "Suite_RoomTab"),
    ]

    results = {}

    for name, tab_id in room_tabs:
        try:
            print(f"\n--- Checking {name} ---")
            debug_log(f"Opening room tab {name} ({tab_id})")

            tab = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, f"[data-ody-id='{tab_id}']")
                )
            )

            driver.execute_script("arguments[0].click();", tab)

            time.sleep(2)

            wait.until(lambda d: has_populated_prices(d))
            debug_log(f"Price table populated for room type {name}")

            prices = extract_prices(wait)
            debug_log(f"Extracted {len(prices)} price options for room type {name}")

            if prices:
                print(f"{name} prices:")
                for i, (ppp, total) in enumerate(prices):
                    print(f"  Option {i+1}: {ppp} | {total}")

                cheapest = min(prices, key=lambda x: parse_money(x[1]))
                best_ppp_eur = gbp_to_eur(parse_money(cheapest[0]))
                best_total_eur = gbp_to_eur(parse_money(cheapest[1]))
                results[name] = {
                    "prices": prices,
                    "best": cheapest,
                    "best_eur": (best_ppp_eur, best_total_eur),
                }

                print(
                    f"Best {name}: {format_best_price(cheapest[0], best_ppp_eur)} | {format_best_price(cheapest[1], best_total_eur)}"
                )
            else:
                print(f"{name}: No prices found")
                debug_log(f"No valid prices found for room type {name}")

        except Exception as e:
            print(f"{name}: ERROR {e}")
            debug_log(f"Room type {name} failed with error: {e}")

    return results

def extract_prices(wait):
    rows = wait.until(
        EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, ".category-price-table-row")
        )
    )

    results = []

    for row in rows:
        try:
            cells = row.find_elements(By.CSS_SELECTOR, ".category-price-cell-table")

            if len(cells) < 2:
                debug_log("Skipping row because fewer than 2 price cells were found")
                continue

            price_per_person = cells[0].find_element(
                By.CSS_SELECTOR, "strong[data-ody-id='TotalPrice']"
            ).text

            cabin_total = cells[1].find_element(
                By.CSS_SELECTOR, "strong[data-ody-id='TotalPrice']"
            ).text

            if not price_per_person.strip() or not cabin_total.strip():
                debug_log("Skipping row because one or more price fields are empty")
                continue

            results.append((price_per_person, cabin_total))

        except Exception as exc:
            debug_log(f"Failed to extract one price row: {exc}")
            continue

    return results


def has_populated_prices(driver):
    rows = driver.find_elements(By.CSS_SELECTOR, ".category-price-table-row")

    for row in rows:
        try:
            cells = row.find_elements(By.CSS_SELECTOR, ".category-price-cell-table")
            if len(cells) < 2:
                continue

            price_per_person = cells[0].find_element(
                By.CSS_SELECTOR, "strong[data-ody-id='TotalPrice']"
            ).text.strip()
            cabin_total = cells[1].find_element(
                By.CSS_SELECTOR, "strong[data-ody-id='TotalPrice']"
            ).text.strip()

            if price_per_person and cabin_total:
                return True
        except Exception:
            continue

    return False

def get_prices(driver):
    debug_log("Starting price retrieval cycle")
    driver.get(URL)
    wait = WebDriverWait(driver, 20)

    try:
        cookie_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "CybotCookiebotDialogBodyButtonAccept"))
        )
        cookie_button.click()
        debug_log("Cookie consent accepted")
    except Exception:
        debug_log("Cookie consent dialog not found or not clickable")
        pass

    guests_dropdown = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "select[data-ody-id='GuestSelectDropdown']")
        )
    )
    Select(guests_dropdown).select_by_value(os.getenv("GUEST_AMOUNT"))
    debug_log(f"Selected guest amount: {os.getenv('GUEST_AMOUNT')}")

    time.sleep(1)

    ages = os.getenv("GUEST_AGES").split(",")
    debug_log(f"Applying guest ages: {ages}")

    for i, age in enumerate(ages):
        age_input = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, f"input[data-ody-id='GuestAge_{i}']")
            )
        )
        age_input.clear()
        age_input.send_keys(age)

    continue_button = wait.until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "button[data-ody-id='ContinueButton']")
        )
    )
    continue_button.click()
    debug_log("Submitted guest selection and waiting for fares")

    wait_result = wait_for_prices_or_error(driver)

    if wait_result[0] == "error":
        print(f"[ERROR] {wait_result[1]}")
        debug_log("Fare retrieval ended with a stop alert")
        return None

    all_results = get_all_room_types(wait, driver)

    print("\n====== SUMMARY ======")
    for room, data in all_results.items():
        print(f"{room}:")
        for i, (ppp, total) in enumerate(data["prices"]):
            print(f"  Option {i+1}: {ppp} | {total}")
        best_ppp, best_total = data["best"]
        best_ppp_eur, best_total_eur = data["best_eur"]
        print(
            f"  Best: {format_best_price(best_ppp, best_ppp_eur)} | {format_best_price(best_total, best_total_eur)}"
        )

    return all_results


def main():
    debug_log("Application startup")
    options = webdriver.ChromeOptions()
    #options.add_argument("--headless=new")

    driver = webdriver.Chrome(options=options)

    try:
        while True:
            try:
                room_results = get_prices(driver)
                if not room_results:
                    print("Waiting for next attempt...\n")
                    debug_log("No room results in this cycle; will retry")
                    time.sleep(CHECK_INTERVAL)
                    continue

                print("[SUCCESS] Captured prices for all room types")
                for room, data in room_results.items():
                    best_ppp, best_total = data["best"]
                    best_ppp_eur, best_total_eur = data["best_eur"]
                    print(
                        f"[BEST] {room}: {format_best_price(best_ppp, best_ppp_eur)} | {format_best_price(best_total, best_total_eur)}"
                    )

                append_best_prices_to_csv(room_results)
                debug_log(f"Appended best prices snapshot to CSV: {CSV_FILE}")
            except Exception as e:
                print(f"[ERROR] {e}")
                debug_log(f"Top-level cycle failed with error: {e}")

            print(f"Waiting {CHECK_INTERVAL} seconds...\n")
            time.sleep(CHECK_INTERVAL)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()