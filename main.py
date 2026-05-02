import os
import uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import bigquery
from pydantic import BaseModel
from passlib.context import CryptContext
import bcrypt # for login

PROJECT_ID = "uncle-joes-coffee-club"
DATASET_ID = "uncle_joes"

app = FastAPI(title="Uncle Joe's Coffee API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = bigquery.Client(project=PROJECT_ID)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Models
class LoginRequest(BaseModel):
    email: str
    password: str

class OrderItem(BaseModel):
    item_id: str
    item_name: str
    size: str
    quantity: int
    price: float

class OrderCreate(BaseModel):
    member_id: str
    store_id: str
    order_total: float
    items: list[OrderItem]

# UTILITY
def run_query(query: str, job_config=None):
    try:
        query_job = client.query(query, job_config=job_config)
        return [dict(row) for row in query_job]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def root():
    return {"message": "Uncle Joe's Coffee API is running"}

# -------------------------
# MEMBER AUTHENTICATION
# -------------------------

@app.post("/login")
def login(body: LoginRequest):
    """
    Verifies credentials on backend and returns member info[cite: 13, 15, 70].
    Shared Pilot Password: Coffee123!.
    """
    query = f"""
        SELECT id, first_name, last_name, email, password
        FROM `{PROJECT_ID}.{DATASET_ID}.members`
        WHERE email = @email
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", body.email)]
    )
    results = run_query(query, job_config)

    if not results:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    row = results[0]
    stored_hash = row["password"]

    # Verify submitted password against bcrypt hash in DB 
    if not bcrypt.checkpw(body.password.encode("utf-8"), stored_hash.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    return {
        "authenticated": True,
        "member_id": row["id"],
        "name": f"{row['first_name']} {row['last_name']}",
        "email": row["email"],
        "token": "simple-session-token-123" # Persists logged-in state [cite: 16]
    }

@app.post("/logout")
def logout():
    """Provides a way for the member to log out """
    return {"message": "Successfully logged out"}

# -------------------------
# MEMBER DASHBOARD
# -------------------------

@app.get("/members/{member_id}/orders")
def get_member_orders(member_id: str):
    """Returns past orders with line item details [cite: 31, 32]"""
    query = f"""
        SELECT 
            o.order_id, o.order_date, o.order_total, 
            l.city, l.state,
            i.item_name, i.size, i.quantity, i.price
        FROM `{PROJECT_ID}.{DATASET_ID}.orders` o
        JOIN `{PROJECT_ID}.{DATASET_ID}.locations` l ON o.store_id = l.id
        JOIN `{PROJECT_ID}.{DATASET_ID}.order_items` i ON o.order_id = i.order_id
        WHERE o.member_id = @member_id
        ORDER BY o.order_date DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("member_id", "STRING", member_id)]
    )
    return run_query(query, job_config)

@app.get("/members/{member_id}/points")
def get_member_points(member_id: str):
    """Calculates points: 1 per whole dollar spent, rounded down [cite: 33, 34]"""
    query = f"""
        SELECT SUM(order_total) AS total_spent
        FROM `{PROJECT_ID}.{DATASET_ID}.orders`
        WHERE member_id = @member_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("member_id", "STRING", member_id)]
    )
    results = run_query(query, job_config)
    
    total_spent = results[0]["total_spent"] or 0
    points = int(total_spent) # Rounds down 
    
    return {
        "member_id": member_id, 
        "loyalty_points": points,
        "raw_spend": round(total_spent, 2)
    }

# -------------------------
# MENU ENDPOINTS
# -------------------------

@app.get("/menu/grouped")
def get_menu_grouped():
    """
    The 'Primary' endpoint. Use this to build the main menu 
    because it provides the structure { "Category": [items...] } in one call.
    """
    # Fetch all items first
    query = f"SELECT * FROM `{PROJECT_ID}.{DATASET_ID}.menu_items` ORDER BY category, name"
    raw_items = run_query(query)
    
    # Organize them into a dictionary: { "CategoryName": [items...] }
    grouped_menu = {}
    for item in raw_items:
        cat = item['category']
        if cat not in grouped_menu:
            grouped_menu[cat] = []
        grouped_menu[cat].append(item)
    
    return grouped_menu


@app.get("/menu/search/keyword")
def search_menu_keyword(q: str):
    # This searches both the name AND category for the keyword
    query = f"""
        SELECT * FROM `{PROJECT_ID}.{DATASET_ID}.menu_items` 
        WHERE LOWER(name) LIKE @q 
           OR LOWER(category) LIKE @q
        ORDER BY category, name
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("q", "STRING", f"%{q.lower()}%")]
    )
    return run_query(query, job_config)


