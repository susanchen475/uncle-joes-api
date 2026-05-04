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
    Authenticates a member by email and password[cite: 70].
    Verifies the bcrypt hash against the members table[cite: 15].
    Pilot Password Requirement: Coffee123![cite: 10].
    """
    query = f"""
        SELECT id, first_name, last_name, email, password, home_store
        FROM `{PROJECT_ID}.{DATASET_ID}.members`
        WHERE email = @email
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", body.email)]
    )
    results = run_query(query, job_config)

    if not results:
        raise HTTPException(status_code=401, detail="Account not found.")

    row = results[0]
   
    # Password verification must happen on the backend [cite: 24]
    if not bcrypt.checkpw(body.password.encode("utf-8"), row["password"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid password.")

    return {
        "authenticated": True,
        "member_id": row["id"],
        "name": f"{row['first_name']} {row['last_name']}",
        "email": row["email"],
        "home_store_id": row["home_store_id"], # Supports home store highlight [cite: 63]
        "token": "simple-session-token-123" # Persists logged-in state
    }

@app.post("/logout")
def logout():
    """Provides a way for the member to log out [cite: 19]"""
    return {"message": "Successfully logged out"}

# -------------------------
# MEMBER DASHBOARD & PROFILE
# -------------------------

@app.get("/members/{member_id}/profile")
def get_member_profile(member_id: str):
    """Returns profile info: name, email, phone, and home store [cite: 58, 62]"""
    query = f"""
        SELECT m.first_name, m.last_name, m.email, m.phone, l.store_name as home_store_name
        FROM `{PROJECT_ID}.{DATASET_ID}.members` m
        LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.locations` l ON m.home_store_id = l.id
        WHERE m.id = @id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", member_id)]
    )
    results = run_query(query, job_config)
    if not results:
        raise HTTPException(status_code=404, detail="Member not found")
    return results[0]

@app.get("/members/{member_id}/orders")
def get_member_orders(member_id: str):
    """Returns order history with line item details [cite: 31, 32, 70]"""
    query = f"""
        SELECT
            o.order_id, o.order_date, o.order_total,
            l.store_name, l.city, l.state,
            i.item_name, i.size, i.quantity, i.price
        FROM `{PROJECT_ID}.{DATASET_ID}.orders` o
        JOIN `{PROJECT_ID}.{DATASET_ID}.locations` l ON o.store_id = l.id
        LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.order_items` i ON o.order_id = i.order_id
        WHERE o.member_id = @member_id
        ORDER BY o.order_date DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("member_id", "STRING", member_id)]
    )
    results = run_query(query, job_config)

    # Group flat rows into a nested structure: one object per order containing a list of items
    orders = []
    order_indices = {}  # Map order_id to its position in the orders list to preserve sorting

    for row in results:
        order_id = row["order_id"]
        if order_id not in order_indices:
            order_indices[order_id] = len(orders)
            orders.append({
                "order_id": order_id,
                "order_date": row["order_date"],
                "order_total": row["order_total"],
                "store_name": row["store_name"],
                "location": f"{row['city']}, {row['state']}",
                "items": []
            })
       
        if row["item_name"]:
            orders[order_indices[order_id]]["items"].append({
                "item_name": row["item_name"],
                "size": row["size"],
                "quantity": row["quantity"],
                "price_per_item": row["price"]
            })

    return orders

@app.get("/members/{member_id}/points")
def get_member_points(member_id: str):
    """
    Calculates points: 1 per whole dollar, rounded down[cite: 34, 70].
    Returns a breakdown of points earned per order[cite: 64].
    """
    query = f"""
        SELECT order_id, order_date, order_total, CAST(FLOOR(order_total) AS INT64) as points_earned
        FROM `{PROJECT_ID}.{DATASET_ID}.orders`
        WHERE member_id = @member_id
        ORDER BY order_date DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("member_id", "STRING", member_id)]
    )
    history = run_query(query, job_config)
   
    total_points = sum(item['points_earned'] for item in history)
   
    return {
        "total_points": total_points,
        "history": history
    }

# -------------------------
# MENU & ORDERING
# -------------------------

@app.get("/menu")
def get_menu():
    """Browse menu by category or search [cite: 42, 44]"""
    query = f"SELECT * FROM `{PROJECT_ID}.{DATASET_ID}.menu_items` ORDER BY category, name"
    return run_query(query)

@app.get("/menu/{item_id}")
def get_menu_item(item_id: str):
    """Detailed view for a single menu item [cite: 46]"""
    query = f"SELECT * FROM `{PROJECT_ID}.{DATASET_ID}.menu_items` WHERE id = @item_id"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("item_id", "STRING", item_id)]
    )
    results = run_query(query, job_config)
    if not results:
        raise HTTPException(status_code=404, detail="Item not found")
    return results[0]

@app.post("/orders")
def place_order(order: OrderCreate):
    """Allows logged-in members to submit a new order (pay-at-store model) [cite: 48]"""
    order_id = str(uuid.uuid4())
    order_date = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    # 1. Insert order header
    order_query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET_ID}.orders` (order_id, member_id, store_id, order_date, order_total)
        VALUES (@oid, @mid, @sid, @odate, @ototal)
    """
   
    # 2. Insert line items
    # We build a bulk insert for the items
    item_placeholders = []
    item_params = [
        bigquery.ScalarQueryParameter("oid", "STRING", order_id),
        bigquery.ScalarQueryParameter("mid", "STRING", order.member_id),
        bigquery.ScalarQueryParameter("sid", "STRING", order.store_id),
        bigquery.ScalarQueryParameter("odate", "DATETIME", order_date),
        bigquery.ScalarQueryParameter("ototal", "FLOAT", order.order_total),
    ]

    for i, item in enumerate(order.items):
        item_placeholders.append(f"(@oid, @iid_{i}, @iname_{i}, @isize_{i}, @iqty_{i}, @iprice_{i})")
        item_params.extend([
            bigquery.ScalarQueryParameter(f"iid_{i}", "STRING", item.item_id),
            bigquery.ScalarQueryParameter(f"iname_{i}", "STRING", item.item_name),
            bigquery.ScalarQueryParameter(f"isize_{i}", "STRING", item.size),
            bigquery.ScalarQueryParameter(f"iqty_{i}", "INT64", item.quantity),
            bigquery.ScalarQueryParameter(f"iprice_{i}", "FLOAT", item.price),
        ])

    items_query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET_ID}.order_items` (order_id, menu_item_id, item_name, size, quantity, price)
        VALUES {", ".join(item_placeholders)}
    """

    full_script = f"{order_query}; {items_query}"
   
    try:
        job_config = bigquery.QueryJobConfig(query_parameters=item_params)
        client.query(full_script, job_config=job_config).result()
        return {"status": "success", "order_id": order_id, "message": "Order submitted successfully!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/orders/{order_id}/confirmation")
def get_order_confirmation(order_id: str):
    """Returns a summary for the order confirmation screen [cite: 49]"""
    query = f"""
        SELECT o.order_id, o.order_total, l.store_name, l.address_one, l.city
        FROM `{PROJECT_ID}.{DATASET_ID}.orders` o
        JOIN `{PROJECT_ID}.{DATASET_ID}.locations` l ON o.store_id = l.id
        WHERE o.order_id = @oid
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("oid", "STRING", order_id)]
    )
    results = run_query(query, job_config)
    if not results:
        raise HTTPException(status_code=404, detail="Order not found")
    return results[0]

# -------------------------
# LOCATION ENHANCEMENTS
# -------------------------

@app.get("/locations")
def get_locations():
    """
    Returns locations with amenities and map coordinates[cite: 52, 54].
    Includes latitude and longitude for Map Integration[cite: 53].
    """
    query = f"SELECT * FROM `{PROJECT_ID}.{DATASET_ID}.locations` WHERE open_for_business = TRUE ORDER BY state, city"
    return run_query(query)

@app.get("/locations/search")
def search_locations(query_str: str):
    """Filter or search locations by city or state [cite: 51]"""
    query = f"""
        SELECT * FROM `{PROJECT_ID}.{DATASET_ID}.locations`
        WHERE (LOWER(city) LIKE @q OR LOWER(state) LIKE @q)
        AND open_for_business = TRUE
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("q", "STRING", f"%{query_str.lower()}%")]
    )
    return run_query(query, job_config)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))