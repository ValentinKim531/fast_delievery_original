import os
import psycopg2
from psycopg2 import sql
from fastapi import FastAPI, Request
from pydantic import BaseModel
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
    # Receive the front end data (city hash, sku's, user address)
    request_data = await request.json()
    encoded_city = request_data.get("city")  # Encoded city hash
    sku_data = request_data.get("skus", [])  # List of SKU items
    address = request_data.get("address", {})  # User address


    #Save the latitude and longitude of user
    user_adress = request_data.get("address", {}).get("lng")
    user_lat = request_data.get("address", {}).get("lat")
    user_lon = request_data.get("address", {}).get("lng")
    
    # Validate the incoming data
    if not encoded_city or not sku_data or user_lat is None or user_lon is None:
        return {"error": "City, SKU data, and user coordinates are required"}
    if not encoded_city or not sku_data:
        return {"error": "City and SKU data are required"}

    # Build the payload
    payload = [{"sku": item["sku"], "count_desired": item["count_desired"]} for item in sku_data]

    # Perform the search for medicines in pharmacies
    pharmacies = await find_medicines_in_pharmacies(encoded_city, payload)
    no_variants = False
    #Save only pharmacies with all sku's in stock
    filtered_pharmacies = await filter_pharmacies(pharmacies)


    #If there is no pharmacy with full stock
    all_pharmacies_empty = not filtered_pharmacies.get("filtered_pharmacies")
    if all_pharmacies_empty:
        logger.info("No pharmacies")
        return 0

    #Get several pharmacies with cheapest sku's
    cheapest_pharmacies = await get_top_cheapest_pharmacies(filtered_pharmacies)
    #Get 2 closest Pharmacies
    closest_pharmacies = await get_top_closest_pharmacies(filtered_pharmacies, user_lat, user_lon)
    #Compare Check delivery price for 2 closest pharmacies and 3 cheapest pharmacies
    delivery_options1 = await get_delivery_options(closest_pharmacies, user_lat, user_lon, sku_data)
    delivery_options2 = await get_delivery_options(cheapest_pharmacies, user_lat, user_lon, sku_data)
    result = await best_option(delivery_options1, delivery_options2)
    return result


async def find_medicines_in_pharmacies(encoded_city, payload):
    async with httpx.AsyncClient() as client:
        response = await client.post(URL_SEARCH, params=encoded_city, json=payload)
        response.raise_for_status()  # Raise an error for bad responses
        return response.json()  # Return the JSON response


#Save only pharmacies with all sku's in stock
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

    return {"filtered_pharmacies": filtered_pharmacies}


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
        
        # Calculate Euclidean distance
        distance = haversine_distance(user_lat, user_lon, pharmacy_lat, pharmacy_lon)
        
        # Add the pharmacy and its distance to the list
        pharmacies_with_distance.append({"pharmacy": pharmacy, "distance": distance})
    
    # Sort pharmacies by distance
    sorted_pharmacies = sorted(pharmacies_with_distance, key=lambda x: x["distance"])
    
    # Get the top 2 closest pharmacies
    closest_pharmacies = [item["pharmacy"] for item in sorted_pharmacies[:2]]
    
    return {"list_pharmacies": closest_pharmacies}


#Algorithm to determine distance in 2 dimensions
def haversine_distance(lat1, lon1, lat2, lon2):
    distance = math.sqrt((lat2 - lat1) ** 2 + (lon2 - lon1) ** 2)
    return distance


async def get_delivery_options(pharmacies, user_lat, user_lon, sku_data):
    cheapest_option = None
    fastest_option = None

    for pharmacy in pharmacies["list_pharmacies"]:
        # Build the POST request payload
        payload = {
            "items": sku_data,  # Pass the SKU data
            "dst": {
                "lat": user_lat,
                "lng": user_lon
            },
            "source_code": pharmacy["source"]["code"]
        }

        # Send the POST request to the external endpoint
        async with httpx.AsyncClient() as client:
            response = await client.post(URL_PRICE, json=payload)
            response.raise_for_status()
            delivery_data = response.json()  # Parse the JSON response

        # Extract pricing and delivery options from the response
        if delivery_data.get("status") == "success":
            items_price = delivery_data["result"]["items_price"]
            delivery_options = delivery_data["result"]["delivery"]

            # Compare for cheapest option
            for option in delivery_options:
                total_price = items_price + option["price"]  # Item price + delivery price
                if cheapest_option is None or total_price < cheapest_option["total_price"]:
                    cheapest_option = {
                        "pharmacy": pharmacy,
                        "total_price": total_price,
                        "delivery_option": option
                    }

                # Compare for fastest option
                if fastest_option is None or option["eta"] < fastest_option["delivery_option"]["eta"]:
                    fastest_option = {
                        "pharmacy": pharmacy,
                        "total_price": total_price,
                        "delivery_option": option
                    }

    return {
        "cheapest_delivery_option": cheapest_option,
        "fastest_delivery_option": fastest_option
    }



async def best_option(var1, var2):
    # Initialize cheapest and fastest options
    best_cheapest_option = None
    best_fastest_option = None

    # Get the cheapest and fastest options from var1 and var2
    cheapest_option_1 = var1.get("cheapest_delivery_option")
    fastest_option_1 = var1.get("fastest_delivery_option")
    cheapest_option_2 = var2.get("cheapest_delivery_option")
    fastest_option_2 = var2.get("fastest_delivery_option")

    # Compare the cheapest options
    if cheapest_option_1 and cheapest_option_2:
        if cheapest_option_1["total_price"] <= cheapest_option_2["total_price"]:
            best_cheapest_option = cheapest_option_1
        else:
            best_cheapest_option = cheapest_option_2
    elif cheapest_option_1:
        best_cheapest_option = cheapest_option_1
    elif cheapest_option_2:
        best_cheapest_option = cheapest_option_2

    # Compare the fastest options
    if fastest_option_1 and fastest_option_2:
        if fastest_option_1["delivery_option"]["eta"] <= fastest_option_2["delivery_option"]["eta"]:
            best_fastest_option = fastest_option_1
        else:
            best_fastest_option = fastest_option_2
    elif fastest_option_1:
        best_fastest_option = fastest_option_1
    elif fastest_option_2:
        best_fastest_option = fastest_option_2

    # Return the best options
    return {
        "best_cheapest_option": best_cheapest_option,
        "best_fastest_option": best_fastest_option
    }