@app.get("/menu/{item_id}")
def get_menu_item(item_id: str):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET_ID}.menu_items`
        WHERE id = @item_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("item_id", "STRING", item_id)
        ]
    )
    results = run_query(query, job_config)
    if not results:
        raise HTTPException(status_code=404, detail="Item not found")
    return results[0]


# -------------------------
# LOCATION ENDPOINTS
# -------------------------

@app.get("/locations/states")
def get_location_states():
    query = f"""
        SELECT DISTINCT state
        FROM `{PROJECT_ID}.{DATASET_ID}.locations`
        WHERE open_for_business = TRUE
        ORDER BY state
    """
    return run_query(query)


@app.get("/locations/filter/state/{state}")
def filter_locations_by_state(state: str):
    """
    Refined: Returns a clean list of stores in a state with 
    clear amenity flags (WiFi, Drive-Thru, Delivery).
    """
    query = f"""
        SELECT 
            id, city, state, address_one, zip_code,
            wifi, drive_thru, door_dash
        FROM `{PROJECT_ID}.{DATASET_ID}.locations` 
        WHERE UPPER(state) = UPPER(@state) 
          AND open_for_business = TRUE
        ORDER BY city
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("state", "STRING", state.upper())]
    )
    results = run_query(query, job_config)
    
    # We map the results to clear labels for the frontend
    return [
        {
            "id": loc["id"],
            "city": loc["city"],
            "address": f"{loc['address_one']}, {loc['city']}, {loc['state']} {loc['zip_code']}",
            "services": {
                "free_wifi": loc["wifi"],
                "drive_thru": loc["drive_thru"],
                "doordash_available": loc["door_dash"]
            }
        } for loc in results
    ]


@app.get("/locations/filter/city/{city}")
def get_locations_by_city(city: str):
    """
    Refined: Now matches the 'State' filter format so the frontend 
    can use the same code to display search results.
    """
    query = f"""
        SELECT 
            id, city, state, address_one, zip_code,
            wifi, drive_thru, door_dash
        FROM `{PROJECT_ID}.{DATASET_ID}.locations` 
        WHERE LOWER(city) = LOWER(@city) 
          AND open_for_business = TRUE
        ORDER BY address_one
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("city", "STRING", city.lower())]
    )
    results = run_query(query, job_config)
    
    if not results:
        raise HTTPException(status_code=404, detail="No locations found for this city")

    return [
        {
            "id": loc["id"],
            "city": loc["city"],
            "address": f"{loc['address_one']}, {loc['city']}, {loc['state']} {loc['zip_code']}",
            "services": {
                "free_wifi": loc["wifi"],
                "drive_thru": loc["drive_thru"],
                "doordash_available": loc["door_dash"]
            }
        } for loc in results
    ]


@app.get("/locations/{location_id}")
def get_location_details(location_id: str):
    """
    Refined: Provides store identity, amenities, and human-readable 
    hours. Excludes latitude/longitude.
    """
    query = f"SELECT * FROM `{PROJECT_ID}.{DATASET_ID}.locations` WHERE id = @id"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", location_id)]
    )
    results = run_query(query, job_config)
    
    if not results:
        raise HTTPException(status_code=404, detail="Location not found")
    
    loc = results[0]
    return {
        "store_info": {
            "name": f"Uncle Joe's Coffee - {loc['city']}",
            "address": f"{loc['address_one']}, {loc['city']}, {loc['state']} {loc['zip_code']}",
            "phone": loc['phone_number'],
            "email": loc['email']
        },
        "amenities": {
            "wifi_available": loc['wifi'],
            "drive_thru_lane": loc['drive_thru'],
            "doordash_partner": loc['door_dash']
        },
        "operating_hours": {
            "Monday": f"{format_time(loc['hours_monday_open'])} - {format_time(loc['hours_monday_close'])}",
            "Tuesday": f"{format_time(loc['hours_tuesday_open'])} - {format_time(loc['hours_tuesday_close'])}",
            "Wednesday": f"{format_time(loc['hours_wednesday_open'])} - {format_time(loc['hours_wednesday_close'])}",
            "Thursday": f"{format_time(loc['hours_thursday_open'])} - {format_time(loc['hours_thursday_close'])}",
            "Friday": f"{format_time(loc['hours_friday_open'])} - {format_time(loc['hours_friday_close'])}",
            "Saturday": f"{format_time(loc['hours_saturday_open'])} - {format_time(loc['hours_saturday_close'])}",
            "Sunday": f"{format_time(loc['hours_sunday_open'])} - {format_time(loc['hours_sunday_close'])}"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))