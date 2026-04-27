import os
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


class LoginRequest(BaseModel):
    email: str
    password: str


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
# MENU ENDPOINTS
# -------------------------

@app.get("/menu")
def get_menu():
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET_ID}.menu_items`
        ORDER BY category, name
    """
    return run_query(query)


@app.get("/menu/categories")
def get_menu_categories():
    query = f"""
        SELECT DISTINCT category
        FROM `{PROJECT_ID}.{DATASET_ID}.menu_items`
        ORDER BY category
    """
    return run_query(query)


@app.get("/menu/category/{category}")
def get_menu_by_category(category: str):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET_ID}.menu_items`
        WHERE LOWER(category) = LOWER(@category)
        ORDER BY name
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("category", "STRING", category)
        ]
    )
    results = run_query(query, job_config)
    if not results:
        raise HTTPException(status_code=404, detail="No menu items found for this category")
    return results


@app.get("/menu/search")
def search_menu(item_name: str):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET_ID}.menu_items`
        WHERE LOWER(TRIM(name)) LIKE @name
        ORDER BY category, name
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("name", "STRING", f"%{item_name.lower()}%")
        ]
    )
    results = run_query(query, job_config)
    if not results:
        raise HTTPException(status_code=404, detail="No matching menu items found")
    return results


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

@app.get("/locations")
def get_locations():
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET_ID}.locations`
        WHERE open_for_business = TRUE
        ORDER BY state, city
    """
    return run_query(query)


@app.get("/locations/states")
def get_location_states():
    query = f"""
        SELECT DISTINCT state
        FROM `{PROJECT_ID}.{DATASET_ID}.locations`
        WHERE open_for_business = TRUE
        ORDER BY state
    """
    return run_query(query)


@app.get("/locations/filter/{state}")
def get_locations_by_state(state: str):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET_ID}.locations`
        WHERE UPPER(state) = UPPER(@state)
          AND open_for_business = TRUE
        ORDER BY city, address_one
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("state", "STRING", state.upper())
        ]
    )
    results = run_query(query, job_config)
    if not results:
        raise HTTPException(status_code=404, detail="No locations found for this state")
    return results


@app.get("/locations/city/{city}")
def get_locations_by_city(city: str):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET_ID}.locations`
        WHERE LOWER(city) = LOWER(@city)
          AND open_for_business = TRUE
        ORDER BY address_one
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("city", "STRING", city)
        ]
    )
    results = run_query(query, job_config)
    if not results:
        raise HTTPException(status_code=404, detail="No locations found for this city")
    return results


@app.get("/locations/{location_id}")
def get_location(location_id: str):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET_ID}.locations`
        WHERE id = @id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", location_id)
        ]
    )
    results = run_query(query, job_config)
    if not results:
        raise HTTPException(status_code=404, detail="Location not found")
    return results[0]


# -------------------------
# MEMBER ENDPOINTS
# -------------------------

@app.post("/login")
def login(body: LoginRequest):
    submitted_bytes = body.password.encode("utf-8")
    
    # Matches the example's parameterized query style
    query = """
        SELECT id, first_name, last_name, email, password
        FROM `{project}.{dataset}.members`
        WHERE email = @email
        LIMIT 1
    """.format(project=PROJECT_ID, dataset=DATASET_ID)

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("email", "STRING", body.email),
        ]
    )

    # Use the client directly to match the example pattern
    results = list(client.query(query, job_config=job_config).result())

    if not results:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    row = results[0]
    stored_hash: str = row["password"]

    # Verify the submitted password against the bcrypt hash from the DB
    if not bcrypt.checkpw(submitted_bytes, stored_hash.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    return {
        "authenticated": True,
        "member_id": row["id"],
        "name": f"{row['first_name']} {row['last_name']}",
        "email": row["email"],
        "token": "simple-session-token-123" # Required to show state persistence
    }


@app.get("/members/{member_id}/orders")
def get_member_orders(member_id: str):
    # This 3-table JOIN pulls the city/state and the itemized details in one go
    query = """
        SELECT 
            o.order_id, 
            o.order_date, 
            o.order_total, 
            l.city, 
            l.state,
            i.item_name, 
            i.size, 
            i.quantity, 
            i.price
        FROM `{project}.{dataset}.orders` o
        JOIN `{project}.{dataset}.locations` l ON o.store_id = l.id
        JOIN `{project}.{dataset}.order_items` i ON o.order_id = i.order_id
        WHERE o.member_id = @member_id
        ORDER BY o.order_date DESC
    """.format(project=PROJECT_ID, dataset=DATASET_ID)
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("member_id", "STRING", member_id)
        ]
    )
    return run_query(query, job_config)


@app.get("/members/{member_id}/points")
def get_member_points(member_id: str):
    query = """
        SELECT SUM(order_total) AS total_spent
        FROM `{project}.{dataset}.orders`
        WHERE member_id = @member_id
    """.format(project=PROJECT_ID, dataset=DATASET_ID)
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("member_id", "STRING", member_id)
        ]
    )
    results = run_query(query, job_config)
    
    # Logic: Rounding down the total spent to the nearest whole dollar
    total_spent = results[0]["total_spent"] or 0
    points = int(total_spent) 
    
    return {
        "member_id": member_id, 
        "loyalty_points": points,
        "raw_spend": round(total_spent, 2)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))