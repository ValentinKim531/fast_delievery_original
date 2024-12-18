import json
import os
from datetime import datetime, timedelta
import pytz
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import logging
import math
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)  
logger = logging.getLogger(__name__)
app = FastAPI()

URL_SEARCH = os.getenv("URL_SEARCH")
URL_PRICE = os.getenv("URL_PRICE")

# Define the payload
payload = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/best_options")
async def main_process(request: Request):

    try:
        # Receive the front end data (city hash, sku's, user address)
        request_data = await request.json()
        encoded_city = request_data.get("city")  # Encoded city hash
        sku_data = request_data.get("skus", [])  # List of SKU items
        address = request_data.get("address", {})  # User address


        #Save the latitude and longitude of user
        user_lat = request_data.get("address", {}).get("lat")
        user_lon = request_data.get("address", {}).get("lng")

        # Validate the incoming data
        if not encoded_city or not sku_data or user_lat is None or user_lon is None:
            return JSONResponse(content={"error": "City, SKU data, and user coordinates are required"}, status_code=400)

        if not isinstance(user_lat, (int, float)) or not isinstance(user_lon, (int, float)):
            return JSONResponse(content={"error": "Invalid data type for user coordinates"}, status_code=400)

        for item in sku_data:
            if not isinstance(item.get("sku"), str) or not isinstance(item.get("count_desired"), int):
                return JSONResponse(content={"error": "Invalid SKU format or count type"}, status_code=400)

        # Build the payload
        payload = [{"sku": item["sku"], "count_desired": item["count_desired"]} for item in sku_data]

        # Perform the search for medicines in pharmacies
        pharmacies = await find_medicines_in_pharmacies(encoded_city, payload)

        if not pharmacies.get("result"):
            logger.error("No pharmacies found with the provided SKU data")
            return JSONResponse(content={"error": "No pharmacies found with the provided SKU data in URL_SEARCH"}, status_code=404)
        save_response_to_file(pharmacies, file_name='data1_found_all.json')

        #Save only pharmacies with all sku's in stock
        filtered_pharmacies = await filter_pharmacies(pharmacies)
        if isinstance(filtered_pharmacies, JSONResponse):
            return filtered_pharmacies  # Возвращаем JSONResponse сразу, если это ошибка
        save_response_to_file(filtered_pharmacies, file_name='data2_filtered_pharmacies.json')

        # Get several pharmacies with cheapest SKU's
        initial_cheapest_pharmacies = await get_top_cheapest_pharmacies(filtered_pharmacies)
        save_response_to_file(initial_cheapest_pharmacies, file_name='data4_top_cheapest_pharmacies.json')

        # Get 2 closest Pharmacies
        initial_closest_pharmacies = await get_top_closest_pharmacies(filtered_pharmacies, user_lat, user_lon)
        save_response_to_file(initial_closest_pharmacies, file_name='data4_top_closest_pharmacies.json')

        # Убедимся, что среди выбранных аптек есть круглосуточные
        updated_cheapest_pharmacies, updated_closest_pharmacies = await ensure_24h_pharmacies(
            filtered_pharmacies["filtered_pharmacies"],
            initial_cheapest_pharmacies,
            initial_closest_pharmacies,
            user_lat,
            user_lon,
        )
        save_response_to_file(updated_cheapest_pharmacies, file_name='data4.1_updated_top_cheapest_pharmacies.json')
        save_response_to_file(updated_closest_pharmacies, file_name='data4_2_updated_top_closest_pharmacies.json')

        #Compare Check delivery price for 2 closest pharmacies and 3 cheapest pharmacies
        delivery_options1 = await get_delivery_options(updated_closest_pharmacies, user_lat, user_lon)
        if isinstance(delivery_options1, JSONResponse):
            return delivery_options1  # Возвращаем JSONResponse сразу, если это ошибка
        save_response_to_file(delivery_options1, file_name='data5_delivery_options_closest.json')

        delivery_options2 = await get_delivery_options(updated_cheapest_pharmacies, user_lat, user_lon)
        if isinstance(delivery_options2, JSONResponse):
            return delivery_options2  # Возвращаем JSONResponse сразу, если это ошибка
        save_response_to_file(delivery_options2, file_name='data5_delivery_options_cheapest.json')

        all_delivery_options = delivery_options1 + delivery_options2
        save_response_to_file(all_delivery_options, file_name='data5_all_delivery_options.json')

        result = await best_option(all_delivery_options)
        save_response_to_file(result, file_name='data6_final_result.json')

        return result

    except json.JSONDecodeError:
        return JSONResponse(content={"error": "Invalid JSON format"}, status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return JSONResponse(content={"error": "An unexpected error occurred"}, status_code=500)



async def find_medicines_in_pharmacies(encoded_city, payload):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(URL_SEARCH, params={"city": encoded_city}, json=payload)
            response.raise_for_status()
            data = response.json()
            # Проверка на наличие ожидаемых ключей в ответе
            if not isinstance(data, dict) or "result" not in data:
                return JSONResponse(content={"error": "Invalid response format from search API"}, status_code=502)
            return data
        except httpx.RequestError as e:
            logger.error(f"Request error while accessing URL_SEARCH: {e}")
            return JSONResponse(content={"error": "Request error while accessing search API"}, status_code=503)
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error while accessing URL_SEARCH: {e}")
            return JSONResponse(content={"error": f"HTTP error {e.response.status_code}"},
                                status_code=e.response.status_code)

#
# # Константа для корректировки количества
# QUANTITY_ADJUSTMENT = 1  # Увеличиваем количество на 1 для каждого товара для поиска
#
# async def find_medicines_in_pharmacies(encoded_city, payload):
#
#
#     if isinstance(payload, list):
#         # Увеличиваем count_desired на QUANTITY_ADJUSTMENT для каждого элемента в списке
#         print(f"payload before adjustment: {payload}")
#         for sku in payload:
#             if isinstance(sku, dict) and "count_desired" in sku:
#                 sku["count_desired"] += QUANTITY_ADJUSTMENT
#         print(f"payload after adjustment: {payload}")
#     else:
#         # Логирование ошибки структуры payload
#         logger.error("Payload должен быть списком словарей с ключом 'count_desired'.")
#
#     async with httpx.AsyncClient() as client:
#         try:
#             response = await client.post(URL_SEARCH, params={"city": encoded_city}, json=payload)
#             response.raise_for_status()
#             data = response.json()
#             save_response_to_file(data, file_name='data_pharmacies.json')
#
#             # Проверка корректности данных от API
#             if not isinstance(data, dict) or "result" not in data:
#                 return JSONResponse(content={"error": "Invalid response format from search API"}, status_code=502)
#
#             # Применяем корректировку после получения данных
#             for pharmacy in data.get("result", []):
#                 # Проверка, что source_tags - это список
#                 source_tags = pharmacy.get("source", {}).get("source_tags", [])
#                 has_stock_tag = False
#                 stock_meta_value = None
#
#                 if isinstance(source_tags, list):
#                     for tag in source_tags:
#                         if isinstance(tag, dict) and tag.get("id") == 1080:
#                             has_stock_tag = True
#                             stock_meta_value = tag.get("meta")
#                             break
#
#                 # Корректируем каждый товар в зависимости от правил
#                 for product in pharmacy.get("products", []):
#                     if not isinstance(product, dict):
#                         continue  # Пропустить, если структура продукта некорректна
#
#                     original_count_desired = product.get("count_desired", 0)
#                     original_quantity = product.get("quantity", 0)
#
#                     if has_stock_tag and stock_meta_value == "0":
#                         # Если есть тег с id 1080 и meta == "0"
#                         if original_count_desired == original_quantity:
#                             # Оба значения равны, уменьшаем и count_desired, и quantity
#                             product["count_desired"] = max(0, original_count_desired - QUANTITY_ADJUSTMENT)
#                             product["quantity"] = max(0, original_quantity - QUANTITY_ADJUSTMENT)
#                         else:
#                             # Уменьшаем только count_desired
#                             product["count_desired"] = max(0, original_count_desired - QUANTITY_ADJUSTMENT)
#                     else:
#                         # Нет тега с id 1080 или он отсутствует, уменьшаем оба значения
#                         product["count_desired"] = max(0, original_count_desired - QUANTITY_ADJUSTMENT)
#                         product["quantity"] = max(0, original_quantity - QUANTITY_ADJUSTMENT)
#
#             return data
#
#         except httpx.RequestError as e:
#             logger.error(f"Request error while accessing URL_SEARCH: {e}")
#             return JSONResponse(content={"error": "Request error while accessing search API"}, status_code=503)
#         except httpx.HTTPStatusError as e:
#             logger.error(f"HTTP error while accessing URL_SEARCH: {e}")
#             return JSONResponse(content={"error": f"HTTP error {e.response.status_code}"}, status_code=e.response.status_code)
#




# мок для тестирования локальных результатов поиска
# async def find_medicines_in_pharmacies(encoded_city, payload):
#     async with httpx.AsyncClient() as client:
#         response = await client.get("http://localhost:8001/search_medicines")
#         response.raise_for_status()  # Проверка на ошибки
#         data = response.json()  # Получаем JSON
#         save_response_to_file(response.json(), file_name='data1_found_all.json')
#         return data  # Возвращаем JSON данные


# Save only pharmacies with all sku's in stock
async def filter_pharmacies(pharmacies):
    filtered_pharmacies = []

    for pharmacy in pharmacies.get("result", []):
        products = pharmacy.get("products", [])

        # Check if all products meet their desired quantities
        all_available = all(
            product["quantity"] >= product["quantity_desired"]
            for product in products if product["quantity_desired"] > 0
        )

        if all_available:
            filtered_pharmacies.append(pharmacy)

    # Возвращаем ошибку, если ни одна аптека не соответствует запросу
    return {"filtered_pharmacies": filtered_pharmacies} if filtered_pharmacies else JSONResponse(
        content={
            "error": "No pharmacies found matching the request (either due to requested medication quantities or invalid SKU(s))"},
        status_code=404
    )


#Find pharmacies with cheapest "total_sum" fro sku's
async def get_top_cheapest_pharmacies(pharmacies):
    # Sort pharmacies by 'total_sum' in ascending order
    sorted_pharmacies = sorted(pharmacies.get("filtered_pharmacies", []), key=lambda x: x["total_sum"])

    # Get the top 3 pharmacies with the lowest 'total_sum'
    cheapest_pharmacies = sorted_pharmacies[:3]

    return {"list_pharmacies": cheapest_pharmacies}

async def get_top_closest_pharmacies(pharmacies, user_lat, user_lon):
    # Create a list of pharmacies with their distance from the user
    pharmacies_with_distance = []
    
    for pharmacy in pharmacies.get("filtered_pharmacies", []):
        pharmacy_lat = pharmacy["source"]["lat"]
        pharmacy_lon = pharmacy["source"]["lon"]

        # Check if lat/lon exist before calculating the distance
        if pharmacy_lat is None or pharmacy_lon is None:
            continue  # Skip if lat/lon is missing

        # Calculate Euclidean distance
        distance = haversine_distance(user_lat, user_lon, pharmacy_lat, pharmacy_lon)
        
        # Add the pharmacy and its distance to the list
        pharmacies_with_distance.append({"pharmacy": pharmacy, "distance": distance})
    
    # Sort pharmacies by distance
    sorted_pharmacies = sorted(pharmacies_with_distance, key=lambda x: x["distance"])
    
    # Get the top 2 closest pharmacies
    closest_pharmacies = [item["pharmacy"] for item in sorted_pharmacies[:2]]
    
    return {"list_pharmacies": closest_pharmacies}


async def get_24h_pharmacies(filtered_pharmacies):
    """Возвращает только круглосуточные аптеки из списка."""

    return [
        pharmacy for pharmacy in filtered_pharmacies
        if "круглосуточно" in pharmacy.get("source", {}).get("opening_hours", "").lower()
    ]
async def ensure_24h_pharmacies(
    filtered_pharmacies, cheapest_pharmacies, closest_pharmacies, user_lat, user_lon
):
    """
    Проверяет наличие круглосуточных аптек в каждом из списков (дешевых и близких)
    и добавляет их, если отсутствуют.
    """
    # Получаем список всех круглосуточных аптек
    all_24h_pharmacies = await get_24h_pharmacies(filtered_pharmacies)


    if all_24h_pharmacies:
        # Проверяем, есть ли круглосуточная аптека среди близких
        has_24h_in_cheapest = any(
            "круглосуточно" in pharmacy.get("source", {}).get("opening_hours", "").lower()
            for pharmacy in cheapest_pharmacies["list_pharmacies"]
        )

        if not has_24h_in_cheapest:
            # Находим самую дешевую круглосуточную аптеку
            cheapest_24h_pharmacy = min(all_24h_pharmacies, key=lambda x: x["total_sum"])
            if cheapest_24h_pharmacy not in cheapest_pharmacies["list_pharmacies"]:
                cheapest_pharmacies["list_pharmacies"].append(cheapest_24h_pharmacy)

        has_24h_in_closest = any(
            "круглосуточно" in pharmacy.get("source", {}).get("opening_hours", "").lower()
            for pharmacy in closest_pharmacies["list_pharmacies"]
        )

        if not has_24h_in_closest:
            # Находим самую близкую круглосуточную аптеку
            closest_24h_pharmacy = min(
                all_24h_pharmacies,
                key=lambda x: haversine_distance(
                    user_lat, user_lon, x["source"]["lat"], x["source"]["lon"]
                ),
            )
            if closest_24h_pharmacy not in closest_pharmacies["list_pharmacies"]:
                closest_pharmacies["list_pharmacies"].append(closest_24h_pharmacy)

    return cheapest_pharmacies, closest_pharmacies


#Algorithm to determine distance in 2 dimensions
def haversine_distance(lat1, lon1, lat2, lon2):
    distance = math.sqrt((lat2 - lat1) ** 2 + (lon2 - lon1) ** 2)
    return distance


def is_pharmacy_open_soon(closes_at, opens_at, opening_hours):
    """Проверяет, закроется ли аптека через 1 час или если аптека работает круглосуточно."""
    almaty_tz = pytz.timezone('Asia/Almaty')
    current_time = datetime.now(almaty_tz)

    # Мок для тестов (замените на текущую дату при работе в продакшн)
    # current_time = almaty_tz.localize(datetime(2024, 12, 6, 3, 0, 0))

    # Проверка, если аптека круглосуточная
    if "круглосуточно" in (opening_hours.lower() or ""):
        return False

    try:
        # Конвертация времени открытия и закрытия
        closes_time = datetime.strptime(closes_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC).astimezone(almaty_tz)
        opens_time = datetime.strptime(opens_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC).astimezone(almaty_tz)
    except ValueError as e:
        logger.error(f"Time opens\closes parsing error: {e}")
        return True  # Если ошибка, считаем, что аптека закрыта для избежания ошибок

    # Проверяем, если аптека еще не открылась
    if current_time < opens_time:
        return False  # Если аптека еще не открылась, она не закроется скоро

    # Проверка, закроется ли аптека через 1 час или меньше
    return timedelta(0) <= closes_time - current_time <= timedelta(hours=1)


def is_pharmacy_closed(closes_at, opens_at, opening_hours):
    """Проверяет, закрыта ли аптека на момент запроса, учитывая расписание."""
    almaty_tz = pytz.timezone('Asia/Almaty')
    current_time = datetime.now(almaty_tz)

    # Мок для тестов (замените на текущую дату при работе в продакшн)
    # current_time = almaty_tz.localize(datetime(2024, 12, 6, 3, 0, 0))

    # Проверка, если аптека круглосуточная
    if "круглосуточно" in (opening_hours.lower() or ""):
        return False

    try:
        # Конвертация времени открытия и закрытия
        closes_time = datetime.strptime(closes_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC).astimezone(almaty_tz)
        opens_time = datetime.strptime(opens_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC).astimezone(almaty_tz)
    except ValueError as e:
        logger.error(f"Time opens\closes parsing error: {e}")
        return True  # Если ошибка, считаем, что аптека закрыта для избежания ошибок

    # Проверка если аптека закрыта сейчас и еще не открылась
    if current_time < opens_time:
        return True

    # Проверка если аптека уже закрылась, но еще не наступило новое время открытия
    if current_time >= closes_time and current_time < (opens_time + timedelta(days=1)):
        return True

    # Если текущее время находится в пределах открытия и закрытия
    return not (opens_time <= current_time < closes_time)


async def get_delivery_options(pharmacies, user_lat, user_lon):
    """Функция возвращает все данные о доставке для аптек без принятия решений."""

    # Проверка на наличие аптек
    if not pharmacies.get("list_pharmacies"):
        return JSONResponse(content={"error": "No pharmacies available for delivery options"}, status_code=404)

    results = []

    for pharmacy in pharmacies["list_pharmacies"]:
        source = pharmacy.get("source", {})
        products = pharmacy.get("products", [])

        if "code" not in source:
            continue

        pharmacy_total_sum = pharmacy.get("total_sum", 0)

        # Формирование списка товаров с учетом оригиналов
        items = []
        for product in products:
            if product["quantity"] >= product["quantity_desired"]:
                items.append({"sku": product["sku"], "quantity": product["quantity_desired"]})

        if not items:
            continue
        # Формируем запрос для расчета доставки
        payload = {
            "items": items,
            "dst": {
                "lat": user_lat,
                "lng": user_lon
            },
            "source_code": source["code"]
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(URL_PRICE, json=payload)
                response.raise_for_status()
                delivery_data = response.json()

                if delivery_data.get("status") == "success":
                    delivery_options = delivery_data["result"]["delivery"]

                    for option in delivery_options:
                        results.append({
                            "pharmacy": pharmacy,
                            "total_price": pharmacy_total_sum + option["price"],
                            "delivery_option": option
                        })
                else:
                    logger.error(f"Unexpected response format from URL_PRICE API: {delivery_data}")
                    return JSONResponse(
                        content={"error": "Unexpected response format from URL_PRICE API", "details": delivery_data},
                        status_code=502
                    )

            except httpx.RequestError as e:
                logger.error(f"Request error while accessing URL_PRICE: {e}")
                results.append({
                    "pharmacy": pharmacy,
                    "total_price": pharmacy_total_sum,
                    "delivery_option": None
                })
                continue
                # return JSONResponse(content={"error": "Request error while accessing URL_PRICE", "details": str(e)},
                #                     status_code=502)

            except httpx.HTTPStatusError as e:
                error_details = e.response.json() if e.response.content else {"error": str(e)}
                logger.error(f"HTTP error while accessing URL_PRICE: {e}")

                results.append({
                    "pharmacy": pharmacy,
                    "total_price": pharmacy_total_sum,
                    "delivery_option": None
                })
                continue

                # return JSONResponse(
                #     content={
                #         "error": f"HTTP error {e.response.status_code}",
                #         "details": error_details
                #     },
                #     status_code=e.response.status_code
                # )

    return results


async def best_option(delivery_data):
    """Функция для сравнения аптек и выбора лучших опций с учетом времени закрытия, цены и условий."""

    # Проверка наличия данных о доставке
    if not delivery_data:
        return JSONResponse(content={"error": "No delivery options found"}, status_code=404)

    # Проверяем, если все delivery_option равны None
    if all(option["delivery_option"] is None for option in delivery_data):
        # Сортируем аптеки по total_sum без учета круглосуточности
        sorted_pharmacies = sorted(delivery_data, key=lambda x: x["pharmacy"].get("total_sum", float("inf")))

        # Находим самую дешевую аптеку
        cheapest_pharmacy = sorted_pharmacies[0]  # Первая в списке — самая дешевая

        # Фильтруем только круглосуточные аптеки
        round_the_clock_pharmacies = [
            option for option in delivery_data
            if "круглосуточно" in option["pharmacy"]["source"].get("opening_hours", "").lower()
        ]

        # Находим самую дешевую круглосуточную аптеку
        fastest_pharmacy = min(
            round_the_clock_pharmacies,
            key=lambda x: x["pharmacy"].get("total_sum", float("inf"))
        )

        return {
            "cheapest_delivery_option": {
                "pharmacy": cheapest_pharmacy["pharmacy"],
                "delivery_option": None
            },
            "alternative_cheapest_option": None,
            "fastest_delivery_option": {
                "pharmacy": fastest_pharmacy["pharmacy"],
                "delivery_option": None
            },
            "alternative_fastest_option": None
        }

    # Проверка корректности формата данных
    for option in delivery_data:
        if "pharmacy" not in option or "total_price" not in option or "delivery_option" not in option:
            return JSONResponse(content={"error": "Invalid delivery option data format"}, status_code=502)

    cheapest_open_pharmacy = None
    cheapest_closed_pharmacy = None
    alternative_cheapest_option = None

    fastest_open_pharmacy = None
    fastest_closed_pharmacy = None
    alternative_fastest_option = None

    # Первый проход для выбора самой дешевой и самой быстрой открытых аптек
    for option in delivery_data:
        pharmacy = option.get("pharmacy", {})
        source = pharmacy.get("source", {})
        closes_at = source.get("closes_at")
        opens_at = source.get("opens_at")
        opening_hours = source.get("opening_hours", "")

        if 'code' not in source:
            logger.warning(f"Missing 'code' in pharmacy source: {source}")
            continue

        pharmacy_closed = is_pharmacy_closed(closes_at, opens_at, opening_hours)
        pharmacy_closes_soon = is_pharmacy_open_soon(closes_at, opens_at, opening_hours) if closes_at else False

        if not pharmacy_closed:
            # Самая дешевая открытая аптека
            if cheapest_open_pharmacy is None or option["total_price"] < cheapest_open_pharmacy["total_price"]:
                cheapest_open_pharmacy = option
                if not pharmacy_closes_soon:
                    alternative_cheapest_option = None
                else:
                    logger.info(f"Step 4: Pharmacy {source['code']} closes soon, looking for an alternative")
                    # Ищем самую дешевую аптеку, которая не закрывается скоро
                    if not alternative_cheapest_option:
                        for alt_option in delivery_data:
                            alt_pharmacy = alt_option.get("pharmacy", {})
                            alt_source = alt_pharmacy.get("source", {})
                            alt_closes_at = alt_source.get("closes_at")
                            alt_opens_at = alt_source.get("opens_at")
                            alt_opening_hours = alt_source.get("opening_hours", "")

                            alt_pharmacy_closes_soon = is_pharmacy_open_soon(alt_closes_at, alt_opens_at, alt_opening_hours)
                            alt_pharmacy_closed = is_pharmacy_closed(alt_closes_at, alt_opens_at, alt_opening_hours)

                            # Логика для поиска самой дешевой альтернативы, которая не закрывается скоро
                            if not alt_pharmacy_closes_soon and not alt_pharmacy_closed and \
                                    (alternative_cheapest_option is None or alt_option["total_price"] <
                                     alternative_cheapest_option["total_price"]):
                                logger.info(
                                    f"Step 5: Found alternative_cheapest_option with code {alt_source.get('code')}, works longer than 1 hour, and price {alt_option['total_price']}")
                                alternative_cheapest_option = alt_option

            # Самая быстрая открытая аптека
            if fastest_open_pharmacy is None or option["delivery_option"]["eta"] < \
                    fastest_open_pharmacy["delivery_option"]["eta"]:
                fastest_open_pharmacy = option
                if not pharmacy_closes_soon:
                    alternative_fastest_option = None
                else:
                    logger.info(
                        f"Step 4.1: Pharmacy {source['code']} closes soon, looking for an alternative fastest pharmacy")
                    # Ищем самую быструю аптеку, которая не закрывается скоро
                    if not alternative_fastest_option:
                        for alt_option in delivery_data:
                            alt_pharmacy = alt_option.get("pharmacy", {})
                            alt_source = alt_pharmacy.get("source", {})
                            alt_closes_at = alt_source.get("closes_at")
                            alt_opens_at = alt_source.get("opens_at")
                            alt_opening_hours = alt_source.get("opening_hours", "")

                            alt_pharmacy_closes_soon = is_pharmacy_open_soon(alt_closes_at, alt_opens_at, alt_opening_hours)
                            alt_pharmacy_closed = is_pharmacy_closed(alt_closes_at, alt_opens_at, alt_opening_hours)

                            # Логика для поиска самой быстрой альтернативы, которая не закрывается скоро
                            if not alt_pharmacy_closes_soon and not alt_pharmacy_closed and \
                                    (alternative_fastest_option is None or alt_option["delivery_option"]["eta"] <
                                     alternative_fastest_option["delivery_option"]["eta"]):
                                logger.info(
                                    f"Step 5.1: Found alternative_fastest_option with code {alt_source.get('code')}, works longer than 1 hour, and eta {alt_option['delivery_option']['eta']}")
                                alternative_fastest_option = alt_option

    # Второй проход для анализа закрытых аптек с учетом уже выбранных открытых аптек
    for option in delivery_data:
        pharmacy = option.get("pharmacy", {})
        source = pharmacy.get("source", {})
        closes_at = source.get("closes_at")
        opens_at = source.get("opens_at")
        opening_hours = source.get("opening_hours", "")

        if 'code' not in source:
            continue

        pharmacy_closed = is_pharmacy_closed(closes_at, opens_at, opening_hours)
        if pharmacy_closed and cheapest_open_pharmacy:

            if option["total_price"] <= cheapest_open_pharmacy["total_price"] * 0.7:
                if cheapest_closed_pharmacy is None or option["total_price"] < cheapest_closed_pharmacy["total_price"]:
                    cheapest_closed_pharmacy = option

        if pharmacy_closed and fastest_open_pharmacy:

            if option["delivery_option"]["eta"] <= fastest_open_pharmacy["delivery_option"]["eta"] * 0.7:
                if fastest_closed_pharmacy is None or option["delivery_option"]["eta"] < \
                        fastest_closed_pharmacy["delivery_option"]["eta"]:
                    fastest_closed_pharmacy = option

        if pharmacy_closed and not cheapest_open_pharmacy:
            if cheapest_closed_pharmacy is None or option["total_price"] < cheapest_closed_pharmacy["total_price"]:
                cheapest_closed_pharmacy = option

        if pharmacy_closed and not fastest_open_pharmacy:
            if fastest_closed_pharmacy is None or option["delivery_option"]["eta"] < \
                    fastest_closed_pharmacy["delivery_option"]["eta"]:
                fastest_closed_pharmacy = option


    if cheapest_closed_pharmacy and cheapest_open_pharmacy:
        logger.info("Step 7: Returning both cheapest open and cheapest closed pharmacies due to 30% discount")
        return {
            "cheapest_delivery_option": cheapest_open_pharmacy,
            "alternative_cheapest_option": cheapest_closed_pharmacy,
            "fastest_delivery_option": fastest_open_pharmacy,
            "alternative_fastest_option": fastest_closed_pharmacy
        }
    # Если открытых аптек нет, а закрытые аптеки найдены
    elif not cheapest_open_pharmacy and not fastest_open_pharmacy and cheapest_closed_pharmacy and fastest_closed_pharmacy:
        logger.info("No open pharmacies found, returning only closed pharmacies as cheapest and fastest options")
        return {
            "cheapest_delivery_option": cheapest_closed_pharmacy,
            "alternative_cheapest_option": None,
            "fastest_delivery_option": fastest_closed_pharmacy,
            "alternative_fastest_option": None
        }


    logger.info(
        f"Step 8: Returning the standard results"
    )
    return {
        "cheapest_delivery_option": cheapest_open_pharmacy,
        "alternative_cheapest_option": alternative_cheapest_option,
        "fastest_delivery_option": fastest_open_pharmacy,
        "alternative_fastest_option": alternative_fastest_option
    }


#  функция для проверки выбранных на каждой стадии отбора аптек (сохраняет списки аптек в файлы локально)
def save_response_to_file(data, file_name='data.json'):
    try:
        # Проверяем, является ли data объектом JSONResponse
        if isinstance(data, JSONResponse):
            # Преобразуем тело JSONResponse в JSON-формат
            data = data.body.decode('utf-8')  # Декодируем из байтов в строку
            data = json.loads(data)  # Преобразуем строку в JSON-объект

        # Сохраняем данные в файл
        with open(file_name, 'w', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=4)

        print(f"Данные успешно сохранены в файл: {file_name}")
    except Exception as e:
        print(f"Ошибка при сохранении данных: {e}")


# мок ручки для возврата тестовых результатов запроса поиска аптек
@app.get("/search_medicines")
async def search_medicines():
    return JSONResponse(content={
        "result": [
        {
            "source": {
                "code": "apteka_sadyhan_3mkr_20a",
                "name": "Melissa Достык 42",
                "city": "Алматы",
                "address": "пр. Достык, 42",
                "lat": 43.242913,
                "lon": 76.877005,
                "opening_hours": "Пн-Вс: 08:00-23:00",
                "network_code": "melissa",
                "with_reserve": False,
                "payment_on_site": True,
                "kaspi_red": False,
                "closes_at": "2024-10-21T18:00:00Z",
                "opens_at": "2024-10-21T03:00:00Z",
                "source_tags": [
                    {
                        "id": 1045,
                        "meta": "5",
                        "color": "#000000",
                        "name": "public_client_time_to_confirmation"
                    },
                    {
                        "id": 1040,
                        "color": "#BFEA7C",
                        "name": "public_client_good_service"
                    }
                ],
                "working_today": True,
                "payment_by_card": False
            },
            "products": [
                {
                    "source_code": "apteka_sadyhan_3mkr_20a",
                    "sku": "dc12ea01-b677-45dc-89bd-127010638f86",
                    "name": "Доспрей спрей назальный 15 мл",
                    "base_price": 100,
                    "price_with_warehouse_discount": 840,
                    "warehouse_discount": 0,
                    "quantity": 1,
                    "quantity_desired": 1,
                    "diff": 0,
                    "avg_price": 0,
                    "min_price": 0,
                    "pp_packing": "1 шт.",
                    "manufacturer_id": "ЛеКос ТОО",
                    "recipe_needed": True,
                    "strong_recipe": False
                },
                {
                    "source_code": "apteka_sadyhan_3mkr_20a",
                    "sku": "57d43666-20fd-4a46-bfe4-57cb7f8d43c9",
                    "name": "Виагра таблетки 100 мг №4",
                    "base_price": 300,
                    "price_with_warehouse_discount": 27630,
                    "warehouse_discount": 0,
                    "quantity": 1,
                    "quantity_desired": 1,
                    "diff": 0,
                    "avg_price": 0,
                    "min_price": 0,
                    "pp_packing": "4 шт",
                    "manufacturer_id": "Фарева Амбуаз",
                    "recipe_needed": True,
                    "strong_recipe": False
                }
            ],
            "total_sum": 400,
            "avg_sum": 14235,
            "min_sum": 840
        },
        {
            "source": {
                "code": "apteka_sadyhan_5mkr_19b",
                "name": "Аптека со склада Мкр 4, 30 (г.Кунаева)",
                "city": "Алматы",
                "address": "​4-й микрорайон, 30",
                "lat": 43.239826,
                "lon": 76.902216,
                "opening_hours": "Пн-Вс: 08:00-00:00",
                "network_code": "apteka_so_sklada_3",
                "with_reserve": True,
                "payment_on_site": True,
                "kaspi_red": False,
                "closes_at": "2024-10-21T19:00:00Z",
                "opens_at": "2024-10-21T03:00:00Z",
                "working_today": True,
                "payment_by_card": False
            },
            "products": [
                {
                    "source_code": "apteka_sadyhan_5mkr_19b",
                    "sku": "dc12ea01-b677-45dc-89bd-127010638f86",
                    "name": "Доспрей спрей назальный 15 мл",
                    "base_price": 1000,
                    "price_with_warehouse_discount": 685,
                    "warehouse_discount": 0,
                    "quantity": 1,
                    "quantity_desired": 1,
                    "diff": 0,
                    "avg_price": 0,
                    "min_price": 0,
                    "pp_packing": "1 шт.",
                    "manufacturer_id": "ЛеКос ТОО",
                    "recipe_needed": True,
                    "strong_recipe": False
                },
                {
                    "source_code": "apteka_sadyhan_5mkr_19b",
                    "sku": "57d43666-20fd-4a46-bfe4-57cb7f8d43c9",
                    "name": "Виагра таблетки 100 мг №4",
                    "base_price": 3000,
                    "price_with_warehouse_discount": 19700,
                    "warehouse_discount": 0,
                    "quantity": 1,
                    "quantity_desired": 1,
                    "diff": 0,
                    "avg_price": 0,
                    "min_price": 0,
                    "pp_packing": "4 шт",
                    "manufacturer_id": "Фарева Амбуаз",
                    "recipe_needed": True,
                    "strong_recipe": False
                }
            ],
            "total_sum": 4000,
            "avg_sum": 10193,
            "min_sum": 685
        },
        {
            "source": {
                "code": "apteka_sadyhan_almaty_satpaeva_90_20",
                "name": "Аптека со склада Мкр Коктем1 д 16",
                "city": "Алматы",
                "address": "Микрорайон Коктем-1, 16",
                "lat": 43.264685,
                "lon": 76.950991,
                "opening_hours": "Пн-Вс: 08:00-00:00",
                # "opening_hours": "Круглосуточно",
                "network_code": "apteka_so_sklada",
                "with_reserve": True,
                "payment_on_site": True,
                "kaspi_red": False,
                "closes_at": "2024-10-21T19:00:00Z",
                "opens_at": "2024-10-21T03:00:00Z",
                "working_today": True,
                "payment_by_card": False
            },
            "products": [
                {
                    "source_code": "apteka_sadyhan_almaty_satpaeva_90_20",
                    "sku": "dc12ea01-b677-45dc-89bd-127010638f86",
                    "name": "Доспрей спрей назальный 15 мл",
                    "base_price": 1000,
                    "price_with_warehouse_discount": 695,
                    "warehouse_discount": 0,
                    "quantity": 1,
                    "quantity_desired": 1,
                    "diff": 0,
                    "avg_price": 0,
                    "min_price": 0,
                    "pp_packing": "1 шт.",
                    "manufacturer_id": "ЛеКос ТОО",
                    "recipe_needed": True,
                    "strong_recipe": False
                },
                {
                    "source_code": "apteka_sadyhan_almaty_satpaeva_90_20",
                    "sku": "57d43666-20fd-4a46-bfe4-57cb7f8d43c9",
                    "name": "Виагра таблетки 100 мг №4",
                    "base_price": 4000,
                    "price_with_warehouse_discount": 20010,
                    "warehouse_discount": 0,
                    "quantity": 1,
                    "quantity_desired": 1,
                    "diff": 0,
                    "avg_price": 0,
                    "min_price": 0,
                    "pp_packing": "4 шт",
                    "manufacturer_id": "Фарева Амбуаз",
                    "recipe_needed": True,
                    "strong_recipe": False
                }
            ],
            "total_sum": 5000,
            "avg_sum": 10353,
            "min_sum": 695
        }
        ]
    })
